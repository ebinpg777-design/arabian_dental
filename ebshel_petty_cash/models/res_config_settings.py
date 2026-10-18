# Part of ebshel_petty_cash. See LICENSE file for full copyright and licensing details.
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    petty_cash_advance_account_id = fields.Many2one(
        related="company_id.petty_cash_advance_account_id", readonly=False
    )
    petty_cash_double_validation = fields.Boolean(
        related="company_id.petty_cash_double_validation", readonly=False
    )
    petty_cash_double_validation_amount = fields.Monetary(
        related="company_id.petty_cash_double_validation_amount",
        readonly=False,
        currency_field="currency_id",
    )
    petty_cash_self_approval = fields.Boolean(
        related="company_id.petty_cash_self_approval", readonly=False
    )
    petty_cash_replenish_approval = fields.Boolean(
        related="company_id.petty_cash_replenish_approval", readonly=False
    )
    petty_cash_duplicate_days = fields.Integer(
        related="company_id.petty_cash_duplicate_days", readonly=False
    )
    petty_cash_advance_due_days = fields.Integer(
        related="company_id.petty_cash_advance_due_days", readonly=False
    )
