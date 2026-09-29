# -*- coding: utf-8 -*-
"""Two more dynamic reports: Budget vs Actual and the Deferral Schedule."""
from odoo import fields, models, _


class BudgetHandler(models.AbstractModel):
    _name = 'ebshel.fin.handler.budget'
    _inherit = 'ebshel.fin.handler'
    _description = 'Financial report engine: budget vs actual'
    _kind_label = 'Budget vs actual'

    KEYS = ('planned', 'actual', 'committed', 'available', 'pct', 'theoretical')

    def adjust_options(self, report, options):
        options['comparison'] = {'mode': 'none', 'periods': 1, 'from': None, 'to': None}
        options['growth'] = False
        return options

    def columns(self, report, options):
        return [{'key': 'planned', 'label': _('Planned'), 'type': 'amount'},
                {'key': 'actual', 'label': _('Actual'), 'type': 'amount'},
                {'key': 'committed', 'label': _('Committed'), 'type': 'amount'},
                {'key': 'available', 'label': _('Available'), 'type': 'amount'},
                {'key': 'pct', 'label': _('Achieved'), 'type': 'percent'},
                {'key': 'theoretical', 'label': _('Theoretical to date'), 'type': 'amount'}]

    def _budgets(self, options):
        d_from = fields.Date.to_date(options['date']['from'])
        d_to = fields.Date.to_date(options['date']['to'])
        return self.env['ebshel.budget'].search([('company_id', 'in', options['companies']), ('state', 'in', ('confirmed', 'done')),
                                                 ('date_from', '<=', d_to), ('date_to', '>=', d_from)])

    def _cells(self, values, currency):
        return [self._cell(values['planned'], currency), self._cell(values['actual'], currency, drill=True),
                self._cell(values['committed'], currency, drill=True), self._cell(values['available'], currency),
                self._cell(values['pct'], currency, display='percent'), self._cell(values['theoretical'], currency)]

    def lines(self, report, options, columns, for_export=False):
        currency = self.engine.currency(options)
        out = []
        grand = dict.fromkeys(self.KEYS, 0.0)
        for budget in self._budgets(options):
            lid = 'bd:%d' % budget.id
            unfolded = self._is_open(options, lid)
            totals = {'planned': budget.planned_total, 'actual': budget.actual_total, 'committed': budget.committed_total,
                      'available': budget.available_total, 'pct': budget.consumed_pct, 'theoretical': budget.theoretical_total}
            out.append(self._line(lid, '%s (%s → %s)' % (budget.name, fields.Date.to_string(budget.date_from), fields.Date.to_string(budget.date_to)),
                                  0, self._cells(totals, currency), unfoldable=True, unfolded=unfolded, bold=True, kind='budget'))
            if unfolded:
                for l in budget.line_ids:
                    values = {'planned': l.planned, 'actual': l.actual, 'committed': l.committed, 'available': l.available,
                              'pct': l.achieved_pct, 'theoretical': l.theoretical}
                    out.append(self._line('bl:%d' % l.id, l.name, 1, self._cells(values, currency), parent_id=lid, kind='budget_line',
                                          model='ebshel.budget.line', res_id=l.id, css='danger' if l.alert == 'over' else 'warning' if l.alert == 'watch' else ''))
            for k in ('planned', 'actual', 'committed', 'available', 'theoretical'):
                grand[k] += totals[k]
        grand['pct'] = (grand['actual'] / grand['planned'] * 100.0) if grand['planned'] else 0.0
        out.append(self._line('total', _('Total'), 0, self._cells(grand, currency), bold=True, kind='total'))
        return out

    def expand(self, report, options, columns, line_id, offset=0):
        if not line_id.startswith('bd:'):
            return {'lines': [], 'has_more': False}
        options = dict(options, expanded=list(options.get('expanded') or []) + [line_id])
        return {'lines': [l for l in self.lines(report, options, columns) if l['parent_id'] == line_id], 'has_more': False}

    def drill(self, report, options, columns, line_id, column_key):
        if not line_id.startswith('bl:'):
            return None
        line = self.env['ebshel.budget.line'].browse(int(line_id.split(':')[1]))
        states = ('draft',) if column_key == 'committed' else ('posted',)
        return line._actual_domain(states=states), line.name


class DeferralHandler(models.AbstractModel):
    _name = 'ebshel.fin.handler.deferral'
    _inherit = 'ebshel.fin.handler'
    _description = 'Financial report engine: deferral schedule'
    _kind_label = 'Deferral schedule'

    def adjust_options(self, report, options):
        options['comparison'] = {'mode': 'none', 'periods': 1, 'from': None, 'to': None}
        options['growth'] = False
        return options

    def columns(self, report, options):
        return [{'key': 'total', 'label': _('Total'), 'type': 'amount'},
                {'key': 'before', 'label': _('Recognised before'), 'type': 'amount'},
                {'key': 'period', 'label': _('In the period'), 'type': 'amount'},
                {'key': 'after', 'label': _('Still to recognise'), 'type': 'amount'},
                {'key': 'balance', 'label': _('Balance at period end'), 'type': 'amount'}]

    def _rows(self, options):
        d_from = fields.Date.to_date(options['date']['from'])
        d_to = fields.Date.to_date(options['date']['to'])
        rows = []
        for d in self.env['ebshel.deferral'].search([('company_id', 'in', options['companies']), ('state', 'in', ('running', 'done')),
                                                     ('date_from', '<=', d_to)], order='kind, date_from, id'):
            lines = d.line_ids.filtered(lambda l: l.state != 'skipped')
            counted = lines.filtered(lambda l: l.state == 'posted' or not options['posted_only'])
            before = sum(counted.filtered(lambda l: l.date < d_from).mapped('amount'))
            period = sum(counted.filtered(lambda l: d_from <= l.date <= d_to).mapped('amount'))
            rows.append({'deferral': d, 'total': d.total, 'before': before, 'period': period,
                         'after': d.total - before - period, 'balance': d.total - before - period})
        return rows

    def lines(self, report, options, columns, for_export=False):
        currency = self.engine.currency(options)
        keys = ('total', 'before', 'period', 'after', 'balance')
        out, by_kind = [], {}
        for row in self._rows(options):
            by_kind.setdefault(row['deferral'].kind, []).append(row)
        grand = dict.fromkeys(keys, 0.0)
        for kind, rows in by_kind.items():
            lid = 'kd:%s' % kind
            totals = {k: sum(r[k] for r in rows) for k in keys}
            unfolded = self._is_open(options, lid)
            out.append(self._line(lid, _('Deferred revenue') if kind == 'revenue' else _('Prepaid expenses'), 0,
                                  [self._cell(totals[k], currency) for k in keys], unfoldable=True, unfolded=unfolded, bold=True, kind='kind'))
            if unfolded:
                for r in rows:
                    d = r['deferral']
                    out.append(self._line('df:%d' % d.id, '%s · %s · %s → %s' % (d.code, d.name, fields.Date.to_string(d.date_from), fields.Date.to_string(d.date_to)),
                                          1, [self._cell(r[k], currency, drill=(k == 'period')) for k in keys], parent_id=lid,
                                          kind='deferral', model='ebshel.deferral', res_id=d.id, css='muted' if d.state == 'done' else ''))
            for k in keys:
                grand[k] += totals[k]
        out.append(self._line('total', _('Total'), 0, [self._cell(grand[k], currency) for k in keys], bold=True, kind='total'))
        return out

    def expand(self, report, options, columns, line_id, offset=0):
        if not line_id.startswith('kd:'):
            return {'lines': [], 'has_more': False}
        options = dict(options, expanded=list(options.get('expanded') or []) + [line_id])
        return {'lines': [l for l in self.lines(report, options, columns) if l['parent_id'] == line_id], 'has_more': False}

    def drill(self, report, options, columns, line_id, column_key):
        if not line_id.startswith('df:'):
            return None
        d = self.env['ebshel.deferral'].browse(int(line_id.split(':')[1]))
        domain = [('move_id.ebshel_deferral_id', '=', d.id), ('parent_state', '=', 'posted'),
                  ('date', '>=', options['date']['from']), ('date', '<=', options['date']['to'])]
        return domain, d.name
