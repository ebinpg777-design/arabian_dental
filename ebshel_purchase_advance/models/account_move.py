# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1.

from odoo import models, fields, api, _
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    # -----------------------------------------------------------------------
    # Fields shared by ALL account.move records
    # -----------------------------------------------------------------------

    is_purchase_advance_bill = fields.Boolean(
        string="Is Purchase Advance Bill",
        copy=False,
        index="btree_not_null",
        help="Technical flag: this vendor bill was created as a purchase advance "
             "bill from a Purchase Order and is NOT a regular expense bill.",
    )
    advance_purchase_order_id = fields.Many2one(
        comodel_name="purchase.order",
        string="Source Purchase Order",
        index="btree_not_null",
        copy=False,
        help="The Purchase Order this advance bill was created against.",
    )

    # For main PO bills: computed deductible advance amount
    purchase_advance_deductible = fields.Monetary(
        string="Advance Available for Deduction",
        compute="_compute_purchase_advance_deductible",
        currency_field="currency_id",
        help="Total of POSTED advance bills (minus what's already been deducted on "
             "this PO's bills) that can still be deducted from this vendor bill.",
    )

    # -----------------------------------------------------------------------
    # Computes
    # -----------------------------------------------------------------------

    @api.depends(
        "move_type", "state",
        "invoice_line_ids.account_id",
        "invoice_line_ids.purchase_order_id",
        "invoice_line_ids.purchase_order_id.advance_bill_ids.state",
        "invoice_line_ids.purchase_order_id.advance_bill_ids.amount_total",
    )
    def _compute_purchase_advance_deductible(self):
        for move in self:
            if move.move_type != "in_invoice" or move.is_purchase_advance_bill:
                move.purchase_advance_deductible = 0.0
                continue
            orders = move.invoice_line_ids.purchase_order_id
            total = 0.0
            conv_date = move.invoice_date or move.date or fields.Date.context_today(move)
            for order in orders:
                avail = order._get_advance_deductible_amount(exclude_invoice=move)
                if order.currency_id == move.currency_id:
                    total += avail
                else:
                    total += order.currency_id._convert(
                        avail, move.currency_id, move.company_id, conv_date)
            move.purchase_advance_deductible = max(0.0, total)

    # -----------------------------------------------------------------------
    # Actions (from vendor bill form)
    # -----------------------------------------------------------------------

    def action_deduct_purchase_advances(self):
        """Manually add advance deduction lines to this vendor bill (draft)."""
        for move in self.filtered(
                lambda m: m.move_type == "in_invoice"
                and not m.is_purchase_advance_bill
                and m.state == "draft"):
            orders = move.invoice_line_ids.purchase_order_id
            for order in orders:
                order._add_advance_deduction_lines(move)
        return True

    def action_open_advance_purchase_order(self):
        """Smart button on advance bill → open the linked PO."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "purchase.order",
            "res_id": self.advance_purchase_order_id.id,
            "view_mode": "form",
        }

    # -----------------------------------------------------------------------
    # Integrity guards
    # -----------------------------------------------------------------------

    def _check_advance_bill_not_deducted(self):
        """Block reset/cancel of a posted advance bill whose amount is still
        applied as a deduction on a main bill — otherwise the advance (bridge)
        account and the partner ledger would be left unbalanced."""
        for move in self.filtered(
                lambda m: m.is_purchase_advance_bill and m.state == "posted"):
            ded_lines = self.env["account.move.line"].search([
                ("advance_source_bill_id", "=", move.id),
                ("move_id.state", "!=", "cancel"),
                ("move_id.is_purchase_advance_bill", "=", False),
            ], limit=1)
            if ded_lines:
                raise UserError(_(
                    "You cannot cancel/reset advance bill %(bill)s: its advance "
                    "amount has already been deducted on vendor bill %(main)s.\n\n"
                    "First remove the corresponding advance-deduction line on that "
                    "bill (or reset it to draft), then cancel this advance bill.",
                    bill=move.name or _("(draft)"),
                    main=ded_lines.move_id.name or _("(draft)"),
                ))

    def button_draft(self):
        self._check_advance_bill_not_deducted()
        return super().button_draft()

    def button_cancel(self):
        self._check_advance_bill_not_deducted()
        return super().button_cancel()


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    advance_source_bill_id = fields.Many2one(
        comodel_name="account.move",
        string="Advance Bill Deducted",
        index="btree_not_null",
        copy=False,
        help="Set on a main vendor bill line that deducts the amount of a specific "
             "posted purchase advance bill. Used to track, per advance bill, how "
             "much has already been deducted across the PO's vendor bills.",
    )
