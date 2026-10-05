# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
"""Recompute queue.

Every write path that can change a done move, its value or a product's
valuation inserts a key ``(company, product, first day to rebuild)`` here;
the cron rebuilds those products' daily rows from that day on.
"""
import logging
import time
from odoo import api, fields, models
from odoo.tools import SQL, split_every

from .stock_value_daily import FULL_REBUILD_DAY

_logger = logging.getLogger(__name__)
CHUNK = 40


class StockDirty(models.Model):
    _name = 'asr.stock.dirty'
    _description = 'Stock Summary Recompute Queue'
    _order = 'day_from, id'
    _rec_name = 'product_id'
    _log_access = False

    company_id = fields.Many2one('res.company', required=True, readonly=True, index=True)
    product_id = fields.Many2one('product.product', required=True, readonly=True, index=True, ondelete='cascade')
    day_from = fields.Date(required=True, readonly=True)
    create_date = fields.Datetime(readonly=True, default=fields.Datetime.now)

    _unique_key = models.UniqueIndex("(company_id, product_id, day_from)")

    # ------------------------------------------------------------------
    # Marking
    # ------------------------------------------------------------------
    @api.model
    def _mark(self, keys):
        """Queue ``keys``: an iterable of (company_id, product_id, day) tuples. Cheap and idempotent."""
        keys = {(c, p, max(d, FULL_REBUILD_DAY)) for c, p, d in keys if c and p and d}
        if not keys:
            return
        values = SQL(', ').join(SQL('(%s, %s, %s::date, now() AT TIME ZONE \'UTC\')', c, p, d) for c, p, d in keys)
        self.env.cr.execute(SQL(
            "INSERT INTO asr_stock_dirty (company_id, product_id, day_from, create_date) VALUES %s "
            "ON CONFLICT (company_id, product_id, day_from) DO NOTHING", values))

    @api.model
    def _mark_moves(self, moves, dates_by_move=None):
        """Queue done moves on the (local) day of their date, plus any former date given."""
        keys = set()
        companies = {}
        for move in moves:
            company = move.company_id
            if not company:
                continue
            companies.setdefault(company.id, company)
            dates = {move.date}
            if dates_by_move and move.id in dates_by_move:
                dates.add(dates_by_move[move.id])
            for dt in dates:
                if dt:
                    keys.add((company.id, move.product_id.id, company._asr_local_day(dt)))
        self._mark(keys)

    @api.model
    def _mark_products(self, products, company=None, day=FULL_REBUILD_DAY):
        companies = company or self.env['res.company'].sudo().search([])  # pylint: disable=no-search-all
        self._mark((c.id, p.id, day) for c in companies for p in products)

    @api.model
    def _mark_full_rebuild(self, companies):
        """Drop the daily tables of ``companies`` and queue every product that has stock or moves."""
        for company in companies:
            self.env.cr.execute(SQL("DELETE FROM asr_stock_move_daily WHERE company_id = %s", company.id))
            self.env.cr.execute(SQL("DELETE FROM asr_stock_value_daily WHERE company_id = %s", company.id))
            self.env.cr.execute(SQL("DELETE FROM asr_stock_dirty WHERE company_id = %s", company.id))
            self.env.cr.execute(SQL("""
                INSERT INTO asr_stock_dirty (company_id, product_id, day_from, create_date)
                SELECT DISTINCT %s, x.product_id, %s::date, now() AT TIME ZONE 'UTC'
                  FROM (SELECT product_id FROM stock_move WHERE company_id = %s AND state = 'done'
                        UNION SELECT product_id FROM stock_quant WHERE company_id = %s) x
            """, company.id, FULL_REBUILD_DAY, company.id, company.id))
            company.sudo().write({'asr_engine_state': 'rebuilding'})

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------
    @api.model
    def _cron_recompute(self):
        self._process_queue(time_budget=240, commit=True)

    @api.model
    def _process_queue(self, time_budget=None, commit=False, companies=None):
        """Rebuild the queued products, oldest day first, in chunks per company.

        :param time_budget: stop (leaving the rest for the next run) after this many seconds.
        :param commit: commit after every chunk (cron only).
        """
        started = time.monotonic()
        domain = [('company_id', 'in', companies.ids)] if companies else []
        processed = 0
        while True:
            if time_budget and time.monotonic() - started > time_budget:
                break
            keys = self.sudo().search(domain, order='company_id, day_from, id', limit=CHUNK * 5)
            if not keys:
                break
            company = keys[0].company_id
            keys = keys.filtered(lambda k: k.company_id == company)
            product_from_day = {}
            for key in keys:
                current = product_from_day.get(key.product_id.id)
                if current is None or key.day_from < current:
                    product_from_day[key.product_id.id] = key.day_from
            for chunk in split_every(CHUNK, list(product_from_day.items()), dict):
                self._rebuild_products(company, chunk)
                done_keys = keys.filtered(lambda k: k.product_id.id in chunk)
                self.env.cr.execute(SQL("DELETE FROM asr_stock_dirty WHERE id IN %s", tuple(done_keys.ids)))
                processed += len(chunk)
                if commit:
                    self.env['ir.cron']._commit_progress(len(chunk))
            remaining = self.sudo().search_count([('company_id', '=', company.id)])
            company.sudo().write({
                'asr_engine_state': 'ready' if not remaining else 'rebuilding',
                'asr_engine_last_run': fields.Datetime.now(),
            })
            if commit:
                self.env['ir.cron']._commit_progress(0, remaining=remaining)
        return processed

    @api.model
    def _rebuild_products(self, company, product_from_day):
        """Rebuild both daily tables for {product_id: first day} inside one company."""
        company = company.sudo()
        self.env['stock.move'].flush_model()
        self.env['stock.move.line'].flush_model()
        self.env['stock.quant'].flush_model()
        self.env['asr.stock.move.daily']._rebuild(company, product_from_day)
        self.env['asr.stock.value.daily']._rebuild(company, product_from_day)
        self.env['asr.stock.move.daily'].invalidate_model()
        self.env['asr.stock.value.daily'].invalidate_model()

    @api.model
    def _ensure_fresh(self, company, products=None, max_wait=0):
        """Called by the reports: process what is queued for these products right now.

        Reports read the daily tables; if a product of the report is still in the
        queue, its rows would be stale. Small queues are processed on the spot.
        """
        domain = [('company_id', '=', company.id)]
        if products is not None:
            domain.append(('product_id', 'in', products.ids))
        keys = self.sudo().search(domain, order='day_from, id', limit=CHUNK * 5 + 1)
        if not keys:
            return True
        if len(keys) > CHUNK * 5:
            return False
        product_from_day = {}
        for key in keys:
            current = product_from_day.get(key.product_id.id)
            if current is None or key.day_from < current:
                product_from_day[key.product_id.id] = key.day_from
        self._rebuild_products(company, product_from_day)
        self.env.cr.execute(SQL("DELETE FROM asr_stock_dirty WHERE id IN %s", tuple(keys.ids)))
        self.invalidate_model()
        return True

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------
    @api.model
    def _health_check(self, company, limit=200):
        """Compare the latest row of up to ``limit`` products with Odoo's live figures.

        Returns (checked, mismatched_products). Mismatches are queued again.
        """
        self._ensure_fresh(company)
        ValueDaily = self.env['asr.stock.value.daily'].sudo()
        self.env.cr.execute(SQL("""
            SELECT DISTINCT product_id FROM asr_stock_value_daily
             WHERE company_id = %s ORDER BY product_id DESC LIMIT %s
        """, company.id, limit))
        product_ids = [r[0] for r in self.env.cr.fetchall()]
        if not product_ids:
            return 0, self.env['product.product']
        today = company._asr_local_day(fields.Datetime.now())
        closing = ValueDaily._closing_at(company, product_ids, today)
        products = self.env['product.product'].with_company(company).with_context(
            allowed_company_ids=company.ids, active_test=False).browse(product_ids)
        instant = fields.Datetime.now()
        live = products.with_context(to_date=instant)
        live_qty = {p.id: p.qty_available for p in live._with_valuation_context()}
        live_value = {p.id: p.total_value for p in live}
        mismatched = self.env['product.product']
        currency = company.currency_id
        for product in products:
            qty, value, _cost = closing.get(product.id, (0.0, 0.0, 0.0))
            if product.uom_id.compare(qty, live_qty.get(product.id, 0.0)) != 0 or \
                    currency.compare_amounts(value, live_value.get(product.id, 0.0)) != 0:
                mismatched |= product
        if mismatched:
            self._mark((company.id, p.id, FULL_REBUILD_DAY) for p in mismatched)
        return len(product_ids), mismatched

    def action_health_check(self):
        company = self.env.company
        checked, mismatched = self._health_check(company)
        if mismatched:
            message = self.env._(
                "%(checked)s products checked, %(bad)s differ from Odoo's live valuation and have been queued "
                "for a rebuild: %(names)s",
                checked=checked, bad=len(mismatched), names=', '.join(mismatched[:10].mapped('display_name')))
            kind = 'warning'
        else:
            message = self.env._("%(checked)s products checked, all match Odoo's live valuation.", checked=checked)
            kind = 'success'
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'type': kind, 'message': message, 'sticky': bool(mismatched)},
        }

    def action_process_now(self):
        self._process_queue(time_budget=120, companies=self.company_id or None)
        return {'type': 'ir.actions.client', 'tag': 'reload'}
