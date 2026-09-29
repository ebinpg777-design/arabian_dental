# -*- coding: utf-8 -*-
"""Budgets: what was planned per account (and analytic account), phased by
month, against what the ledger actually shows."""
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError
from odoo.tools import SQL, date_utils, float_round

REVENUE_TYPES = ('income', 'income_other')
EXPENSE_TYPES = ('expense', 'expense_depreciation', 'expense_direct_cost')


class Budget(models.Model):
    _name = 'ebshel.budget'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Budget'
    _order = 'date_from desc, id desc'

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(readonly=True, copy=False, default=lambda self: _('New'))
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    user_id = fields.Many2one('res.users', string='Responsible', default=lambda self: self.env.user, tracking=True)
    date_from = fields.Date(required=True, default=lambda self: self.env.company.compute_fiscalyear_dates(fields.Date.context_today(self))['date_from'])
    date_to = fields.Date(required=True, default=lambda self: self.env.company.compute_fiscalyear_dates(fields.Date.context_today(self))['date_to'])
    state = fields.Selection([('draft', 'Draft'), ('confirmed', 'Confirmed'), ('done', 'Done'), ('cancelled', 'Cancelled')],
                             default='draft', tracking=True, copy=False)
    kind = fields.Selection([('expense', 'Expenses'), ('revenue', 'Revenue'), ('both', 'Revenue and expenses')],
                            default='expense', required=True)
    line_ids = fields.One2many('ebshel.budget.line', 'budget_id', copy=True)
    note = fields.Html()
    line_count = fields.Integer(compute='_compute_totals')
    planned_total = fields.Monetary(compute='_compute_totals', string='Planned')
    actual_total = fields.Monetary(compute='_compute_totals', string='Actual')
    committed_total = fields.Monetary(compute='_compute_totals', string='Committed')
    theoretical_total = fields.Monetary(compute='_compute_totals', string='Theoretical to date')
    available_total = fields.Monetary(compute='_compute_totals', string='Available')
    consumed_pct = fields.Float(compute='_compute_totals', string='Consumed %')
    elapsed_pct = fields.Float(compute='_compute_totals', string='Elapsed %')
    alert = fields.Selection([('ok', 'On track'), ('watch', 'Watch'), ('over', 'Over budget')], compute='_compute_totals')

    _dates_ok = models.Constraint('CHECK(date_to >= date_from)', 'The budget must end after it starts.')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('code') or vals['code'] == _('New'):
                vals['code'] = self.env['ir.sequence'].next_by_code('ebshel.budget') or _('New')
        return super().create(vals_list)

    def _elapsed_share(self, today=None):
        self.ensure_one()
        today = today or fields.Date.context_today(self)
        total = (self.date_to - self.date_from).days + 1
        done = (min(today, self.date_to) - self.date_from).days + 1
        return max(min(done / total, 1.0), 0.0) if total > 0 else 0.0

    @api.depends('line_ids.planned', 'line_ids.actual', 'line_ids.committed', 'line_ids.theoretical')
    def _compute_totals(self):
        for budget in self:
            lines = budget.line_ids
            budget.line_count = len(lines)
            budget.planned_total = sum(lines.mapped('planned'))
            budget.actual_total = sum(lines.mapped('actual'))
            budget.committed_total = sum(lines.mapped('committed'))
            budget.theoretical_total = sum(lines.mapped('theoretical'))
            budget.available_total = budget.planned_total - budget.actual_total - budget.committed_total
            budget.consumed_pct = (budget.actual_total / budget.planned_total * 100.0) if budget.planned_total else 0.0
            budget.elapsed_pct = budget._elapsed_share() * 100.0
            budget.alert = 'over' if any(l.alert == 'over' for l in lines) else 'watch' if any(l.alert == 'watch' for l in lines) else 'ok'

    def action_confirm(self):
        for budget in self:
            if not budget.line_ids:
                raise UserError(_("Add at least one budget line before confirming."))
            budget.line_ids.filtered(lambda l: not l.phase_ids).action_generate_phases()
            budget.state = 'confirmed'
        return True

    def action_done(self):
        self.write({'state': 'done'})

    def action_cancel(self):
        self.write({'state': 'cancelled'})

    def action_draft(self):
        self.write({'state': 'draft'})

    def action_generate_phases(self):
        self.line_ids.action_generate_phases()
        return True

    def action_board(self):
        self.ensure_one()
        return {'type': 'ir.actions.client', 'tag': 'ebshel_budget_board', 'name': _('Budget board'),
                'params': {'budget_id': self.id}}

    def action_report(self):
        self.ensure_one()
        return {'type': 'ir.actions.client', 'tag': 'ebshel_fin_report', 'name': _('Budget vs Actual'),
                'params': {'report_key': 'budget_vs_actual',
                           'options': {'date': {'preset': 'custom', 'from': fields.Date.to_string(self.date_from),
                                                'to': fields.Date.to_string(self.date_to)}, 'unfold_all': True}}}


class BudgetLine(models.Model):
    _name = 'ebshel.budget.line'
    _description = 'Budget line'
    _order = 'budget_id, sequence, id'

    budget_id = fields.Many2one('ebshel.budget', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='budget_id.company_id', store=True)
    currency_id = fields.Many2one(related='budget_id.currency_id')
    date_from = fields.Date(related='budget_id.date_from', store=True)
    date_to = fields.Date(related='budget_id.date_to', store=True)
    state = fields.Selection(related='budget_id.state', store=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    kind = fields.Selection([('expense', 'Expense'), ('revenue', 'Revenue')], required=True, default='expense')
    account_ids = fields.Many2many('account.account', string='Accounts', check_company=True)
    account_prefix = fields.Char('Or every account starting with', help="Used when no account is picked, e.g. 62 for all 62xxxx accounts.")
    analytic_account_id = fields.Many2one('account.analytic.account', string='Analytic account',
                                          help="Only the share of each entry distributed to this analytic account counts.")
    planned = fields.Monetary(required=True, default=0.0)
    phasing = fields.Selection([('even', 'Equal months'), ('days', 'By days'), ('custom', 'As entered per month')],
                               default='even', required=True)
    phase_ids = fields.One2many('ebshel.budget.phase', 'line_id', copy=True)
    actual = fields.Monetary(compute='_compute_amounts')
    committed = fields.Monetary(compute='_compute_amounts', help="Draft invoices and bills on these accounts.")
    theoretical = fields.Monetary(compute='_compute_amounts', help="What should have been used by today, following the phasing.")
    available = fields.Monetary(compute='_compute_amounts')
    achieved_pct = fields.Float(compute='_compute_amounts', string='Achieved %')
    alert = fields.Selection([('ok', 'On track'), ('watch', 'Watch'), ('over', 'Over')], compute='_compute_amounts')
    phase_total = fields.Monetary(compute='_compute_phase_total')
    phase_gap = fields.Monetary(compute='_compute_phase_total', help="Planned minus the sum of the months; zero when the phasing is complete.")

    @api.onchange('account_ids')
    def _onchange_account_ids(self):
        types = set(self.account_ids.mapped('account_type'))
        if types and types <= set(REVENUE_TYPES):
            self.kind = 'revenue'
        elif types and types <= set(EXPENSE_TYPES):
            self.kind = 'expense'
        if not self.name and self.account_ids:
            self.name = ', '.join(self.account_ids.mapped('name'))[:64]

    @api.depends('phase_ids.planned', 'planned')
    def _compute_phase_total(self):
        for line in self:
            line.phase_total = sum(line.phase_ids.mapped('planned'))
            line.phase_gap = line.planned - line.phase_total if line.phase_ids else 0.0

    def _account_ids(self):
        self.ensure_one()
        if self.account_ids:
            return self.account_ids.ids
        if self.account_prefix:
            return self.env['account.account'].with_company(self.company_id).search(
                [('code', '=like', self.account_prefix.strip() + '%'), ('company_ids', 'in', self.company_id.id)]).ids
        return []

    def _weight_sql(self):
        if not self.analytic_account_id:
            return SQL("1")
        return SQL("""(SELECT COALESCE(SUM(v.value::numeric), 0) / 100.0 FROM jsonb_each_text(l.analytic_distribution) v
                        WHERE %s = ANY(string_to_array(v.key, ',')))""", str(self.analytic_account_id.id))

    def _amount(self, date_from, date_to, states=('posted',)):
        """Signed amount on the line's accounts in the window: expenses as debits, revenue as credits."""
        self.ensure_one()
        accounts = self._account_ids()
        if not accounts:
            return 0.0
        self.env['account.move.line'].flush_model()
        sign = SQL("-1") if self.kind == 'revenue' else SQL("1")
        row = self.env.execute_query(SQL("""
            SELECT COALESCE(SUM(l.balance * %s * %s), 0) FROM account_move_line l
             WHERE l.account_id IN %s AND l.company_id = %s AND l.parent_state IN %s
               AND l.date >= %s AND l.date <= %s AND l.display_type NOT IN ('line_section', 'line_note')
        """, sign, self._weight_sql(), tuple(accounts), self.company_id.id, tuple(states), date_from, date_to))[0]
        return float(row[0] or 0.0)

    def _theoretical(self, today):
        self.ensure_one()
        if today < self.date_from:
            return 0.0
        if today >= self.date_to:
            return self.planned
        if self.phase_ids:
            done = sum(p.planned for p in self.phase_ids if p.date_to <= today)
            current = self.phase_ids.filtered(lambda p: p.date_from <= today < p.date_to)
            for p in current:
                span = (p.date_to - p.date_from).days + 1
                done += p.planned * ((today - p.date_from).days + 1) / span
            return done
        return self.planned * self.budget_id._elapsed_share(today)

    @api.depends('planned', 'account_ids', 'account_prefix', 'analytic_account_id', 'kind', 'phase_ids.planned')
    def _compute_amounts(self):
        today = fields.Date.context_today(self)
        for line in self:
            if not line.budget_id.date_from or not line.budget_id.date_to:
                line.actual = line.committed = line.theoretical = line.available = 0.0
                line.achieved_pct = 0.0
                line.alert = 'ok'
                continue
            line.actual = line._amount(line.date_from, line.date_to)
            line.committed = line._amount(line.date_from, line.date_to, states=('draft',))
            line.theoretical = line._theoretical(today)
            line.available = line.planned - line.actual - line.committed
            line.achieved_pct = (line.actual / line.planned * 100.0) if line.planned else (100.0 if line.actual else 0.0)
            if line.kind == 'expense':
                line.alert = 'over' if line.actual > line.planned + 0.005 else \
                    'watch' if line.theoretical and line.actual > line.theoretical * 1.1 else 'ok'
            else:
                line.alert = 'over' if line.theoretical and line.actual < line.theoretical * 0.8 else \
                    'watch' if line.theoretical and line.actual < line.theoretical * 0.95 else 'ok'

    def action_generate_phases(self):
        """One row per month of the budget, the planned amount split by the phasing rule."""
        for line in self:
            months = []
            start = line.date_from
            while start <= line.date_to:
                end = min(date_utils.end_of(start, 'month'), line.date_to)
                months.append((start, end))
                start = end + timedelta(days=1)
            existing = {(p.date_from, p.date_to): p for p in line.phase_ids}
            total_days = (line.date_to - line.date_from).days + 1
            amounts = []
            for (a, b) in months:
                if line.phasing == 'days':
                    amounts.append(line.planned * ((b - a).days + 1) / total_days)
                elif line.phasing == 'even':
                    amounts.append(line.planned / len(months))
                else:
                    amounts.append(existing[(a, b)].planned if (a, b) in existing else 0.0)
            if line.phasing != 'custom' and months:
                rounded = [float_round(x, precision_rounding=line.currency_id.rounding or 0.01) for x in amounts[:-1]]
                amounts = rounded + [line.planned - sum(rounded)]
            commands = [(2, p.id) for key, p in existing.items() if key not in months]
            for (a, b), amount in zip(months, amounts):
                vals = {'name': a.strftime('%b %Y'), 'date_from': a, 'date_to': b, 'planned': amount}
                commands.append((1, existing[(a, b)].id, vals) if (a, b) in existing else (0, 0, vals))
            line.write({'phase_ids': commands})
        return True

    def _actual_domain(self, states=('posted',), date_from=None, date_to=None):
        self.ensure_one()
        domain = [('account_id', 'in', self._account_ids()), ('company_id', '=', self.company_id.id),
                  ('parent_state', 'in', list(states)), ('date', '>=', date_from or self.date_from),
                  ('date', '<=', date_to or self.date_to), ('display_type', 'not in', ('line_section', 'line_note'))]
        if self.analytic_account_id:
            domain.append(('analytic_distribution', 'in', [self.analytic_account_id.id]))
        return domain

    def action_view_actuals(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'account.move.line', 'name': self.name,
                'view_mode': 'list,pivot,form', 'domain': self._actual_domain(), 'context': {'create': False}}

    def _month_series(self):
        """[{label, planned, actual}] per month of the budget."""
        self.ensure_one()
        out = []
        phases = self.phase_ids.sorted('date_from')
        if not phases:
            return out
        for p in phases:
            out.append({'label': p.name, 'from': fields.Date.to_string(p.date_from), 'planned': p.planned,
                        'actual': self._amount(p.date_from, p.date_to)})
        return out


class BudgetPhase(models.Model):
    _name = 'ebshel.budget.phase'
    _description = 'Budget month'
    _order = 'line_id, date_from'

    line_id = fields.Many2one('ebshel.budget.line', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='line_id.currency_id')
    name = fields.Char(required=True)
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    planned = fields.Monetary()
    actual = fields.Monetary(compute='_compute_actual')

    def _compute_actual(self):
        for phase in self:
            phase.actual = phase.line_id._amount(phase.date_from, phase.date_to) if phase.line_id.budget_id else 0.0


class BudgetBoard(models.AbstractModel):
    """The budget at a glance: bars per line, months, the run-rate projection."""
    _name = 'ebshel.budget.board'
    _description = 'Budget board'

    @api.model
    def get_data(self, budget_id=None):
        if not self.env.su and not self.env.user.has_groups('account.group_account_readonly,account.group_account_invoice'):
            raise AccessError(_("The budget board is for the accounting team."))
        self.env.flush_all()
        Budget = self.env['ebshel.budget']
        today = fields.Date.context_today(self)
        company = self.env.company
        budgets = Budget.search([('company_id', '=', company.id), ('state', '!=', 'cancelled')])
        budget = Budget.browse(budget_id) if budget_id else (
            budgets.filtered(lambda b: b.state == 'confirmed' and b.date_from <= today <= b.date_to)[:1] or budgets[:1])
        if not budget:
            return {'budgets': [], 'budget': None}
        elapsed = budget._elapsed_share(today)
        lines = []
        for l in budget.line_ids:
            lines.append({'id': l.id, 'name': l.name, 'kind': l.kind, 'planned': l.planned, 'actual': l.actual,
                          'committed': l.committed, 'theoretical': l.theoretical, 'available': l.available,
                          'pct': round(l.achieved_pct, 1), 'alert': l.alert,
                          'analytic': l.analytic_account_id.name or '', 'accounts': len(l._account_ids()),
                          'projected': (l.actual / elapsed) if elapsed > 0.05 and today < l.date_to else l.actual})
        # months: sum of every line's phases
        months = {}
        for l in budget.line_ids:
            for m in l._month_series():
                row = months.setdefault(m['from'], {'label': m['label'], 'from': m['from'], 'planned': 0.0, 'actual': 0.0})
                row['planned'] += m['planned'] if l.kind == budget.kind or budget.kind == 'both' else m['planned']
                row['actual'] += m['actual']
        series, cp, ca = [], 0.0, 0.0
        for key in sorted(months):
            row = months[key]
            cp += row['planned']
            ca += row['actual'] if key <= fields.Date.to_string(today) else 0.0
            row.update({'cum_planned': cp, 'cum_actual': ca if key <= fields.Date.to_string(today) else None,
                        'past': key <= fields.Date.to_string(today)})
            series.append(row)
        planned, actual, committed = budget.planned_total, budget.actual_total, budget.committed_total
        projected = (actual / elapsed) if elapsed > 0.05 and today < budget.date_to else actual
        alerts = []
        for l in lines:
            if l['alert'] == 'over':
                alerts.append(_('%s is over budget (%s%%)', l['name'], l['pct']))
            elif l['alert'] == 'watch':
                alerts.append(_('%s is ahead of its phasing', l['name']) if l['kind'] == 'expense' else _('%s is behind its phasing', l['name']))
        return {
            'budgets': [{'id': b.id, 'name': b.name, 'state': b.state, 'from': fields.Date.to_string(b.date_from),
                         'to': fields.Date.to_string(b.date_to)} for b in budgets],
            'budget': {'id': budget.id, 'name': budget.name, 'code': budget.code, 'state': budget.state, 'kind': budget.kind,
                       'from': fields.Date.to_string(budget.date_from), 'to': fields.Date.to_string(budget.date_to),
                       'elapsed_pct': round(elapsed * 100, 1), 'responsible': budget.user_id.name or ''},
            'kpis': {'planned': planned, 'actual': actual, 'committed': committed, 'available': planned - actual - committed,
                     'consumed_pct': round(actual / planned * 100, 1) if planned else 0.0,
                     'theoretical': budget.theoretical_total, 'projected': projected, 'projected_gap': planned - projected},
            'lines': lines, 'months': series, 'alerts': alerts,
            'currency': {'symbol': company.currency_id.symbol, 'position': company.currency_id.position,
                         'decimals': company.currency_id.decimal_places},
        }
