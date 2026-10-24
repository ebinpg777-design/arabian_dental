# -*- coding: utf-8 -*-
"""A stretch the phone did not record counts its road (2026-10-24).

The last seven days with a trail are read again: their trips (until approved) and
day sheets (until a desk signs them) take the km with each blind stretch measured
along the road already known for it, instead of the straight line across. Only roads
already held are used - nothing is asked of the routing service during the upgrade;
the five-minute job measures the rest of today and yesterday.
"""
import logging
from datetime import timedelta

from odoo import SUPERUSER_ID, api, fields

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Ping = env['lab.location.ping']
    Users = env['res.users'].with_context(active_test=False)
    since = fields.Date.today() - timedelta(days=7)
    cr.execute("SELECT DISTINCT user_id, date FROM lab_location_ping WHERE date >= %s ORDER BY date", [since])
    days = cr.fetchall()
    for user_id, day in days:
        Ping._refresh_day_km(Users.browse(user_id), day)
    _logger.info("lab_fieldwork 20.0.7.4.0: km read again for %d person-day(s), gaps along their roads", len(days))
