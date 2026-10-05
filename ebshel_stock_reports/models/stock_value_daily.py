# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
"""Daily closing quantity and value per product, as Odoo computes them.

For every day on which a product moved or was revalued, one row holds the
company-level closing quantity (anchored on the current quants and walked
back through the daily summary, like ``_compute_quantities_dict``) and the
closing value Odoo's ``product.product.total_value`` returns for the last
instant of that day in the company's reporting timezone:

* standard  - quantity x last manual price (``product.value`` history);
* average   - a replay of every valued move since the last manual price,
              exactly as ``_run_average_batch`` does it;
* fifo      - the newest incoming moves that cover the quantity, pro rata on
              the oldest, exactly as ``_run_fifo`` does it;
* lot valuated products - Odoo's own figure (slow path, flagged).
"""
import logging
from collections import defaultdict
from odoo import fields, models
from odoo.tools import SQL, float_round

from .stock_move_daily import FULL_REBUILD_DAY, LEDGER_IN_TYPES, LEDGER_OUT_TYPES

_logger = logging.getLogger(__name__)


class StockValueDaily(models.Model):
    _name = 'asr.stock.value.daily'
    _description = 'Daily Stock Valuation Summary'
    _order = 'day desc, id desc'
    _rec_name = 'product_id'
    _log_access = False

    company_id = fields.Many2one('res.company', required=True, readonly=True, index=True)
    product_id = fields.Many2one('product.product', required=True, readonly=True, index=True)
    day = fields.Date(required=True, readonly=True)
    closing_qty = fields.Float('Closing Quantity', digits='Product Unit of Measure', readonly=True)
    closing_value = fields.Monetary(currency_field='currency_id', readonly=True,
                                    groups='ebshel_stock_reports.group_see_values')
    avg_cost = fields.Monetary('Unit Cost', currency_field='currency_id', readonly=True,
                               groups='ebshel_stock_reports.group_see_values')
    revaluation_value = fields.Monetary('Revaluation', currency_field='currency_id', readonly=True,
                                        groups='ebshel_stock_reports.group_see_values',
                                        help="Change of value that day caused by standard price updates "
                                             "(no stock move).")
    cost_method = fields.Selection([
        ('standard', 'Standard Price'),
        ('fifo', 'FIFO'),
        ('average', 'AVCO'),
    ], readonly=True)
    replay_qty = fields.Float(readonly=True, help="AVCO replay quantity (not anchored on quants).")
    replay_avg = fields.Float(readonly=True, digits=(16, 8), help="AVCO replay average cost at full precision.")
    exact_slow_path = fields.Boolean(readonly=True, help="Value read from Odoo (lot valuated product).")
    computed_at = fields.Datetime(readonly=True)
    currency_id = fields.Many2one(related='company_id.currency_id')

    _unique_idx = models.UniqueIndex("(company_id, product_id, day)")
    _company_day_idx = models.Index("(company_id, day)")

    # ------------------------------------------------------------------
    # Builder
    # ------------------------------------------------------------------
    def _delete_from(self, company, product_from_day):
        if not product_from_day:
            return
        values = SQL(', ').join(SQL('(%s, %s::date)', pid, day) for pid, day in product_from_day.items())
        self.env.cr.execute(SQL("""
            DELETE FROM asr_stock_value_daily d
                  USING (VALUES %s) AS k(product_id, day_from)
                  WHERE d.company_id = %s
                    AND d.product_id = k.product_id
                    AND (d.day >= k.day_from OR k.day_from <= %s)
        """, values, company.id, FULL_REBUILD_DAY))

    def _rebuild(self, company, product_from_day):
        """Recompute the rows of the given products from their first day on.

        The movement summary must already be up to date for those products.
        """
        if not product_from_day:
            return
        self._delete_from(company, product_from_day)
        products = self.env['product.product'].with_company(company).with_context(active_test=False).browse(
            list(product_from_day))
        products.fetch(['cost_method', 'lot_valuated', 'uom_id', 'standard_price', 'is_storable'])
        current_qty = self._current_valued_qty(company, products.ids)
        nets = self._daily_nets(company, products.ids)
        manual_values = self._manual_values(company, products.ids)
        now = fields.Datetime.now()
        rows = []
        for product in products:
            try:
                rows.extend(self._compute_product(
                    company, product, product_from_day[product.id],
                    current_qty.get(product.id, 0.0), nets.get(product.id, {}),
                    manual_values.get(product.id, []), now))
            except Exception:  # noqa: BLE001 - one product must not block the queue
                _logger.exception("Advanced Stock Reports: valuation replay failed for product %s", product.id)
                raise
        if rows:
            self._insert_rows(rows)

    def _insert_rows(self, rows):
        cols = ('company_id', 'product_id', 'day', 'closing_qty', 'closing_value', 'avg_cost',
                'revaluation_value', 'cost_method', 'replay_qty', 'replay_avg', 'exact_slow_path', 'computed_at')
        for start in range(0, len(rows), 2000):
            chunk = rows[start:start + 2000]
            values = SQL(', ').join(
                SQL('(%s)', SQL(', ').join(SQL('%s', r[c]) for c in cols)) for r in chunk)
            self.env.cr.execute(SQL(
                "INSERT INTO asr_stock_value_daily (%s) VALUES %s "
                "ON CONFLICT (company_id, product_id, day) DO UPDATE SET "
                "closing_qty = EXCLUDED.closing_qty, closing_value = EXCLUDED.closing_value, "
                "avg_cost = EXCLUDED.avg_cost, "
                "revaluation_value = EXCLUDED.revaluation_value, cost_method = EXCLUDED.cost_method, "
                "replay_qty = EXCLUDED.replay_qty, replay_avg = EXCLUDED.replay_avg, "
                "exact_slow_path = EXCLUDED.exact_slow_path, computed_at = EXCLUDED.computed_at",
                SQL(', ').join(SQL.identifier(c) for c in cols), values))

    # ------------------------------------------------------------------
    # Inputs
    # ------------------------------------------------------------------
    def _current_valued_qty(self, company, product_ids):
        """Quantity in the company's valued locations today, owner empty or the company itself."""
        if not product_ids:
            return {}
        self.env['stock.quant'].flush_model(['quantity', 'product_id', 'location_id', 'owner_id', 'company_id'])
        self.env.cr.execute(SQL("""
            SELECT q.product_id, SUM(q.quantity)
              FROM stock_quant q
              JOIN stock_location l ON l.id = q.location_id
             WHERE q.product_id IN %s
               AND l.company_id = %s
               AND l.usage IN ('internal', 'transit')
               AND (q.owner_id IS NULL OR q.owner_id = %s)
             GROUP BY q.product_id
        """, tuple(product_ids), company.id, company.partner_id.id))
        return dict(self.env.cr.fetchall())

    def _daily_nets(self, company, product_ids):
        """{product_id: {day: (net_qty, has_ledger_move)}} from the movement summary."""
        if not product_ids:
            return {}
        self.env.cr.execute(SQL("""
            SELECT product_id, day,
                   SUM(CASE WHEN move_type IN %s THEN qty_in ELSE 0 END)
                 - SUM(CASE WHEN move_type IN %s THEN qty_out ELSE 0 END)
              FROM asr_stock_move_daily
             WHERE company_id = %s AND product_id IN %s AND move_type IN %s
             GROUP BY product_id, day
        """, LEDGER_IN_TYPES, LEDGER_OUT_TYPES, company.id, tuple(product_ids), LEDGER_IN_TYPES + LEDGER_OUT_TYPES))
        res = defaultdict(dict)
        for product_id, day, net in self.env.cr.fetchall():
            res[product_id][day] = float(net or 0.0)
        return res

    def _manual_values(self, company, product_ids):
        """Product-level ``product.value`` rows (standard price history), oldest first."""
        if not product_ids:
            return {}
        self.env['product.value'].flush_model()
        self.env.cr.execute(SQL("""
            SELECT product_id, date, value, id
              FROM product_value
             WHERE product_id IN %s AND company_id = %s AND move_id IS NULL AND lot_id IS NULL
             ORDER BY product_id, date, id
        """, tuple(product_ids), company.id))
        res = defaultdict(list)
        for product_id, dt, value, pv_id in self.env.cr.fetchall():
            res[product_id].append((dt, float(value), pv_id))
        return res

    def _valued_moves(self, company, product, in_only=False):
        """Valued moves of the product, oldest first: (id, date, value, quantity, in_qty, out_qty, is_in, is_out).

        ``in_qty`` / ``out_qty`` are the valued quantities (sum of the picked,
        non-consigned move lines entering / leaving a valued location), i.e.
        ``stock.move._get_valued_qty()``.
        """
        self.env['stock.move'].flush_model(['value', 'date', 'state', 'is_in', 'is_out', 'quantity'])
        self.env['stock.move.line'].flush_model(
            ['quantity_product_uom', 'location_id', 'location_dest_id', 'owner_id', 'picked'])
        self.env.cr.execute(SQL("""
            SELECT sm.id, sm.date, COALESCE(sm.value, 0), COALESCE(sm.quantity, 0),
                   COALESCE(SUM(CASE WHEN NOT ls_v AND ld_v THEN q ELSE 0 END), 0) AS in_qty,
                   COALESCE(SUM(CASE WHEN ls_v AND NOT ld_v THEN q ELSE 0 END), 0) AS out_qty,
                   sm.is_in, sm.is_out
              FROM stock_move sm
              LEFT JOIN LATERAL (
                   SELECT sml.quantity_product_uom AS q,
                          (ls.company_id IS NOT NULL AND ls.usage IN ('internal', 'transit')) AS ls_v,
                          (ld.company_id IS NOT NULL AND ld.usage IN ('internal', 'transit')) AS ld_v
                     FROM stock_move_line sml
                     JOIN stock_location ls ON ls.id = sml.location_id
                     JOIN stock_location ld ON ld.id = sml.location_dest_id
                    WHERE sml.move_id = sm.id
                      AND COALESCE(sml.picked, TRUE)
                      AND (sml.owner_id IS NULL OR sml.owner_id = %s)
              ) lines ON TRUE
             WHERE sm.product_id = %s AND sm.company_id = %s AND sm.state = 'done'
               AND (sm.is_in OR (sm.is_out AND NOT %s))
             GROUP BY sm.id
             ORDER BY sm.date, sm.id
        """, company.partner_id.id, product.id, company.id, in_only))
        return [(mid, dt, float(value), float(qty), float(in_qty), float(out_qty), is_in, is_out)
                for mid, dt, value, qty, in_qty, out_qty, is_in, is_out in self.env.cr.fetchall()]

    def _qty_at(self, company, product, instant, current_qty):
        """Company valued quantity at a UTC instant: current quants minus the lines done after it."""
        self.env.cr.execute(SQL("""
            SELECT COALESCE(SUM(CASE WHEN NOT ls_v AND ld_v THEN q ELSE 0 END), 0)
                 - COALESCE(SUM(CASE WHEN ls_v AND NOT ld_v THEN q ELSE 0 END), 0)
              FROM (
                   SELECT sml.quantity_product_uom AS q,
                          (ls.company_id IS NOT NULL AND ls.usage IN ('internal', 'transit')) AS ls_v,
                          (ld.company_id IS NOT NULL AND ld.usage IN ('internal', 'transit')) AS ld_v
                     FROM stock_move sm
                     JOIN stock_move_line sml ON sml.move_id = sm.id
                     JOIN stock_location ls ON ls.id = sml.location_id
                     JOIN stock_location ld ON ld.id = sml.location_dest_id
                    WHERE sm.product_id = %s AND sm.company_id = %s AND sm.state = 'done'
                      AND sm.date > %s
                      AND COALESCE(sml.picked, TRUE)
                      AND (sml.owner_id IS NULL OR sml.owner_id = %s)
              ) x
        """, product.id, company.id, instant, company.partner_id.id))
        after = float(self.env.cr.fetchone()[0] or 0.0)
        return product.uom_id.round(current_qty - after)

    # ------------------------------------------------------------------
    # Replay
    # ------------------------------------------------------------------
    def _compute_product(self, company, product, day_from, current_qty, nets, manual_values, now):
        cost_method = product.cost_method
        uom = product.uom_id
        tz_day = company._asr_local_day

        # Days that get a row: ledger movements and (standard/average) manual revaluations.
        days = set(nets)
        if days and cost_method in ('standard', 'average'):
            # A price change before the first movement (Odoo writes one at product creation,
            # dated year 1) values nothing: no row for it.
            first_day = min(days)
            days.update(d for d in (tz_day(dt) for dt, _value, _id in manual_values) if d >= first_day)
        if not days:
            return []
        all_days = sorted(days)

        # Closing quantity per day, anchored on today's quants (like Odoo's qty at date).
        closing_qty = {}
        acc = current_qty
        for day in reversed(all_days):
            closing_qty[day] = uom.round(acc)
            acc -= nets.get(day, 0.0)

        days_to_compute = [d for d in all_days if d >= day_from]
        if not days_to_compute:
            return []

        base = {
            'company_id': company.id, 'product_id': product.id, 'cost_method': cost_method,
            'replay_qty': 0.0, 'replay_avg': 0.0, 'exact_slow_path': False, 'computed_at': now,
            'revaluation_value': 0.0,
        }
        if product.lot_valuated:
            return self._compute_lot_valuated(company, product, days_to_compute, closing_qty, base)
        if cost_method == 'standard':
            return self._compute_standard(company, product, days_to_compute, closing_qty, manual_values,
                                          current_qty, base)
        if cost_method == 'average':
            return self._compute_average(company, product, days_to_compute, closing_qty, manual_values,
                                         current_qty, base)
        return self._compute_fifo(company, product, days_to_compute, closing_qty, base)

    def _compute_lot_valuated(self, company, product, days, closing_qty, base):
        rows = []
        product = product.with_company(company).with_context(allowed_company_ids=company.ids)
        for day in days:
            instant = company._asr_day_end_utc(day)
            value = product.with_context(to_date=instant).total_value
            product.invalidate_recordset(['total_value', 'avg_cost', 'qty_available'])
            qty = closing_qty[day]
            rows.append(dict(base, day=day, closing_qty=qty, closing_value=value,
                             avg_cost=value / qty if qty else 0.0, exact_slow_path=True))
        return rows

    def _compute_standard(self, company, product, days, closing_qty, manual_values, current_qty, base):
        rows = []
        std_price = product.standard_price
        for day in days:
            instant = company._asr_day_end_utc(day)
            price = std_price
            for dt, value, _id in manual_values:
                if dt <= instant:
                    price = value
                else:
                    break
            qty = closing_qty[day]
            # revaluation that day: each manual change x quantity at that moment
            reval = 0.0
            for i, (dt, value, _id) in enumerate(manual_values):
                if company._asr_local_day(dt) != day:
                    continue
                before = manual_values[i - 1][1] if i > 0 else std_price
                reval += (value - before) * self._qty_at(company, product, dt, current_qty)
            rows.append(dict(base, day=day, closing_qty=qty, closing_value=qty * price, avg_cost=price,
                             revaluation_value=reval))
        return rows

    def _compute_average(self, company, product, days, closing_qty, manual_values, current_qty, base):
        """Mirror of ``_run_average_batch(at_date)`` with the state kept between days."""
        moves = self._valued_moves(company, product)
        events = [(m[1], 0, m[0], m) for m in moves]
        events += [(dt, 1, pv_id, (dt, value)) for dt, value, pv_id in manual_values]
        events.sort(key=lambda e: (e[0], e[1], e[2]))

        qty, value, avg = 0.0, 0.0, None
        rows = []
        day_from = days[0]
        # Resume from the previous row when there is one; otherwise replay from
        # the first move, which is where Odoo starts when no manual value exists.
        previous = self.search([('company_id', '=', company.id), ('product_id', '=', product.id),
                                ('day', '<', day_from)], order='day desc', limit=1)
        pos = 0
        if previous:
            qty, value, avg = previous.replay_qty, previous.sudo().closing_value, previous.replay_avg
            prev_end = company._asr_day_end_utc(previous.day)
            while pos < len(events) and events[pos][0] <= prev_end:
                pos += 1

        def apply_move(m):
            nonlocal qty, value, avg
            _mid, _dt, m_value, _m_qty, in_qty, out_qty, is_in, is_out = m
            if is_in:
                average_cost = avg if avg is not None else (m_value / in_qty if in_qty else 0.0)
                previous_qty = qty
                qty += in_qty
                if previous_qty > 0:
                    value += m_value
                    average_cost = value / qty if qty else average_cost
                else:
                    average_cost = m_value / in_qty if in_qty else average_cost
                    value = average_cost * qty
                avg = average_cost
            if is_out:
                average_cost = avg if avg is not None else (m_value / out_qty if out_qty else 0.0)
                value -= out_qty * average_cost
                qty -= out_qty
                avg = average_cost

        for day in days:
            instant = company._asr_day_end_utc(day)
            reval = 0.0
            while pos < len(events) and events[pos][0] <= instant:
                dt, kind, _key, payload = events[pos]
                pos += 1
                if kind == 0:
                    apply_move(payload)
                else:
                    manual_price = payload[1]
                    before = value
                    qty = self._qty_at(company, product, dt, current_qty)
                    avg = manual_price
                    value = manual_price * qty
                    reval += value - before
            c_qty = closing_qty[day]
            rows.append(dict(base, day=day, closing_qty=c_qty, closing_value=value,
                             avg_cost=(value / c_qty) if c_qty else (avg or 0.0),
                             revaluation_value=reval, replay_qty=qty, replay_avg=avg or 0.0))
        return rows

    def _compute_fifo(self, company, product, days, closing_qty, base):
        """Mirror of ``_run_fifo(qty, at_date)``: newest incoming moves covering the quantity."""
        moves = self._valued_moves(company, product, in_only=True)  # (id, date, value, quantity, in_qty, ...)
        std_price = product.standard_price
        rows = []
        idx = 0  # number of moves with date <= instant
        for day in days:
            instant = company._asr_day_end_utc(day)
            while idx < len(moves) and moves[idx][1] <= instant:
                idx += 1
            qty = closing_qty[day]
            if product.uom_id.compare(qty, 0) <= 0:
                last_in = next((m for m in reversed(moves[:idx])), None)
                if last_in:
                    price = last_in[2] / last_in[4] if last_in[4] else 0.0
                else:
                    price = std_price
                value = qty * price
            else:
                remaining = qty
                value = 0.0
                last_move = None
                for m in reversed(moves[:idx]):
                    if product.uom_id.compare(remaining, 0) <= 0:
                        break
                    _mid, _dt, m_value, m_qty, in_qty, _o, _i, _u = m
                    last_move = m
                    take = min(in_qty, remaining)
                    value += (m_value * take / in_qty) if in_qty else 0.0
                    remaining -= in_qty
                if product.uom_id.compare(remaining, 0) > 0:
                    if last_move and last_move[3]:
                        value += remaining * (last_move[2] / last_move[3])
                    else:
                        value += remaining * std_price
            rows.append(dict(base, day=day, closing_qty=qty, closing_value=value,
                             avg_cost=(value / qty) if qty else 0.0))
        return rows

    # ------------------------------------------------------------------
    # Readers shared by the reports
    # ------------------------------------------------------------------
    def _closing_at(self, company, product_ids, day):
        """{product_id: (closing_qty, closing_value, avg_cost)} as of the end of ``day``."""
        if not product_ids:
            return {}
        self.env.cr.execute(SQL("""
            SELECT DISTINCT ON (product_id) product_id, closing_qty, closing_value, avg_cost
              FROM asr_stock_value_daily
             WHERE company_id = %s AND product_id IN %s AND day <= %s
             ORDER BY product_id, day DESC
        """, company.id, tuple(product_ids), day))
        return {pid: (float(q or 0), float(v or 0), float(c or 0)) for pid, q, v, c in self.env.cr.fetchall()}

    def _location_value(self, company_value, company_qty, location_qty, standard_price):
        """Odoo's warehouse ratio method (DESIGN.md §1.2 point 4)."""
        if float_round(location_qty, precision_digits=6) == 0:
            return 0.0
        if float_round(company_qty, precision_digits=6) == 0:
            return standard_price * location_qty
        return company_value * location_qty / company_qty
