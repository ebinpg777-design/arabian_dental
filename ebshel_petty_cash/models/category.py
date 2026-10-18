# Part of ebshel_petty_cash. See LICENSE file for full copyright and licensing details.
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class PettyCashCategory(models.Model):
    """What petty cash is spent on: the expense account, default taxes and
    analytic distribution of a voucher line, with a per-line ceiling."""

    _name = "ebshel.petty.cash.category"
    _description = "Petty Cash Expense Category"
    _inherit = ["analytic.mixin"]
    _order = "sequence, name"
    _check_company_auto = True

    name = fields.Char(required=True, translate=True)
    code = fields.Char()
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    account_id = fields.Many2one(
        "account.account",
        string="Expense Account",
        required=True,
        check_company=True,
        domain="[('account_type', 'not in', ('asset_receivable', 'liability_payable', 'asset_cash', 'liability_credit_card', 'off_balance'))]",
    )
    tax_ids = fields.Many2many(
        "account.tax",
        string="Default Taxes",
        check_company=True,
        domain="[('type_tax_use', '=', 'purchase')]",
    )
    max_amount = fields.Monetary(
        string="Max per Line",
        help="Largest amount allowed on one voucher line of this category "
        "(company currency). 0 = no limit.",
    )
    receipt_required = fields.Boolean(
        help="A voucher with a line of this category cannot be submitted without "
        "an attached receipt, whatever the amount."
    )
    partner_required = fields.Boolean(
        string="Payee Required",
        help="Vouchers with a line of this category need a payee.",
    )
    note = fields.Text(
        string="Guidance",
        translate=True,
        help="Shown to the requester: what this category covers.",
    )

    @api.constrains("max_amount")
    def _check_max_amount(self):
        if any(category.max_amount < 0 for category in self):
            raise ValidationError(self.env._("The maximum per line cannot be negative."))
