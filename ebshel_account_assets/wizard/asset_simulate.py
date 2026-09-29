# -*- coding: utf-8 -*-
"""Try the methods before choosing one: the same value under straight line,
declining balance and declining-then-straight, side by side."""
from odoo import api, fields, models, _
from odoo.tools.misc import formatLang


class AssetSimulate(models.TransientModel):
    _name = 'ebshel.asset.simulate'
    _description = 'Depreciation simulator'

    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    purchase_value = fields.Monetary(default=100000.0, required=True)
    salvage_value = fields.Monetary(default=0.0)
    start_date = fields.Date(default=fields.Date.context_today, required=True)
    period = fields.Selection([('month', 'Monthly'), ('year', 'Yearly')], default='year', required=True)
    duration = fields.Integer(default=5, required=True)
    declining_rate = fields.Float(default=30.0)
    prorata = fields.Selection([('none', 'Full first period'), ('days', 'Prorated by days')], default='days', required=True)
    result_html = fields.Html(readonly=True, sanitize=False)

    def action_run(self):
        self.ensure_one()
        Asset = self.env['ebshel.asset'].with_company(self.company_id)
        proxy = Asset.new({'company_id': self.company_id.id})
        total = self.purchase_value - self.salvage_value
        tables = {}
        for method in ('linear', 'declining', 'declining_linear'):
            tables[method] = proxy._schedule_amounts(total, self.start_date, self.duration, method,
                                                     self.declining_rate, self.period, self.prorata)
        n = max(len(t) for t in tables.values()) if tables else 0
        labels = {'linear': _('Straight line'), 'declining': _('Declining %s%%', self.declining_rate),
                  'declining_linear': _('Declining, then straight')}

        def money(v):
            return formatLang(self.env, v, currency_obj=self.currency_id)
        head = ''.join('<th style="text-align:right">%s</th>' % labels[m] for m in tables)
        rows = []
        for i in range(n):
            cells = []
            date = None
            for m in tables:
                if i < len(tables[m]):
                    date = date or tables[m][i][0]
                    cells.append('<td style="text-align:right">%s</td>' % money(tables[m][i][1]))
                else:
                    cells.append('<td></td>')
            rows.append('<tr><td>%d</td><td>%s</td>%s</tr>' % (i + 1, date, ''.join(cells)))
        foot = ''.join('<th style="text-align:right">%s</th>' % money(sum(a for _d, a in tables[m])) for m in tables)
        first = ''.join('<td style="text-align:right">%s</td>' % money(tables[m][0][1] if tables[m] else 0) for m in tables)
        self.result_html = (
            '<table class="table table-sm"><thead><tr><th>#</th><th>Date</th>%s</tr></thead>'
            '<tbody>%s</tbody><tfoot><tr><th colspan="2">Total</th>%s</tr>'
            '<tr><td colspan="2" class="text-muted">First period</td>%s</tr></tfoot></table>'
            % (head, ''.join(rows), foot, first))
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id,
                'view_mode': 'form', 'target': 'new'}
