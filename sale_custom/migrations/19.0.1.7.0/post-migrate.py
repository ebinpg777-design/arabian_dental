# -*- coding: utf-8 -*-
"""Re-run the three Odoo-10 data loads with SQL-based id mapping.

19.0.1.2.0 (partner Sales Routes / salespeople), 19.0.1.3.0 (pickings, moves, done
quantities, invoice-line links) and 19.0.1.5.0 (team leaders) guarded on
``'x_odoo10_id' in Model._fields``. During a real ``-u`` the module that declares those
fields (lab_migration) is loaded AFTER sale_custom, so at that moment they are not in the
registry although the columns are in the database - and the loads silently skipped
("no migration tags"). Everything here reads the tags straight from SQL, fills only what
is still empty, and is safe to run again.
"""
import csv
import logging
import os
from collections import defaultdict

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)
DATA = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', 'data'))


def _has_column(cr, table, column):
    cr.execute("SELECT 1 FROM information_schema.columns WHERE table_name=%s AND column_name=%s", (table, column))
    return bool(cr.fetchone())


# ------------------------------------------------------------------ routes / salespeople
def _load_partner_routes(cr):
    path = os.path.join(DATA, 'v10_partner_sales_route.csv')
    if not os.path.exists(path) or not _has_column(cr, 'res_partner', 'x_odoo10_id'):
        _logger.warning("partner routes: CSV or x_odoo10_id column missing - skipped")
        return
    cr.execute("SELECT x_odoo10_id, id FROM res_partner WHERE x_odoo10_id IS NOT NULL"); pmap = dict(cr.fetchall())
    cr.execute("SELECT x_odoo10_id, id FROM crm_team WHERE x_odoo10_id IS NOT NULL"); tmap = dict(cr.fetchall())
    cr.execute("SELECT upper(trim(COALESCE(name->>'en_US', name::text))), min(id) FROM crm_team GROUP BY 1"); tname = dict(cr.fetchall())
    cr.execute("SELECT x_odoo10_id, id FROM res_users WHERE x_odoo10_id IS NOT NULL"); umap = dict(cr.fetchall())
    cr.execute("SELECT id, team_id, user_id FROM res_partner"); current = {r[0]: (r[1], r[2]) for r in cr.fetchall()}
    team_upd, user_upd = defaultdict(list), defaultdict(list)
    with open(path, newline='') as f:
        for row in csv.DictReader(f):
            pid = pmap.get(int(row['partner_x10']))
            if not pid:
                continue
            cur_team, cur_user = current.get(pid, (None, None))
            if row['team_x10'] and not cur_team:
                tid = tmap.get(int(row['team_x10'])) or tname.get((row['team_name'] or '').strip().upper())
                if tid:
                    team_upd[tid].append(pid)
            if row['user_x10'] and not cur_user:
                uid = umap.get(int(row['user_x10']))
                if uid:
                    user_upd[uid].append(pid)
    n_t = n_u = 0
    for tid, pids in team_upd.items():
        cr.execute("UPDATE res_partner SET team_id=%s WHERE id = ANY(%s) AND team_id IS NULL", (tid, pids)); n_t += cr.rowcount
    for uid, pids in user_upd.items():
        cr.execute("UPDATE res_partner SET user_id=%s WHERE id = ANY(%s) AND user_id IS NULL", (uid, pids)); n_u += cr.rowcount
    _logger.info("partner routes: team set on %s, salesperson set on %s", n_t, n_u)


# ------------------------------------------------------------------ deliveries / invoices
def _link_pickings(cr):
    cr.execute("""UPDATE stock_picking sp SET sale_id = so.id FROM sale_order so
                   WHERE sp.sale_id IS NULL AND sp.x_odoo10_id IS NOT NULL AND sp.origin IS NOT NULL AND so.name = sp.origin""")
    _logger.info("delivery links: %s pickings attached to their order", cr.rowcount)


def _link_moves(cr):
    cr.execute("""SELECT sm.id, sp.sale_id, sm.product_id FROM stock_move sm JOIN stock_picking sp ON sp.id = sm.picking_id
                   WHERE sm.sale_line_id IS NULL AND sp.sale_id IS NOT NULL AND sp.x_odoo10_id IS NOT NULL ORDER BY sp.sale_id, sm.id""")
    moves = cr.fetchall()
    if not moves:
        _logger.info("delivery links: no unlinked moves"); return
    cr.execute("""SELECT order_id, product_id, id FROM sale_order_line WHERE order_id IN %s AND display_type IS NULL
                   ORDER BY order_id, sequence, id""", (tuple({m[1] for m in moves}),))
    lines = defaultdict(list)
    for order_id, product_id, line_id in cr.fetchall():
        lines[(order_id, product_id)].append(line_id)
    cursor, updates = defaultdict(int), []
    for move_id, order_id, product_id in moves:
        cands = lines.get((order_id, product_id))
        if not cands:
            continue
        i = cursor[(order_id, product_id)]; updates.append((cands[i % len(cands)], move_id)); cursor[(order_id, product_id)] += 1
    if updates:
        cr.executemany("UPDATE stock_move SET sale_line_id=%s WHERE id=%s", updates)
    _logger.info("delivery links: %s of %s moves attached to an order line", len(updates), len(moves))


def _fix_done_quantities(cr):
    cr.execute("""UPDATE stock_move sm SET quantity = sm.product_uom_qty, picked = TRUE FROM stock_picking sp
                   WHERE sp.id = sm.picking_id AND sp.x_odoo10_id IS NOT NULL AND sm.state = 'done'
                     AND COALESCE(sm.quantity, 0) = 0 AND sm.product_uom_qty > 0
                     AND NOT EXISTS (SELECT 1 FROM stock_move_line ml WHERE ml.move_id = sm.id)""")
    _logger.info("delivery links: done quantity set on %s migrated done moves", cr.rowcount)


def _link_invoice_lines(cr):
    path = os.path.join(DATA, 'v10_invoice_sale_lines.csv')
    if not os.path.exists(path):
        _logger.warning("invoice link CSV not found: %s", path); return
    cr.execute("SELECT x_odoo10_id, id FROM account_move WHERE x_odoo10_id IS NOT NULL"); move_by_x10 = dict(cr.fetchall())
    cr.execute("SELECT x_odoo10_id, id, product_id FROM sale_order_line WHERE x_odoo10_id IS NOT NULL"); sol_by_x10 = {x: (i, p) for x, i, p in cr.fetchall()}
    cr.execute("""SELECT aml.move_id, aml.product_id, aml.id FROM account_move_line aml JOIN account_move am ON am.id = aml.move_id
                   WHERE am.x_odoo10_id IS NOT NULL AND aml.display_type = 'product'
                     AND NOT EXISTS (SELECT 1 FROM sale_order_line_invoice_rel r WHERE r.invoice_line_id = aml.id)
                   ORDER BY aml.move_id, aml.sequence, aml.id""")
    free = defaultdict(list)
    for move_id, product_id, aml_id in cr.fetchall():
        free[(move_id, product_id)].append(aml_id)
    inserts, missing = [], 0
    with open(path, newline='') as f:
        for row in csv.DictReader(f):
            move_id = move_by_x10.get(int(row['invoice_x10'])); sol = sol_by_x10.get(int(row['sol_x10']))
            if not move_id or not sol:
                missing += 1; continue
            pool = free.get((move_id, sol[1]))
            if not pool:
                missing += 1; continue
            inserts.append((pool.pop(0), sol[0]))
    if inserts:
        cr.executemany("INSERT INTO sale_order_line_invoice_rel (invoice_line_id, order_line_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", inserts)
    _logger.info("invoice links: %s invoice lines attached to order lines (%s rows unmatched)", len(inserts), missing)


def _recompute(env):
    cr = env.cr
    cr.execute("SELECT id FROM sale_order WHERE x_odoo10_id IS NOT NULL")
    order_ids = [r[0] for r in cr.fetchall()]
    SO, SOL = env['sale.order'].sudo(), env['sale.order.line'].sudo()
    names = ('qty_delivered', 'qty_invoiced', 'qty_to_invoice', 'untaxed_amount_invoiced', 'untaxed_amount_to_invoice', 'invoice_status', 'invoice_lines')
    step = 2000
    for i in range(0, len(order_ids), step):
        orders = SO.browse(order_ids[i:i + step]); lines = orders.order_line
        lines.invalidate_recordset()
        for name in names:
            field = SOL._fields.get(name)
            if field is not None and field.store and field.compute:
                env.add_to_compute(field, lines)
        env.add_to_compute(SO._fields['invoice_status'], orders)
        env.flush_all(); env.invalidate_all()
    cr.execute("""SELECT count(*) FILTER (WHERE qty_delivered > 0), count(*) FILTER (WHERE qty_invoiced > 0), count(*)
                    FROM sale_order_line l JOIN sale_order so ON so.id = l.order_id WHERE so.x_odoo10_id IS NOT NULL AND l.display_type IS NULL""")
    _logger.info("recompute: migrated lines delivered>0 / invoiced>0 / total = %s", cr.fetchone())


# ------------------------------------------------------------------ team leaders
def _load_team_leaders(cr):
    path = os.path.join(DATA, 'v10_team_leaders.csv')
    if not os.path.exists(path) or not _has_column(cr, 'crm_team', 'x_odoo10_id'):
        _logger.warning("team leaders: CSV or x_odoo10_id column missing - skipped"); return
    cr.execute("SELECT x_odoo10_id, id FROM crm_team WHERE x_odoo10_id IS NOT NULL"); tmap = dict(cr.fetchall())
    cr.execute("SELECT upper(trim(COALESCE(name->>'en_US', name::text))), min(id) FROM crm_team GROUP BY 1"); tname = dict(cr.fetchall())
    cr.execute("SELECT x_odoo10_id, id FROM res_users WHERE x_odoo10_id IS NOT NULL"); umap = dict(cr.fetchall())
    done = 0
    with open(path, newline='') as f:
        for row in csv.DictReader(f):
            tid = tmap.get(int(row['team_x10'])) or tname.get((row['team_name'] or '').strip().upper())
            uid = umap.get(int(row['user_x10']))
            if not tid or not uid:
                continue
            cr.execute("UPDATE crm_team SET user_id=%s WHERE id=%s AND user_id IS NULL", (uid, tid)); done += cr.rowcount
    _logger.info("team leaders: %s set", done)


def migrate(cr, version):
    if not version:
        return
    if not _has_column(cr, 'sale_order', 'x_odoo10_id'):
        _logger.info("no x_odoo10_id columns: nothing to load")
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    _load_partner_routes(cr)
    _link_pickings(cr); _link_moves(cr); _fix_done_quantities(cr); _link_invoice_lines(cr)
    _load_team_leaders(cr)
    env.invalidate_all()
    _recompute(env)
