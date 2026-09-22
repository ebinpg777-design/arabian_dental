# -*- coding: utf-8 -*-
"""Re-attach the migrated deliveries and invoices to their sale orders.

The Odoo 10 -> 19 transaction sync created every picking, move and invoice line as a
stand-alone document: `stock.picking.sale_id`, `stock.move.sale_line_id` and the
invoice-line <-> order-line link were never written (they were not needed for the
balance-forward cut-over). Consequences the client hit on 2026-08-17:

* every migrated order shows **0 delivered** although its delivery is done
  (`qty_delivered` is computed from moves linked to the line — there were none), and its
  Delivery smart button is empty;
* `qty_invoiced` is 0 everywhere, so the moment delivered quantities appear, ~20k orders
  that were invoiced long ago would flip to "To Invoice".

This links, in order:
1. pickings to orders (`sale_id`) — the migration kept the order number in `origin`;
2. moves to order lines (`sale_line_id`) — by product, in line order, within the order;
3. done outgoing moves' done quantity — the migration set the state by SQL and left
   `quantity` at 0 with no move lines: done = demand, picked;
4. invoice lines to order lines — from `data/v10_invoice_sale_lines.csv` (the v10
   `sale_order_line_invoice_rel`, exported 2026-08-17), matched inside the v19 invoice
   (`account.move.x_odoo10_id`) by product, in order;
5. then recomputes qty_delivered / qty_invoiced / invoice_status for the touched lines.

Idempotent: every step only fills what is empty. Only migrated documents (x_odoo10_id
set) are touched; anything created in v19 already carries its links.
"""
import csv
import logging
import os
from collections import defaultdict

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

CSV_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'v10_invoice_sale_lines.csv')


def _link_pickings(cr):
    cr.execute("""
        UPDATE stock_picking sp SET sale_id = so.id
          FROM sale_order so
         WHERE sp.sale_id IS NULL AND sp.x_odoo10_id IS NOT NULL
           AND sp.origin IS NOT NULL AND so.name = sp.origin""")
    _logger.info("delivery links: %s pickings attached to their order", cr.rowcount)


def _link_moves(cr):
    """Moves of a linked, migrated picking -> the order's lines, matched by product."""
    cr.execute("""
        SELECT sm.id, sp.sale_id, sm.product_id
          FROM stock_move sm
          JOIN stock_picking sp ON sp.id = sm.picking_id
         WHERE sm.sale_line_id IS NULL AND sp.sale_id IS NOT NULL AND sp.x_odoo10_id IS NOT NULL
         ORDER BY sp.sale_id, sm.id""")
    moves = cr.fetchall()
    if not moves:
        return
    order_ids = tuple({m[1] for m in moves})
    cr.execute("""
        SELECT order_id, product_id, id FROM sale_order_line
         WHERE order_id IN %s AND display_type IS NULL
         ORDER BY order_id, sequence, id""", (order_ids,))
    lines = defaultdict(list)          # (order, product) -> [line ids in order]
    for order_id, product_id, line_id in cr.fetchall():
        lines[(order_id, product_id)].append(line_id)
    cursor = defaultdict(int)          # round-robin per (order, product)
    updates = []
    for move_id, order_id, product_id in moves:
        candidates = lines.get((order_id, product_id))
        if not candidates:
            continue
        i = cursor[(order_id, product_id)]
        updates.append((candidates[i % len(candidates)], move_id))
        cursor[(order_id, product_id)] += 1
    if updates:
        cr.executemany("UPDATE stock_move SET sale_line_id=%s WHERE id=%s", updates)
    _logger.info("delivery links: %s of %s moves attached to an order line", len(updates), len(moves))


def _fix_done_quantities(cr):
    cr.execute("""
        UPDATE stock_move sm SET quantity = sm.product_uom_qty, picked = TRUE
          FROM stock_picking sp
         WHERE sp.id = sm.picking_id AND sp.x_odoo10_id IS NOT NULL
           AND sm.state = 'done' AND COALESCE(sm.quantity, 0) = 0 AND sm.product_uom_qty > 0
           AND NOT EXISTS (SELECT 1 FROM stock_move_line ml WHERE ml.move_id = sm.id)""")
    _logger.info("delivery links: done quantity set on %s migrated done moves", cr.rowcount)


def _link_invoice_lines(cr):
    path = os.path.normpath(CSV_PATH)
    if not os.path.exists(path):
        _logger.warning("invoice link CSV not found: %s", path)
        return
    cr.execute("SELECT x_odoo10_id, id FROM account_move WHERE x_odoo10_id IS NOT NULL")
    move_by_x10 = dict(cr.fetchall())
    cr.execute("SELECT x_odoo10_id, id, product_id FROM sale_order_line WHERE x_odoo10_id IS NOT NULL")
    sol_by_x10 = {x: (i, p) for x, i, p in cr.fetchall()}
    # invoice product lines, per invoice, in order, minus those already linked
    cr.execute("""
        SELECT aml.move_id, aml.product_id, aml.id
          FROM account_move_line aml
          JOIN account_move am ON am.id = aml.move_id
         WHERE am.x_odoo10_id IS NOT NULL AND aml.display_type = 'product'
           AND NOT EXISTS (SELECT 1 FROM sale_order_line_invoice_rel r WHERE r.invoice_line_id = aml.id)
         ORDER BY aml.move_id, aml.sequence, aml.id""")
    free = defaultdict(list)           # (move, product) -> [aml ids]
    for move_id, product_id, aml_id in cr.fetchall():
        free[(move_id, product_id)].append(aml_id)
    inserts, missing = [], 0
    with open(path, newline='') as f:
        for row in csv.DictReader(f):
            move_id = move_by_x10.get(int(row['invoice_x10']))
            sol = sol_by_x10.get(int(row['sol_x10']))
            if not move_id or not sol:
                missing += 1
                continue
            sol_id, product_id = sol
            pool = free.get((move_id, product_id))
            if not pool:
                missing += 1
                continue
            inserts.append((pool.pop(0), sol_id))
    if inserts:
        cr.executemany("""
            INSERT INTO sale_order_line_invoice_rel (invoice_line_id, order_line_id)
            VALUES (%s, %s) ON CONFLICT DO NOTHING""", inserts)
    _logger.info("invoice links: %s invoice lines attached to order lines (%s rows unmatched)",
                 len(inserts), missing)


def _recompute(env):
    cr = env.cr
    cr.execute("""
        SELECT DISTINCT l.id FROM sale_order_line l
          JOIN sale_order so ON so.id = l.order_id
         WHERE so.x_odoo10_id IS NOT NULL AND l.display_type IS NULL""")
    ids = [r[0] for r in cr.fetchall()]
    SOL = env['sale.order.line'].sudo()
    fields_ = [SOL._fields[n] for n in ('qty_delivered', 'qty_invoiced', 'qty_to_invoice',
                                        'untaxed_amount_invoiced', 'untaxed_amount_to_invoice',
                                        'invoice_status', 'invoice_lines') if n in SOL._fields]
    step = 2000
    for i in range(0, len(ids), step):
        lines = SOL.browse(ids[i:i + step])
        lines.invalidate_recordset()
        for field in fields_:
            if field.store and field.compute:
                env.add_to_compute(field, lines)
        env.flush_all()
        env.invalidate_all()
    # order-level status follows its lines
    cr.execute("SELECT id FROM sale_order WHERE x_odoo10_id IS NOT NULL")
    order_ids = [r[0] for r in cr.fetchall()]
    SO = env['sale.order'].sudo()
    for i in range(0, len(order_ids), step):
        orders = SO.browse(order_ids[i:i + step])
        env.add_to_compute(SO._fields['invoice_status'], orders)
        env.flush_all()
        env.invalidate_all()
    cr.execute("""
        SELECT count(*) FILTER (WHERE qty_delivered > 0), count(*) FILTER (WHERE qty_invoiced > 0), count(*)
          FROM sale_order_line l JOIN sale_order so ON so.id = l.order_id
         WHERE so.x_odoo10_id IS NOT NULL AND l.display_type IS NULL""")
    _logger.info("recompute: migrated lines delivered>0 / invoiced>0 / total = %s", cr.fetchone())


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    if 'x_odoo10_id' not in env['stock.picking']._fields:
        _logger.info("no migration tags on stock.picking: nothing to relink")
        return
    _link_pickings(cr)
    _link_moves(cr)
    _fix_done_quantities(cr)
    _link_invoice_lines(cr)
    _recompute(env)
