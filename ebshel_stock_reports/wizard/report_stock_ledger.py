# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
"""Report #1 - Stock ledger and stock card.

Ledger mode: one row per product with the opening quantity, every movement
type of the period, the closing quantity, and the values. Card mode: one row
per product and day with a running balance. Moves mode: live from the move
lines, one row per line with a running balance.

Values follow DESIGN.md §2.1: receipts and issues at move values, transfers at
quantity only, the closing value of a location set by Odoo's ratio method,
and a "valuation adjustment" column (closing - opening - in + out) so every
level foots to Odoo's closing value instead of hiding the gap.
"""
from collections import defaultdict
from datetime import timedelta

from odoo import fields, models
from odoo.tools import SQL

from .report_mixin import col

QTY_COLUMNS = [
    ('receipt', 'qty_receipt', 'Receipts'),
    ('customer_return', 'qty_customer_return', 'Customer Returns'),
    ('production', 'qty_production', 'Production'),
    ('count_gain', 'qty_count_gain', 'Count Gains'),
    ('transfer_in', 'qty_transfer_in', 'Transfers In'),
    ('other_in', 'qty_other_in', 'Other In'),
    ('delivery', 'qty_delivery', 'Deliveries'),
    ('vendor_return', 'qty_vendor_return', 'Vendor Returns'),
    ('consumption', 'qty_consumption', 'Consumption'),
    ('scrap', 'qty_scrap', 'Scrap'),
    ('count_loss', 'qty_count_loss', 'Count Losses'),
    ('transfer_out', 'qty_transfer_out', 'Transfers Out'),
    ('other_out', 'qty_other_out', 'Other Out'),
]
IN_KEYS = {t: k for t, k, _l in QTY_COLUMNS[:6]}
OUT_KEYS = {t: k for t, k, _l in QTY_COLUMNS[6:]}
SUMMARY_TYPES = tuple(IN_KEYS) + tuple(OUT_KEYS)


class ReportStockLedger(models.TransientModel):
    _name = 'asr.report.stock.ledger'
    _inherit = 'asr.report.mixin'
    _description = 'Stock Ledger / Stock Card'
    _asr_title = 'Stock Ledger'
    _asr_line_model = 'asr.report.stock.ledger.line'

    mode = fields.Selection([
        ('ledger', 'Ledger: one row per product'),
        ('card', 'Stock card: one row per product and day'),
        ('moves', 'Stock card: one row per move line (live)'),
    ], default='ledger', required=True)
    hide_empty = fields.Boolean('Hide products without movement', default=True)
    line_ids = fields.One2many('asr.report.stock.ledger.line', 'wizard_id')

    # ------------------------------------------------------------------
    # Columns
    # ------------------------------------------------------------------
    def _asr_columns(self):
        columns = [
            col('product_id', 'Product', 'many2one', width=34),
            col('default_code', 'Internal Reference', width=14),
            col('categ_id', 'Category', 'many2one', width=20),
            col('uom_id', 'Unit', 'many2one', width=8),
        ]
        if self.mode != 'ledger':
            columns.append(col('day', 'Date', 'date'))
        if self.mode == 'moves':
            columns += [
                col('datetime', 'Time', 'datetime'),
                col('reference', 'Reference', width=16),
                col('partner_id', 'Partner', 'many2one', width=20),
                col('location_from_id', 'From', 'many2one', width=18),
                col('location_to_id', 'To', 'many2one', width=18),
                col('move_type', 'Type'),
                col('qty_in', 'In', 'qty', total=True),
                col('qty_out', 'Out', 'qty', total=True),
                col('qty_closing', 'Balance', 'qty'),
                col('value_in', 'Value In', 'monetary', value=True, total=True),
                col('value_out', 'Value Out', 'monetary', value=True, total=True),
                col('unit_cost', 'Unit Value', 'monetary', value=True),
            ]
            return columns
        columns.append(col('qty_opening', 'Opening Qty', 'qty'))
        for _mtype, key, label in QTY_COLUMNS[:6]:
            columns.append(col(key, label, 'qty', total=True))
        columns.append(col('qty_in', 'Total In', 'qty', total=True))
        for _mtype, key, label in QTY_COLUMNS[6:]:
            columns.append(col(key, label, 'qty', total=True))
        columns += [
            col('qty_out', 'Total Out', 'qty', total=True),
            col('qty_closing', 'Closing Qty', 'qty'),
            col('value_opening', 'Opening Value', 'monetary', value=True, total=True),
            col('value_in', 'Value In', 'monetary', value=True, total=True),
            col('value_out', 'Value Out', 'monetary', value=True, total=True),
            col('value_adjustment', 'Valuation Adjustment', 'monetary', value=True, total=True,
                help_text="Closing - opening - in + out: standard price changes, AVCO drift, later "
                          "adjustments of incoming values, transfers at value for a location set."),
            col('value_closing', 'Closing Value', 'monetary', value=True, total=True),
            col('unit_cost', 'Unit Cost', 'monetary', value=True),
        ]
        return columns

    def _asr_view_context(self):
        return {'search_default_group_product': 1} if self.mode == 'card' else {}

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------
    def _asr_rows(self):
        self.ensure_one()
        if self.mode == 'moves':
            return self._rows_moves()
        return self._rows_summary()

    def _period(self):
        date_to = self.date_to or fields.Date.context_today(self)
        date_from = self.date_from or date_to.replace(day=1)
        return date_from, date_to

    def _location_sql(self, locations, alias='d'):
        if not locations:
            return SQL("TRUE")
        return SQL("%s.location_id IN %s", SQL.identifier(alias), tuple(locations.ids))

    def _product_sql(self, product_ids, alias='d'):
        if product_ids is None:
            return SQL("TRUE")
        if not product_ids:
            return SQL("FALSE")
        return SQL("%s.product_id IN %s", SQL.identifier(alias), tuple(product_ids))

    def _filtered_product_ids(self):
        if self.product_ids or self.categ_ids:
            return self._asr_products().ids
        return None

    def _level_qty_at(self, company, locations, product_ids, day):
        """Closing quantity per product of a location set at the end of ``day``:
        current quants in the set minus the net movements of the set after that day."""
        self.env.cr.execute(SQL("""
            SELECT q.product_id, SUM(q.quantity)
              FROM stock_quant q
              JOIN stock_location l ON l.id = q.location_id
             WHERE l.company_id = %s AND l.usage IN ('internal', 'transit')
               AND (q.owner_id IS NULL OR q.owner_id = %s)
               AND %s AND %s
             GROUP BY q.product_id
        """, company.id, company.partner_id.id,
             self._location_sql(locations, 'q'), self._product_sql(product_ids, 'q')))
        qty = defaultdict(float, {pid: float(q or 0) for pid, q in self.env.cr.fetchall()})
        self.env.cr.execute(SQL("""
            SELECT d.product_id, SUM(d.qty_in) - SUM(d.qty_out)
              FROM asr_stock_move_daily d
             WHERE d.company_id = %s AND d.day > %s AND d.move_type IN %s
               AND %s AND %s
             GROUP BY d.product_id
        """, company.id, day, SUMMARY_TYPES, self._location_sql(locations), self._product_sql(product_ids)))
        for pid, net in self.env.cr.fetchall():
            qty[pid] -= float(net or 0)
        return qty

    def _rows_summary(self):
        company = self.company_id.sudo()
        date_from, date_to = self._period()
        day_before = date_from - timedelta(days=1)
        locations = self._asr_locations()
        location_level = bool(self.location_ids or self.warehouse_ids)
        product_ids = self._filtered_product_ids()
        per_day = self.mode == 'card'

        # Movements of the period
        day_sql = SQL("d.day") if per_day else SQL("NULL::date")
        self.env.cr.execute(SQL("""
            SELECT d.product_id, %s AS day, d.move_type,
                   SUM(d.qty_in), SUM(d.qty_out), SUM(d.value_in), SUM(d.value_out)
              FROM asr_stock_move_daily d
             WHERE d.company_id = %s AND d.day >= %s AND d.day <= %s AND d.move_type IN %s
               AND %s AND %s
             GROUP BY d.product_id, 2, d.move_type
             ORDER BY d.product_id, 2
        """, day_sql, company.id, date_from, date_to, SUMMARY_TYPES,
             self._location_sql(locations), self._product_sql(product_ids)))
        movements = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))  # product -> day -> key -> qty
        for pid, day, mtype, qty_in, qty_out, value_in, value_out in self.env.cr.fetchall():
            bucket = movements[pid][day]
            if mtype in IN_KEYS:
                bucket[IN_KEYS[mtype]] += float(qty_in or 0)
                bucket['qty_in'] += float(qty_in or 0)
                bucket['value_in'] += float(value_in or 0)
            else:
                bucket[OUT_KEYS[mtype]] += float(qty_out or 0)
                bucket['qty_out'] += float(qty_out or 0)
                bucket['value_out'] += float(value_out or 0)

        closing_qty = self._level_qty_at(company, locations if location_level else None, product_ids, date_to)
        candidate_ids = set(movements) | {pid for pid, q in closing_qty.items() if q}
        if product_ids is not None:
            candidate_ids &= set(product_ids)
        if not candidate_ids:
            return []
        products = self.env['product.product'].with_context(active_test=False).browse(sorted(candidate_ids))
        products = products.sorted(key=lambda p: (p.default_code or '', p.display_name, p.id))
        products.fetch(['default_code', 'categ_id', 'uom_id', 'standard_price', 'display_name'])

        ValueDaily = self.env['asr.stock.value.daily'].sudo()
        company_close = ValueDaily._closing_at(company, products.ids, date_to)
        company_open = ValueDaily._closing_at(company, products.ids, day_before)
        if location_level:
            opening_qty = {}
            for pid in products.ids:
                period = movements.get(pid, {})
                net = sum(b['qty_in'] - b['qty_out'] for b in period.values())
                opening_qty[pid] = closing_qty.get(pid, 0.0) - net
        else:
            closing_qty = {pid: company_close.get(pid, (0.0, 0.0, 0.0))[0] for pid in products.ids}
            opening_qty = {pid: company_open.get(pid, (0.0, 0.0, 0.0))[0] for pid in products.ids}

        def level_value(product, company_data, level_qty):
            c_qty, c_value, _c = company_data
            if not location_level:
                return c_value
            return ValueDaily._location_value(c_value, c_qty, level_qty, product.with_company(company).standard_price)

        rows = []
        for product in products:
            uom = product.uom_id
            period = movements.get(product.id, {})
            total = defaultdict(float)
            for bucket in period.values():
                for key, qty in bucket.items():
                    total[key] += qty
            q_open = uom.round(opening_qty.get(product.id, 0.0))
            q_close = uom.round(closing_qty.get(product.id, 0.0))
            if self.hide_empty and not period and uom.is_zero(q_open) and uom.is_zero(q_close):
                continue
            v_open = level_value(product, company_open.get(product.id, (0.0, 0.0, 0.0)), q_open)
            v_close = level_value(product, company_close.get(product.id, (0.0, 0.0, 0.0)), q_close)
            base = {
                'product_id': product, 'default_code': product.default_code or '',
                'categ_id': product.categ_id, 'uom_id': uom,
            }
            if not per_day:
                rows.append(dict(base, qty_opening=q_open, qty_closing=q_close, value_opening=v_open,
                                 value_closing=v_close,
                                 value_adjustment=v_close - v_open - total['value_in'] + total['value_out'],
                                 unit_cost=(v_close / q_close) if q_close else 0.0,
                                 **{k: total.get(k, 0.0) for _t, k, _l in QTY_COLUMNS},
                                 qty_in=total['qty_in'], qty_out=total['qty_out'],
                                 value_in=total['value_in'], value_out=total['value_out']))
                continue
            rows.append({'_group': product.display_name})
            running_qty = q_open
            running_value = v_open
            days = sorted(period)
            day_close = {}
            if days:
                closings = self._daily_company_closing(company, product.id, days)
                day_close = closings
            for day in days:
                bucket = period[day]
                running_qty = uom.round(running_qty + bucket['qty_in'] - bucket['qty_out'])
                c_qty, c_value = day_close.get(day, (q_close, company_close.get(product.id, (0, 0, 0))[1]))
                if location_level:
                    v_day = ValueDaily._location_value(c_value, c_qty, running_qty,
                                                       product.with_company(company).standard_price)
                else:
                    v_day = c_value
                rows.append(dict(base, day=day, qty_opening=running_qty - bucket['qty_in'] + bucket['qty_out'],
                                 qty_closing=running_qty, value_opening=running_value, value_closing=v_day,
                                 value_adjustment=v_day - running_value - bucket['value_in'] + bucket['value_out'],
                                 unit_cost=(v_day / running_qty) if running_qty else 0.0,
                                 **{k: bucket.get(k, 0.0) for _t, k, _l in QTY_COLUMNS},
                                 qty_in=bucket['qty_in'], qty_out=bucket['qty_out'],
                                 value_in=bucket['value_in'], value_out=bucket['value_out']))
                running_value = v_day
        return rows

    def _daily_company_closing(self, company, product_id, days):
        """{day: (closing_qty, closing_value)} for the given days (last row at or before each day)."""
        self.env.cr.execute(SQL("""
            SELECT day, closing_qty, closing_value FROM asr_stock_value_daily
             WHERE company_id = %s AND product_id = %s AND day <= %s ORDER BY day
        """, company.id, product_id, max(days)))
        history = self.env.cr.fetchall()
        res = {}
        idx = -1
        for day in days:
            while idx + 1 < len(history) and history[idx + 1][0] <= day:
                idx += 1
            res[day] = (float(history[idx][1] or 0), float(history[idx][2] or 0)) if idx >= 0 else (0.0, 0.0)
        return res

    # ------------------------------------------------------------------
    # Moves mode: live from the move lines
    # ------------------------------------------------------------------
    def _rows_moves(self):
        company = self.company_id.sudo()
        date_from, date_to = self._period()
        start = company._asr_day_start_utc(date_from)
        end = company._asr_day_end_utc(date_to)
        locations = self._asr_locations()
        location_level = bool(self.location_ids or self.warehouse_ids)
        product_ids = self._filtered_product_ids()
        valued = self.env['stock.location'].with_context(active_test=False).search(
            [('company_id', '=', company.id), ('usage', 'in', ('internal', 'transit'))])
        level_ids = tuple((locations if location_level else valued).ids) or (0,)
        opening = self._level_qty_at(company, locations if location_level else None, product_ids,
                                     date_from - timedelta(days=1))
        self.env.cr.execute(SQL("""
            SELECT sml.id, sm.id, sm.product_id, sm.date, sm.reference, sm.partner_id,
                   sml.location_id, sml.location_dest_id,
                   sml.quantity_product_uom, sm.value, sm.is_in, sm.is_out, sm.scrap_id, sm.is_inventory,
                   ls.usage, ld.usage,
                   (sml.location_id IN %(level)s) AS from_level,
                   (sml.location_dest_id IN %(level)s) AS to_level,
                   SUM(CASE WHEN (sml2.owner_id IS NULL OR sml2.owner_id = %(partner)s)
                            THEN sml2.quantity_product_uom ELSE 0 END) AS move_qty
              FROM stock_move_line sml
              JOIN stock_move sm ON sm.id = sml.move_id
              JOIN stock_location ls ON ls.id = sml.location_id
              JOIN stock_location ld ON ld.id = sml.location_dest_id
              LEFT JOIN stock_move_line sml2 ON sml2.move_id = sm.id
             WHERE sm.state = 'done' AND sm.company_id = %(company)s
               AND sm.date >= %(start)s AND sm.date <= %(end)s
               AND (sml.owner_id IS NULL OR sml.owner_id = %(partner)s)
               AND COALESCE(sml.picked, TRUE)
               AND (sml.location_id IN %(level)s) <> (sml.location_dest_id IN %(level)s)
               AND %(products)s
             GROUP BY sml.id, sm.id, ls.usage, ld.usage
             ORDER BY sm.product_id, sm.date, sm.id, sml.id
        """, level=level_ids, partner=company.partner_id.id, company=company.id, start=start, end=end,
             products=self._product_sql(product_ids, 'sm')))
        data = self.env.cr.fetchall()
        if not data:
            return []
        product_ids_seen = sorted({r[2] for r in data})
        Product = self.env['product.product'].with_context(active_test=False)
        products = {p.id: p for p in Product.browse(product_ids_seen)}
        partners = {p.id: p for p in self.env['res.partner'].browse([r[5] for r in data if r[5]])}
        Location = self.env['stock.location'].with_context(active_test=False)
        location_objs = {loc.id: loc for loc in Location.browse(list({r[6] for r in data} | {r[7] for r in data}))}
        rows = []
        current = None
        balance = 0.0
        for (_sml_id, _sm_id, pid, dt, reference, partner_id, src, dst, qty, value, is_in, is_out, scrap_id,
             is_inventory, src_usage, dst_usage, from_level, to_level, move_qty) in data:
            product = products[pid]
            if current != pid:
                current = pid
                balance = opening.get(pid, 0.0)
                rows.append({'_group': product.display_name})
            qty = float(qty or 0)
            share = (float(value or 0) * qty / float(move_qty)) if move_qty else 0.0
            if to_level and not from_level:
                qty_in, qty_out = qty, 0.0
                value_in = share if is_in else 0.0
                value_out = 0.0
                mtype = ('count_gain' if is_inventory else 'receipt' if src_usage == 'supplier'
                         else 'customer_return' if src_usage == 'customer'
                         else 'production' if src_usage == 'production'
                         else 'transfer_in' if src in valued.ids else 'other_in')
            else:
                qty_in, qty_out = 0.0, qty
                value_in = 0.0
                value_out = share if is_out else 0.0
                mtype = ('scrap' if scrap_id else 'count_loss' if is_inventory
                         else 'delivery' if dst_usage == 'customer'
                         else 'vendor_return' if dst_usage == 'supplier'
                         else 'consumption' if dst_usage == 'production'
                         else 'transfer_out' if dst in valued.ids else 'other_out')
            balance = product.uom_id.round(balance + qty_in - qty_out)
            if qty_in and value_in:
                unit = value_in / qty_in
            elif qty_out and value_out:
                unit = value_out / qty_out
            else:
                unit = 0.0
            rows.append({
                'product_id': product, 'default_code': product.default_code or '', 'categ_id': product.categ_id,
                'uom_id': product.uom_id, 'day': company._asr_local_day(dt), 'datetime': dt,
                'reference': reference or '',
                'partner_id': partners.get(partner_id), 'location_from_id': location_objs.get(src),
                'location_to_id': location_objs.get(dst), 'move_type': mtype,
                'qty_in': qty_in, 'qty_out': qty_out, 'qty_closing': balance,
                'value_in': value_in, 'value_out': value_out, 'unit_cost': unit,
            })
        return rows


class ReportStockLedgerLine(models.TransientModel):
    _name = 'asr.report.stock.ledger.line'
    _description = 'Stock Ledger Line'
    _order = 'product_id, day, id'

    wizard_id = fields.Many2one('asr.report.stock.ledger', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='wizard_id.currency_id')
    product_id = fields.Many2one('product.product', readonly=True)
    default_code = fields.Char('Internal Reference', readonly=True)
    categ_id = fields.Many2one('product.category', 'Category', readonly=True)
    uom_id = fields.Many2one('uom.uom', 'Unit', readonly=True)
    day = fields.Date('Date', readonly=True)
    datetime = fields.Datetime('Time', readonly=True)
    reference = fields.Char(readonly=True)
    partner_id = fields.Many2one('res.partner', readonly=True)
    location_from_id = fields.Many2one('stock.location', 'From', readonly=True)
    location_to_id = fields.Many2one('stock.location', 'To', readonly=True)
    move_type = fields.Char('Type', readonly=True)
    qty_opening = fields.Float('Opening Qty', digits='Product Unit of Measure', readonly=True, aggregator=None)
    qty_receipt = fields.Float('Receipts', digits='Product Unit of Measure', readonly=True)
    qty_customer_return = fields.Float('Customer Returns', digits='Product Unit of Measure', readonly=True)
    qty_production = fields.Float('Production', digits='Product Unit of Measure', readonly=True)
    qty_count_gain = fields.Float('Count Gains', digits='Product Unit of Measure', readonly=True)
    qty_transfer_in = fields.Float('Transfers In', digits='Product Unit of Measure', readonly=True)
    qty_other_in = fields.Float('Other In', digits='Product Unit of Measure', readonly=True)
    qty_in = fields.Float('Total In', digits='Product Unit of Measure', readonly=True)
    qty_delivery = fields.Float('Deliveries', digits='Product Unit of Measure', readonly=True)
    qty_vendor_return = fields.Float('Vendor Returns', digits='Product Unit of Measure', readonly=True)
    qty_consumption = fields.Float('Consumption', digits='Product Unit of Measure', readonly=True)
    qty_scrap = fields.Float('Scrap', digits='Product Unit of Measure', readonly=True)
    qty_count_loss = fields.Float('Count Losses', digits='Product Unit of Measure', readonly=True)
    qty_transfer_out = fields.Float('Transfers Out', digits='Product Unit of Measure', readonly=True)
    qty_other_out = fields.Float('Other Out', digits='Product Unit of Measure', readonly=True)
    qty_out = fields.Float('Total Out', digits='Product Unit of Measure', readonly=True)
    qty_closing = fields.Float('Closing Qty', digits='Product Unit of Measure', readonly=True, aggregator=None)
    value_opening = fields.Monetary('Opening Value', currency_field='currency_id', readonly=True,
                                    groups='ebshel_stock_reports.group_see_values')
    value_in = fields.Monetary(currency_field='currency_id', readonly=True,
                               groups='ebshel_stock_reports.group_see_values')
    value_out = fields.Monetary(currency_field='currency_id', readonly=True,
                                groups='ebshel_stock_reports.group_see_values')
    value_adjustment = fields.Monetary(currency_field='currency_id', readonly=True,
                                       groups='ebshel_stock_reports.group_see_values')
    value_closing = fields.Monetary(currency_field='currency_id', readonly=True,
                                    groups='ebshel_stock_reports.group_see_values')
    unit_cost = fields.Monetary(currency_field='currency_id', readonly=True, aggregator=None,
                                groups='ebshel_stock_reports.group_see_values')
