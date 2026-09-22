# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.tools.misc import formatLang

from .collection_performance import GROUPINGS


class CollectionReportRender(models.AbstractModel):
    _name = 'report.lab_collections.report_collection'
    _description = 'Collections renderer'

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        options = data.get('options') or {}
        group = 'user_id' if (data.get('tab') == 'user') else 'team_id'
        Perf = self.env['lab.collection.performance']
        all_rows = Perf.report_rows(group, options)
        # The same fence the screen puts up (dashboard_data): an executive's PDF is
        # their own round, not the company's. The leaderboards stay whole below,
        # exactly as they do on screen.
        scope = Perf._viewer_route_ids()
        rows = all_rows
        if scope is not None:
            rows = [r for r in all_rows
                    if (r['key'] in scope if group == 'team_id'
                        else r['key'] == self.env.uid)]
        # dd/mm/yyyy on paper, same as on the screen (client, 2026-08-21)
        dates = {k: v.strftime('%d/%m/%Y') for k, v in Perf._dates(options).items()}
        company = Perf._company(options)
        currency = company.currency_id
        totals = {
            'sales': round(sum(r['sales'] for r in rows), 2),
            'collected': round(sum(r['collected'] for r in rows), 2),
            'invoices': sum(r['invoices'] for r in rows),
            'overdue': round(sum(r['overdue'] for r in rows), 2),
        }
        totals['pending'] = round(totals['sales'] - totals['collected'], 2)
        totals['percent'] = round(totals['collected'] / totals['sales'] * 100, 1) \
            if totals['sales'] else 0.0
        totals['sales_target'] = round(sum(r['sales_target'] for r in rows), 2)
        # Reuse the rows already computed above: leaderboards() would otherwise
        # recompute the whole salesperson set a second time for every print.
        boards = Perf.leaderboards(options, _rows=all_rows if group == 'user_id' else None)
        excluded_names = ', '.join(e['label'] for e in boards.get('excluded', []))
        return {
            'doc_ids': docids, 'doc_model': 'lab.collection.performance', 'docs': [],
            'rows': rows, 'totals': totals, 'dates': dates,
            'boards': boards,
            'excluded_names': excluded_names,
            'target': Perf._targets(company)[0],
            'group_label': dict(GROUPINGS)[group],
            'money': lambda amount: u'%s %s' % (
                currency.symbol or '',
                formatLang(self.env, amount or 0.0, digits=currency.decimal_places)),
            'printed_on': fields.Datetime.context_timestamp(
                self.env.user, fields.Datetime.now()).strftime('%d/%m/%Y %H:%M'),
        }
