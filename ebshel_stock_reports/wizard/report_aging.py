# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
"""Report #4 - Inventory aging.

Valuation mode mirrors Odoo's remaining stack (DESIGN.md §1.5 and §2.3): for
every product the incoming valued moves are walked newest first until the
quantity at the report date is covered, pro rata on the oldest move. FIFO
products value each slice at ``move.value x share``, AVCO and standard
products at ``quantity x standard_price`` (``stock.move.remaining_value``).
Each slice is bucketed by the age of its incoming move. Today the report
foots to ``product._get_remaining_moves()`` and to the Stock report total; at
a past date it is the same walk with ``date <= D`` and the closing quantity
of that day.

Physical mode uses the quants of the selected locations and their
``in_date``, valued at the day's average cost from the valuation summary.
"""
from collections import defaultdict
from datetime import datetime

from odoo import api, fields, models
from odoo.tools import SQL

from .report_mixin import col

BUCKET_SLOTS = 8  # the line model carries this many bucket columns; extra bounds fold into the last one


class ReportAging(models.TransientModel):
    _name = 'asr.report.aging'
    _inherit = 'asr.report.mixin'
    _description = 'Inventory Aging'
    _asr_title = 'Inventory Aging'
    _asr_line_model = 'asr.report.aging.line'

    mode = fields.Selection([
        ('valuation', 'Valuation: remaining incoming moves (foots to the Stock report)'),
        ('physical', 'Physical: quants and their incoming date'),
    ], default='valuation', required=True)
    date_from = fields.Date(default=False)  # the aging has one report date: date_to
    bucket_info = fields.Char(compute='_compute_bucket_info')
    line_ids = fields.One2many('asr.report.aging.line', 'wizard_id')

    @api.depends('company_id')
    def _compute_bucket_info(self):
        for wizard in self:
            labels = wizard._bucket_labels() if wizard.company_id else []
            wizard.bucket_info = ', '.join(labels)

    # ------------------------------------------------------------------
    # Buckets
    # ------------------------------------------------------------------
    def _bucket_bounds(self):
        """Upper bounds in days, e.g. [30, 60, 90, 180]; at most BUCKET_SLOTS - 1 of them."""
        bounds = self.company_id.sudo()._asr_parse_list('asr_aging_buckets', int)
        return bounds[:BUCKET_SLOTS - 1]

    def _bucket_labels(self):
        bounds = self._bucket_bounds()
        labels = []
        low = 0
        for bound in bounds:
            labels.append(f"{low}-{bound}")
            low = bound + 1
        labels.append(f">{bounds[-1]}" if bounds else self.env._("All"))
        return labels

    @staticmethod
    def _bucket_index(age_days, bounds):
        for index, bound in enumerate(bounds):
            if age_days <= bound:
                return index
        return len(bounds)

    # ------------------------------------------------------------------
    # Columns
    # ------------------------------------------------------------------
    def _asr_columns(self):
        labels = self._bucket_labels()
        columns = [
            col('product_id', 'Product', 'many2one', width=34),
            col('default_code', 'Internal Reference', width=14),
            col('categ_id', 'Category', 'many2one', width=20),
            col('uom_id', 'Unit', 'many2one', width=8),
        ]
        for index, label in enumerate(labels):
            columns.append(col(f'qty_b{index + 1}', self.env._("Qty %s days", label), 'qty', total=True))
        columns.append(col('qty_total', 'Total Qty', 'qty', total=True))
        for index, label in enumerate(labels):
            columns.append(col(f'value_b{index + 1}', self.env._("Value %s days", label), 'monetary',
                               value=True, total=True))
        columns += [
            col('value_total', 'Total Value', 'monetary', value=True, total=True),
            col('oldest_date', 'Oldest Date', 'date'),
        ]
        return columns

    def _asr_filter_text(self):
        text = super()._asr_filter_text()
        buckets = self.env._("Buckets (days): %s", ', '.join(self._bucket_labels()))
        return f"{text} | {buckets}" if text else buckets

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------
    def _report_day(self):
        today = fields.Date.context_today(self)
        day = self.date_to or today
        return min(day, today), day >= today

    def _filtered_product_ids(self):
        if self.product_ids or self.categ_ids:
            return self._asr_products().ids
        return None

    def _product_sql(self, product_ids, alias):
        if product_ids is None:
            return SQL("TRUE")
        if not product_ids:
            return SQL("FALSE")
        return SQL("%s.product_id IN %s", SQL.identifier(alias), tuple(product_ids))

    def _asr_rows(self):
        self.ensure_one()
        self.env.flush_all()
        if self.mode == 'physical':
            return self._rows_physical()
        return self._rows_valuation()

    def _target_quantities(self, company, day, is_today, locations, location_level, product_ids):
        """{product_id: quantity to cover} at the report date, company level or location level."""
        if location_level:
            qty = self.env['asr.report.stock.ledger']._level_qty_at(company, locations, product_ids, day)
            return {pid: q for pid, q in qty.items() if q}
        if is_today:
            self.env.cr.execute(SQL("""
                SELECT q.product_id, SUM(q.quantity)
                  FROM stock_quant q
                  JOIN stock_location l ON l.id = q.location_id
                 WHERE l.company_id = %s AND l.usage IN ('internal', 'transit')
                   AND (q.owner_id IS NULL OR q.owner_id = %s) AND %s
                 GROUP BY q.product_id
            """, company.id, company.partner_id.id, self._product_sql(product_ids, 'q')))
            return {pid: float(q) for pid, q in self.env.cr.fetchall() if q}
        self.env.cr.execute(SQL("""
            SELECT DISTINCT ON (product_id) product_id, closing_qty
              FROM asr_stock_value_daily
             WHERE company_id = %s AND day <= %s AND %s
             ORDER BY product_id, day DESC
        """, company.id, day, self._product_sql(product_ids, 'asr_stock_value_daily')))
        return {pid: float(q) for pid, q in self.env.cr.fetchall() if q}

    def _stack_moves(self, company, targets, instant):
        """Newest incoming valued moves per product until the target quantity is covered.

        Returns {product_id: [(move_id, date, value, in_qty, before_qty), ...]} newest first,
        exactly the moves ``_run_fifo_get_stack`` would return.
        """
        if not targets:
            return {}
        values = SQL(', ').join(SQL('(%s, %s::numeric)', pid, qty) for pid, qty in targets.items())
        date_clause = SQL("AND sm.date <= %s", instant) if instant else SQL("")
        self.env.cr.execute(SQL("""
            WITH targets(product_id, target) AS (VALUES %(targets)s),
            moves AS (
                SELECT sm.id, sm.product_id, sm.date, COALESCE(sm.value, 0) AS value,
                       COALESCE(SUM(lines.q), 0) AS in_qty
                  FROM stock_move sm
                  JOIN targets t ON t.product_id = sm.product_id
                  LEFT JOIN LATERAL (
                       SELECT sml.quantity_product_uom AS q
                         FROM stock_move_line sml
                         JOIN stock_location ls ON ls.id = sml.location_id
                         JOIN stock_location ld ON ld.id = sml.location_dest_id
                        WHERE sml.move_id = sm.id
                          AND COALESCE(sml.picked, TRUE)
                          AND (sml.owner_id IS NULL OR sml.owner_id = %(partner)s)
                          AND NOT (ls.company_id IS NOT NULL AND ls.usage IN ('internal', 'transit'))
                          AND (ld.company_id IS NOT NULL AND ld.usage IN ('internal', 'transit'))
                  ) lines ON TRUE
                 WHERE sm.state = 'done' AND sm.is_in AND sm.company_id = %(company)s %(date_clause)s
                 GROUP BY sm.id
            ), ranked AS (
                SELECT m.*,
                       COALESCE(SUM(m.in_qty) OVER (PARTITION BY m.product_id ORDER BY m.date DESC, m.id DESC
                                                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) AS before_qty
                  FROM moves m
            )
            SELECT r.id, r.product_id, r.date, r.value, r.in_qty, r.before_qty
              FROM ranked r
              JOIN targets t ON t.product_id = r.product_id
             WHERE r.before_qty < t.target
             ORDER BY r.product_id, r.date DESC, r.id DESC
        """, targets=values, partner=company.partner_id.id, company=company.id, date_clause=date_clause))
        stacks = defaultdict(list)
        for move_id, pid, dt, value, in_qty, before_qty in self.env.cr.fetchall():
            stacks[pid].append((move_id, dt, float(value or 0), float(in_qty or 0), float(before_qty or 0)))
        return stacks

    def _rows_valuation(self):
        company = self.company_id.sudo()
        day, is_today = self._report_day()
        locations = self._asr_locations()
        location_level = bool(self.location_ids or self.warehouse_ids)
        product_ids = self._filtered_product_ids()
        bounds = self._bucket_bounds()
        n_buckets = len(bounds) + 1

        targets = self._target_quantities(company, day, is_today, locations, location_level, product_ids)
        products = self.env['product.product'].with_context(active_test=False).browse(list(targets))
        products.fetch(['default_code', 'categ_id', 'uom_id', 'standard_price', 'display_name', 'cost_method'])
        targets = {p.id: targets[p.id] for p in products if p.uom_id.compare(targets[p.id], 0) > 0}
        if not targets:
            return []
        instant = None if is_today else company._asr_day_end_utc(day)
        stacks = self._stack_moves(company, targets, instant)
        products = products.filtered(lambda p: p.id in targets).sorted(
            key=lambda p: (p.default_code or '', p.display_name, p.id))

        rows = []
        for product in products:
            product = product.with_company(company)
            uom = product.uom_id
            target = targets[product.id]
            fifo = product.cost_method == 'fifo'
            std_price = product.standard_price
            qty_b = [0.0] * n_buckets
            value_b = [0.0] * n_buckets
            oldest = None
            covered = 0.0
            last_unit = None
            for _move_id, dt, value, in_qty, before_qty in stacks.get(product.id, []):
                take = min(in_qty, target - before_qty)
                if uom.compare(take, 0) <= 0:
                    continue
                covered += take
                unit = (value / in_qty) if in_qty else 0.0
                last_unit = unit
                slice_value = value * take / in_qty if (fifo and in_qty) else take * std_price
                age = (day - company._asr_local_day(dt)).days
                index = self._bucket_index(max(age, 0), bounds)
                qty_b[index] += take
                value_b[index] += slice_value
                local_day = company._asr_local_day(dt)
                oldest = local_day if oldest is None or local_day < oldest else oldest
            leftover = uom.round(target - covered)
            if uom.compare(leftover, 0) > 0:
                # the stack ran out (stock without incoming moves): Odoo extrapolates at the last price
                price = last_unit if (fifo and last_unit is not None) else std_price
                qty_b[-1] += leftover
                value_b[-1] += leftover * price
            row = {
                'product_id': product, 'default_code': product.default_code or '',
                'categ_id': product.categ_id, 'uom_id': uom,
                'qty_total': sum(qty_b), 'value_total': sum(value_b), 'oldest_date': oldest,
            }
            for index in range(BUCKET_SLOTS):
                row[f'qty_b{index + 1}'] = qty_b[index] if index < n_buckets else 0.0
                row[f'value_b{index + 1}'] = value_b[index] if index < n_buckets else 0.0
            rows.append(row)
        return rows

    def _rows_physical(self):
        company = self.company_id.sudo()
        day, _is_today = self._report_day()
        locations = self._asr_locations()
        product_ids = self._filtered_product_ids()
        bounds = self._bucket_bounds()
        n_buckets = len(bounds) + 1
        location_sql = SQL("q.location_id IN %s", tuple(locations.ids)) if locations else SQL("TRUE")
        self.env.cr.execute(SQL("""
            SELECT q.product_id, q.in_date::date, SUM(q.quantity)
              FROM stock_quant q
              JOIN stock_location l ON l.id = q.location_id
             WHERE l.company_id = %s AND l.usage IN ('internal', 'transit')
               AND (q.owner_id IS NULL OR q.owner_id = %s)
               AND %s AND %s
             GROUP BY q.product_id, q.in_date::date
             ORDER BY q.product_id, q.in_date::date
        """, company.id, company.partner_id.id, location_sql, self._product_sql(product_ids, 'q')))
        data = self.env.cr.fetchall()
        if not data:
            return []
        per_product = defaultdict(list)
        for pid, in_day, qty in data:
            per_product[pid].append((in_day, float(qty or 0)))
        products = self.env['product.product'].with_context(active_test=False).browse(list(per_product))
        products.fetch(['default_code', 'categ_id', 'uom_id', 'standard_price', 'display_name'])
        products = products.sorted(key=lambda p: (p.default_code or '', p.display_name, p.id))
        closing = self.env['asr.stock.value.daily'].sudo()._closing_at(company, products.ids, day)
        rows = []
        for product in products:
            uom = product.uom_id
            c_qty, _c_value, avg_cost = closing.get(product.id, (0.0, 0.0, 0.0))
            if not c_qty and not avg_cost:
                avg_cost = product.with_company(company).standard_price
            qty_b = [0.0] * n_buckets
            value_b = [0.0] * n_buckets
            oldest = None
            total = 0.0
            for in_day, qty in per_product[product.id]:
                if uom.is_zero(qty):
                    continue
                if isinstance(in_day, datetime):
                    in_day = in_day.date()
                age = (day - in_day).days if in_day else 0
                index = self._bucket_index(max(age, 0), bounds)
                qty_b[index] += qty
                value_b[index] += qty * avg_cost
                total += qty
                if in_day and (oldest is None or in_day < oldest):
                    oldest = in_day
            if uom.is_zero(total):
                continue
            row = {
                'product_id': product, 'default_code': product.default_code or '',
                'categ_id': product.categ_id, 'uom_id': uom,
                'qty_total': total, 'value_total': sum(value_b), 'oldest_date': oldest,
            }
            for index in range(BUCKET_SLOTS):
                row[f'qty_b{index + 1}'] = qty_b[index] if index < n_buckets else 0.0
                row[f'value_b{index + 1}'] = value_b[index] if index < n_buckets else 0.0
            rows.append(row)
        return rows


class ReportAgingLine(models.TransientModel):
    _name = 'asr.report.aging.line'
    _description = 'Inventory Aging Line'
    _order = 'product_id, id'

    wizard_id = fields.Many2one('asr.report.aging', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='wizard_id.currency_id')
    product_id = fields.Many2one('product.product', readonly=True)
    default_code = fields.Char('Internal Reference', readonly=True)
    categ_id = fields.Many2one('product.category', 'Category', readonly=True)
    uom_id = fields.Many2one('uom.uom', 'Unit', readonly=True)
    qty_b1 = fields.Float('Qty Bucket 1', digits='Product Unit of Measure', readonly=True)
    qty_b2 = fields.Float('Qty Bucket 2', digits='Product Unit of Measure', readonly=True)
    qty_b3 = fields.Float('Qty Bucket 3', digits='Product Unit of Measure', readonly=True)
    qty_b4 = fields.Float('Qty Bucket 4', digits='Product Unit of Measure', readonly=True)
    qty_b5 = fields.Float('Qty Bucket 5', digits='Product Unit of Measure', readonly=True)
    qty_b6 = fields.Float('Qty Bucket 6', digits='Product Unit of Measure', readonly=True)
    qty_b7 = fields.Float('Qty Bucket 7', digits='Product Unit of Measure', readonly=True)
    qty_b8 = fields.Float('Qty Bucket 8', digits='Product Unit of Measure', readonly=True)
    qty_total = fields.Float('Total Qty', digits='Product Unit of Measure', readonly=True)
    value_b1 = fields.Monetary('Value Bucket 1', currency_field='currency_id', readonly=True,
                               groups='ebshel_stock_reports.group_see_values')
    value_b2 = fields.Monetary('Value Bucket 2', currency_field='currency_id', readonly=True,
                               groups='ebshel_stock_reports.group_see_values')
    value_b3 = fields.Monetary('Value Bucket 3', currency_field='currency_id', readonly=True,
                               groups='ebshel_stock_reports.group_see_values')
    value_b4 = fields.Monetary('Value Bucket 4', currency_field='currency_id', readonly=True,
                               groups='ebshel_stock_reports.group_see_values')
    value_b5 = fields.Monetary('Value Bucket 5', currency_field='currency_id', readonly=True,
                               groups='ebshel_stock_reports.group_see_values')
    value_b6 = fields.Monetary('Value Bucket 6', currency_field='currency_id', readonly=True,
                               groups='ebshel_stock_reports.group_see_values')
    value_b7 = fields.Monetary('Value Bucket 7', currency_field='currency_id', readonly=True,
                               groups='ebshel_stock_reports.group_see_values')
    value_b8 = fields.Monetary('Value Bucket 8', currency_field='currency_id', readonly=True,
                               groups='ebshel_stock_reports.group_see_values')
    value_total = fields.Monetary('Total Value', currency_field='currency_id', readonly=True,
                                  groups='ebshel_stock_reports.group_see_values')
    oldest_date = fields.Date(readonly=True)
