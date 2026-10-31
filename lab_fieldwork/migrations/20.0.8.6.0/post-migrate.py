# -*- coding: utf-8 -*-
"""Halts end with the day, a place nobody went to is off the trail (2026-10-31).

The last seven days with a trail are read again: their halts - a stop open at the end
of a day now ends at the last fix, and one inside a clinic or the pinned lab goes -
and their trips (until approved) and day sheets (until a desk signs them), without the
lone fixes drop_detours leaves out. Only roads already held are used; the five-minute
job measures the new stretches of the days still open.
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
        user = Users.browse(user_id)
        Ping._detect_halts(user, day)
        Ping._refresh_day_km(user, day)
    _logger.info("lab_fieldwork 20.0.8.6.0: halts and km read again for %d person-day(s)", len(days))
