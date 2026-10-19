# Part of ebshel_purchase_budget. See LICENSE file for full copyright and licensing details.
from odoo import api, models


class PurchaseAnalyticLine(models.Model):
    _inherit = 'purchase.analytic.line'

    @api.model
    def _budget_figures(self, analytic_account_id, company_id, ref_date,
                        exclude_request_ids=()):
        """Read the budget from the open analytic (expense) budgets covering
        ``ref_date``; without one, fall back to the other budget sources."""
        lines = self.env['ebshel.account.budget.line']._get_open_lines(
            analytic_account_id, company_id, ref_date).filtered(
            lambda line: line.budget_type == 'expense')
        if not analytic_account_id or not lines:
            return super()._budget_figures(
                analytic_account_id, company_id, ref_date,
                exclude_request_ids=exclude_request_ids)
        budget = sum(lines.mapped('budget_amount'))
        date_from = min(lines.mapped('date_from'))
        date_to = max(lines.mapped('date_to'))
        # Realised spend from posted bills, read once for the whole window so two
        # lines on the same account never count the same bill twice. Order
        # commitments release as they are billed, so the two never overlap.
        booked = self.env['ebshel.account.budget.line']._get_booked_amounts(
            [analytic_account_id], company_id, date_from, date_to)
        achieved = -booked.get(analytic_account_id, 0.0)
        amounts = self._amounts_by_type(
            analytic_account_id, company_id, date_from, date_to,
            exclude_request_ids=exclude_request_ids)
        purchased, invoiced = amounts['request'], amounts['order']
        committed = purchased + invoiced
        return {
            'has_budget': bool(budget or committed or achieved),
            'budget': budget, 'purchased': purchased, 'invoiced': invoiced,
            'committed': committed, 'achieved': achieved,
            'available': budget - achieved - committed,
            'date_from': date_from, 'date_to': date_to,
        }
