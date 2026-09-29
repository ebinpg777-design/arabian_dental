# -*- coding: utf-8 -*-
"""The figures behind the assets dashboard, in one call."""
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.tools import date_utils


class AssetDashboard(models.AbstractModel):
    _name = 'ebshel.asset.dashboard'
    _description = 'Assets dashboard'

    @api.model
    def get_data(self):
        if not self.env.su and not self.env.user.has_groups('account.group_account_readonly,account.group_account_invoice'):
            return {'error': _("The assets dashboard is for the accounting team.")}
        today = fields.Date.context_today(self)
        company = self.env.company
        fy = company.compute_fiscalyear_dates(today)
        Asset = self.env['ebshel.asset']
        live = Asset.search([('company_id', '=', company.id), ('state', 'in', ('running', 'paused', 'closed'))])
        drafts = Asset.search([('company_id', '=', company.id), ('state', '=', 'draft')], order='purchase_date desc')
        disposed_fy = Asset.search([('company_id', '=', company.id), ('state', '=', 'disposed'),
                                    ('disposal_date', '>=', fy['date_from']), ('disposal_date', '<=', today)])
        additions_fy = live.filtered(lambda a: fy['date_from'] <= a.purchase_date <= today)
        Line = self.env['ebshel.asset.line']
        charge_fy = sum(Line.search([('asset_id.company_id', '=', company.id), ('state', '=', 'posted'),
                                     ('kind', '!=', 'disposal'), ('date', '>=', fy['date_from']),
                                     ('date', '<=', today)]).mapped('amount'))
        next_month_end = date_utils.end_of(today + relativedelta(months=1), 'month')
        due = Line.search([('asset_id.company_id', '=', company.id), ('state', '=', 'draft'),
                           ('asset_id.state', '=', 'running'), ('date', '<=', next_month_end)])
        overdue = due.filtered(lambda l: l.date <= today)
        by_cat = {}
        for a in live:
            row = by_cat.setdefault(a.category_id.id, {'name': a.category_id.name, 'gross': 0.0, 'book': 0.0, 'count': 0})
            row['gross'] += a.purchase_value
            row['book'] += a.book_value
            row['count'] += 1
        cats = sorted(by_cat.values(), key=lambda r: -r['book'])
        # the charge ahead: draft lines by fiscal year, five years out
        forecast = {}
        for line in Line.search([('asset_id.company_id', '=', company.id), ('state', '=', 'draft'),
                                 ('asset_id.state', 'in', ('running', 'paused'))]):
            year = company.compute_fiscalyear_dates(line.date)['date_to'].year
            forecast[year] = forecast.get(year, 0.0) + line.amount
        years = sorted(forecast)[:6]
        soon = today + timedelta(days=60)
        cover = Asset.search([('company_id', '=', company.id), ('state', 'in', ('running', 'paused')), '|',
                              '&', ('warranty_end', '!=', False), ('warranty_end', '<=', soon),
                              '&', ('insurance_end', '!=', False), ('insurance_end', '<=', soon)], order='warranty_end, insurance_end')
        cur = company.currency_id
        fmt = lambda v: cur.format(v) if hasattr(cur, 'format') else '%.2f' % v   # noqa: E731
        return {
            'currency': cur.symbol or '', 'fy_label': '%s – %s' % (fy['date_from'].strftime('%b %Y'), fy['date_to'].strftime('%b %Y')),
            'kpis': [
                {'key': 'count', 'label': _('Assets in use'), 'value': len(live.filtered(lambda a: a.state != 'closed')), 'text': str(len(live.filtered(lambda a: a.state != 'closed'))), 'sub': _('%s fully depreciated', len(live.filtered(lambda a: a.state == 'closed')))},
                {'key': 'gross', 'label': _('Gross value'), 'value': sum(live.mapped('purchase_value')), 'text': fmt(sum(live.mapped('purchase_value'))), 'sub': _('at cost')},
                {'key': 'book', 'label': _('Net book value'), 'value': sum(live.mapped('book_value')), 'text': fmt(sum(live.mapped('book_value'))), 'sub': _('after %s depreciation', fmt(sum(live.mapped('depreciated_value'))))},
                {'key': 'charge', 'label': _('Depreciation this year'), 'value': charge_fy, 'text': fmt(charge_fy), 'sub': _('posted so far')},
                {'key': 'additions', 'label': _('Additions this year'), 'value': sum(additions_fy.mapped('purchase_value')), 'text': fmt(sum(additions_fy.mapped('purchase_value'))), 'sub': _('%s asset(s)', len(additions_fy))},
                {'key': 'disposals', 'label': _('Disposals this year'), 'value': sum(disposed_fy.mapped('purchase_value')), 'text': fmt(sum(disposed_fy.mapped('purchase_value'))), 'sub': _('gain (loss) %s', fmt(sum(disposed_fy.mapped('gain_loss'))))},
                {'key': 'due', 'label': _('Charge due by next month end'), 'value': sum(due.mapped('amount')), 'text': fmt(sum(due.mapped('amount'))), 'sub': _('%s entr(y/ies), %s overdue', len(due), len(overdue)), 'warn': bool(overdue)},
            ],
            'categories': [dict(c, gross_text=fmt(c['gross']), book_text=fmt(c['book'])) for c in cats],
            'forecast': [{'year': y, 'value': forecast[y], 'text': fmt(forecast[y])} for y in years],
            'drafts': [{'id': a.id, 'name': a.name, 'value': fmt(a.purchase_value), 'date': fields.Date.to_string(a.purchase_date),
                        'bill': a.bill_id.name or ''} for a in drafts[:8]],
            'cover': [{'id': a.id, 'name': a.name, 'warranty': fields.Date.to_string(a.warranty_end) if a.warranty_end else '',
                       'insurance': fields.Date.to_string(a.insurance_end) if a.insurance_end else '', 'state': a.cover_state}
                      for a in cover[:8]],
            'overdue_ids': overdue.mapped('asset_id').ids,
            'paused': [{'id': a.id, 'name': a.name, 'since': fields.Date.to_string(a.paused_on) if a.paused_on else ''}
                       for a in live.filtered(lambda a: a.state == 'paused')[:8]],
        }

    @api.model
    def post_overdue(self):
        Asset = self.env['ebshel.asset']
        Asset.search([('company_id', '=', self.env.company.id), ('state', '=', 'running')]).action_post_due()
        return True
