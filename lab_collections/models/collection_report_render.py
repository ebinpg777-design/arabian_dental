# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
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
        company = Perf._company(options)
        # The paper reads against the same figure the screen does - the
        # screen's own choice if it was printed from one, else the company's.
        basis = Perf._basis(options, company)
        dates_raw = Perf._dates(options)
        # Both countbacks and the salesperson map ONCE, for the rows and for the
        # leaderboards under them: asked separately, a route-tab print ran the
        # countback four times. Nothing on paper draws a sparkline, so the
        # trend is not computed either. (2026-09-27)
        debits, opening_debits, users = Perf._ledger_snapshot(company, dates_raw)
        closing_day, closing_debits, _is_today = Perf._closing(
            company, dates_raw, today_debits=debits)
        opening_day_raw = Perf._opening_day(dates_raw)
        carried_rows = [d for d in closing_debits if d['date'] <= opening_day_raw]

        def rows_for(grouping):
            return Perf.report_rows(
                grouping, options, _debits=debits, _users=users, _trend={},
                _opening=Perf._overdue_by(grouping, opening_debits, company,
                                          users=users),
                _basis=basis,
                _carried=Perf._carried_by(grouping, carried_rows, company, users=users))

        all_rows = rows_for(group)
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
        dates = {k: v.strftime('%d/%m/%Y') for k, v in dates_raw.items()}
        opening_date = Perf._opening_day(dates_raw).strftime('%d/%m/%Y')
        currency = company.currency_id
        totals = {
            'sales': round(sum(r['sales'] for r in rows), 2),
            'collected': round(sum(r['collected'] for r in rows), 2),
            'opening': round(sum(r['opening'] for r in rows), 2),
            'invoices': sum(r['invoices'] for r in rows),
            'overdue': round(sum(r['overdue'] for r in rows), 2),
        }
        # The opening book's fate for the rows on this paper; the CEI needs the
        # period's billing, which is only read for the whole lab.
        if scope is not None:
            opening_debits = [d for d in opening_debits if d['team_key'] in scope]
            closing_debits = [d for d in closing_debits if d['team_key'] in scope]
        book = Perf._opening_book(
            dates_raw, opening_debits, closing_debits, closing_day,
            billed=(Perf._billed_between(company, dates_raw['pay_from'], closing_day)
                    if scope is None else None))
        book.pop('silent_partner_ids')
        totals['carried'] = book['carried']
        totals['base'] = totals['opening'] if basis == 'opening' else totals['sales']
        totals['pending'] = round(totals['base'] - totals['collected'], 2)
        totals['percent'] = round(totals['collected'] / totals['base'] * 100, 1) \
            if totals['base'] > 0 else 0.0
        totals['sales_target'] = round(sum(r['sales_target'] for r in rows), 2)
        # The boards rank people whichever tab is printed: the person rows are
        # the ones already built when that is the tab, else built from the same
        # snapshot rather than from a fresh countback.
        boards = Perf.leaderboards(
            options, _rows=all_rows if group == 'user_id' else rows_for('user_id'))
        excluded_names = ', '.join(e['label'] for e in boards.get('excluded', []))
        return {
            'doc_ids': docids, 'doc_model': 'lab.collection.performance', 'docs': [],
            'rows': rows, 'totals': totals, 'dates': dates,
            'boards': boards,
            'excluded_names': excluded_names,
            'target': Perf._targets(company)[0],
            'basis': basis,
            'book': book,
            'basis_note': (_('of what was open on %s', opening_date)
                           if basis == 'opening' else _('of what was invoiced')),
            'opening_date': opening_date,
            'group_label': dict(GROUPINGS)[group],
            'money': lambda amount: u'%s %s' % (
                currency.symbol or '',
                formatLang(self.env, amount or 0.0, digits=currency.decimal_places)),
            'printed_on': fields.Datetime.context_timestamp(
                self.env.user, fields.Datetime.now()).strftime('%d/%m/%Y %H:%M'),
        }
