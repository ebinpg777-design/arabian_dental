# -*- coding: utf-8 -*-
"""Invoice <-> order links, second pass: by `invoice_origin`.

19.0.1.3.0 linked migrated invoice lines to order lines from the exported v10 relation.
Invoices that relation did not cover (client report 2026-08-17: the Arabian Dental Lab
Aligners company's invoices still showed no order; then a "To Invoice" tile of 21,019 on
the server, i.e. delivered quantities set but invoices not linked at all) are linked here
from what every invoice made from an order carries: `invoice_origin` = the order number.
Deliberately NOT restricted to migration-tagged records: whatever the reason the first
pass did not apply on a given database, this one only needs the origin. Product lines of
such an invoice are matched to the order's lines by product, in order. Only invoices with
NO existing sale-line link are touched, so re-running is harmless, and only migrated
documents (x_odoo10_id) are considered.
"""
import logging
from collections import defaultdict

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def _link_by_origin(cr):
    cr.execute("""
        SELECT am.id, so.id
          FROM account_move am
          JOIN sale_order so ON so.name = am.invoice_origin AND so.company_id = am.company_id
         WHERE am.move_type IN ('out_invoice', 'out_refund') AND am.state != 'cancel'
           AND NOT EXISTS (SELECT 1 FROM account_move_line l
                             JOIN sale_order_line_invoice_rel r ON r.invoice_line_id = l.id
                            WHERE l.move_id = am.id)""")
    pairs = cr.fetchall()
    if not pairs:
        _logger.info("invoice links by origin: nothing to do")
        return set()
    move_ids = tuple({m for m, _ in pairs}); order_ids = tuple({o for _, o in pairs})
    cr.execute("""SELECT move_id, product_id, id FROM account_move_line
                   WHERE move_id IN %s AND display_type = 'product' ORDER BY move_id, sequence, id""", (move_ids,))
    inv_lines = defaultdict(list)
    for move_id, product_id, aml_id in cr.fetchall():
        inv_lines[move_id].append((product_id, aml_id))
    cr.execute("""SELECT order_id, product_id, id FROM sale_order_line
                   WHERE order_id IN %s AND display_type IS NULL ORDER BY order_id, sequence, id""", (order_ids,))
    so_lines = defaultdict(list)
    for order_id, product_id, sol_id in cr.fetchall():
        so_lines[(order_id, product_id)].append(sol_id)
    inserts, touched_orders, skipped = [], set(), 0
    for move_id, order_id in pairs:
        cursor = defaultdict(int)
        for product_id, aml_id in inv_lines.get(move_id, []):
            candidates = so_lines.get((order_id, product_id))
            if not candidates:
                skipped += 1
                continue
            i = cursor[product_id]
            inserts.append((aml_id, candidates[i % len(candidates)]))
            cursor[product_id] += 1
            touched_orders.add(order_id)
    if inserts:
        cr.executemany("""INSERT INTO sale_order_line_invoice_rel (invoice_line_id, order_line_id)
                          VALUES (%s, %s) ON CONFLICT DO NOTHING""", inserts)
    _logger.info("invoice links by origin: %s invoices considered, %s lines linked, %s lines without a matching product",
                 len(pairs), len(inserts), skipped)
    return touched_orders


def _recompute(env, order_ids):
    if not order_ids:
        return
    SO = env['sale.order'].sudo(); SOL = env['sale.order.line'].sudo()
    order_ids = list(order_ids)
    step = 2000
    for i in range(0, len(order_ids), step):
        orders = SO.browse(order_ids[i:i + step])
        lines = orders.order_line
        lines.invalidate_recordset()
        for name in ('qty_invoiced', 'qty_to_invoice', 'untaxed_amount_invoiced',
                     'untaxed_amount_to_invoice', 'invoice_status', 'invoice_lines'):
            field = SOL._fields.get(name)
            if field is not None and field.store and field.compute:
                env.add_to_compute(field, lines)
        env.add_to_compute(SO._fields['invoice_status'], orders)
        env.flush_all(); env.invalidate_all()


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    touched = _link_by_origin(cr)
    _recompute(env, touched)
