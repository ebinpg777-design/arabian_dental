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
from .local_day import local_midnight_utc, tz_name
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
# What one person's phone may write in a day, however often it asks. 4000 since
# 2026-10-23: the app samples every 10 s from 1.0.3, and 2000 was five and a half
# hours of driving - the evening was dropped.
MAX_PINGS_PER_DAY = 4000
MOTIONS = [('vehicle', 'In a Vehicle'), ('foot', 'On Foot'), ('still', 'Still')]


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
    # The live layer (location_live.py, 2026-10-19): how fast, which way, how much
    # phone was left. Speed comes from the phone when it has one, else from the
    # distance to the previous kept ping; over the limit it is flagged for the map.
    speed_kmh = fields.Float('Speed (km/h)', digits=(6, 1))
    motion = fields.Selection(
        MOTIONS, readonly=True,
        help="What the phone's own motion sensors said the person was doing at this fix "
             "(the Lab app from 1.0.4, Traccar Client v9). Empty when the phone did not "
             "say: the trail then tells a drive from a walk by speed alone.")
    heading = fields.Float(digits=(5, 1))
    battery = fields.Float('Phone Battery (%)', digits=(5, 1))
    overspeed = fields.Boolean(index=True)
    # Where it came from: the browser while My Day is open, or the Traccar Client app
    # (controllers/gps.py), which keeps sending with the screen off. (2026-10-19)
    source = fields.Selection([('browser', 'Browser'), ('app', 'Tracking app')],
                              default='browser', required=True)
    company_id = fields.Many2one('res.company', required=True, index=True,
                                 default=lambda self: self.env.company)

    _ping_unique = models.Constraint(
        'unique(user_id, ts)', 'That position has already been recorded.')

    # ---------------------------------------------------------------- settings
    @api.model
    def _tracking_enabled(self):
        return self.env['ir.config_parameter'].sudo().get_str(
            'lab_fieldwork.track_live', 'True') in ('True', 'true', '1')

    @api.model
    def _keep_days(self):
        try:
            return max(1, int(self.env['ir.config_parameter'].sudo().get_str(
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
            extra = {}
            for key in ('speed_kmh', 'heading', 'battery'):
                try:
                    value = ping.get(key)
                    # `0.0 in (None, '', False)` is True in Python: a phone standing
                    # still, reporting 0 km/h, had its speed thrown away and a speed
                    # worked out from the ground covered instead - after a GPS jump,
                    # 88 km/h on a phone that had not moved (2026-10-26).
                    extra[key] = None if value is None or value is False or value == '' else float(value)
                except (TypeError, ValueError):
                    extra[key] = None
            motion = ping.get('motion')
            extra['motion'] = motion if motion in dict(MOTIONS) else False
            cleaned.append((when, latitude, longitude, accuracy, extra))
        if not cleaned:
            return 0
        cleaned.sort(key=lambda row: row[0])

        # One day per batch is the normal case; a batch that straddles midnight is
        # split rather than filed under the day it was sent.
        kept = 0
        by_day = {}
        # tz_name, not `user.tz or 'UTC'`: most of the lab's users carry no timezone,
        # and UTC filed the evening's pings under tomorrow (found 2026-10-19).
        zone = tz_name(self.env, user)
        for when, latitude, longitude, accuracy, extra in cleaned:
            local_day = fields.Datetime.context_timestamp(
                self.with_context(tz=zone), when).date()
            by_day.setdefault(local_day, []).append((when, latitude, longitude, accuracy, extra))

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
            last_speed = (last.speed_kmh or 0.0) if last else 0.0
            limit = self._speed_limit()
            # A speed the phone did not give is the ground covered since the last
            # fix that sits on a road - never since a tower position: 2 km "covered"
            # from a tower in 30 s read as 240 km/h on the next real fix.
            trail_limit = self._trail_accuracy()
            good = last if last and (not trail_limit or not last.accuracy_m
                                     or last.accuracy_m < trail_limit) else \
                self.sudo().search([('user_id', '=', user.id), ('date', '=', day),
                                    '|', ('accuracy_m', '=', 0),
                                    ('accuracy_m', '<', trail_limit or 1e9)],
                                   order='ts desc', limit=1)
            good_point = (good.ts, good.latitude, good.longitude) if good else None

            values = []
            held = None                   # the day's fixes already held, read once, for late ones
            for when, latitude, longitude, accuracy, extra in rows:
                if not self._on_duty_at(windows, when):
                    continue
                late = bool(last_ts) and when <= last_ts
                if late:
                    # Taken BEFORE the newest fix held: a phone back in coverage sending
                    # what it took meanwhile, after something newer got in first (a
                    # browser fix, a second phone). Until 2026-10-24 this was read as
                    # "already have this stretch" and dropped - the network coming back
                    # left the gap it had made. Only a fix held at that very second is
                    # a retry; the rest are filed in their place, judged against the
                    # held fix just before each.
                    if held is None:
                        held = self.sudo().search_read(
                            [('user_id', '=', user.id), ('date', '=', day)],
                            ['ts', 'latitude', 'longitude', 'accuracy_m', 'speed_kmh'], order='ts')
                    before = [h for h in held if h['ts'] <= when]
                    if before and (when - before[-1]['ts']).total_seconds() < 1.0:
                        continue                  # held already: a retry
                    prev = before[-1] if before else None
                    prev_ts = prev['ts'] if prev else None
                    prev_point = (prev['latitude'], prev['longitude']) if prev else None
                    prev_good = prev_point and (not trail_limit or not prev['accuracy_m']
                                                or prev['accuracy_m'] < trail_limit)
                    prev_good_point = (prev_ts,) + prev_point if prev_good else None
                    prev_speed = (prev or {}).get('speed_kmh') or 0.0
                else:
                    prev_ts, prev_point, prev_good_point = last_ts, last_point, good_point
                    prev_speed = last_speed
                moved = waited = 0.0
                if prev_point:
                    moved = metres_between(prev_point[0], prev_point[1],
                                           latitude, longitude) or 0.0
                    waited = (when - prev_ts).total_seconds() if prev_ts else 0
                    if moved < MIN_MOVE_METRES and waited < HEARTBEAT_SECONDS:
                        continue
                speed = extra.get('speed_kmh')
                derived = speed is None
                if derived and prev_good_point and when > prev_good_point[0]:
                    # the ground covered, when the phone gave none
                    speed = (metres_between(prev_good_point[1], prev_good_point[2], latitude, longitude) or 0.0) \
                        / (when - prev_good_point[0]).total_seconds() * 3.6
                speed = round(max(speed or 0.0, 0.0), 1)
                # A speed the phone read over the limit is over the limit. One worked
                # out from the ground covered is a GPS error as often as a hurry - a
                # fix 200 m off the road reads as 70 km/h - so it counts only when the
                # step before was over the limit too (2026-10-26: "18 times over
                # 60 km/h" on a morning with nine jumps the phone never made).
                over = bool(limit) and limit < speed < 150
                if over and derived:
                    over = prev_speed > limit
                values.append({
                    'user_id': user.id, 'date': day, 'ts': when,
                    'latitude': latitude, 'longitude': longitude,
                    'accuracy_m': accuracy, 'company_id': company.id,
                    'speed_kmh': speed,
                    'heading': extra.get('heading') or 0.0,
                    'battery': extra.get('battery') or 0.0,
                    'motion': extra.get('motion') or False,
                    # a GPS jump reads as 300 km/h; a real motorbike never does
                    'overspeed': over,
                    # set only by the app endpoint: the browser cannot claim to be the app
                    'source': 'app' if self.env.context.get('fw_ping_source') == 'app' else 'browser',
                })
                if late:
                    # a second late fix of the batch is judged against this one
                    held.append({'ts': when, 'latitude': latitude, 'longitude': longitude,
                                 'accuracy_m': accuracy})
                    held.sort(key=lambda h: h['ts'])
                else:
                    last_ts, last_point, last_speed = when, (latitude, longitude), speed
                    if not trail_limit or not accuracy or accuracy < trail_limit:
                        good_point = (when, latitude, longitude)
                if existing + len(values) >= MAX_PINGS_PER_DAY:
                    break
            if values:
                # sudo on the create: the executive is entitled to record where they
                # are, and the ACL grants exactly that — but the batch is written in
                # one go and must not half-fail on a company rule.
                self.sudo().create(values)
                kept += len(values)
                # the live layer: last position, auto-arrival, halts, the trip's km
                self.sudo()._after_record(user, day, values)
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
                self.with_context(tz=tz_name(self.env, user)), ping.ts)
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
        # An excused phone is never watched: the trail is a location too.
        enabled = pings._tracking_enabled() and \
            not self.env.user._fw_phone_exceptions()['location']
        try:
            interval = max(30, int(self.env['ir.config_parameter'].sudo().get_str(
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
