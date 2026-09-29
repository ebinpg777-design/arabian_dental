# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    ebshel_deferred_revenue_account_id = fields.Many2one(
        'account.account', string='Deferred revenue account', check_company=True,
        domain="[('account_type', 'in', ('liability_current', 'liability_non_current'))]",
        help="Where invoiced revenue waits until the month it is earned.")
    ebshel_deferred_expense_account_id = fields.Many2one(
        'account.account', string='Prepaid expense account', check_company=True,
        domain="[('account_type', 'in', ('asset_current', 'asset_non_current', 'asset_prepayments'))]",
        help="Where billed expenses wait until the month they are used.")
    ebshel_deferral_journal_id = fields.Many2one(
        'account.journal', string='Deferral journal', check_company=True, domain="[('type', '=', 'general')]")
    ebshel_deferral_method = fields.Selection(
        [('days', 'By days'), ('months', 'Equal months')], string='Deferral split', default='days', required=True)
    ebshel_deferral_auto = fields.Boolean('Start deferrals when the invoice is posted', default=True)
    ebshel_followup_auto = fields.Boolean('Send automatic reminders', default=True,
                                          help="The nightly job sends the follow-up levels marked automatic.")
    ebshel_followup_min_amount = fields.Monetary('Do not remind below', currency_field='currency_id', default=0.0)
    ebshel_close_lock = fields.Boolean('Lock the books when a period is closed', default=True)
    ebshel_cash_weeks = fields.Integer('Cash forecast horizon (weeks)', default=13)
    ebshel_cash_collection_rate = fields.Integer('Expected collection rate (%)', default=90)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ebshel_deferred_revenue_account_id = fields.Many2one(related='company_id.ebshel_deferred_revenue_account_id', readonly=False)
    ebshel_deferred_expense_account_id = fields.Many2one(related='company_id.ebshel_deferred_expense_account_id', readonly=False)
    ebshel_deferral_journal_id = fields.Many2one(related='company_id.ebshel_deferral_journal_id', readonly=False)
    ebshel_deferral_method = fields.Selection(related='company_id.ebshel_deferral_method', readonly=False)
    ebshel_deferral_auto = fields.Boolean(related='company_id.ebshel_deferral_auto', readonly=False)
    ebshel_followup_auto = fields.Boolean(related='company_id.ebshel_followup_auto', readonly=False)
    ebshel_followup_min_amount = fields.Monetary(related='company_id.ebshel_followup_min_amount', readonly=False)
    ebshel_close_lock = fields.Boolean(related='company_id.ebshel_close_lock', readonly=False)
    ebshel_cash_weeks = fields.Integer(related='company_id.ebshel_cash_weeks', readonly=False)
    ebshel_cash_collection_rate = fields.Integer(related='company_id.ebshel_cash_collection_rate', readonly=False)
