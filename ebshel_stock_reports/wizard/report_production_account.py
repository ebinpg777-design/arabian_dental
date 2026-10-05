# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
"""Report #20 - Monthly production account (India GST).

For every month of the period, two sections:

* "Finished goods": one row per product finished by the manufacturing orders
  done in the month (by-products as separate, flagged rows): opening stock,
  quantity manufactured (picked finished moves, product UoM), other receipts,
  issued for sale, consumed in manufacturing, wastage, other issues, closing
  stock and the value of production (sum of the finished moves' values).
* "Materials consumed": one row per component of those orders with the same
  stock columns and the consumption value (sum of the raw moves' values).

Opening and closing quantities and values come from the engine (DESIGN.md
§2.1); the movement columns from the daily movement summary, so every row
foots: opening + produced + received - sold - consumed - wastage - other = closing.
"""
from collections import defaultdict
from dateutil.relativedelta import relativedelta

from odoo import fields, models
from odoo.exceptions import UserError
from odoo.tools import SQL

from .report_gst_register import IN_TYPES, engine_summary
from .report_mixin import col

SECTIONS = [('finished', 'Finished Goods'), ('material', 'Materials Consumed')]


class ReportProductionAccount(models.TransientModel):
    _name = 'asr.report.production.account'
    _inherit = 'asr.report.mixin'
    _description = 'Monthly Production Account'
    _asr_title = 'Monthly Production Account'
    _asr_line_model = 'asr.report.production.account.line'
    _asr_default_months = 3

    line_ids = fields.One2many('asr.report.production.account.line', 'wizard_id')

    # ------------------------------------------------------------------
    # GST switch
    # ------------------------------------------------------------------
    def _asr_check(self):
        res = super()._asr_check()
        if not self.company_id.asr_india_gst:
            raise UserError(self.env._("The India GST reports are disabled for %s. Enable them in "
                                       "Inventory > Configuration > Settings > Advanced Stock Reports.",
                                       self.company_id.name))
        return res

    # ------------------------------------------------------------------
    # Columns
    # ------------------------------------------------------------------
    def _asr_columns(self):
        return [
            col('month', 'Month', width=10),
            col('section', 'Section', width=16),
            col('product_id', 'Product', 'many2one', width=34),
            col('default_code', 'Internal Reference', width=14),
            col('uom_id', 'Unit', 'many2one', width=8),
            col('is_byproduct', 'By-product', 'bool'),
            col('mo_count', 'Manufacturing Orders', 'int'),
            col('qty_opening', 'Opening Qty', 'qty'),
            col('qty_produced', 'Manufactured', 'qty', total=True,
                help_text="Picked finished moves of the manufacturing orders done in the month."),
            col('qty_received', 'Other Receipts', 'qty', total=True,
                help_text="Receipts, returns, count gains, transfers in, and production entries "
                          "not coming from an order done in the month (e.g. unbuilds)."),
            col('qty_sold', 'Issued for Sale', 'qty', total=True),
            col('qty_consumed', 'Consumed in Manufacturing', 'qty', total=True),
            col('qty_wastage', 'Wastage / Scrap', 'qty', total=True),
            col('qty_other_out', 'Other Issues', 'qty', total=True),
            col('qty_closing', 'Closing Qty', 'qty'),
            col('value_production', 'Value of Production', 'monetary', value=True, total=True),
            col('value_consumption', 'Consumption Value', 'monetary', value=True, total=True),
            col('value_opening', 'Opening Value', 'monetary', value=True),
            col('value_closing', 'Closing Value', 'monetary', value=True),
        ]

    def _asr_view_context(self):
        return {'search_default_group_month': 1, 'search_default_group_section': 1}

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------
    def _months(self):
        """[(label, first day, last day)] of the calendar months of the period, clipped to it."""
        date_to = self.date_to or fields.Date.context_today(self)
        date_from = self.date_from or date_to.replace(day=1)
        months = []
        start = date_from.replace(day=1)
        while start <= date_to:
            end = start + relativedelta(months=1, days=-1)
            months.append((start.strftime('%Y-%m'), max(start, date_from), min(end, date_to)))
            start += relativedelta(months=1)
        return months

    def _mo_product_sql(self):
        if self.product_ids or self.categ_ids:
            ids = self._asr_products().ids
            return SQL("mp.product_id IN %s", tuple(ids) or (0,))
        return SQL("TRUE")

    def _mo_moves(self, start, end):
        """Finished and raw moves of the orders done in the window.

        Returns ``(finished, materials)``, both {product_id: {'qty', 'value', 'mo_ids'}}; ``finished``
        also carries ``main`` (False when the product only came out as a by-product).
        """
        company = self.company_id.sudo()
        self.env.cr.execute(SQL("""
            SELECT 'finished' AS kind, sm.id, mp.id, sm.product_id,
                   (sm.product_id <> mp.product_id OR sm.byproduct_id IS NOT NULL) AS byproduct,
                   sm.value, SUM(sml.quantity_product_uom)
              FROM mrp_production mp
              JOIN stock_move sm ON sm.production_id = mp.id
              JOIN stock_move_line sml ON sml.move_id = sm.id
             WHERE mp.state = 'done' AND mp.company_id = %(company)s
               AND mp.date_finished >= %(start)s AND mp.date_finished <= %(end)s
               AND sm.state = 'done' AND sm.picked AND COALESCE(sml.picked, TRUE)
               AND %(products)s
             GROUP BY sm.id, mp.id
            UNION ALL
            SELECT 'material', sm.id, mp.id, sm.product_id, FALSE, sm.value, SUM(sml.quantity_product_uom)
              FROM mrp_production mp
              JOIN stock_move sm ON sm.raw_material_production_id = mp.id
              JOIN stock_move_line sml ON sml.move_id = sm.id
             WHERE mp.state = 'done' AND mp.company_id = %(company)s
               AND mp.date_finished >= %(start)s AND mp.date_finished <= %(end)s
               AND sm.state = 'done' AND sm.picked AND COALESCE(sml.picked, TRUE)
               AND %(products)s
             GROUP BY sm.id, mp.id
        """, company=company.id, start=company._asr_day_start_utc(start), end=company._asr_day_end_utc(end),
             products=self._mo_product_sql()))
        finished = defaultdict(lambda: {'qty': 0.0, 'value': 0.0, 'mo_ids': set(), 'main': False})
        materials = defaultdict(lambda: {'qty': 0.0, 'value': 0.0, 'mo_ids': set()})
        for kind, _move_id, mo_id, pid, byproduct, value, qty in self.env.cr.fetchall():
            bucket = finished[pid] if kind == 'finished' else materials[pid]
            bucket['qty'] += float(qty or 0)
            bucket['value'] += float(value or 0)
            bucket['mo_ids'].add(mo_id)
            if kind == 'finished' and not byproduct:
                bucket['main'] = True
        return finished, materials

    def _asr_rows(self):
        self.ensure_one()
        self.env.flush_all()  # the SQL below reads stored computed columns
        rows = []
        Product = self.env['product.product'].with_context(active_test=False)
        empty = {'moves': {}, 'qty_opening': 0.0, 'qty_closing': 0.0, 'value_opening': 0.0, 'value_closing': 0.0}
        for label, start, end in self._months():
            finished, materials = self._mo_moves(start, end)
            if not finished and not materials:
                continue
            product_ids = sorted(set(finished) | set(materials))
            summary = engine_summary(self, start, end, product_ids)
            products = {p.id: p for p in Product.browse(product_ids)}

            def sort_key(pid):
                return (products[pid].default_code or '', products[pid].display_name, pid)

            def stock_row(pid, section, mo_data):
                """Engine columns of the product in the month; the month's production is split out
                of the engine's incoming quantity so the row foots to the closing quantity."""
                product = products[pid]
                data = summary.get(pid, empty)
                m = data['moves']
                produced = finished[pid]['qty'] if pid in finished else 0.0
                return {
                    'month': label, 'month_start': start, 'section': section,
                    'product_id': product, 'default_code': product.default_code or '', 'uom_id': product.uom_id,
                    'is_byproduct': False, 'mo_count': len(mo_data['mo_ids']),
                    'qty_opening': data['qty_opening'],
                    'qty_produced': produced,
                    'qty_received': sum(m.get(t, 0.0) for t in IN_TYPES) - produced,
                    'qty_sold': m.get('delivery', 0.0),
                    'qty_consumed': m.get('consumption', 0.0),
                    'qty_wastage': m.get('scrap', 0.0) + m.get('count_loss', 0.0),
                    'qty_other_out': m.get('vendor_return', 0.0) + m.get('other_out', 0.0) + m.get('transfer_out', 0.0),
                    'qty_closing': data['qty_closing'],
                    'value_production': 0.0, 'value_consumption': 0.0,
                    'value_opening': data['value_opening'],
                    'value_closing': data['value_closing'],
                }

            if finished:
                rows.append({'_group': self.env._("%s - Finished goods", label)})
                for pid in sorted(finished, key=lambda k: (not finished[k]['main'],) + sort_key(k)):
                    row = stock_row(pid, 'finished', finished[pid])
                    row.update(is_byproduct=not finished[pid]['main'], value_production=finished[pid]['value'])
                    rows.append(row)
            if materials:
                rows.append({'_group': self.env._("%s - Materials consumed", label)})
                for pid in sorted(materials, key=sort_key):
                    row = stock_row(pid, 'material', materials[pid])
                    row['value_consumption'] = materials[pid]['value']
                    rows.append(row)
        return rows


class ReportProductionAccountLine(models.TransientModel):
    _name = 'asr.report.production.account.line'
    _description = 'Monthly Production Account Line'
    _order = 'month, section, is_byproduct, default_code, product_id, id'

    wizard_id = fields.Many2one('asr.report.production.account', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='wizard_id.currency_id')
    month = fields.Char(readonly=True)
    month_start = fields.Date(readonly=True)
    section = fields.Selection(SECTIONS, readonly=True)
    product_id = fields.Many2one('product.product', readonly=True)
    default_code = fields.Char('Internal Reference', readonly=True)
    uom_id = fields.Many2one('uom.uom', 'Unit', readonly=True)
    is_byproduct = fields.Boolean('By-product', readonly=True)
    mo_count = fields.Integer('Manufacturing Orders', readonly=True)
    qty_opening = fields.Float('Opening Qty', digits='Product Unit of Measure', readonly=True, aggregator=None)
    qty_produced = fields.Float('Manufactured', digits='Product Unit of Measure', readonly=True)
    qty_received = fields.Float('Other Receipts', digits='Product Unit of Measure', readonly=True)
    qty_sold = fields.Float('Issued for Sale', digits='Product Unit of Measure', readonly=True)
    qty_consumed = fields.Float('Consumed in Manufacturing', digits='Product Unit of Measure', readonly=True)
    qty_wastage = fields.Float('Wastage / Scrap', digits='Product Unit of Measure', readonly=True)
    qty_other_out = fields.Float('Other Issues', digits='Product Unit of Measure', readonly=True)
    qty_closing = fields.Float('Closing Qty', digits='Product Unit of Measure', readonly=True, aggregator=None)
    value_production = fields.Monetary('Value of Production', currency_field='currency_id', readonly=True,
                                       groups='ebshel_stock_reports.group_see_values')
    value_consumption = fields.Monetary('Consumption Value', currency_field='currency_id', readonly=True,
                                        groups='ebshel_stock_reports.group_see_values')
    value_opening = fields.Monetary('Opening Value', currency_field='currency_id', readonly=True,
                                    groups='ebshel_stock_reports.group_see_values', aggregator=None)
    value_closing = fields.Monetary('Closing Value', currency_field='currency_id', readonly=True,
                                    groups='ebshel_stock_reports.group_see_values', aggregator=None)
