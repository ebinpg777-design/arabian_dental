# -*- coding: utf-8 -*-
"""The depreciation schedule, as a dynamic report: every asset by category with
its gross value, what was depreciated before the period, in it, to its end, and
the book value it leaves."""
from odoo import api, fields, models, _


class AssetScheduleHandler(models.AbstractModel):
    _name = 'ebshel.fin.handler.asset_schedule'
    _inherit = 'ebshel.fin.handler'
    _description = 'Financial report engine: depreciation schedule'
    _kind_label = 'Depreciation schedule'

    def adjust_options(self, report, options):
        options['comparison'] = {'mode': 'none', 'periods': 1, 'from': None, 'to': None}
        options['growth'] = False
        return options

    def columns(self, report, options):
        return [{'key': 'gross', 'label': _('Gross value'), 'type': 'amount'},
                {'key': 'before', 'label': _('Depreciated before'), 'type': 'amount'},
                {'key': 'period', 'label': _('Depreciation in period'), 'type': 'amount'},
                {'key': 'todate', 'label': _('Depreciated to date'), 'type': 'amount'},
                {'key': 'book', 'label': _('Book value'), 'type': 'amount'}]

    def _rows(self, options):
        d_from = fields.Date.to_date(options['date']['from'])
        d_to = fields.Date.to_date(options['date']['to'])
        Asset = self.env['ebshel.asset']
        domain = [('company_id', 'in', options['companies']), ('state', 'not in', ('draft', 'cancelled')),
                  ('purchase_date', '<=', d_to)]
        rows = []
        for asset in Asset.search(domain, order='category_id, purchase_date, id'):
            lines = asset.line_ids.filtered(lambda l: l.kind != 'disposal' and l.state != 'skipped')
            posted_or_due = lines.filtered(lambda l: l.state == 'posted' or not options['posted_only'])
            before = asset.already_depreciated + sum(posted_or_due.filtered(lambda l: l.date < d_from).mapped('amount'))
            period = sum(posted_or_due.filtered(lambda l: d_from <= l.date <= d_to).mapped('amount'))
            todate = before + period
            rows.append({'asset': asset, 'gross': asset.purchase_value, 'before': before, 'period': period,
                         'todate': todate, 'book': asset.purchase_value - todate})
        return rows

    def lines(self, report, options, columns, for_export=False):
        currency = self.engine.currency(options)
        out, by_cat = [], {}
        for row in self._rows(options):
            by_cat.setdefault(row['asset'].category_id, []).append(row)
        keys = ('gross', 'before', 'period', 'todate', 'book')
        grand = dict.fromkeys(keys, 0.0)
        for cat, rows in by_cat.items():
            totals = {k: sum(r[k] for r in rows) for k in keys}
            lid = 'cat:%d' % cat.id
            unfolded = self._is_open(options, lid) or options.get('unfold_all')
            out.append(self._line(lid, cat.name, 0, [self._cell(totals[k], currency) for k in keys],
                                  unfoldable=True, unfolded=unfolded, bold=True, kind='category'))
            if unfolded:
                for r in rows:
                    a = r['asset']
                    out.append(self._line('as:%d' % a.id, '%s · %s' % (a.code, a.name), 1,
                                          [self._cell(r[k], currency, drill=(k in ('period', 'todate'))) for k in keys],
                                          parent_id=lid, kind='asset', model='ebshel.asset', res_id=a.id,
                                          css='muted' if a.state in ('disposed', 'closed') else ''))
            for k in keys:
                grand[k] += totals[k]
        out.append(self._line('total', _('Total'), 0, [self._cell(grand[k], currency) for k in keys], bold=True, kind='total'))
        return out

    def expand(self, report, options, columns, line_id, offset=0):
        if not line_id.startswith('cat:'):
            return {'lines': [], 'has_more': False}
        options = dict(options, expanded=list(options.get('expanded') or []) + [line_id])
        lines = self.lines(report, options, columns)
        return {'lines': [l for l in lines if l['parent_id'] == line_id], 'has_more': False}

    def drill(self, report, options, columns, line_id, column_key):
        if not line_id.startswith('as:'):
            return None
        asset = self.env['ebshel.asset'].browse(int(line_id.split(':')[1]))
        d_from = fields.Date.to_date(options['date']['from'])
        d_to = fields.Date.to_date(options['date']['to'])
        domain = [('move_id.ebshel_asset_id', '=', asset.id), ('parent_state', '=', 'posted')]
        if column_key == 'period':
            domain += [('date', '>=', d_from), ('date', '<=', d_to)]
        else:
            domain.append(('date', '<=', d_to))
        return domain, asset.name
