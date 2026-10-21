# -*- coding: utf-8 -*-
"""Where the field is RIGHT NOW - the live layer over the trail.

The trail (location_ping.py) already records where an executive's phone went while
their day was running, and the day sheet draws it afterwards. This file is everything
that reads the same pings while the day is still going on (client, 2026-10-19:
"implement live GPS in field executive visits and travel", with the gas-delivery
project's live map as the pattern):

* **A last position on the user** - six small fields written forward-only with every
  batch, so the live map and the desk board read one row per executive instead of
  the day's trail.
* **A status nobody types**: moving / at a clinic / stopped / offline / off, from the
  age of the last fix, the speed and the open visit.
* **Arrival the trail noticed.** A ping inside a planned clinic's radius stamps the
  visit `auto_arrived_at`; one well outside an open visit's radius stamps
  `auto_left_at`. Neither changes the visit - My Day nudges the executive to check in
  or out, because a check-in is an act and a note, not a geofence.
* **Halts**: stopped ten minutes or more somewhere that is not a clinic, as records
  (`lab.location.halt`), re-read idempotently.
* **The trip's km from the trail**, beside the odometer claim.
* **`get_live_map`**: one call that gives the manager's map (and the executive's own
  mini-map) everything: positions, trails split at recording gaps, doors, halts,
  over-speed points, the next clinic with an ETA.

All of it is derived from pings that only exist while the executive is on duty, so
the privacy boundary stays the one the trail already has.
"""
import logging
import secrets
from datetime import datetime, timedelta

from .field_app import APP_LIVE_SECONDS
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .lab_visit import has_fix, metres_between
from .local_day import local_midnight_utc, tz_name

_logger = logging.getLogger(__name__)

# One colour per executive on the map, by position in the day's roster.
PALETTE = ['#2563eb', '#dc2626', '#059669', '#d97706', '#7c3aed', '#db2777',
           '#0891b2', '#65a30d', '#ea580c', '#4f46e5', '#0d9488', '#be123c']
MOVING_KMH = 4.0            # below this a fix is "standing", whatever the GPS jitter says
JITTER_M = 15.0             # two fixes closer than this are the same place
MAX_KMH = 150.0             # a jump faster than this is a GPS error, not a journey
TRAIL_GAP_SECONDS = 900     # silence longer than this breaks the trail into a new leg
MAX_TRAIL_POINTS = 1500     # what the browser is sent per executive per day
MAX_OVERSPEED_POINTS = 400
HALT_RADIUS_M = 60.0        # how far a "stopped" phone may drift and still be stopped
HALT_SPEED_KMH = 5.0
NEAR_CLINIC_M = 500.0       # a halt this close to the day's clinic is named after it
LEAVE_MARGIN_M = 150.0      # outside radius + this = left the clinic
ROAD_FACTOR = 1.3           # straight line -> road, for the ETA
ETA_FLOOR_KMH = 20.0        # an executive who just stopped is not stuck there


def split_legs(points, gap_seconds=TRAIL_GAP_SECONDS):
    """[[point, ...], ...] and [(from, to)] - the trail cut wherever the recording
    stopped for longer than `gap_seconds`. A point is (ts, lat, lon, speed)."""
    legs, gaps, leg = [], [], []
    last = None
    for point in points:
        if last is not None and (point[0] - last[0]).total_seconds() > gap_seconds:
            gaps.append((last[0], point[0]))
            if leg:
                legs.append(leg)
            leg = []
        leg.append(point)
        last = point
    if leg:
        legs.append(leg)
    return legs, gaps


def trail_km(points):
    """Distance along the trail, skipping jitter and jumps, and never across a gap."""
    km = 0.0
    for leg in split_legs(points)[0]:
        for a, b in zip(leg, leg[1:]):
            metres = metres_between(a[1], a[2], b[1], b[2]) or 0.0
            seconds = (b[0] - a[0]).total_seconds() or 1.0
            if metres < JITTER_M or metres / seconds * 3.6 > MAX_KMH:
                continue
            km += metres / 1000.0
    return round(km, 2)


def downsample(points, limit=MAX_TRAIL_POINTS):
    """At most `limit` points, the last one always kept."""
    if len(points) <= limit:
        return points
    step = -(-len(points) // limit)
    kept = points[::step]
    if kept[-1] is not points[-1]:
        kept.append(points[-1])
    return kept


def epoch(when):
    return int(when.replace(tzinfo=None).timestamp() - datetime.utcfromtimestamp(0).timestamp()) \
        if when else None


class LabLocationPingLive(models.Model):
    _inherit = 'lab.location.ping'

    # ---------------------------------------------------------------- settings
    @api.model
    def _param_int(self, key, default):
        try:
            return int(self.env['ir.config_parameter'].sudo().get_str(key, str(default)))
        except (TypeError, ValueError):
            return default

    @api.model
    def _speed_limit(self):
        return max(0, self._param_int('lab_fieldwork.speed_limit', 60))

    @api.model
    def _halt_minutes(self):
        return max(3, self._param_int('lab_fieldwork.halt_minutes', 10))

    @api.model
    def _stale_minutes(self):
        return max(1, self._param_int('lab_fieldwork.ping_stale_minutes', 5))

    @api.model
    def _offline_minutes(self):
        return max(self._stale_minutes() + 1, self._param_int('lab_fieldwork.ping_offline_minutes', 30))

    @api.model
    def _auto_arrive_enabled(self):
        return self.env['ir.config_parameter'].sudo().get_str(
            'lab_fieldwork.auto_arrive', 'True') in ('True', 'true', '1')

    # ---------------------------------------------------------------- reading the day
    @api.model
    def _day_points(self, user, day):
        """The day's trail as [(ts, lat, lon, speed_kmh, overspeed, battery), ...]."""
        pings = self.sudo().search_read(
            [('user_id', '=', user.id), ('date', '=', day)],
            ['ts', 'latitude', 'longitude', 'speed_kmh', 'overspeed', 'battery', 'accuracy_m', 'source'],
            order='ts')
        return [(p['ts'], p['latitude'], p['longitude'], p['speed_kmh'] or 0.0,
                 bool(p['overspeed']), p['battery'] or 0.0, p['accuracy_m'] or 0.0,
                 p['source'] or 'browser')
                for p in pings]

    @api.model
    def _trail_km_for(self, user, day):
        return trail_km(self._day_points(user, day))

    @api.model
    def _day_summary(self, user, day):
        """What the day sheet keeps from the trail."""
        points = self._day_points(user, day)
        halts = self.env['lab.location.halt'].sudo().search(
            [('user_id', '=', user.id), ('date', '=', day)])
        return {
            'gps_km': trail_km(points),
            'halt_minutes': sum(halts.mapped('minutes')),
            'overspeed_count': sum(1 for p in points if p[4]),
        }

    @api.model
    def _today_for(self, user):
        return fields.Datetime.context_timestamp(
            self.with_context(tz=tz_name(self.env, user)), fields.Datetime.now()).date()

    # ---------------------------------------------------------------- after a batch
    @api.model
    def _after_record(self, user, day, values):
        """Everything that follows from a batch just written. Never raises: the
        executive's POST must not fail because a side effect disagreed."""
        try:
            self._touch_last_fix(user, values)
            if day == self._today_for(user):
                self._auto_visits(user, day, values)
                self._detect_halts(user, day)
                self._refresh_trip_km(user, day)
        except Exception:                                      # noqa: BLE001
            _logger.exception("live layer: after-record step failed for %s", user.login)

    @api.model
    def _touch_last_fix(self, user, values):
        """The user's last position, only ever moved forward in time."""
        latest = max(values, key=lambda v: v['ts'])
        user = user.sudo()
        if user.fw_last_ping_at and user.fw_last_ping_at >= latest['ts']:
            return
        user.write({
            'fw_last_ping_at': latest['ts'],
            'fw_last_lat': latest['latitude'], 'fw_last_lon': latest['longitude'],
            'fw_last_speed': latest.get('speed_kmh') or 0.0,
            'fw_last_accuracy': latest.get('accuracy_m') or 0.0,
            # a phone that reports no battery keeps the last reading
            'fw_last_battery': latest.get('battery') or user.fw_last_battery or 0.0,
        })

    @api.model
    def _visit_radius(self, clinic):
        default = float(self.env['ir.config_parameter'].sudo().get_str(
            'lab_fieldwork.visit_radius_m', 300))
        return clinic.visit_radius_m or default

    @api.model
    def _auto_visits(self, user, day, values):
        """Stamp what the trail saw on today's visits - arrival at a planned clinic,
        departure from an open one. Nudges, not actions (see the module docstring)."""
        if not self._auto_arrive_enabled():
            return
        Visit = self.env['lab.visit'].sudo()
        visits = Visit.search([('user_id', '=', user.id), ('date', '=', day),
                               ('state', 'in', ('planned', 'open'))])
        visits = visits.filtered(lambda v: v.partner_id.is_geolocated)
        if not visits:
            return
        rows = sorted(values, key=lambda v: v['ts'])
        for visit in visits:
            clinic = visit.partner_id
            radius = self._visit_radius(clinic)
            for row in rows:
                metres = metres_between(row['latitude'], row['longitude'],
                                        clinic.partner_latitude, clinic.partner_longitude)
                if metres is None:
                    continue
                if visit.state == 'planned' and not visit.check_in:
                    if metres <= radius and not visit.auto_arrived_at:
                        visit.auto_arrived_at = row['ts']
                elif visit.state == 'open' and visit.check_in and not visit.check_out:
                    if metres > radius + LEAVE_MARGIN_M:
                        if not visit.auto_left_at:
                            visit.auto_left_at = row['ts']
                    elif visit.auto_left_at:
                        visit.auto_left_at = False     # came back: it was a walk, not a leave

    @api.model
    def _clinic_zones(self, user, day):
        """[(lat, lon, radius, name)] of the day's clinics and the lab itself - the
        places a long stop is NOT a halt."""
        zones = []
        visits = self.env['lab.visit'].sudo().search(
            [('user_id', '=', user.id), ('date', '=', day), ('state', '!=', 'cancel')])
        for clinic in visits.mapped('partner_id').filtered('is_geolocated'):
            zones.append((clinic.partner_latitude, clinic.partner_longitude,
                          self._visit_radius(clinic), clinic.display_name))
        lab = self.env.company.partner_id
        if has_fix(lab.partner_latitude, lab.partner_longitude):
            zones.append((lab.partner_latitude, lab.partner_longitude, 300.0, self.env.company.name))
        return zones

    @api.model
    def _detect_halts(self, user, day):
        """Stretches of the day's trail where the phone sat still away from any
        clinic. Re-reading the day never doubles a halt: they are keyed by when they
        began, and an open one is extended or closed in place."""
        points = self._day_points(user, day)
        if len(points) < 2:
            return self.env['lab.location.halt']
        minimum = timedelta(minutes=self._halt_minutes())
        zones = self._clinic_zones(user, day)
        found = []          # (started, ended or None, lat, lon)
        anchor = None       # (ts, lat, lon)
        last_ts = points[0][0]
        for ts, lat, lon, speed, _over, _batt, _acc, _src in points:
            still = speed < HALT_SPEED_KMH and (
                anchor is None or (metres_between(anchor[1], anchor[2], lat, lon) or 0.0) <= HALT_RADIUS_M)
            if anchor is not None and (ts - last_ts).total_seconds() > TRAIL_GAP_SECONDS:
                still = False                  # a recording gap ends the stop; we cannot know
            if still:
                if anchor is None:
                    anchor = (ts, lat, lon)
            else:
                if anchor is not None and last_ts - anchor[0] >= minimum:
                    found.append((anchor[0], last_ts, anchor[1], anchor[2]))
                anchor = (ts, lat, lon) if speed < HALT_SPEED_KMH else None
            last_ts = ts
        now = fields.Datetime.now()
        if anchor is not None and (last_ts - anchor[0] >= minimum
                                   or (day == self._today_for(user) and now - anchor[0] >= minimum)):
            found.append((anchor[0], None, anchor[1], anchor[2]))   # still there

        Halt = self.env['lab.location.halt'].sudo()
        existing = {h.started_at: h for h in Halt.search(
            [('user_id', '=', user.id), ('date', '=', day)])}
        touched = Halt
        for started, ended, lat, lon in found:
            if any((metres_between(lat, lon, z[0], z[1]) or 0.0) <= z[2] for z in zones):
                continue                        # a clinic or the lab: a visit, not a halt
            place = ''
            near = [(metres_between(lat, lon, z[0], z[1]) or 1e9, z[3]) for z in zones]
            near = [n for n in near if n[0] <= NEAR_CLINIC_M]
            if near:
                place = min(near)[1]
            halt = existing.get(started)
            vals = {'ended_at': ended, 'latitude': lat, 'longitude': lon, 'place': place}
            if halt:
                if halt.ended_at != ended or halt.place != place:
                    halt.write(vals)
            else:
                halt = Halt.create(dict(vals, user_id=user.id, date=day, started_at=started,
                                        company_id=user.company_id.id))
            touched |= halt
        return touched

    @api.model
    def _refresh_trip_km(self, user, day):
        trips = self.env['lab.trip'].sudo().search(
            [('user_id', '=', user.id), ('date', '=', day), ('state', '!=', 'cancel')])
        if trips:
            trips._compute_gps_km()

    # ---------------------------------------------------------------- the live map
    @api.model
    def _is_manager(self):
        return self.env.user.has_group('lab_fieldwork.group_fieldwork_manager') \
            or self.env.user.has_group('base.group_system')

    @api.model
    def _map_users(self, day, user_ids=None):
        """Who is on the map: the executives' group plus anyone with a trail or a
        visit that day; an executive sees only themselves."""
        if not self._is_manager():
            return self.env.user
        group = self.env.ref('lab_fieldwork.group_fieldwork_executive', raise_if_not_found=False)
        users = group.sudo().all_user_ids if group else self.env['res.users']
        pinged = self.sudo().search([('date', '=', day)]).mapped('user_id')
        visited = self.env['lab.visit'].sudo().search([('date', '=', day)]).mapped('user_id')
        users = (users | pinged | visited).filtered(lambda u: u.active and u.company_ids & self.env.companies)
        if user_ids:
            users = users.filtered(lambda u: u.id in user_ids)
        return users.sorted('name')

    @api.model
    def _status_of(self, user, last, open_visit, on_duty, is_today, now):
        """One word for the board: what the last fix, the open visit and the duty
        say together. Returns (status, label, since)."""
        if not is_today:
            return 'off', _('day over'), last[0] if last else None
        if open_visit:
            return 'at_clinic', _('at a clinic'), open_visit.check_in
        if last:
            age = (now - last[0]).total_seconds() / 60.0
            if age <= self._stale_minutes():
                if last[3] >= MOVING_KMH:
                    return 'moving', _('on the road'), last[0]
                return 'stopped', _('stopped'), last[0]
            if age <= self._offline_minutes():
                return 'stopped', _('stopped'), last[0]
            if on_duty:
                return 'offline', _('no signal'), last[0]
            return 'off', _('day ended'), last[0]
        if on_duty:
            return 'offline', _('no position yet'), None
        return 'off', _('off today'), None

    @api.model
    def _next_clinic(self, user, day, last):
        """The next planned visit with a pinned clinic, with how far and how long."""
        visit = self.env['lab.visit'].sudo().search(
            [('user_id', '=', user.id), ('date', '=', day), ('state', '=', 'planned')],
            order='id', limit=10).filtered(lambda v: v.partner_id.is_geolocated)[:1]
        if not visit:
            return None
        clinic = visit.partner_id
        out = {'clinic_id': clinic.id, 'name': clinic.display_name, 'visit_id': visit.id,
               'lat': clinic.partner_latitude, 'lon': clinic.partner_longitude,
               'distance_km': None, 'eta_min': None}
        if last:
            metres = metres_between(last[1], last[2], clinic.partner_latitude,
                                    clinic.partner_longitude) or 0.0
            road_km = metres / 1000.0 * ROAD_FACTOR
            speed = max(last[3], ETA_FLOOR_KMH)
            out['distance_km'] = round(road_km, 1)
            out['eta_min'] = max(1, int(round(road_km / speed * 60)))
        return out

    @api.model
    def _map_home(self):
        """Where the map opens when nobody has a position yet: the company's pin,
        else the middle of its pinned clinics, else central Kerala."""
        lab = self.env.company.partner_id
        if has_fix(lab.partner_latitude, lab.partner_longitude):
            return {'lat': lab.partner_latitude, 'lon': lab.partner_longitude, 'zoom': 10}
        self.env.cr.execute(
            """SELECT avg(partner_latitude), avg(partner_longitude), count(*)
                 FROM res_partner
                WHERE is_clinic AND active
                  AND coalesce(partner_latitude, 0) <> 0 AND coalesce(partner_longitude, 0) <> 0""")
        lat, lon, count = self.env.cr.fetchone()
        if count:
            return {'lat': lat, 'lon': lon, 'zoom': 9}
        return {'lat': 10.5, 'lon': 76.2, 'zoom': 8}

    @api.model
    def get_live_map(self, day=None, user_ids=None):
        """Everything the live map and the executive's own mini-map draw (see the
        module docstring for the shape). A manager sees the team; an executive only
        themselves - the call refuses anything else rather than filtering quietly."""
        me = self.env.user
        if not self._is_manager() and user_ids and set(user_ids) != {me.id}:
            raise AccessError(_("Only field managers can see where others are."))
        day = fields.Date.to_date(day) if day else self._today_for(me)
        today = self._today_for(me)
        is_today = day == today
        now = fields.Datetime.now()
        users = self._map_users(day, user_ids)
        Visit = self.env['lab.visit'].sudo()
        Halt = self.env['lab.location.halt'].sudo()
        emp_by_user = {e.user_id.id: e for e in self.env['hr.employee'].sudo().search(
            [('user_id', 'in', users.ids)])}
        executives, clinics = [], {}
        for index, user in enumerate(users):
            points = self._day_points(user, day)
            if is_today and self._auto_arrive_enabled():
                self._detect_halts(user, day)       # a phone that went quiet still closes its halt
            visits = Visit.search([('user_id', '=', user.id), ('date', '=', day),
                                   ('state', '!=', 'cancel')], order='id')
            open_visit = visits.filtered(lambda v: v.state == 'open')[:1]
            employee = emp_by_user.get(user.id)
            on_duty = bool(employee) and employee.attendance_state == 'checked_in' and is_today
            duty_since = None
            if on_duty:
                att = self.env['hr.attendance'].sudo().search(
                    [('employee_id', '=', employee.id), ('check_out', '=', False)],
                    order='check_in desc', limit=1)
                duty_since = epoch(att.check_in) if att else None
            last = points[-1] if points else None
            status, label, since = self._status_of(user, last, open_visit, on_duty, is_today, now)
            legs, gaps = split_legs(points)
            trail_points = sum(len(leg) for leg in legs)
            if trail_points > MAX_TRAIL_POINTS:
                keep = max(1, MAX_TRAIL_POINTS * 1 // max(1, len(legs)))
                legs = [downsample(leg, keep) for leg in legs]
            doors = []
            for visit in visits:
                clinic = visit.partner_id
                if clinic.is_geolocated:
                    clinics[clinic.id] = {'id': clinic.id, 'name': clinic.display_name,
                                          'lat': clinic.partner_latitude, 'lon': clinic.partner_longitude,
                                          'radius_m': self._visit_radius(clinic)}
                if visit.check_in and has_fix(visit.gps_lat, visit.gps_lon):
                    doors.append({'visit_id': visit.id, 'clinic_id': clinic.id, 'name': clinic.display_name,
                                  'lat': visit.gps_lat, 'lon': visit.gps_lon, 'epoch': epoch(visit.check_in),
                                  'left_epoch': epoch(visit.check_out), 'state': visit.gps_state or 'nofix',
                                  'kind': 'in'})
                if visit.check_out and has_fix(visit.out_gps_lat, visit.out_gps_lon):
                    doors.append({'visit_id': visit.id, 'clinic_id': clinic.id, 'name': clinic.display_name,
                                  'lat': visit.out_gps_lat, 'lon': visit.out_gps_lon, 'epoch': epoch(visit.check_out),
                                  'left_epoch': None, 'state': visit.out_gps_state or 'nofix', 'kind': 'out'})
            halts = [{'id': h.id, 'lat': h.latitude, 'lon': h.longitude,
                      'started_epoch': epoch(h.started_at), 'ended_epoch': epoch(h.ended_at),
                      'minutes': h.minutes, 'place': h.place or ''}
                     for h in Halt.search([('user_id', '=', user.id), ('date', '=', day)], order='started_at')]
            overspeed = [[p[1], p[2], epoch(p[0]), p[3]] for p in points if p[4]][-MAX_OVERSPEED_POINTS:]
            executives.append({
                'user_id': user.id, 'name': user.name,
                'phone': user.partner_id.phone or '',      # Odoo 20 has no `mobile`
                'colour': PALETTE[index % len(PALETTE)],
                'status': status, 'status_label': label, 'since_epoch': epoch(since),
                'last': {'lat': last[1], 'lon': last[2], 'epoch': epoch(last[0]),
                         'age_s': int((now - last[0]).total_seconds()), 'speed_kmh': last[3],
                         'accuracy_m': last[6], 'battery': last[5] or user.sudo().fw_last_battery or None,
                         'source': last[7]}
                if last else None,
                'at_clinic': {'clinic_id': open_visit.partner_id.id, 'name': open_visit.partner_id.display_name,
                              'visit_id': open_visit.id, 'since_epoch': epoch(open_visit.check_in)}
                if open_visit else None,
                'next': self._next_clinic(user, day, last) if is_today else None,
                'km': trail_km(points),
                'visits_done': len(visits.filtered(lambda v: v.state == 'done')),
                'visits_planned': len(visits),
                'overspeed': sum(1 for p in points if p[4]),
                'halt_minutes': sum(h['minutes'] for h in halts),
                'on_duty': on_duty, 'duty_since_epoch': duty_since,
                'legs': [[[p[1], p[2], epoch(p[0]), p[3]] for p in leg] for leg in legs],
                'gaps': [{'from_epoch': epoch(a), 'to_epoch': epoch(b),
                          'minutes': int((b - a).total_seconds() // 60)} for a, b in gaps],
                'doors': doors, 'halts': halts, 'overspeed_points': overspeed,
            })
        icp = self.env['ir.config_parameter'].sudo()
        return {
            'home': self._map_home(),
            'day': fields.Date.to_string(day), 'is_today': is_today, 'now_epoch': epoch(now),
            'tiles': icp.get_str('lab_fieldwork.map_tiles',
                                 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'),
            'attribution': icp.get_str('lab_fieldwork.map_attribution',
                                       '&copy; OpenStreetMap contributors'),
            'config': {'live_ms': 20000, 'stale_minutes': self._stale_minutes(),
                       'offline_minutes': self._offline_minutes(), 'speed_limit': self._speed_limit()},
            'executives': executives,
            'clinics': list(clinics.values()),
        }


class ResUsersLive(models.Model):
    _inherit = 'res.users'

    # The last fix, forward-only, written by the trail. Manager-only fields: where a
    # person is is theirs and their manager's, not the whole company's.
    fw_last_ping_at = fields.Datetime('Last Seen', readonly=True,
                                      groups='lab_fieldwork.group_fieldwork_manager')
    fw_last_lat = fields.Float(digits=(10, 7), readonly=True,
                               groups='lab_fieldwork.group_fieldwork_manager')
    fw_last_lon = fields.Float(digits=(10, 7), readonly=True,
                               groups='lab_fieldwork.group_fieldwork_manager')
    fw_last_speed = fields.Float('Last Speed (km/h)', digits=(6, 1), readonly=True,
                                 groups='lab_fieldwork.group_fieldwork_manager')
    fw_last_accuracy = fields.Float('Last Fix Accuracy (m)', readonly=True,
                                    groups='lab_fieldwork.group_fieldwork_manager')
    fw_last_battery = fields.Float('Phone Battery (%)', digits=(5, 1), readonly=True,
                                   groups='lab_fieldwork.group_fieldwork_manager')

    # The free Traccar Client app instead of the browser (client, 2026-10-19). It
    # tracks with the screen off; the browser cannot. The identifier is the phone's
    # password to the endpoint - whoever holds it can post positions as this person -
    # so it is long, random, unique, and only an administrator sees or changes it.
    fw_gps_app = fields.Boolean(
        'Tracked by the Traccar app', readonly=True, copy=False,
        groups='lab_fieldwork.group_fieldwork_manager',
        help="This person's phone runs the free Traccar Client app, which sends positions "
             "with the screen off. The browser then stops asking for one.")
    fw_gps_device_id = fields.Char(
        'Tracking App Device ID', readonly=True, copy=False, index=True,
        groups='lab_fieldwork.group_fieldwork_admin,base.group_system')

    _fw_gps_device_unique = models.Constraint(
        'unique(fw_gps_device_id)', 'That tracking device ID is already in use.')

    @api.model
    def _fw_new_device_id(self):
        return secrets.token_hex(8).upper()

    def _fw_check_app_admin(self):
        if not (self.env.su or self.env.user.has_group('lab_fieldwork.group_fieldwork_admin')
                or self.env.user.has_group('base.group_system')):
            raise AccessError(_("Only a Field Work administrator sets up the tracking app."))

    def action_fw_tracking_app(self):
        """Switch the tracking app on or off for this person (button: a field-work
        administrator cannot edit a user record - see action_fw_set_exception)."""
        self._fw_check_app_admin()
        value = bool(self.env.context.get('fw_value'))
        for user in self.sudo():
            if user.fw_gps_app == value:
                continue
            vals = {'fw_gps_app': value}
            if value and not user.fw_gps_device_id:
                vals['fw_gps_device_id'] = self._fw_new_device_id()
            user.write(vals)
            user.partner_id.message_post(body=_(
                "Tracking app switched %(state)s by %(who)s.",
                state=_("on") if value else _("off"), who=self.env.user.name))
        return True

    def action_fw_new_device_id(self):
        """A new identifier: the old phone stops being accepted at once (a lost or
        handed-over phone)."""
        self._fw_check_app_admin()
        for user in self.sudo():
            user.fw_gps_device_id = self._fw_new_device_id()
            user.partner_id.message_post(body=_(
                "Tracking app device ID replaced by %(who)s; the phone must be set up again.",
                who=self.env.user.name))
        return True

    def _fw_app_setup(self):
        """What the executive types into Traccar Client, or None without the app."""
        self.ensure_one()
        me = self.sudo()
        if not me.fw_gps_app or not me.fw_gps_device_id:
            return None
        base = self.env['ir.config_parameter'].sudo().get_str('web.base.url', '').rstrip('/')
        last = self.env['lab.location.ping'].sudo().search(
            [('user_id', '=', me.id), ('source', '=', 'app')], order='ts desc', limit=1)
        return {
            'server_url': '%s/lab_fieldwork/gps' % base,
            'device_id': me.fw_gps_device_id,
            'last_app_fix_age_s': int((fields.Datetime.now() - last.ts).total_seconds())
            if last else None,
        }


class LabMyDayLive(models.AbstractModel):
    _inherit = 'lab.my.day'

    @api.model
    def tracking_state(self, client=None, app_version=None):
        """Plus what the trail noticed: the km so far, how fresh the last fix is, and
        the nudges - a planned clinic the phone is standing in, an open visit the
        phone has walked away from.

        `client` says who is asking. 'app' is the Lab app's own page (2026-10-20,
        models/field_app.py): it gets its tracking service's settings back as
        `native` - provisioning the phone on the first ask - and its page never
        watches by itself. Anyone else gets the Traccar settings if that app is on,
        or stands down while the Lab app is evidently posting (`app_live`): two GPS
        watches on one phone is double the battery for the same trail.
        """
        state = super().tracking_state()
        user = self.env.user
        state.update({'app': None, 'native': None, 'app_mode': False, 'app_live': False})
        in_app = client == 'app'
        if state.get('enabled'):
            if in_app:
                # The phone is provisioned whenever the app asks; the recorder's
                # settings go out only while the day is running. Start my day is
                # when recording starts and End the day is when it stops, and in
                # between the app is handed nothing it could record with.
                # (client, 2026-10-20)
                config = user._fw_native_config(app_version)
                state['native'] = config if state.get('on_duty') else None
            else:
                state['app'] = user._fw_app_setup()
                if not state['app']:
                    age = user._fw_native_last_fix_age()
                    state['app_live'] = age is not None and age <= APP_LIVE_SECONDS
        if in_app or state['app'] or state['app_live']:
            # Inside the app the page never watches by itself, running day or not.
            state['enabled'] = False
            state['app_mode'] = True
        me = user.sudo()
        state['native_app'] = {
            'version': me.fw_native_app_version or '',
            'last_fix_age_s': user._fw_native_last_fix_age(),
        } if me.fw_native_app else None
        # For the nudge on My Day (Android, outside the app): None until an APK is up.
        state['app_download'] = self.env['lab.field.app']._info()
        Ping = self.env['lab.location.ping'].sudo()
        day = Ping._today_for(user)
        points = Ping._day_points(user, day)
        state['km_today'] = trail_km(points)
        state['last_fix_age_s'] = int((fields.Datetime.now() - points[-1][0]).total_seconds()) \
            if points else None
        nudges = []
        if (state.get('enabled') or state.get('app_mode')) and state.get('on_duty') \
                and Ping._auto_arrive_enabled():
            visits = self.env['lab.visit'].sudo().search(
                [('user_id', '=', user.id), ('date', '=', day),
                 ('state', 'in', ('planned', 'open'))], order='id')
            zone = tz_name(self.env, user)
            for visit in visits:
                when = None
                if visit.state == 'planned' and visit.auto_arrived_at and not visit.check_in:
                    kind, when = 'arrived', visit.auto_arrived_at
                elif visit.state == 'open' and visit.auto_left_at and not visit.check_out:
                    kind, when = 'left', visit.auto_left_at
                if when:
                    nudges.append({
                        'kind': kind, 'visit_id': visit.id, 'clinic': visit.partner_id.display_name,
                        'since_epoch': epoch(when),
                        'since_label': fields.Datetime.context_timestamp(
                            self.with_context(tz=zone), when).strftime('%H:%M'),
                    })
        state['nudges'] = nudges
        return state


class LabDeskLive(models.AbstractModel):
    _inherit = 'lab.desk'

    def _field_board(self, today):
        """The board's rows gain the live facts: when the phone was last heard from,
        where it is in words, how far it has come, how long it has sat still."""
        board = super()._field_board(today)
        Ping = self.env['lab.location.ping'].sudo()
        now = fields.Datetime.now()
        halts = self.env['lab.location.halt'].sudo().search(
            [('date', '=', today), ('user_id', 'in', [r['id'] for r in board['rows']])])
        halt_minutes = {}
        for halt in halts:
            halt_minutes[halt.user_id.id] = halt_minutes.get(halt.user_id.id, 0) + halt.minutes
        clinics = {}
        for visit in board['visits']:
            if visit.partner_id.is_geolocated:
                clinics.setdefault(visit.user_id.id, []).append(visit.partner_id)
        for row in board['rows']:
            user = self.env['res.users'].sudo().browse(row['id'])
            last_at = user.fw_last_ping_at
            seen_today = bool(last_at) and fields.Datetime.context_timestamp(
                self.with_context(tz=tz_name(self.env, user)), last_at).date() == today
            row['last_seen_epoch'] = epoch(last_at) if seen_today else None
            row['last_age_s'] = int((now - last_at).total_seconds()) if seen_today else None
            row['live'] = 'none'
            row['where'] = ''
            if seen_today:
                age_min = row['last_age_s'] / 60.0
                if age_min <= Ping._stale_minutes():
                    row['live'] = 'moving' if user.fw_last_speed >= MOVING_KMH else 'stopped'
                elif age_min <= Ping._offline_minutes():
                    row['live'] = 'stopped'
                else:
                    row['live'] = 'offline'
                near = []
                for clinic in clinics.get(user.id, []):
                    metres = metres_between(user.fw_last_lat, user.fw_last_lon,
                                            clinic.partner_latitude, clinic.partner_longitude)
                    if metres is not None and metres <= NEAR_CLINIC_M:
                        near.append((metres, clinic.display_name))
                if row['live'] == 'offline':
                    # silent for a while: where it was last heard, never "on the road"
                    row['where'] = _('last near %s', min(near)[1]) if near else _('no signal')
                else:
                    row['where'] = _('near %s', min(near)[1]) if near else _('on the road')
            row['halt_minutes'] = halt_minutes.get(user.id, 0)
            row['gps_km'] = Ping._trail_km_for(user, today) if seen_today else 0.0
        return board
