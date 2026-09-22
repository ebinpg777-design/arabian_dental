# -*- coding: utf-8 -*-
"""Recount every stored daily target on the lab's day.

`done_count`, `achieved_pct` and `met` were counted in the timezone of whoever
refreshed them: the hourly cron runs as a user with no timezone, so its rows were
counted on UTC days while a manager's refresh used local ones, and each flipped the
other. They are now counted on the lab's one day (lab.station._lab_tz), so every
existing row is recounted once. (review, 2026-09-15)
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    rows = env['lab.work.target'].search([])
    rows.action_refresh()
    _logger.info("Daily targets: %s row(s) recounted on the lab's day", len(rows))
