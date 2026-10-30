# -*- coding: utf-8 -*-
"""The rough fixes between satellite ones are off the trail (2026-10-29, drop_headless).

The last seven days with a trail are read again: their trips (until approved) and day
sheets (until a desk signs them) take the km without the Wi-Fi and tower positions a
phone sent between its satellite fixes. Only roads already held are used; the
five-minute job measures the new stretches of today and yesterday along their roads.
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
    _logger.info("lab_fieldwork 20.0.8.3.0: km read again for %d person-day(s), rough fixes left out", len(days))
