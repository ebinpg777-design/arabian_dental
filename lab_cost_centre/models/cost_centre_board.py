# -*- coding: utf-8 -*-
"""The Cost Centre Cockpit: what every department spent, used and earned.

One screen, one call. The figures a manager asks for by department are spread
across four models - analytic items for money, manufacturing orders for work in
hand, material requests for what is on its way, employees for who is there - and
asking them one department at a time is what makes such a screen slow enough to
stop being opened.
"""
from datetime import date
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

PERIODS = ('month', 'last_month', 'quarter', 'year')


class LabCostCentreBoard(models.AbstractModel):
    _name = 'lab.cost.centre.board'
    _description = 'Cost Centre Cockpit'

    # ------------------------------------------------------------------ window
    @api.model
    def _period_window(self, period):
        """Start and end of the period asked for, in the lab's own days."""
        today = fields.Date.context_today(self)
        if period == 'last_month':
            first = date(today.year, today.month, 1) - relativedelta(months=1)
            return first, first + relativedelta(months=1)
        if period == 'quarter':
            first = date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
            return first, first + relativedelta(months=3)
        if period == 'year':
            first = date(today.year, 1, 1)
            return first, first + relativedelta(years=1)
        first = date(today.year, today.month, 1)
        return first, first + relativedelta(months=1)

    # ------------------------------------------------------------------- board
    @api.model
    def get_board(self, period='month'):
        period = period if period in PERIODS else 'month'
        start, end = self._period_window(period)
        departments = self.env['hr.department'].search([])
        centres = {d.cost_centre_id.id: d.id for d in departments if d.cost_centre_id}

        # MONEY. Analytic items carry the sign Odoo gives them: a cost is
        # negative and revenue positive, which is why they are split here rather
        # than summed - a department that both spends and bills would otherwise
        # report the difference and call it spend.
        spend, earned = {}, {}
        if centres:
            # Grouped on the cost centre plan's OWN column, not on
            # `auto_account_id`. Odoo 19 gives each analytic plan a column of its
            # own on the line - `account_id` for the first, `x_plan<id>_id` for
            # the rest - and `auto_account_id` is an unstored convenience that
            # reads whichever is filled. Grouping on it raises rather than
            # falling back, so the whole cockpit would answer with a traceback.
            column = self.env['account.analytic.plan']._lab_cost_centre_plan()._column_name()
            for centre, amount in self.env['account.analytic.line']._read_group(
                    [(column, 'in', list(centres)),
                     ('date', '>=', start), ('date', '<', end),
                     ('amount', '<', 0)],
                    [column], ['amount:sum']):
                spend[centres[centre.id]] = -amount
            for centre, amount in self.env['account.analytic.line']._read_group(
                    [(column, 'in', list(centres)),
                     ('date', '>=', start), ('date', '<', end),
                     ('amount', '>', 0)],
                    [column], ['amount:sum']):
                earned[centres[centre.id]] = amount

        # WORK IN HAND, one grouped read each.
        jobs = dict(self.env['mrp.production']._read_group(
            [('state', 'not in', ('done', 'cancel')),
             ('department_id', 'in', departments.ids)],
            ['department_id'], ['__count']))
        steps = dict(self.env['mrp.workorder']._read_group(
            [('state', 'not in', ('done', 'cancel')),
             ('department_id', 'in', departments.ids)],
            ['department_id'], ['__count']))
        requests = dict(self.env['material.request']._read_group(
            [('state', 'in', ('confirm', 'approved')),
             ('department_id', 'in', departments.ids)],
            ['department_id'], ['__count']))
        orders = dict(self.env['sale.order']._read_group(
            [('state', '=', 'sale'), ('date_order', '>=', start),
             ('date_order', '<', end), ('department_id', 'in', departments.ids)],
            ['department_id'], ['__count']))

        rows = []
        for department in departments:
            rows.append({
                'id': department.id,
                'name': department.name,
                'code': department.code or '',
                'kind': department.dept_type or '',
                'parent': department.parent_id.id or False,
                'parent_name': department.parent_id.name or '',
                'centre': department.cost_centre_id.id or False,
                'spend': round(spend.get(department.id, 0.0), 2),
                'earned': round(earned.get(department.id, 0.0), 2),
                'jobs': jobs.get(department, 0),
                'steps': steps.get(department, 0),
                'requests': requests.get(department, 0),
                'orders': orders.get(department, 0),
                'people': len(department.member_ids),
                'benches': department.workcenter_count,
            })
        # The biggest spender sets the length of every bar, so the cards can be
        # read against each other at a glance instead of one at a time.
        top = max([r['spend'] for r in rows] + [0.0]) or 1.0
        for row in rows:
            row['share'] = round(100.0 * row['spend'] / top, 1)

        return {
            'period': period,
            'from': fields.Date.to_string(start),
            'to': fields.Date.to_string(end - relativedelta(days=1)),
            'currency': self.env.company.currency_id.symbol,
            'rows': rows,
            'totals': {
                'spend': round(sum(r['spend'] for r in rows), 2),
                'earned': round(sum(r['earned'] for r in rows), 2),
                'jobs': sum(r['jobs'] for r in rows),
                'requests': sum(r['requests'] for r in rows),
                'departments': len(rows),
                'uncosted': sum(1 for r in rows if not r['centre']),
            },
        }

    @api.model
    def open_items(self, department_id, period='month'):
        """The analytic items behind one card."""
        start, end = self._period_window(period)
        department = self.env['hr.department'].browse(int(department_id))
        return {
            'type': 'ir.actions.act_window',
            'name': '%s — %s' % (department.display_name, start.strftime('%B %Y')),
            'res_model': 'account.analytic.line',
            'view_mode': 'list,pivot,graph',
            # `views`, not just `view_mode`: this action is fetched by the
            # board through `call_kw`, and only `call_button` runs Odoo's
            # `clean_action`, which is what turns a view_mode string into the
            # list the web client reads. Without it the client does
            # `action.views.map(...)` on undefined and the click dies with a
            # TypeError instead of opening anything.
            'views': [(False, 'list'), (False, 'pivot'), (False, 'graph'),
                      (False, 'form')],
            'domain': [('auto_account_id', '=', department.cost_centre_id.id),
                       ('date', '>=', fields.Date.to_string(start)),
                       ('date', '<', fields.Date.to_string(end))],
            # See `hr_department.action_view_analytic_items`: without the plan in
            # context the account column is blank on every row.
            'context': {'analytic_plan_id': department.cost_centre_id.plan_id.id},
        }
