# -*- coding: utf-8 -*-
"""20.0.9.2.0: a browser's heartbeat on waking is off the trail (2026-11-12).

The days of the last week that hold positions sent by a browser are read again
without the places a sleeping page repeated on waking (location_live.drop_stale):
their halts, their trips (until approved) and their day sheets (until a desk signs
them). Only roads already held are used here; the five-minute job measures the
stretches that are new - from the fix before each silence to the true one after it
- on the days whose trip is still open or closed, and reads the km once more.
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
    cr.execute("""SELECT DISTINCT user_id, date FROM lab_location_ping
                   WHERE date >= %s AND source = 'browser' ORDER BY date""", [since])
    days = cr.fetchall()
    for user_id, day in days:
        user = Users.browse(user_id)
        Ping._detect_halts(user, day)
        Ping._refresh_day_km(user, day)
    _logger.info("lab_fieldwork 20.0.9.2.0: halts and km read again for %d browser-recorded person-day(s)",
                 len(days))
