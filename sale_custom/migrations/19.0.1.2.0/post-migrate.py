# -*- coding: utf-8 -*-
"""Continue the Odoo 10 numbering and carry the clinic -> Sales Route / Salesperson link.

1. Sequences. v10 ran `standard` (PostgreSQL) sequences, so `ir_sequence.number_next`
   in its table was STALE (SO: 131170, RE: 1) while the real counters stood at
   SO282464 / RE27678. The migration copied the stale numbers, so the first order
   registered in v19 came out as SO131170 and reworks took plain SO numbers. Here every
   order sequence is re-seeded from the highest number actually used, per prefix — which
   is also what the Aligners company needs: v10 numbered it ALGSO#### from its own
   sequence, and v19 had none, so it would have fallen into the SO range.

2. Partner routes. v10 stored the sales team on the partner and each order took it from
   there (23,211 of 23,439 orders in the last cycle). v19 dropped res.partner.team_id;
   `sale_custom` re-adds it and this loads the v10 values from the CSV shipped with the
   module (data/v10_partner_sales_route.csv, exported from the live v10 database on
   2026-08-17), matched through the migration's x_odoo10_id tags. The salesperson
   (res.partner.user_id, 387 partners in v10) comes along. Only EMPTY values are filled:
   anything set by hand since go-live is left alone.

Idempotent: re-running neither moves a sequence backwards nor overwrites a value.
"""
import csv
import logging
import os
import re

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

CSV_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'v10_partner_sales_route.csv')


def _max_number(cr, prefix, company_id=None):
    """Highest numeric suffix used by sale orders named <prefix><digits>, any state."""
    where = "name ~ %s"
    params = ['^' + re.escape(prefix) + r'[0-9]+$']
    if company_id:
        where += " AND company_id = %s"
        params.append(company_id)
    cr.execute(f"SELECT max(substring(name from '[0-9]+$')::bigint) FROM sale_order WHERE {where}", params)
    return cr.fetchone()[0] or 0


def _seed(seq, next_number):
    """Move a sequence forward to `next_number` (never backwards)."""
    if seq.number_next_actual < next_number:
        seq.sudo().write({'number_next_actual': next_number})
        _logger.info("sequence %s (%s, company %s): next -> %s",
                     seq.code, seq.prefix, seq.company_id.name or '-', next_number)


def _seed_sequences(env):
    cr = env.cr
    Seq = env['ir.sequence'].sudo().with_context(active_test=False)

    # Global (Arabian Dental Lab) counters, prefix SO / RE.
    for code, prefix in (('sale.order', 'SO'), ('sale.rework', 'RE')):
        seq = Seq.search([('code', '=', code), ('company_id', '=', False)], limit=1)
        if seq:
            _seed(seq, _max_number(cr, prefix) + 1)

    # Aligners company: its own ALGSO / ALGRE counters, as in v10 (company 9 there).
    Company = env['res.company'].sudo()
    aligners = env['res.company']
    if 'x_odoo10_id' in Company._fields:
        aligners = Company.search([('x_odoo10_id', '=', 9)], limit=1)
    if not aligners:
        aligners = Company.search([('name', 'ilike', 'Aligner')], limit=1)
    if not aligners:
        _logger.info("no Aligners company found: ALGSO/ALGRE sequences not created")
        return
    for code, prefix, label in (('sale.order', 'ALGSO', 'Sales Order (Aligners)'),
                                ('sale.rework', 'ALGRE', 'Sales Rework (Aligners)')):
        seq = Seq.search([('code', '=', code), ('company_id', '=', aligners.id)], limit=1)
        if not seq:
            seq = Seq.create({
                'name': label, 'code': code, 'prefix': prefix, 'padding': 3,
                'company_id': aligners.id, 'implementation': 'standard',
            })
        _seed(seq, _max_number(cr, prefix, aligners.id) + 1)


def _load_partner_routes(env):
    path = os.path.normpath(CSV_PATH)
    if not os.path.exists(path):
        _logger.warning("partner route CSV not found: %s", path)
        return
    Partner = env['res.partner'].sudo().with_context(active_test=False)
    Team = env['crm.team'].sudo().with_context(active_test=False)
    Users = env['res.users'].sudo().with_context(active_test=False)
    if 'x_odoo10_id' not in Partner._fields:
        _logger.warning("res.partner has no x_odoo10_id (lab_migration absent): routes not loaded")
        return

    env.cr.execute("SELECT x_odoo10_id, id FROM res_partner WHERE x_odoo10_id IS NOT NULL")
    partner_by_x10 = dict(env.cr.fetchall())
    team_by_x10 = {t.x_odoo10_id: t.id for t in Team.search([('x_odoo10_id', '!=', False)])}
    team_by_name = {}
    for t in Team.search([]):
        team_by_name.setdefault((t.name or '').strip().upper(), t.id)
    user_by_x10 = {u.x_odoo10_id: u.id for u in Users.search([('x_odoo10_id', '!=', False)])}

    # Current values, to fill only what is empty.
    env.cr.execute("SELECT id, team_id, user_id FROM res_partner")
    current = {pid: (tid, uid) for pid, tid, uid in env.cr.fetchall()}

    team_updates, user_updates = {}, {}
    missing_team, missing_user, missing_partner = set(), set(), 0
    with open(path, newline='') as f:
        for row in csv.DictReader(f):
            pid = partner_by_x10.get(int(row['partner_x10']))
            if not pid:
                missing_partner += 1
                continue
            cur_team, cur_user = current.get(pid, (None, None))
            if row['team_x10'] and not cur_team:
                tid = team_by_x10.get(int(row['team_x10'])) \
                    or team_by_name.get((row['team_name'] or '').strip().upper())
                if tid:
                    team_updates.setdefault(tid, []).append(pid)
                else:
                    missing_team.add(row['team_name'])
            if row['user_x10'] and not cur_user:
                uid = user_by_x10.get(int(row['user_x10']))
                if uid:
                    user_updates.setdefault(uid, []).append(pid)
                else:
                    missing_user.add(row['user_x10'])

    # Direct SQL: 6k partners, two columns, no chatter/tracking noise on each clinic.
    n_team = n_user = 0
    for tid, pids in team_updates.items():
        env.cr.execute("UPDATE res_partner SET team_id=%s WHERE id = ANY(%s) AND team_id IS NULL", (tid, pids))
        n_team += env.cr.rowcount
    for uid, pids in user_updates.items():
        env.cr.execute("UPDATE res_partner SET user_id=%s WHERE id = ANY(%s) AND user_id IS NULL", (uid, pids))
        n_user += env.cr.rowcount
    Partner.invalidate_model(['team_id', 'user_id'])
    _logger.info("partner routes: team set on %s, salesperson set on %s; "
                 "unmatched partners %s, unknown teams %s, unknown users %s",
                 n_team, n_user, missing_partner, sorted(missing_team), sorted(missing_user))


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    _seed_sequences(env)
    _load_partner_routes(env)
