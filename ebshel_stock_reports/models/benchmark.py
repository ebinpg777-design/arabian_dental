# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
"""Synthetic volume for the benchmark described in doc/PERFORMANCE.md.

Run from an Odoo shell on a throw-away database::

    env['asr.stock.dirty']._benchmark_generate(n_moves=1000000, n_products=20000)
    env.cr.commit()

Moves and move lines are inserted with SQL (the ORM would take hours for a
million done moves); quants are rebuilt from the generated moves so the
anchored quantities are consistent. Receipts are valued at a price per
product, deliveries at the running average, which is enough to exercise the
engine; the figures are not meant to match Odoo's own valuation.
"""
import logging
import time

from odoo import api, models
from odoo.tools import SQL

_logger = logging.getLogger(__name__)


class StockDirtyBenchmark(models.AbstractModel):
    _name = 'asr.benchmark'
    _description = 'Advanced Stock Reports Benchmark Tools'

    @api.model
    def _generate(self, n_moves=100000, n_products=2000, days=730, company=None, seed=42):
        company = company or self.env.company
        warehouse = self.env['stock.warehouse'].search([('company_id', '=', company.id)], limit=1)
        stock = warehouse.lot_stock_id
        supplier = self.env.ref('stock.stock_location_suppliers')
        customer = self.env.ref('stock.stock_location_customers')
        uom = self.env.ref('uom.product_uom_unit')
        category = self.env['product.category'].create({
            'name': 'Benchmark AVCO', 'property_cost_method': 'average', 'property_valuation': 'periodic'})
        started = time.monotonic()
        products = self.env['product.product'].create([{
            'name': f'Benchmark product {i:06d}', 'default_code': f'BM{i:06d}', 'is_storable': True,
            'type': 'consu', 'categ_id': category.id, 'uom_id': uom.id, 'standard_price': 0.0,
        } for i in range(n_products)])
        _logger.info("benchmark: %s products in %.1fs", n_products, time.monotonic() - started)
        cr = self.env.cr
        self.env.flush_all()
        cr.execute(SQL("SELECT setseed(%s)", seed / 100.0))
        # moves: 60% receipts, 40% deliveries, spread over `days` days, quantities 1..50
        cr.execute(SQL("""
            CREATE TEMP TABLE asr_bench AS
            SELECT g AS n,
                   (%(pids)s::int[])[1 + floor(random() * %(np)s)::int] AS product_id,
                   (now() AT TIME ZONE 'UTC') - (random() * %(days)s || ' days')::interval AS date,
                   (1 + floor(random() * 50))::numeric AS qty,
                   (random() < 0.6) AS is_receipt,
                   (5 + random() * 95)::numeric AS price
              FROM generate_series(1, %(n)s) g
        """, pids=products.ids, np=n_products, days=days, n=n_moves))
        cr.execute(SQL("""
            INSERT INTO stock_move (company_id, product_id, product_uom, product_uom_qty, quantity, location_id,
                                    location_dest_id, procure_method, date, state, picked, is_in, is_out,
                                    is_dropship, value, reference, priority,
                                    create_uid, create_date, write_uid, write_date)
            SELECT %(company)s, b.product_id, %(uom)s, b.qty, b.qty,
                   CASE WHEN b.is_receipt THEN %(supplier)s ELSE %(stock)s END,
                   CASE WHEN b.is_receipt THEN %(stock)s ELSE %(customer)s END,
                   'make_to_stock', b.date, 'done', TRUE, b.is_receipt, NOT b.is_receipt, FALSE,
                   b.qty * b.price, 'BENCH/' || b.n, '0', 1, now(), 1, now()
              FROM asr_bench b
        """, company=company.id, uom=uom.id, supplier=supplier.id, stock=stock.id, customer=customer.id))
        cr.execute(SQL("""
            INSERT INTO stock_move_line (company_id, move_id, product_id, product_uom_id, quantity,
                                         quantity_product_uom, location_id, location_dest_id, date, state, picked,
                                         create_uid, create_date, write_uid, write_date)
            SELECT sm.company_id, sm.id, sm.product_id, sm.product_uom, sm.quantity, sm.quantity,
                   sm.location_id, sm.location_dest_id, sm.date, 'done', TRUE, 1, now(), 1, now()
              FROM stock_move sm
             WHERE sm.reference LIKE 'BENCH/%%' AND sm.company_id = %s
        """, company.id))
        cr.execute(SQL("""
            INSERT INTO stock_quant (company_id, product_id, location_id, quantity, reserved_quantity, in_date,
                                     create_uid, create_date, write_uid, write_date)
            SELECT %(company)s, product_id, %(stock)s,
                   SUM(CASE WHEN location_dest_id = %(stock)s THEN quantity ELSE -quantity END), 0, now(),
                   1, now(), 1, now()
              FROM stock_move WHERE reference LIKE 'BENCH/%%' AND company_id = %(company)s
             GROUP BY product_id
        """, company=company.id, stock=stock.id))
        cr.execute("DROP TABLE asr_bench")
        _logger.info("benchmark: %s moves generated in %.1fs", n_moves, time.monotonic() - started)
        return products

    @api.model
    def _run(self, n_moves=100000, n_products=2000):
        """Generate, rebuild, time. Returns a dict of timings in seconds."""
        timings = {}
        started = time.monotonic()
        self._generate(n_moves, n_products)
        timings['generate'] = time.monotonic() - started
        company = self.env.company
        started = time.monotonic()
        self.env['asr.stock.dirty']._mark_full_rebuild(company)
        self.env['asr.stock.dirty']._process_queue()
        timings['full_rebuild'] = time.monotonic() - started
        # incremental: touch one move per 1000 products and recompute
        self.env.cr.execute(SQL("""
            SELECT DISTINCT ON (product_id) id FROM stock_move
             WHERE reference LIKE 'BENCH/%%' AND company_id = %s ORDER BY product_id, date LIMIT 50
        """, company.id))
        moves = self.env['stock.move'].browse([r[0] for r in self.env.cr.fetchall()])
        started = time.monotonic()
        moves.write({'value': 1.0})
        self.env['asr.stock.dirty']._process_queue()
        timings['incremental_50_products'] = time.monotonic() - started
        started = time.monotonic()
        ledger = self.env['asr.report.stock.ledger'].create({'company_id': company.id})
        rows = ledger._asr_rows()
        timings['ledger_rows'] = time.monotonic() - started
        timings['ledger_row_count'] = len(rows)
        started = time.monotonic()
        ledger._asr_xlsx()
        timings['ledger_xlsx'] = time.monotonic() - started
        _logger.info("benchmark timings: %s", timings)
        return timings
