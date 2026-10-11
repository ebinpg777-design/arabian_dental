# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1.

from odoo import models, fields, api, _
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_round


class PurchaseAdvanceBillWizard(models.TransientModel):
    _name = "purchase.advance.bill.wizard"
    _description = "Create Purchase Advance Bill"

    # -----------------------------------------------------------------------
    # Core reference
    # -----------------------------------------------------------------------

    purchase_order_id = fields.Many2one(
        comodel_name="purchase.order",
        string="Purchase Order",
        required=True,
        readonly=True,
    )
    company_id = fields.Many2one(
        related="purchase_order_id.company_id",
        store=True,
        precompute=True,    # the advance account below is precomputed from it
    )
    partner_id = fields.Many2one(
        related="purchase_order_id.partner_id",
        string="Vendor",
        readonly=True,
    )
    currency_id = fields.Many2one(
        related="purchase_order_id.currency_id",
        readonly=True,
    )
    purchase_order_state = fields.Selection(
        related="purchase_order_id.state",
        string="PO Status",
    )
    purchase_order_date = fields.Datetime(
        related="purchase_order_id.date_order",
        string="Order Date",
    )

    # -----------------------------------------------------------------------
    # Order financial summary (read-only, informational)
    # -----------------------------------------------------------------------

    order_total = fields.Monetary(
        related="purchase_order_id.amount_total",
        string="Order Total",
        currency_field="currency_id",
    )
    order_tax_total = fields.Monetary(
        related="purchase_order_id.amount_tax",
        string="Total Tax",
        currency_field="currency_id",
    )
    order_untaxed = fields.Monetary(
        related="purchase_order_id.amount_untaxed",
        string="Untaxed Amount",
        currency_field="currency_id",
    )
    amount_invoiced = fields.Monetary(
        string="Already Billed (Main Bills)",
        compute="_compute_billing_summary",
        currency_field="currency_id",
        help="Amount already billed in posted main vendor bills for this PO.",
    )
    amount_remaining_to_bill = fields.Monetary(
        string="Remaining to Bill",
        compute="_compute_billing_summary",
        currency_field="currency_id",
    )

    # Advance summary
    advance_bill_count = fields.Integer(
        related="purchase_order_id.advance_bill_count",
        string="Advance Bill Count",
    )
    advance_bill_total = fields.Monetary(
        related="purchase_order_id.advance_bill_total",
        string="Total Advance Billed",
        currency_field="currency_id",
    )
    advance_bill_posted_total = fields.Monetary(
        related="purchase_order_id.advance_bill_posted_total",
        string="Advance Committed",
        currency_field="currency_id",
    )
    advance_bill_paid = fields.Monetary(
        related="purchase_order_id.advance_bill_paid",
        string="Total Advance Paid",
        currency_field="currency_id",
    )
    advance_bill_pending = fields.Monetary(
        related="purchase_order_id.advance_bill_pending",
        string="Advance Pending Payment",
        currency_field="currency_id",
    )
    advance_bill_available = fields.Monetary(
        related="purchase_order_id.advance_bill_available",
        string="Available for Deduction",
        currency_field="currency_id",
    )
    advance_bill_status = fields.Selection(
        related="purchase_order_id.advance_bill_status",
        string="Advance Status",
    )

    # Remaining headroom for new advance
    advance_headroom = fields.Monetary(
        string="Advance Headroom",
        compute="_compute_billing_summary",
        currency_field="currency_id",
        help="How much more advance can be created (Order Total – existing advance bills).",
    )

    # Order lines preview
    order_lines_preview = fields.Html(
        string="Order Lines",
        compute="_compute_order_lines_preview",
        sanitize=False,
    )

    # Existing advance bills table
    existing_advance_bill_ids = fields.Many2many(
        comodel_name="account.move",
        compute="_compute_existing_advance_bills",
        string="Existing Advance Bills",
    )

    # -----------------------------------------------------------------------
    # Advance definition
    # -----------------------------------------------------------------------

    amount_type = fields.Selection(
        selection=[
            ("percentage", "Percentage"),
            ("fixed",      "Fixed Amount"),
            ("remaining",  "Full Remaining Advance Headroom"),
        ],
        string="Advance Type",
        default="percentage",
        required=True,
    )
    percentage_base = fields.Selection(
        selection=[
            ("order_total",    "Order Total (incl. Tax)"),
            ("order_untaxed",  "Untaxed Amount"),
        ],
        string="Percentage Base",
        default="order_total",
        required=True,
        help="The amount used as the base when computing a percentage advance.",
    )
    percentage = fields.Float(
        string="Percentage (%)",
        default=30.0,
        help="Percentage of the selected base amount to advance.",
    )
    fixed_amount = fields.Monetary(
        string="Fixed Amount",
        currency_field="currency_id",
    )
    advance_amount = fields.Monetary(
        string="Advance Bill Amount",
        compute="_compute_advance_amount",
        store=True,
        readonly=False,
        currency_field="currency_id",
    )

    # -----------------------------------------------------------------------
    # Bill details
    # -----------------------------------------------------------------------

    bill_date = fields.Date(
        string="Bill Date",
        required=True,
        default=fields.Date.context_today,
    )
    bill_ref = fields.Char(
        string="Vendor Reference / Memo",
        compute="_compute_bill_ref",
        store=True,
        readonly=False,
    )
    advance_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Advance Account",
        compute="_compute_advance_account",
        store=True,
        # Odoo 20 inserts the row before computing a stored field: a required one
        # has to be computed first, or creating the wizard outside its form fails
        # on the NOT NULL column
        precompute=True,
        readonly=False,
        required=True,
        domain="[('company_ids', 'in', [company_id])]",
        help="Account used for the advance line (asset/prepaid account). "
             "Configured in Purchase > Settings.",
    )

    # -----------------------------------------------------------------------
    # Computes
    # -----------------------------------------------------------------------

    @api.depends("purchase_order_id.invoice_ids",
                 "purchase_order_id.invoice_ids.state",
                 "purchase_order_id.invoice_ids.amount_total",
                 "purchase_order_id.amount_total",
                 "advance_bill_total")
    def _compute_billing_summary(self):
        for wiz in self:
            order = wiz.purchase_order_id
            currency = wiz.currency_id or wiz.company_id.currency_id
            # Main bills (not advance bills)
            main_bills = order.invoice_ids.filtered(
                lambda m: not m.is_purchase_advance_bill and m.state == "posted")
            invoiced = sum(main_bills.mapped("amount_total"))
            wiz.amount_invoiced = float_round(invoiced, precision_rounding=currency.rounding)
            wiz.amount_remaining_to_bill = float_round(
                max(0.0, order.amount_total - invoiced),
                precision_rounding=currency.rounding)
            wiz.advance_headroom = float_round(
                max(0.0, order.amount_total - wiz.advance_bill_total),
                precision_rounding=currency.rounding)

    @api.depends("purchase_order_id.order_line")
    def _compute_order_lines_preview(self):
        for wiz in self:
            lines = wiz.purchase_order_id.order_line.filtered(
                lambda l: not l.display_type)
            if not lines:
                wiz.order_lines_preview = "<p class='text-muted'>No order lines.</p>"
                continue
            rows = "".join(
                "<tr>"
                f"<td class='text-start'>{line.product_id.display_name or '—'}</td>"
                f"<td class='text-center'>{line.product_qty:g} {line.uom_id.name or ''}</td>"
                f"<td class='text-end'>{line.currency_id.symbol} {line.price_unit:,.2f}</td>"
                f"<td class='text-end fw-semibold'>{line.currency_id.symbol} {line.price_subtotal:,.2f}</td>"
                f"<td class='text-center'><span class='badge {'text-bg-success' if line.qty_invoiced >= line.product_qty else 'text-bg-warning'}'>"
                f"{line.qty_invoiced:g}/{line.product_qty:g}</span></td>"
                "</tr>"
                for line in lines
            )
            wiz.order_lines_preview = f"""
<table class="table table-sm table-hover mb-0">
  <thead class="table-light">
    <tr>
      <th class="text-start">Product</th>
      <th class="text-center">Qty</th>
      <th class="text-end">Unit Price</th>
      <th class="text-end">Subtotal</th>
      <th class="text-center">Billed</th>
    </tr>
  </thead>
  <tbody>{rows}</tbody>
</table>"""

    @api.depends("purchase_order_id.advance_bill_ids")
    def _compute_existing_advance_bills(self):
        for wiz in self:
            wiz.existing_advance_bill_ids = wiz.purchase_order_id.advance_bill_ids

    @api.depends("amount_type", "percentage", "percentage_base", "fixed_amount",
                 "advance_headroom", "order_total", "order_untaxed", "currency_id")
    def _compute_advance_amount(self):
        for wiz in self:
            currency = wiz.currency_id or wiz.company_id.currency_id
            if wiz.amount_type == "percentage":
                base = wiz.order_untaxed if wiz.percentage_base == "order_untaxed" else wiz.order_total
                amt = base * (wiz.percentage / 100.0)
            elif wiz.amount_type == "fixed":
                amt = wiz.fixed_amount
            else:  # remaining
                amt = wiz.advance_headroom
            wiz.advance_amount = float_round(
                max(0.0, amt), precision_rounding=currency.rounding) if currency else max(0.0, amt)

    @api.depends("purchase_order_id", "company_id")
    def _compute_advance_account(self):
        for wiz in self:
            order = wiz.purchase_order_id
            # Resolve softly (category → company); an unconfigured account is
            # reported at validation time, not while the wizard is loading.
            wiz.advance_account_id = order._get_advance_account() if order else False

    @api.depends("purchase_order_id")
    def _compute_bill_ref(self):
        for wiz in self:
            order = wiz.purchase_order_id
            wiz.bill_ref = _("Advance – %s", order.name) if order else ""

    # -----------------------------------------------------------------------
    # Onchange helpers
    # -----------------------------------------------------------------------

    @api.onchange("amount_type")
    def _onchange_amount_type(self):
        if self.amount_type == "percentage" and not self.percentage:
            self.percentage = 30.0

    # -----------------------------------------------------------------------
    # Validation
    # -----------------------------------------------------------------------

    def _validate(self):
        self.ensure_one()
        currency = self.currency_id or self.company_id.currency_id
        if float_compare(self.advance_amount, 0.0,
                         precision_rounding=currency.rounding) <= 0:
            raise UserError(_("The advance bill amount must be positive."))

        if not self.advance_account_id:
            raise UserError(_(
                "No Purchase Advance Account is configured.\n\n"
                "Set one on the product (Accounting tab), on the product category "
                "(Inventory > Configuration > Product Categories) or as the "
                "company default in Purchase > Settings > Purchase Advance Bills, "
                "or pick an account directly on this form before creating the "
                "advance bill."))

        if self.company_id.purchase_advance_limit_to_order:
            headroom = self.advance_headroom
            if float_compare(self.advance_amount, headroom,
                             precision_rounding=currency.rounding) > 0:
                raise UserError(_(
                    "The advance amount (%(amount)s) exceeds the remaining advance "
                    "headroom (%(headroom)s) for this order.\n\n"
                    "You can disable the 'Limit Advance to Order Amount' check "
                    "in Purchase > Settings.",
                    amount=currency.format(self.advance_amount),
                    headroom=currency.format(headroom),
                ))

    # -----------------------------------------------------------------------
    # Main action
    # -----------------------------------------------------------------------

    def _prepare_advance_bill_vals(self):
        self.ensure_one()
        order = self.purchase_order_id
        partner_invoice = self.env["res.partner"].browse(
            order.partner_id.address_get(["invoice"])["invoice"])
        partner_bank = order.partner_id.commercial_partner_id.bank_ids.filtered_domain(
            ["|", ("company_id", "=", False), ("company_id", "=", order.company_id.id)])[:1]

        return {
            "move_type": "in_invoice",
            "is_purchase_advance_bill": True,
            "advance_purchase_order_id": order.id,
            "partner_id": partner_invoice.id,
            "partner_bank_id": partner_bank.id,
            "currency_id": self.currency_id.id,
            "invoice_date": self.bill_date,
            "ref": self.bill_ref,
            "invoice_origin": order.name,
            "fiscal_position_id": (
                order.fiscal_position_id
                or order.fiscal_position_id._get_fiscal_position(partner_invoice)
            ).id,
            "invoice_payment_term_id": order.payment_term_id.id,
            "company_id": order.company_id.id,
            "invoice_line_ids": [(0, 0, {
                "name": self.bill_ref or _("Purchase Advance – %s", order.name),
                "account_id": self.advance_account_id.id,
                "quantity": 1.0,
                "price_unit": self.advance_amount,
                "tax_ids": [],
                "sequence": 10,
            })],
        }

    def action_create_advance_bill(self):
        self.ensure_one()
        self._validate()

        bill = self.env["account.move"].create(self._prepare_advance_bill_vals())

        self.purchase_order_id.message_post(body=_(
            "Advance bill %(name)s created for %(amount)s. "
            "The bill is in draft – confirm and pay it to register the advance.",
            name=bill._get_html_link(title=bill.name or _("(draft)")),
            amount=(self.currency_id or self.company_id.currency_id).format(self.advance_amount),
        ))

        return {
            "name": _("Advance Bill"),
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": bill.id,
            "view_mode": "form",
            "target": "current",
        }
