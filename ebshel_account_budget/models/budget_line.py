# Part of ebshel_account_budget. See LICENSE file for full copyright and licensing details.
from collections import defaultdict

from odoo import api, fields, models


class EbshelAccountBudgetLine(models.Model):
    """What is planned for one analytic account over the budget's period."""
    _name = 'ebshel.account.budget.line'
    _description = 'Analytic Budget Line'
    _order = 'budget_id, sequence, id'
    _check_company_auto = True

    budget_id = fields.Many2one(
        'ebshel.account.budget', string='Budget', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    analytic_account_id = fields.Many2one(
        'account.analytic.account', string='Analytic Account', required=True,
        index=True, check_company=True, ondelete='restrict')
    plan_id = fields.Many2one(related='analytic_account_id.root_plan_id', string='Analytic Plan', store=True)
    company_id = fields.Many2one(related='budget_id.company_id', store=True)
    currency_id = fields.Many2one(related='budget_id.currency_id')
    date_from = fields.Date(related='budget_id.date_from', store=True)
    date_to = fields.Date(related='budget_id.date_to', store=True)
    state = fields.Selection(related='budget_id.state', store=True, string='Budget Status')
    budget_type = fields.Selection(related='budget_id.budget_type', store=True)
    user_id = fields.Many2one(related='budget_id.user_id', store=True, string='Responsible')

    budget_amount = fields.Monetary('Budgeted', default=0.0)
    achieved_amount = fields.Monetary(
        'Achieved', compute='_compute_achieved_amount',
        help='Booked to the analytic account in posted journal entries within the period.')
    committed_amount = fields.Monetary(
        'Committed', compute='_compute_committed_amount',
        help='Reserved but not booked yet, such as open purchase requests and purchase orders.')
    theoretical_amount = fields.Monetary(
        'Theoretical', compute='_compute_theoretical_amount',
        help='What should have been used by today, pro rata over the period.')
    available_amount = fields.Monetary(
        'Available', compute='_compute_available_amount',
        help='Budget minus achieved minus committed.')
    achieved_percentage = fields.Float('Used %', compute='_compute_available_amount')
    is_over_budget = fields.Boolean('Over Budget', compute='_compute_available_amount')

    @api.depends('analytic_account_id', 'date_from', 'date_to', 'company_id', 'budget_type')
    def _compute_achieved_amount(self):
        groups = defaultdict(lambda: self.browse())
        for line in self:
            line.achieved_amount = 0.0
            if line.analytic_account_id and line.date_from and line.date_to:
                groups[line.company_id, line.date_from, line.date_to] |= line
        for (company, date_from, date_to), lines in groups.items():
            totals = self._get_booked_amounts(lines.analytic_account_id.ids, company.id, date_from, date_to)
            for line in lines:
                amount = totals.get(line.analytic_account_id.id, 0.0)
                line.achieved_amount = -amount if line.budget_type == 'expense' else amount

    @api.model
    def _get_booked_amounts(self, analytic_account_ids, company_id, date_from, date_to):
        """{analytic account id: signed analytic amount} booked by posted journal
        entries in the period; costs are negative, income positive. One query
        per analytic plan column, whatever the number of accounts."""
        AnalyticLine = self.env['account.analytic.line'].sudo()
        project_plan, other_plans = self.env['account.analytic.plan']._get_all_plans()
        totals = defaultdict(float)
        for plan in project_plan + other_plans:
            column = plan._column_name()
            if column not in AnalyticLine._fields:
                continue
            for account, amount in AnalyticLine._read_group([
                (column, 'in', analytic_account_ids),
                ('general_account_id', '!=', False),
                ('company_id', '=', company_id),
                ('date', '>=', date_from),
                ('date', '<=', date_to),
            ], [column], ['amount:sum']):
                totals[account.id] += amount
        return totals

    def _compute_committed_amount(self):
        """Hook: modules that reserve budget before it is booked add to this."""
        for line in self:
            line.committed_amount = 0.0

    @api.depends('budget_amount', 'date_from', 'date_to')
    def _compute_theoretical_amount(self):
        today = fields.Date.context_today(self)
        for line in self:
            if not line.date_from or not line.date_to:
                line.theoretical_amount = 0.0
                continue
            total_days = (line.date_to - line.date_from).days + 1
            elapsed = (min(today, line.date_to) - line.date_from).days + 1
            share = max(min(elapsed / total_days, 1.0), 0.0) if total_days > 0 else 0.0
            line.theoretical_amount = line.budget_amount * share

    @api.depends('budget_amount', 'achieved_amount', 'committed_amount')
    def _compute_available_amount(self):
        for line in self:
            used = line.achieved_amount + line.committed_amount
            line.available_amount = line.budget_amount - used
            line.achieved_percentage = used / line.budget_amount * 100.0 if line.budget_amount else 0.0
            line.is_over_budget = line.currency_id.compare_amounts(used, line.budget_amount) > 0

    @api.depends('budget_id', 'analytic_account_id')
    def _compute_display_name(self):
        for line in self:
            line.display_name = f"{line.budget_id.name} - {line.analytic_account_id.display_name}"

    @api.model
    def _get_open_lines(self, analytic_account_id, company_id, ref_date):
        """Lines of open budgets covering ``ref_date`` for the analytic account."""
        return self.sudo().search([
            ('analytic_account_id', '=', analytic_account_id),
            ('company_id', '=', company_id),
            ('state', '=', 'confirmed'),
            ('date_from', '<=', ref_date),
            ('date_to', '>=', ref_date),
        ])

    def action_view_analytic_items(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('analytic.account_analytic_line_action_entries')
        action['domain'] = [
            ('auto_account_id', '=', self.analytic_account_id.id),
            ('general_account_id', '!=', False),
            ('company_id', '=', self.company_id.id),
            ('date', '>=', self.date_from),
            ('date', '<=', self.date_to),
        ]
        action['context'] = {}
        return action
