# Part of ebshel_account_budget. See LICENSE file for full copyright and licensing details.
from odoo import api, fields, models
from odoo.exceptions import UserError


class EbshelAccountBudget(models.Model):
    """A budget: one period, one line per analytic account."""
    _name = 'ebshel.account.budget'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Analytic Budget'
    _order = 'date_from desc, id desc'

    name = fields.Char(required=True, tracking=True)
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    user_id = fields.Many2one(
        'res.users', string='Responsible', default=lambda self: self.env.user, tracking=True)
    date_from = fields.Date(
        'Start Date', required=True, tracking=True,
        default=lambda self: self._default_fiscal_year()['date_from'])
    date_to = fields.Date(
        'End Date', required=True, tracking=True,
        default=lambda self: self._default_fiscal_year()['date_to'])
    budget_type = fields.Selection(
        [('expense', 'Expense'), ('revenue', 'Revenue')],
        string='Type', default='expense', required=True, tracking=True,
        help='Expense budgets count costs booked to the analytic accounts, '
             'revenue budgets count income.')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('confirmed', 'Open'),
        ('revised', 'Revised'),
        ('done', 'Done'),
        ('canceled', 'Cancelled'),
    ], default='draft', required=True, tracking=True, copy=False)
    line_ids = fields.One2many('ebshel.account.budget.line', 'budget_id', string='Lines', copy=True)
    parent_id = fields.Many2one(
        'ebshel.account.budget', string='Revision Of', readonly=True, copy=False,
        index='btree_not_null', help='The budget this one replaces.')
    children_ids = fields.One2many('ebshel.account.budget', 'parent_id', string='Revisions')
    note = fields.Html()

    budget_amount = fields.Monetary('Budget', compute='_compute_totals')
    achieved_amount = fields.Monetary('Achieved', compute='_compute_totals')
    committed_amount = fields.Monetary('Committed', compute='_compute_totals')
    available_amount = fields.Monetary('Available', compute='_compute_totals')
    achieved_percentage = fields.Float('Used %', compute='_compute_totals')

    _dates_ok = models.Constraint(
        'CHECK(date_to >= date_from)', 'The budget must end after it starts.')

    def _default_fiscal_year(self):
        return self.env.company.compute_fiscalyear_dates(fields.Date.context_today(self))

    @api.depends('line_ids.budget_amount', 'line_ids.achieved_amount', 'line_ids.committed_amount')
    def _compute_totals(self):
        for budget in self:
            lines = budget.line_ids
            budget.budget_amount = sum(lines.mapped('budget_amount'))
            budget.achieved_amount = sum(lines.mapped('achieved_amount'))
            budget.committed_amount = sum(lines.mapped('committed_amount'))
            budget.available_amount = budget.budget_amount - budget.achieved_amount - budget.committed_amount
            budget.achieved_percentage = (
                (budget.achieved_amount + budget.committed_amount) / budget.budget_amount * 100.0
                if budget.budget_amount else 0.0)

    def action_confirm(self):
        for budget in self:
            if not budget.line_ids:
                raise UserError(self.env._("Add at least one budget line before opening %s.", budget.name))
        self.write({'state': 'confirmed'})

    def action_draft(self):
        self.write({'state': 'draft'})

    def action_done(self):
        self.write({'state': 'done'})

    def action_cancel(self):
        self.write({'state': 'canceled'})

    def action_revise(self):
        """Replace an open budget by a draft copy; the original is kept as Revised."""
        self.ensure_one()
        if self.state != 'confirmed':
            raise UserError(self.env._("Only an open budget can be revised."))
        revision = self.copy({
            'name': self.env._("%s (revised)", self.name),
            'parent_id': self.id,
        })
        self.state = 'revised'
        self.message_post(body=self.env._("Revised by %s.", revision._get_html_link()))
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': revision.id,
            'view_mode': 'form',
        }

    def action_view_lines(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('ebshel_account_budget.action_budget_line_analysis')
        action['domain'] = [('budget_id', '=', self.id)]
        action['context'] = {'search_default_group_analytic': 0}
        return action
