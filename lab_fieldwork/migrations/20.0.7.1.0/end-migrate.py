# -*- coding: utf-8 -*-
"""A trail's km is travel now - in a vehicle, or blind - never a walk (2026-10-23).

The trips and day sheets written before this carried the trail's whole length,
walks included. Every day whose pings are still kept is read again, once; a day
with nothing left to read keeps the figure it had. An END script: the whole
registry is loaded, so the trip and the sheet compute exactly as they will from
now on.
"""
import logging

from odoo import SUPERUSER_ID, api, fields

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Ping = env['lab.location.ping']
    Users = env['res.users'].with_context(active_test=False)
    cr.execute("SELECT DISTINCT user_id, date FROM lab_location_ping ORDER BY date, user_id")
    known, sheets = {}, 0
    days = cr.fetchall()
    if not days:
        return
    for user_id, day in days:
        summary = Ping._day_summary(Users.browse(user_id), day)
        known['%s/%s' % (user_id, fields.Date.to_string(day))] = summary['gps_km']
        sheet = env['lab.daily.update'].search([('user_id', '=', user_id), ('date', '=', day)])
        if sheet:
            sheet.write({key: summary[key] for key in ('gps_km', 'foot_km', 'blind_km')})
            sheets += len(sheet)
    trips = env['lab.trip'].search([('date', '>=', days[0][1]), ('user_id', '!=', False)]).filtered(
        lambda t: '%s/%s' % (t.user_id.id, fields.Date.to_string(t.date)) in known)
    trips.with_context(fw_trail_km=known)._compute_gps_km()
    _logger.info("lab_fieldwork 20.0.7.1.0: %d day(s) of trail read again as travel: "
                 "%d day sheet(s), %d trip(s)", len(known), sheets, len(trips))
