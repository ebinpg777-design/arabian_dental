# Part of ebshel_account_budget. See LICENSE file for full copyright and licensing details.
from odoo import api, fields, models


class AccountAnalyticAccount(models.Model):
    _inherit = 'account.analytic.account'

    budget_line_ids = fields.One2many(
        'ebshel.account.budget.line', 'analytic_account_id', string='Budget Lines')
    budget_count = fields.Integer('Budgets', compute='_compute_budget_count')

    @api.depends('budget_line_ids')
    def _compute_budget_count(self):
        counts = dict(self.env['ebshel.account.budget.line']._read_group(
            [('analytic_account_id', 'in', self.ids)], ['analytic_account_id'], ['budget_id:count_distinct']))
        for account in self:
            account.budget_count = counts.get(account, 0)

    def action_view_budgets(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('ebshel_account_budget.action_budget')
        action['domain'] = [('line_ids.analytic_account_id', '=', self.id)]
        return action
