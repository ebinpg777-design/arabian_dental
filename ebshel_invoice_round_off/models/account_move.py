# Part of ebshel_invoice_round_off. See LICENSE file for full copyright and licensing details.
"""Round Off on the invoice form.

Odoo's cash rounding does the accounting: once ``invoice_cash_rounding_id`` is set on
a draft invoice, the dynamic lines add a rounding line for the difference (or adjust
the biggest tax, by the method's strategy), the total becomes a whole amount and the
difference lands on the method's profit or loss account. Odoo 20 only applies a
method by itself when it is tied to a currency, a partner category or a payment
method; this module applies the company's method on demand (a button) or to every
new customer invoice (a setting).
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    round_off_available = fields.Boolean(compute="_compute_round_off")
    round_off_applied = fields.Boolean(compute="_compute_round_off")

    @api.depends("state", "move_type", "invoice_cash_rounding_id")
    def _compute_round_off(self):
        for move in self:
            move.round_off_available = (
                move.is_invoice(include_receipts=True) and move.state == "draft"
            )
            move.round_off_applied = bool(move.invoice_cash_rounding_id)

    def _round_off_method(self):
        """The company's round-off method, else the first rounding method it may use."""
        self.ensure_one()
        method = self.company_id.invoice_round_off_id.filtered("active")
        if not method:
            CashRounding = self.env["account.cash.rounding"]
            method = CashRounding.search(
                CashRounding._check_company_domain(self.company_id), limit=1
            )
        if not method:
            raise UserError(
                _(
                    "There is no rounding method to round off with. Create one under "
                    "Accounting > Configuration > Cash Roundings (switch on Cash Rounding "
                    "in the Accounting settings), or pick one under Accounting > Settings "
                    "> Round Off."
                )
            )
        return method

    @api.depends("company_id.invoice_round_off_auto", "company_id.invoice_round_off_id")
    def _compute_invoice_cash_rounding_id(self):
        super()._compute_invoice_cash_rounding_id()
        for move in self:
            if (
                move.state == "draft"
                and not move.invoice_cash_rounding_id
                and move.is_sale_document(include_receipts=True)
                and move.company_id.invoice_round_off_auto
            ):
                move.invoice_cash_rounding_id = move._round_off_method()

    def action_round_off(self):
        for move in self:
            if not move.round_off_available:
                raise UserError(_("Only a draft invoice or bill can be rounded off."))
            move.invoice_cash_rounding_id = move._round_off_method()
        return True

    def action_remove_round_off(self):
        for move in self:
            if not move.round_off_available:
                raise UserError(_("Only a draft invoice or bill can be changed."))
            move.invoice_cash_rounding_id = False
        return True
