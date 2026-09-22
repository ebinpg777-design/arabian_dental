from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    petty_cash_warning_threshold = fields.Float(
        string='Petty Cash Warning Threshold (%)', default=40.0,
        help='Remaining balance percentage at or below which an allocation is flagged as Low Balance.')
    petty_cash_critical_threshold = fields.Float(
        string='Petty Cash Critical Threshold (%)', default=15.0,
        help='Remaining balance percentage at or below which an allocation is flagged as Critical.')
    petty_cash_alerts_enabled = fields.Boolean(
        string='Petty Cash Low-Balance Alerts', default=True,
        help='Send the daily low-balance email/activity alerts for this company.')
    petty_cash_auto_replenish_default = fields.Boolean(
        string='Auto Replenish by Default',
        help='New allocations enable automatic replenishment by default.')
    petty_cash_expense_attachment_required = fields.Boolean(
        string='Require Expense Receipts',
        help='Require at least one attachment on expenses at or above the threshold amount before submission.')
    petty_cash_expense_attachment_min = fields.Monetary(
        string='Receipt Required Above', currency_field='currency_id',
        help='Expenses with a total at or above this amount must carry a receipt attachment.')
    petty_cash_expense_auto_approve_max = fields.Monetary(
        string='Auto-approve Expenses Up To', currency_field='currency_id',
        help='Expenses with a total at or below this amount are approved automatically on submission. '
             'Set to 0 to disable and require manual approval for every expense.')
    petty_cash_max_expense_amount = fields.Monetary(
        string='Maximum Expense Amount', currency_field='currency_id',
        help='Maximum total amount allowed per expense submission. '
             'Set to 0 for no limit.')
    petty_cash_self_request_limit = fields.Monetary(
        string='Self-service Request Limit', currency_field='currency_id',
        help='Maximum amount a non-manager user may request for themselves without manager involvement. '
             'Set to 0 for no limit.')
    petty_cash_auto_close = fields.Boolean(
        string='Auto-close at Zero Balance',
        help='Automatically close an allocation once a return brings its balance to zero, '
             'instead of leaving it open for further top-ups.')
    petty_cash_write_off_account_id = fields.Many2one(
        'account.account', string='Default Write-off Account',
        help='Default expense account used when writing off a small unrecoverable petty cash balance.')
    petty_cash_write_off_threshold = fields.Monetary(
        string='Write-off Threshold', currency_field='currency_id',
        help='Maximum remaining balance that may be written off on closure. '
             'Set to 0 to allow any amount.')
    # ── Category budget warnings ─────────────────────────────────────────────
    petty_cash_category_budget_warning = fields.Boolean(
        string='Category Budget Warnings',
        help='Show a warning when an expense exceeds the category budget set on the allocation.')
    # ── Duplicate detection ───────────────────────────────────────────────────
    petty_cash_duplicate_detection = fields.Boolean(
        string='Duplicate Receipt Detection', default=True,
        help='Warn officers when a submitted expense looks like a duplicate '
             '(same vendor, same amount, same category within the detection window).')
    petty_cash_duplicate_window_days = fields.Integer(
        string='Duplicate Detection Window (days)', default=30,
        help='Number of past days to check for potential duplicate expenses. '
             'Set to 0 to disable.')
