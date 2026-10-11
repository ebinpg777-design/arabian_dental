# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1.

import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_round

_logger = logging.getLogger(__name__)


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    # -----------------------------------------------------------------------
    # Relational fields
    # -----------------------------------------------------------------------

    advance_bill_ids = fields.One2many(
        comodel_name="account.move",
        inverse_name="advance_purchase_order_id",
        string="Advance Bills",
        domain=[("is_purchase_advance_bill", "=", True)],
        copy=False,
    )

    # -----------------------------------------------------------------------
    # Computed summary fields
    # -----------------------------------------------------------------------

    advance_bill_count = fields.Integer(
        string="Advance Bill Count",
        compute="_compute_advance_bill_summary",
    )
    advance_bill_total = fields.Monetary(
        string="Advance Billed",
        compute="_compute_advance_bill_summary",
        currency_field="currency_id",
        help="Total face-value of all advance bills (posted + draft) for this PO.",
    )
    advance_bill_posted_total = fields.Monetary(
        string="Advance Committed",
        compute="_compute_advance_bill_summary",
        currency_field="currency_id",
        help="Total face-value of POSTED advance bills — the committed advance that "
             "is deducted from the main bills to keep the books balanced.",
    )
    advance_bill_paid = fields.Monetary(
        string="Advance Paid",
        compute="_compute_advance_bill_summary",
        currency_field="currency_id",
        help="Amount actually settled on posted advance bills (cash view only).",
    )
    advance_bill_pending = fields.Monetary(
        string="Advance Pending Payment",
        compute="_compute_advance_bill_summary",
        currency_field="currency_id",
        help="Posted advance bills not yet fully paid.",
    )
    advance_bill_deducted = fields.Monetary(
        string="Advance Deducted",
        compute="_compute_advance_bill_summary",
        currency_field="currency_id",
        help="Amount already deducted from main vendor bills of this PO.",
    )
    advance_bill_available = fields.Monetary(
        string="Advance Available",
        compute="_compute_advance_bill_summary",
        currency_field="currency_id",
        help="Committed advance still available to be deducted from future main "
             "bills = posted advance bills − already deducted.",
    )
    advance_bill_status = fields.Selection(
        selection=[
            ("none",      "No Advance Bills"),
            ("draft",     "Draft – Awaiting Posting"),
            ("unpaid",    "Posted – Awaiting Payment"),
            ("partial",   "Partially Paid"),
            ("paid",      "Fully Paid"),
            ("deducted",  "Fully Deducted from Bills"),
        ],
        string="Advance Status",
        compute="_compute_advance_bill_summary",
        help="Overall lifecycle status of advance bills on this order.",
    )

    # -----------------------------------------------------------------------
    # Computes
    # -----------------------------------------------------------------------

    @api.depends(
        "advance_bill_ids.state",
        "advance_bill_ids.payment_state",
        "advance_bill_ids.amount_total",
        "advance_bill_ids.amount_residual",
        "invoice_ids.invoice_line_ids.advance_source_bill_id",
        "invoice_ids.invoice_line_ids.price_subtotal",
        "invoice_ids.state",
        "currency_id",
    )
    def _compute_advance_bill_summary(self):
        for order in self:
            currency = order.currency_id or order.company_id.currency_id
            bills = order.advance_bill_ids

            order.advance_bill_count = len(bills)

            if not bills:
                order.advance_bill_total = 0.0
                order.advance_bill_posted_total = 0.0
                order.advance_bill_paid = 0.0
                order.advance_bill_pending = 0.0
                order.advance_bill_deducted = 0.0
                order.advance_bill_available = 0.0
                order.advance_bill_status = "none"
                continue

            total = 0.0
            posted_total = 0.0
            paid = 0.0
            pending = 0.0
            for bill in bills:
                conv_date = bill.invoice_date or bill.date or fields.Date.context_today(order)
                bill_currency = bill.currency_id or order.company_id.currency_id
                face_val = self._advance_convert(
                    bill.amount_total, bill_currency, currency,
                    order.company_id, conv_date)
                total += face_val
                if bill.state == "posted":
                    posted_total += face_val
                    residual = self._advance_convert(
                        bill.amount_residual, bill_currency, currency,
                        order.company_id, conv_date)
                    pending += max(0.0, residual)
                    paid += max(0.0, face_val - max(0.0, residual))

            deducted = order._get_already_deducted_amount()
            # Deduction follows the POSTED face value of the advance bills (not the
            # cash paid): the full advance is credited back against the advance
            # (bridge) account on the main bill, so the advance account always
            # clears to zero and the vendor is never over-billed — regardless of
            # whether the advance bill has been paid yet.
            available = max(0.0, posted_total - deducted)

            order.advance_bill_total = float_round(total, precision_rounding=currency.rounding)
            order.advance_bill_posted_total = float_round(posted_total, precision_rounding=currency.rounding)
            order.advance_bill_paid = float_round(paid, precision_rounding=currency.rounding)
            order.advance_bill_pending = float_round(pending, precision_rounding=currency.rounding)
            order.advance_bill_deducted = float_round(deducted, precision_rounding=currency.rounding)
            order.advance_bill_available = float_round(available, precision_rounding=currency.rounding)

            # Overall status
            posted = bills.filtered(lambda b: b.state == "posted")
            if not posted:
                order.advance_bill_status = "draft"
            elif currency.is_zero(available) and not currency.is_zero(posted_total):
                order.advance_bill_status = "deducted"
            elif currency.is_zero(pending):
                order.advance_bill_status = "paid"
            elif currency.is_zero(paid):
                order.advance_bill_status = "unpaid"
            else:
                order.advance_bill_status = "partial"

    @staticmethod
    def _advance_convert(amount, from_currency, to_currency, company, date):
        if from_currency == to_currency:
            return amount
        return from_currency._convert(amount, to_currency, company, date)

    # -----------------------------------------------------------------------
    # Advance account resolution
    # -----------------------------------------------------------------------

    def _get_advance_account(self, raise_if_missing=False):
        """Resolve the advance (bridge) account for this order.

        Priority (most specific first):
          1. Product of the order's lines (company-specific).
          2. Product category of the order's products (company-specific).
          3. Company-level default (Purchase > Settings).

        Within a layer, the first configured account across the order's
        products wins. Returns an empty recordset when nothing is configured,
        unless ``raise_if_missing`` is set, in which case a clear UserError is
        raised.
        """
        self.ensure_one()
        company = self.company_id or self.env.company
        # Switch company context once on the whole recordset — the advance
        # account properties are company-dependent.
        products = self.order_line.filtered(
            lambda l: not l.display_type).product_id.with_company(company)
        # 1. Product-level account — first configured wins (a product override
        # always beats a category default, hence a separate pass).
        for product in products:
            account = product._get_purchase_advance_account()
            if account:
                return account
        # 2. Product category (walking ancestors) — first configured wins.
        for categ in products.categ_id:
            account = categ._get_purchase_advance_account()
            if account:
                return account
        # 3. Company default (optionally raising).
        if raise_if_missing:
            return company._require_purchase_advance_account()
        return company._get_purchase_advance_account()

    # -----------------------------------------------------------------------
    # Settlement helpers
    # -----------------------------------------------------------------------

    def _get_posted_advance_bills(self):
        """Posted advance vendor bills of this order, oldest first."""
        self.ensure_one()
        return self.advance_bill_ids.filtered(
            lambda b: b.state == "posted" and b.move_type == "in_invoice"
        ).sorted(lambda b: (b.invoice_date or b.date or fields.Date.context_today(self), b.id))

    def _advance_bill_face_value(self, bill):
        """Posted face value of an advance bill, expressed in the PO currency."""
        self.ensure_one()
        currency = self.currency_id or self.company_id.currency_id
        conv_date = bill.invoice_date or bill.date or fields.Date.context_today(self)
        bill_currency = bill.currency_id or self.company_id.currency_id
        return self._advance_convert(
            bill.amount_total, bill_currency, currency, self.company_id, conv_date)

    def _advance_bill_deducted(self, bill, exclude_invoice=None):
        """How much of `bill` has already been deducted across this PO's main
        bills, in PO currency. Optionally exclude one invoice (used while it is
        being recomputed so its own lines don't count against itself)."""
        self.ensure_one()
        currency = self.currency_id or self.company_id.currency_id
        lines = self.env["account.move.line"].search([
            ("advance_source_bill_id", "=", bill.id),
            ("move_id.state", "!=", "cancel"),
            ("move_id.is_purchase_advance_bill", "=", False),
        ])
        total = 0.0
        for line in lines:
            if exclude_invoice and line.move_id == exclude_invoice:
                continue
            move = line.move_id
            conv_date = move.invoice_date or move.date or fields.Date.context_today(self)
            move_currency = move.currency_id or self.company_id.currency_id
            total += self._advance_convert(
                abs(line.price_subtotal), move_currency, currency,
                self.company_id, conv_date)
        return total

    def _get_already_deducted_amount(self):
        """Sum of all advance deduction lines already written onto this PO's
        main bills, in PO currency."""
        self.ensure_one()
        return sum(self._advance_bill_deducted(bill) for bill in self.advance_bill_ids)

    def _get_advance_deductible_amount(self, exclude_invoice=None):
        """Committed advance still available to apply to main bills, PO currency.

        Follows the POSTED face value of the advance bills minus whatever has
        already been deducted on this PO's main bills."""
        self.ensure_one()
        currency = self.currency_id or self.company_id.currency_id
        total = 0.0
        for bill in self._get_posted_advance_bills():
            remaining = self._advance_bill_face_value(bill) \
                - self._advance_bill_deducted(bill, exclude_invoice=exclude_invoice)
            total += max(0.0, remaining)
        return float_round(max(0.0, total), precision_rounding=currency.rounding)

    def _get_invoice_order_base(self, invoice):
        """Untaxed value the *real* lines of this order contribute to `invoice`.

        Used as the cap so an order's advance deduction can never exceed what the
        order is actually being billed on this document (which would flip the bill
        negative / create a phantom debit on the partner)."""
        self.ensure_one()
        base = 0.0
        for line in invoice.invoice_line_ids:
            # In Odoo 18 real product lines carry an empty display_type; only
            # section/note lines are non-accounting.
            if line.display_type in ('line_section', 'line_note'):
                continue
            if line.advance_source_bill_id:
                continue  # skip existing advance-deduction lines (any order)
            if line.purchase_order_id and line.purchase_order_id != self:
                continue  # another order's lines on a consolidated bill
            base += line.price_subtotal
        return max(0.0, base)

    def _add_advance_deduction_lines(self, invoice):
        """Add (or refresh) one advance-deduction line PER posted advance bill on
        a draft main bill. Each line references its source advance bill and posts
        to that bill's advance account, so the advance account clears to zero."""
        self.ensure_one()
        company = self.company_id
        currency = self.currency_id or company.currency_id
        inv_currency = invoice.currency_id or company.currency_id
        conv_date = invoice.invoice_date or invoice.date or fields.Date.context_today(self)

        # Remove THIS invoice's existing deduction lines for this order's advance
        # bills FIRST, so amounts are recomputed cleanly (idempotent re-trigger).
        own_bills = self.advance_bill_ids
        existing = invoice.invoice_line_ids.filtered(
            lambda l: l.advance_source_bill_id in own_bills)
        if existing:
            existing.unlink()

        posted_bills = self._get_posted_advance_bills()
        if not posted_bills:
            return

        # Cap: never drive this order's billed base on THIS document negative.
        # The base is computed in invoice currency from the invoice lines.
        cap_remaining = self._get_invoice_order_base(invoice)

        new_lines = []
        applied_total = 0.0
        for bill in posted_bills:
            remaining = self._advance_bill_face_value(bill) \
                - self._advance_bill_deducted(bill, exclude_invoice=invoice)
            remaining = float_round(max(0.0, remaining), precision_rounding=currency.rounding)
            if currency.is_zero(remaining):
                continue

            # Convert the remaining advance to the invoice currency.
            deductible = remaining
            if currency != inv_currency:
                deductible = currency._convert(remaining, inv_currency, company, conv_date)

            # Cap against the headroom still left on this bill.
            if float_compare(deductible, cap_remaining,
                             precision_rounding=inv_currency.rounding) > 0:
                deductible = cap_remaining
            deductible = float_round(deductible, precision_rounding=inv_currency.rounding)
            if float_compare(deductible, 0.0,
                             precision_rounding=inv_currency.rounding) <= 0:
                break  # no headroom left on this bill; leave the rest for later

            cap_remaining = float_round(cap_remaining - deductible,
                                        precision_rounding=inv_currency.rounding)
            applied_total += deductible

            # Deduct against the SAME account the advance bill posted to, so the
            # bridge account clears to zero. Fall back to the order's resolved
            # account (category → company) only if the bill has no usable line.
            advance_account = bill.invoice_line_ids.filtered(
                lambda l: l.display_type not in ('line_section', 'line_note')
                and l.account_id)[:1].account_id \
                or self._get_advance_account(raise_if_missing=True)
            new_lines.append((0, 0, {
                "name": _("Advance Deduction – %(bill)s (%(po)s)",
                          bill=bill.name or _("Advance"), po=self.name),
                "account_id": advance_account.id,
                "quantity": 1.0,
                "price_unit": -deductible,
                "tax_ids": [],
                "sequence": 9999,
                "advance_source_bill_id": bill.id,
            }))

        if not new_lines:
            return

        invoice.write({"invoice_line_ids": new_lines})
        invoice.message_post(body=_(
            "Advance deduction of %(amount)s applied from %(count)s posted advance "
            "bill(s) of purchase order %(order)s.",
            amount=inv_currency.format(applied_total),
            count=len(new_lines),
            order=self.name,
        ))

    # -----------------------------------------------------------------------
    # Override action_create_invoice → inject deduction lines
    # -----------------------------------------------------------------------

    def action_create_invoice(self):
        result = super().action_create_invoice()

        # Find newly created bills from the result action
        if isinstance(result, dict) and result.get("res_model") == "account.move":
            if result.get("res_id"):
                new_bills = self.env["account.move"].browse(result["res_id"])
            else:
                domain = result.get("domain")
                new_bills = self.env["account.move"].search(domain) if domain else self.env["account.move"]
        else:
            new_bills = self.env["account.move"]

        for order in self:
            if not order.company_id.purchase_advance_auto_deduct:
                continue
            currency = order.currency_id or order.company_id.currency_id
            # Only process orders that actually have posted advance bills.
            if float_compare(order.advance_bill_posted_total, 0.0,
                             precision_rounding=currency.rounding) <= 0:
                continue
            # Draft main bills (not advance bills) that belong to this order.
            bills_for_order = new_bills.filtered(
                lambda m: not m.is_purchase_advance_bill
                and m.state == "draft"
                and order.name in (m.invoice_origin or ""))
            for bill in bills_for_order:
                try:
                    order._add_advance_deduction_lines(bill)
                except Exception as err:
                    _logger.warning(
                        "Could not add advance deduction lines to bill %s "
                        "for PO %s: %s", bill.name, order.name, err)

        return result

    # -----------------------------------------------------------------------
    # Actions
    # -----------------------------------------------------------------------

    def action_create_advance_bill(self):
        """Open the advance bill creation wizard."""
        self.ensure_one()
        if self.state not in ("purchase", "done"):
            raise UserError(_(
                "You can only create advance bills on a confirmed Purchase Order."))
        return {
            "name": _("Create Purchase Advance Bill"),
            "type": "ir.actions.act_window",
            "res_model": "purchase.advance.bill.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_purchase_order_id": self.id,
            },
        }

    def action_view_advance_bills(self):
        """Open advance bills list/form for this PO."""
        self.ensure_one()
        bills = self.advance_bill_ids
        action = {
            "name": _("Advance Bills"),
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "context": {"create": False, "default_move_type": "in_invoice"},
        }
        if len(bills) == 1:
            action.update({"view_mode": "form", "res_id": bills.id})
        else:
            action.update({
                "view_mode": "list,form",
                "domain": [("id", "in", bills.ids)],
            })
        return action

    # -----------------------------------------------------------------------
    # Cancel guard
    # -----------------------------------------------------------------------

    def button_cancel(self):
        res = super().button_cancel()
        for order in self:
            currency = order.currency_id or order.company_id.currency_id
            if not currency.is_zero(order.advance_bill_posted_total):
                order.message_post(body=_(
                    "This Purchase Order was cancelled while it still has "
                    "%(amount)s in posted advance bills. Please cancel/reverse "
                    "those advance bills if the advance is no longer expected.",
                    amount=currency.format(order.advance_bill_posted_total),
                ))
        return res
