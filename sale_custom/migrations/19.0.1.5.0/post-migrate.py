# -*- coding: utf-8 -*-
"""Team Leaders of the Sales Routes, from Odoo 10.

The salesperson of a new order / invoice now comes from the leader of the clinic's Sales
Route (sale_custom.sale_order / account_move); the leaders were never migrated (the
crm.team spec carried name/phone/address only). This loads them from
data/v10_team_leaders.csv (v10 crm_team.user_id, exported 2026-08-17), matching teams and
users through their x_odoo10_id tags (team by name as a fallback), and only where the
team has no leader yet.
"""
import csv
import logging
import os

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

CSV_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'v10_team_leaders.csv')


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Team = env['crm.team'].sudo().with_context(active_test=False)
    Users = env['res.users'].sudo().with_context(active_test=False)
    if 'x_odoo10_id' not in Team._fields or 'x_odoo10_id' not in Users._fields:
        _logger.info("no migration tags: team leaders not loaded")
        return
    path = os.path.normpath(CSV_PATH)
    if not os.path.exists(path):
        _logger.warning("team leader CSV not found: %s", path)
        return
    team_by_x10 = {t.x_odoo10_id: t for t in Team.search([('x_odoo10_id', '!=', False)])}
    team_by_name = {}
    for t in Team.search([]):
        team_by_name.setdefault((t.name or '').strip().upper(), t)
    user_by_x10 = {u.x_odoo10_id: u for u in Users.search([('x_odoo10_id', '!=', False)])}
    done = skipped = 0
    with open(path, newline='') as f:
        for row in csv.DictReader(f):
            team = team_by_x10.get(int(row['team_x10'])) or team_by_name.get((row['team_name'] or '').strip().upper())
            user = user_by_x10.get(int(row['user_x10']))
            if not team or not user or team.user_id:
                skipped += 1
                continue
            team.user_id = user
            done += 1
    _logger.info("team leaders: %s set, %s rows skipped (unknown or already set)", done, skipped)
