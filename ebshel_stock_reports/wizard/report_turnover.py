# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
"""Report #7 - Stock turnover and days of cover.

Per product over the period: the issue quantity and the issue value at cost
(deliveries and consumption from the daily movement summary, DESIGN.md
§2.3), the average inventory value (mean of the opening value and the
closing values at each month end of the period, from the valuation
summary), the turnover ratio = issue value at cost / average inventory
value, the average daily issue over the company's cover window and the days
of cover = on-hand quantity / average daily issue. With a warehouse or
location filter the quantities are those of the selected locations and the
values Odoo's ratio of the company value.
"""
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.tools import SQL

from ..models.stock_move_daily import ISSUE_TYPES
from .report_mixin import col


class ReportTurnover(models.TransientModel):
    _name = 'asr.report.turnover'
    _inherit = 'asr.report.mixin'
    _description = 'Turnover / Days of Cover'
    _asr_title = 'Turnover / Days of Cover'
    _asr_line_model = 'asr.report.turnover.line'
    _asr_default_months = 12

    hide_empty = fields.Boolean('Hide products without issue or stock', default=True)
    cover_days = fields.Integer(compute='_compute_cover_days')
    line_ids = fields.One2many('asr.report.turnover.line', 'wizard_id')

    @api.depends('company_id')
    def _compute_cover_days(self):
        for wizard in self:
            wizard.cover_days = wizard.company_id.sudo().asr_cover_days or 90

    # ------------------------------------------------------------------
    # Columns
    # ------------------------------------------------------------------
    def _asr_columns(self):
        return [
            col('product_id', 'Product', 'many2one', width=34),
            col('default_code', 'Internal Reference', width=14),
            col('categ_id', 'Category', 'many2one', width=20),
            col('uom_id', 'Unit', 'many2one', width=8),
            col('qty_opening', 'Opening Qty', 'qty', total=True),
            col('qty_issue', 'Issue Qty', 'qty', total=True),
            col('value_issue', 'Issue Value at Cost', 'monetary', value=True, total=True),
            col('value_avg_inventory', 'Average Inventory Value', 'monetary', value=True, total=True,
                help_text="Mean of the opening value and the closing values at each month end of the period."),
            col('turnover', 'Turnover', 'float', value=True,
                help_text="Issue value at cost / average inventory value."),
            col('qty_on_hand', 'On Hand', 'qty', total=True),
            col('avg_daily_issue', 'Avg Daily Issue', 'qty',
                help_text="Issue quantity over the cover window back from the end date, per day."),
            col('days_of_cover', 'Days of Cover', 'float',
                help_text="On-hand quantity / average daily issue; blank when there is no issue."),
        ]

    def _asr_filter_text(self):
        text = super()._asr_filter_text()
        window = self.env._("Cover window: %s days", self.cover_days)
        return f"{text} | {window}" if text else window

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------
    def _period(self):
        date_to = self.date_to or fields.Date.context_today(self)
        date_from = self.date_from or date_to.replace(day=1)
        return date_from, date_to

    def _filtered_product_ids(self):
        if self.product_ids or self.categ_ids:
            return self._asr_products().ids
        return None

    def _product_sql(self, product_ids, alias='d'):
        if product_ids is None:
            return SQL("TRUE")
        if not product_ids:
            return SQL("FALSE")
        return SQL("%s.product_id IN %s", SQL.identifier(alias), tuple(product_ids))

    def _value_points(self, date_from, date_to):
        """The days whose closing values are averaged: the opening day, every month end of the
        period and, when it is not a month end itself, the end date."""
        points = [date_from - timedelta(days=1)]
        cursor = date_from.replace(day=1) + relativedelta(months=1) - timedelta(days=1)
        while cursor <= date_to:
            points.append(cursor)
            cursor = cursor + timedelta(days=1) + relativedelta(months=1) - timedelta(days=1)
        if points[-1] != date_to:
            points.append(date_to)
        return points

    def _issues(self, company, date_from, date_to, locations, location_level, product_ids):
        """{product_id: (issue qty, issue value)} over the period."""
        location_sql = SQL("d.location_id IN %s", tuple(locations.ids)) if location_level else SQL("TRUE")
        self.env.cr.execute(SQL("""
            SELECT d.product_id, SUM(d.qty_out), SUM(d.value_out)
              FROM asr_stock_move_daily d
             WHERE d.company_id = %s AND d.day >= %s AND d.day <= %s AND d.move_type IN %s
               AND %s AND %s
             GROUP BY d.product_id
        """, company.id, date_from, date_to, ISSUE_TYPES, location_sql, self._product_sql(product_ids)))
        return {pid: (float(q or 0), float(v or 0)) for pid, q, v in self.env.cr.fetchall()}

    def _company_closings(self, company, product_ids, days):
        """{day: {product_id: (closing_qty, closing_value)}} from the valuation summary."""
        if not product_ids:
            return {day: {} for day in days}
        values = SQL(', ').join(SQL('(%s::date)', day) for day in days)
        self.env.cr.execute(SQL("""
            SELECT p.day, v.product_id, v.closing_qty, v.closing_value
              FROM (VALUES %s) AS p(day)
              JOIN LATERAL (
                   SELECT DISTINCT ON (product_id) product_id, closing_qty, closing_value
                     FROM asr_stock_value_daily
                    WHERE company_id = %s AND product_id IN %s AND day <= p.day
                    ORDER BY product_id, day DESC
              ) v ON TRUE
        """, values, company.id, tuple(product_ids)))
        res = {day: {} for day in days}
        for day, pid, qty, value in self.env.cr.fetchall():
            res[day][pid] = (float(qty or 0), float(value or 0))
        return res

    def _asr_rows(self):
        self.ensure_one()
        self.env.flush_all()
        company = self.company_id.sudo()
        date_from, date_to = self._period()
        locations = self._asr_locations()
        location_level = bool(self.location_ids or self.warehouse_ids)
        product_ids = self._filtered_product_ids()
        cover_days = company.asr_cover_days or 90
        cover_from = date_to - timedelta(days=cover_days - 1)
        points = self._value_points(date_from, date_to)
        opening_day = points[0]

        issues = self._issues(company, date_from, date_to, locations, location_level, product_ids)
        cover_issues = self._issues(company, cover_from, date_to, locations, location_level, product_ids)
        Ledger = self.env['asr.report.stock.ledger']
        if location_level:
            level_qty = {day: Ledger._level_qty_at(company, locations, product_ids, day) for day in points}
            candidates = set(issues) | {pid for pid, q in level_qty[date_to].items() if q} \
                | {pid for pid, q in level_qty[opening_day].items() if q}
        else:
            self.env.cr.execute(SQL("""
                SELECT DISTINCT ON (product_id) product_id, closing_qty
                  FROM asr_stock_value_daily
                 WHERE company_id = %s AND day <= %s AND %s
                 ORDER BY product_id, day DESC
            """, company.id, date_to, self._product_sql(product_ids, 'asr_stock_value_daily')))
            candidates = set(issues) | {pid for pid, q in self.env.cr.fetchall() if q}
            level_qty = {}
        if product_ids is not None:
            candidates &= set(product_ids)
        if not candidates:
            return []
        products = self.env['product.product'].with_context(active_test=False).browse(sorted(candidates))
        products.fetch(['default_code', 'categ_id', 'uom_id', 'standard_price', 'display_name'])
        products = products.sorted(key=lambda p: (p.default_code or '', p.display_name, p.id))
        closings = self._company_closings(company, products.ids, points)
        ValueDaily = self.env['asr.stock.value.daily'].sudo()

        rows = []
        for product in products:
            uom = product.uom_id
            std_price = product.with_company(company).standard_price
            values = []
            qty_at = {}
            for day in points:
                c_qty, c_value = closings[day].get(product.id, (0.0, 0.0))
                if location_level:
                    qty = uom.round(level_qty[day].get(product.id, 0.0))
                    values.append(ValueDaily._location_value(c_value, c_qty, qty, std_price))
                else:
                    qty = uom.round(c_qty)
                    values.append(c_value)
                qty_at[day] = qty
            q_open = qty_at[opening_day]
            q_close = qty_at[date_to]
            issue_qty, issue_value = issues.get(product.id, (0.0, 0.0))
            if self.hide_empty and uom.is_zero(issue_qty) and uom.is_zero(q_open) and uom.is_zero(q_close):
                continue
            avg_inventory = sum(values) / len(values) if values else 0.0
            avg_daily = cover_issues.get(product.id, (0.0, 0.0))[0] / cover_days if cover_days else 0.0
            rows.append({
                'product_id': product, 'default_code': product.default_code or '',
                'categ_id': product.categ_id, 'uom_id': uom,
                'qty_opening': q_open, 'qty_issue': issue_qty, 'value_issue': issue_value,
                'value_avg_inventory': avg_inventory,
                'turnover': (issue_value / avg_inventory) if avg_inventory else None,
                'qty_on_hand': q_close, 'avg_daily_issue': avg_daily,
                'days_of_cover': (q_close / avg_daily) if avg_daily and q_close > 0 else None,
            })
        return rows


class ReportTurnoverLine(models.TransientModel):
    _name = 'asr.report.turnover.line'
    _description = 'Turnover / Days of Cover Line'
    _order = 'product_id, id'

    wizard_id = fields.Many2one('asr.report.turnover', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='wizard_id.currency_id')
    product_id = fields.Many2one('product.product', readonly=True)
    default_code = fields.Char('Internal Reference', readonly=True)
    categ_id = fields.Many2one('product.category', 'Category', readonly=True)
    uom_id = fields.Many2one('uom.uom', 'Unit', readonly=True)
    qty_opening = fields.Float('Opening Qty', digits='Product Unit of Measure', readonly=True)
    qty_issue = fields.Float('Issue Qty', digits='Product Unit of Measure', readonly=True)
    value_issue = fields.Monetary('Issue Value at Cost', currency_field='currency_id', readonly=True,
                                  groups='ebshel_stock_reports.group_see_values')
    value_avg_inventory = fields.Monetary('Average Inventory Value', currency_field='currency_id', readonly=True,
                                          groups='ebshel_stock_reports.group_see_values')
    turnover = fields.Float(digits=(16, 2), readonly=True, aggregator='avg',
                            groups='ebshel_stock_reports.group_see_values')
    qty_on_hand = fields.Float('On Hand', digits='Product Unit of Measure', readonly=True)
    avg_daily_issue = fields.Float(digits='Product Unit of Measure', readonly=True, aggregator=None)
    days_of_cover = fields.Float('Days of Cover', digits=(16, 1), readonly=True, aggregator='avg')
