# -*- coding: utf-8 -*-
"""What the phone said about itself (ADL Pro 1.0.9, 2026-10-24): location turned off in
its settings and on again, the location permission taken from the app, and the recorder
started again after the phone had stopped it. Client: "if any time location off in the
phone settings then that information needed" - a stretch with no route is explained by
the phone, not guessed at by the office."""
from datetime import timedelta

from odoo import api, fields, models

from .local_day import tz_name

KINDS = [
    ('location_off', "Location turned off in the phone's settings"),
    ('location_on', 'Location turned on again'),
    ('permission_off', 'Location permission taken from ADL Pro'),
    ('recorder_restarted', 'Recording started again after the phone stopped it'),
    # ADL Pro 1.0.13 (2026-10-28): a phone placing itself by Wi-Fi and towers records
    # nothing where the mobile data drops - battery saver taking the GPS away with the
    # screen, and the road with no satellite fix, said by the phone.
    ('gps_saver_on', 'Battery saver switched the GPS off'),
    ('gps_saver_off', 'Battery saver off: GPS back'),
    ('gps_silent', 'On the road with no GPS fix for 5 minutes'),
]


class LabLocationEvent(models.Model):
    _name = 'lab.location.event'
    _description = 'Field Phone Event'
    _order = 'ts desc, id desc'

    user_id = fields.Many2one('res.users', string='Executive', required=True, index=True,
                              ondelete='cascade')
    date = fields.Date(required=True, index=True,
                       help="The executive's own local day, as for the trail.")
    ts = fields.Datetime('When', required=True)
    kind = fields.Selection(KINDS, required=True)
    company_id = fields.Many2one('res.company', required=True, index=True,
                                 default=lambda self: self.env.company)

    _event_unique = models.Constraint(
        'unique(user_id, ts, kind)', 'That event is already recorded.')

    @api.model
    def _record(self, user, events):
        """[{'kind', 'ts'}] from the phone, each kept once - the app sends a batch again
        when an answer did not reach it. Returns how many were new."""
        known = dict(KINDS)
        zone = tz_name(self.env, user)
        horizon = fields.Datetime.now() + timedelta(minutes=5)
        Event = self.sudo()
        made = 0
        for event in events:
            kind, when = event.get('kind'), event.get('ts')
            if kind not in known or not when:
                continue
            try:
                when = fields.Datetime.to_datetime(when)
            except (TypeError, ValueError):
                continue
            if not when or when > horizon:
                continue
            if Event.search_count([('user_id', '=', user.id), ('ts', '=', when), ('kind', '=', kind)], limit=1):
                continue
            day = fields.Datetime.context_timestamp(self.with_context(tz=zone), when).date()
            Event.create({'user_id': user.id, 'date': day, 'ts': when, 'kind': kind,
                          'company_id': user.company_id.id})
            made += 1
        return made

    @api.model
    def _location_off_since(self, events):
        """The time location went off and has not come back on, or None - from a day's
        events in time order. A fix recorded after it is the phone's location back."""
        off = None
        for event in events:
            if event.kind == 'location_off':
                off = off or event.ts
            elif event.kind == 'location_on':
                off = None
        return off

    @api.model
    def _saver_on_since(self, events):
        """The time battery saver took the GPS away and has not given it back, or None
        (ADL Pro 1.0.13), from a day's events in time order."""
        since = None
        for event in events:
            if event.kind == 'gps_saver_on':
                since = since or event.ts
            elif event.kind == 'gps_saver_off':
                since = None
        return since
