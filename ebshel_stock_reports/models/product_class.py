# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
"""ABC / XYZ classification per company and product (report #6)."""
import statistics
from collections import defaultdict

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.tools import SQL

from .stock_move_daily import ISSUE_TYPES


class ProductClass(models.Model):
    _name = 'asr.product.class'
    _description = 'Product ABC / XYZ Class'
    _order = 'issue_value desc, id'
    _rec_name = 'product_id'

    company_id = fields.Many2one('res.company', required=True, readonly=True, index=True)
    product_id = fields.Many2one('product.product', required=True, readonly=True, index=True, ondelete='cascade')
    categ_id = fields.Many2one(related='product_id.categ_id', store=True)
    abc_class = fields.Selection([('A', 'A'), ('B', 'B'), ('C', 'C')], string='ABC', readonly=True)
    xyz_class = fields.Selection([('X', 'X'), ('Y', 'Y'), ('Z', 'Z')], string='XYZ', readonly=True)
    combined_class = fields.Char(compute='_compute_combined_class', store=True)
    issue_value = fields.Monetary(
        currency_field='currency_id', readonly=True,
        groups='ebshel_stock_reports.group_see_values',
        help="Value at cost of deliveries and consumption over the classification window.")
    issue_qty = fields.Float('Issue Quantity', digits='Product Unit of Measure', readonly=True)
    cumulative_share = fields.Float('Cumulative Share (%)', readonly=True, aggregator=None)
    variation = fields.Float('Coefficient of Variation', readonly=True, digits=(16, 3), aggregator='avg',
                             help="Standard deviation / mean of the monthly issue quantity.")
    months = fields.Integer('Window (months)', readonly=True)
    date_from = fields.Date(readonly=True)
    date_to = fields.Date(readonly=True)
    computed_at = fields.Datetime(readonly=True)
    currency_id = fields.Many2one(related='company_id.currency_id')

    _unique_product = models.UniqueIndex("(company_id, product_id)")

    @api.depends('abc_class', 'xyz_class')
    def _compute_combined_class(self):
        for record in self:
            record.combined_class = (record.abc_class or '') + (record.xyz_class or '')

    @api.model
    def _cron_classify(self):
        for company in self.env['res.company'].sudo().search([]):  # pylint: disable=no-search-all
            self.with_company(company)._classify(company)
            self.env['ir.cron']._commit_progress(1)

    @api.model
    def _classify(self, company, months=12, date_to=None):
        """(Re)compute every class of the company over the last ``months`` full months."""
        company = company.sudo()
        date_to = date_to or company._asr_local_day(fields.Datetime.now())
        date_from = (date_to.replace(day=1) - relativedelta(months=months - 1))
        abc_a, abc_b = company._asr_parse_list('asr_abc_thresholds', float)
        xyz_x, xyz_y = company._asr_parse_list('asr_xyz_thresholds', float)
        self.env['asr.stock.dirty']._ensure_fresh(company)
        self.env.cr.execute(SQL("""
            SELECT product_id, date_trunc('month', day)::date AS month, SUM(qty_out), SUM(value_out)
              FROM asr_stock_move_daily
             WHERE company_id = %s AND move_type IN %s AND day >= %s AND day <= %s
             GROUP BY product_id, month
        """, company.id, ISSUE_TYPES, date_from, date_to))
        per_product = defaultdict(lambda: {'value': 0.0, 'qty': 0.0, 'months': defaultdict(float)})
        for product_id, month, qty, value in self.env.cr.fetchall():
            data = per_product[product_id]
            data['value'] += float(value or 0)
            data['qty'] += float(qty or 0)
            data['months'][month] += float(qty or 0)
        total_value = sum(d['value'] for d in per_product.values())
        month_keys = []
        cursor = date_from
        while cursor <= date_to:
            month_keys.append(cursor)
            cursor += relativedelta(months=1)
        ranked = sorted(per_product.items(), key=lambda kv: kv[1]['value'], reverse=True)
        now = fields.Datetime.now()
        rows = []
        cumulative = 0.0
        for product_id, data in ranked:
            cumulative += data['value']
            share = (cumulative / total_value * 100.0) if total_value else 100.0
            abc = 'A' if share <= abc_a else 'B' if share <= abc_b else 'C'
            series = [data['months'].get(m, 0.0) for m in month_keys]
            mean = statistics.fmean(series) if series else 0.0
            cv = (statistics.pstdev(series) / mean) if mean else 0.0
            xyz = 'X' if cv <= xyz_x else 'Y' if cv <= xyz_y else 'Z'
            rows.append({
                'company_id': company.id, 'product_id': product_id, 'abc_class': abc, 'xyz_class': xyz,
                'issue_value': data['value'], 'issue_qty': data['qty'], 'cumulative_share': share,
                'variation': cv, 'months': months, 'date_from': date_from, 'date_to': date_to,
                'computed_at': now,
            })
        existing = {r.product_id.id: r for r in self.sudo().search([('company_id', '=', company.id)])}
        to_create = []
        seen = set()
        for row in rows:
            seen.add(row['product_id'])
            record = existing.get(row['product_id'])
            if record:
                record.write(row)
            else:
                to_create.append(row)
        if to_create:
            self.sudo().create(to_create)
        # products without issues in the window: class C / Z, kept for completeness
        stale = self.sudo().browse([r.id for pid, r in existing.items() if pid not in seen])
        stale.write({'abc_class': 'C', 'xyz_class': 'Z', 'issue_value': 0.0, 'issue_qty': 0.0,
                     'cumulative_share': 100.0, 'variation': 0.0, 'months': months,
                     'date_from': date_from, 'date_to': date_to, 'computed_at': now})
        return len(rows)

    def action_classify_now(self):
        company = self.env.company
        self._classify(company)
        return {'type': 'ir.actions.client', 'tag': 'reload'}
