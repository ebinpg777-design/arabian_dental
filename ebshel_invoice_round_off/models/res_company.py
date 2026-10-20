# Part of ebshel_invoice_round_off. See LICENSE file for full copyright and licensing details.
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    invoice_round_off_id = fields.Many2one(
        "account.cash.rounding",
        string="Round-Off Method",
        check_company=True,
        help="The rounding the Round Off button applies - typically to the nearest "
        "whole unit of currency, adding a rounding line. Empty: the first rounding "
        "method of the company.",
    )
    invoice_round_off_auto = fields.Boolean(
        string="Round Off New Customer Invoices",
        help="Every new customer invoice, credit note or receipt is rounded off as "
        "it is created, unless another rounding method already applies to it.",
    )
