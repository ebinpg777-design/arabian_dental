from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    petty_cash_warning_threshold = fields.Float(
        related='company_id.petty_cash_warning_threshold', readonly=False)
    petty_cash_critical_threshold = fields.Float(
        related='company_id.petty_cash_critical_threshold', readonly=False)
    petty_cash_alerts_enabled = fields.Boolean(
        related='company_id.petty_cash_alerts_enabled', readonly=False)
    petty_cash_auto_replenish_default = fields.Boolean(
        related='company_id.petty_cash_auto_replenish_default', readonly=False)
    petty_cash_expense_attachment_required = fields.Boolean(
        related='company_id.petty_cash_expense_attachment_required', readonly=False)
    petty_cash_expense_attachment_min = fields.Monetary(
        related='company_id.petty_cash_expense_attachment_min', readonly=False)
    petty_cash_expense_auto_approve_max = fields.Monetary(
        related='company_id.petty_cash_expense_auto_approve_max', readonly=False)
    petty_cash_max_expense_amount = fields.Monetary(
        related='company_id.petty_cash_max_expense_amount', readonly=False)
    petty_cash_self_request_limit = fields.Monetary(
        related='company_id.petty_cash_self_request_limit', readonly=False)
    petty_cash_auto_close = fields.Boolean(
        related='company_id.petty_cash_auto_close', readonly=False)
    petty_cash_write_off_account_id = fields.Many2one(
        related='company_id.petty_cash_write_off_account_id', readonly=False)
    petty_cash_write_off_threshold = fields.Monetary(
        related='company_id.petty_cash_write_off_threshold', readonly=False)
    petty_cash_category_budget_warning = fields.Boolean(
        related='company_id.petty_cash_category_budget_warning', readonly=False)
    petty_cash_duplicate_detection = fields.Boolean(
        related='company_id.petty_cash_duplicate_detection', readonly=False)
    petty_cash_duplicate_window_days = fields.Integer(
        related='company_id.petty_cash_duplicate_window_days', readonly=False)
