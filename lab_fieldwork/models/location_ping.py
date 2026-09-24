# -*- coding: utf-8 -*-
"""Where an executive was between the doors.

The day sheet already shows every place the phone was *asked* for a position — a
check-in, a check-out, a delivery. Between those it says nothing, so a round that went
the long way round, or sat somewhere for two hours, or never left the first town, reads
exactly like one that did not.

This is the trail between them: while the executive is on duty, the phone offers its
position and the ones that show movement are kept.

Three rules hold it honest, and all three are enforced here rather than promised by the
phone app:

* **On duty only.** A ping is kept only if its own timestamp falls inside one of that
  day's attendance intervals. Start the day and the trail begins; end the day and it
  stops, whatever the app keeps sending.
* **Movement, not surveillance-by-volume.** A phone sitting on a desk repeats itself;
  those are dropped. A ping is kept when it is far enough from the last one to mean
  the person moved, or when enough time has passed to be worth a heartbeat.
* **Not kept forever.** A cron drops trails older than the retention window.

What this is NOT: background tracking. A browser only runs while its page is open, so a
locked phone or a switched app suspends the watch — the trail then has a gap, which is
shown as a gap rather than drawn over with a straight line. Genuine background location
needs a native app, and is a bigger decision than a map.
"""
import logging
from datetime import timedelta

from .lab_visit import has_fix, metres_between
from .local_day import local_midnight_utc
from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# A fix has to differ from the last one by this much before it is worth keeping: a
# stationary phone drifts by tens of metres and would otherwise write a row a minute
# from a desk.
MIN_MOVE_METRES = 50
# ...but a long stop is a fact too, so one ping is kept every so often regardless.
HEARTBEAT_SECONDS = 300
# A fix this vague is a cell tower, not a person; it can land in the wrong town.
WORST_ACCURACY_M = 250
# What one person's phone may write in a day, however often it asks.
MAX_PINGS_PER_DAY = 2000


class LabLocationPing(models.Model):
    _name = 'lab.location.ping'
    _description = 'Field Location Trail'
    _order = 'ts'
    # Never in the chatter, never a note: this is a stream, not a document.
    _log_access = True

    user_id = fields.Many2one('res.users', string='Executive', required=True,
                              index=True, ondelete='cascade')
    date = fields.Date(required=True, index=True,
                       help="The executive's own local day, so a trail is read "
                            "against the day sheet for the same date.")
    ts = fields.Datetime('Taken At', required=True, index=True)
    latitude = fields.Float(digits=(10, 7), required=True)
    longitude = fields.Float(digits=(10, 7), required=True)
    accuracy_m = fields.Float('Fix Accuracy (m)')
    company_id = fields.Many2one('res.company', required=True, index=True,
                                 default=lambda self: self.env.company)

    _ping_unique = models.Constraint(
        'unique(user_id, ts)', 'That position has already been recorded.')

    # ---------------------------------------------------------------- settings
    @api.model
    def _tracking_enabled(self):
        return self.env['ir.config_parameter'].sudo().get_param(
            'lab_fieldwork.track_live', 'True') in ('True', 'true', '1')

    @api.model
    def _keep_days(self):
        try:
            return max(1, int(self.env['ir.config_parameter'].sudo().get_param(
                'lab_fieldwork.track_keep_days', '90')))
        except (TypeError, ValueError):
            return 90

    # ---------------------------------------------------------------- on duty
    @api.model
    def _duty_windows(self, user, day):
        """The stretches of `day` the executive was actually on duty.

        Read from `hr.attendance`, which is where "Start my day" writes — so the
        privacy boundary is the same record the payroll uses, not a second opinion.
        An open attendance (started, not yet ended) runs to now.
        """
        employee = user.sudo()._fw_employee()
        if not employee:
            return []
        start = local_midnight_utc(self.env, day, user)
        end = local_midnight_utc(self.env, day + timedelta(days=1), user)
        attendances = self.env['hr.attendance'].sudo().search([
            ('employee_id', '=', employee.id),
            ('check_in', '<', end),
            '|', ('check_out', '=', False), ('check_out', '>', start),
        ])
        now = fields.Datetime.now()
        return [(a.check_in, a.check_out or now) for a in attendances]

    @api.model
    def _on_duty_at(self, windows, when):
        return any(start <= when <= stop for start, stop in windows)

    # ---------------------------------------------------------------- writing
    @api.model
    def record(self, pings):
        """Take a batch of positions from the phone. Returns how many were kept.

        Deliberately forgiving about what it is sent and strict about what it stores:
        a phone on a bad connection retries, sends duplicates, and sometimes sends a
        fix it took ten minutes ago. None of that may raise — an executive must never
        meet an error dialog because a background POST disagreed with the server — so
        anything unusable is dropped quietly and counted.
        """
        if not pings or not self._tracking_enabled():
            return 0
        user = self.env.user
        company = self.env.company

        cleaned = []
        for ping in pings:
            try:
                latitude = float(ping.get('latitude'))
                longitude = float(ping.get('longitude'))
                when = fields.Datetime.to_datetime(ping.get('ts'))
            except (TypeError, ValueError):
                continue
            if not when or not has_fix(latitude, longitude):
                continue
            accuracy = float(ping.get('accuracy_m') or 0.0)
            if accuracy and accuracy > WORST_ACCURACY_M:
                continue
            cleaned.append((when, latitude, longitude, accuracy))
        if not cleaned:
            return 0
        cleaned.sort()

        # One day per batch is the normal case; a batch that straddles midnight is
        # split rather than filed under the day it was sent.
        kept = 0
        by_day = {}
        for when, latitude, longitude, accuracy in cleaned:
            local_day = fields.Datetime.context_timestamp(
                self.with_context(tz=user.tz or 'UTC'), when).date()
            by_day.setdefault(local_day, []).append((when, latitude, longitude, accuracy))

        for day, rows in by_day.items():
            windows = self._duty_windows(user, day)
            if not windows:
                continue
            existing = self.sudo().search_count([('user_id', '=', user.id),
                                                 ('date', '=', day)])
            if existing >= MAX_PINGS_PER_DAY:
                continue
            last = self.sudo().search([('user_id', '=', user.id), ('date', '=', day)],
                                      order='ts desc', limit=1)
            last_ts = last.ts
            last_point = (last.latitude, last.longitude) if last else None

            values = []
            for when, latitude, longitude, accuracy in rows:
                if not self._on_duty_at(windows, when):
                    continue
                if last_ts and when <= last_ts:
                    continue                      # already have this stretch
                if last_point:
                    moved = metres_between(last_point[0], last_point[1],
                                           latitude, longitude) or 0.0
                    waited = (when - last_ts).total_seconds() if last_ts else 0
                    if moved < MIN_MOVE_METRES and waited < HEARTBEAT_SECONDS:
                        continue
                values.append({
                    'user_id': user.id, 'date': day, 'ts': when,
                    'latitude': latitude, 'longitude': longitude,
                    'accuracy_m': accuracy, 'company_id': company.id,
                })
                last_ts, last_point = when, (latitude, longitude)
                if existing + len(values) >= MAX_PINGS_PER_DAY:
                    break
            if values:
                # sudo on the create: the executive is entitled to record where they
                # are, and the ACL grants exactly that — but the batch is written in
                # one go and must not half-fail on a company rule.
                self.sudo().create(values)
                kept += len(values)
        return kept

    # ---------------------------------------------------------------- reading
    @api.model
    def trail_for(self, user, day):
        """The day's trail as [[lat, lon, 'HH:MM'], ...], oldest first."""
        pings = self.sudo().search([('user_id', '=', user.id), ('date', '=', day)],
                                   order='ts')
        out = []
        for ping in pings:
            local = fields.Datetime.context_timestamp(
                self.with_context(tz=user.tz or 'UTC'), ping.ts)
            out.append([ping.latitude, ping.longitude, local.strftime('%H:%M')])
        return out

    # ---------------------------------------------------------------- housekeeping
    @api.model
    def _cron_prune(self):
        """Drop trails past the retention window."""
        cutoff = fields.Date.context_today(self) - timedelta(days=self._keep_days())
        old = self.sudo().search([('date', '<', cutoff)], limit=50000)
        count = len(old)
        if count:
            old.unlink()
            _logger.info("location trail: pruned %s ping(s) older than %s", count, cutoff)
        return count


class LabMyDay(models.AbstractModel):
    _inherit = 'lab.my.day'

    @api.model
    def _attendance_payload(self):
        """Tell the card whether the route is being recorded.

        It rides on the attendance payload rather than on a second call because that
        payload is what the card re-reads every time the day is started or ended — so
        the light comes on and goes off with the button, not five minutes later.
        """
        payload = super()._attendance_payload()
        payload['tracking'] = bool(
            self.env['lab.location.ping']._tracking_enabled()
            and payload.get('state') == 'checked_in')
        return payload

    @api.model
    def tracking_state(self):
        """Whether the phone should be watching the executive's position right now.

        The browser asks this when the web client loads, again on a timer, and once
        more each time the attendance button is pressed. It is the only thing that
        turns the watch on, which keeps one rule in one place: a day that was ended on
        another device, or tracking switched off by the lab this morning, stops the
        watch on this one within a few minutes without anybody signing out.
        """
        pings = self.env['lab.location.ping']
        payload = self._attendance_payload()
        enabled = pings._tracking_enabled()
        try:
            interval = max(30, int(self.env['ir.config_parameter'].sudo().get_param(
                'lab_fieldwork.track_interval', '90')))
        except (TypeError, ValueError):
            interval = 90
        return {
            'enabled': enabled,
            'on_duty': payload.get('state') == 'checked_in',
            'interval': interval,
            # The executive is told, in the card, that this is running. A trail nobody
            # mentions is a trap; one with a light on it is a tool.
            'notice': _("Your route is recorded while your day is running.")
            if enabled else '',
        }
