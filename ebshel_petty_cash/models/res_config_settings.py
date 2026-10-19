# Part of ebshel_petty_cash. See LICENSE file for full copyright and licensing details.
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    petty_cash_advance_account_id = fields.Many2one(
        related="company_id.petty_cash_advance_account_id", readonly=False
    )
    petty_cash_self_approval = fields.Boolean(
        related="company_id.petty_cash_self_approval", readonly=False
    )
    petty_cash_distinct_approvers = fields.Boolean(
        related="company_id.petty_cash_distinct_approvers", readonly=False
    )
    petty_cash_risk_review = fields.Boolean(
        related="company_id.petty_cash_risk_review", readonly=False
    )
    petty_cash_late_days = fields.Integer(related="company_id.petty_cash_late_days", readonly=False)
    petty_cash_duplicate_days = fields.Integer(
        related="company_id.petty_cash_duplicate_days", readonly=False
    )
    petty_cash_advance_due_days = fields.Integer(
        related="company_id.petty_cash_advance_due_days", readonly=False
    )
