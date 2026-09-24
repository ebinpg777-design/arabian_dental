# -*- coding: utf-8 -*-
"""The day's movement, as a track.

The sheet already says how many of the day's doors carry a location ("2 of 2 carry a
location"). That answers whether the round happened; it does not show *where* it
happened, and an approver looking at a day that reads oddly — thirty kilometres between
two clinics a mile apart, a visit stamped from the next town — has no way to see it.

This turns the fixes the phone already recorded into one picture: every check-in and
check-out, every delivery handed over, in the order they happened, joined into the
executive's path through the day, with the clinic's own pin beside each door so drift
is visible rather than argued about.

Nothing new is captured. These are the same fixes `lab.visit` and `lab.delivery` have
stored all along — this is the reading of them. A *continuous* breadcrumb trail (a ping
every few minutes whether or not the executive is at a door) would be a different and
much larger thing: a background permission on the phone, a row per ping, and a decision
by the lab about tracking people between clinics. Not assumed here.
"""
import json
import logging

import requests

import odoo.modules.module

from .lab_visit import has_fix, metres_between
from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# Kerala, so the whole state fits when a day has one lonely fix and no spread of its
# own to zoom to.
FALLBACK_SPAN_DEG = 0.02

# Arriving and leaving are two fixes, but usually of the SAME doorstep: a phone that
# has not moved still reports a few metres of jitter, and one that could not get a new
# fix reuses the old one exactly. Drawn as two marks they sit on top of each other and
# the lower one can neither be seen nor clicked. Within this radius they are one door,
# carrying both times; beyond it the executive genuinely left from somewhere else and
# both marks are drawn. (client, 2026-09-23)
SAME_PLACE_METRES = 30

# Following the roads needs a routing service; Odoo ships none. The default is the
# OSRM project's public demo server, which needs no key and is fine for a handful of
# day sheets a day — but it is explicitly a DEMO server with no SLA and a rate limit,
# so a lab that leans on this should point `lab_fieldwork.routing_url` at its own OSRM
# (docker, one Kerala extract) or a keyed provider. Emptying the parameter turns road
# routing off and the map falls back to straight lines between the fixes.
DEFAULT_ROUTING_URL = 'https://router.project-osrm.org/route/v1/driving/'
ROUTING_TIMEOUT = 8
# OSRM takes coordinates in the URL; a very long round would make a URL nobody's proxy
# will forward. Beyond this the track is thinned evenly before asking.
MAX_WAYPOINTS = 60
# Longer than this between two recorded positions and the phone was asleep, out
# of coverage, or in somebody's pocket with the app closed. The trail breaks.
TRAIL_GAP_SECONDS = 900


class LabDailyUpdate(models.Model):
    _inherit = 'lab.daily.update'

    # The road route, worked out once and kept: the day is over, its fixes do not
    # change, and the two desks open the same sheet repeatedly.
    route_geometry = fields.Text(readonly=True, copy=False,
                                 help="The road route as JSON [[lat, lon], ...].")
    route_km = fields.Float('By Road (km)', readonly=True, copy=False)
    route_state = fields.Selection(
        [('none', 'Not worked out yet'), ('ok', 'Following the roads'),
         ('failed', 'Routing service did not answer'), ('off', 'Road routing is off')],
        default='none', readonly=True, copy=False)
    route_key = fields.Char(readonly=True, copy=False,
                            help="The fixes the stored route was worked out from.")

    def _routing_url(self):
        """Where to ask for a road route, or nothing at all.

        A test suite must not depend on somebody else's server being up, and none of
        the day-sheet tests are about routing — so under the test runner this is off
        unless a test asks for it by context and patches the call itself.
        """
        if odoo.modules.module.current_test and not self.env.context.get(
                'lab_test_routing'):
            return ''
        # Read the record, not get_param: that helper returns `value or default`, so a
        # parameter an admin has deliberately BLANKED to turn routing off would hand
        # back the default URL and keep calling out. Absent means "use the default";
        # present and empty means off.
        parameter = self.env['ir.config_parameter'].sudo().search(
            [('key', '=', 'lab_fieldwork.routing_url')], limit=1)
        url = parameter.value if parameter else DEFAULT_ROUTING_URL
        return (url or '').strip()

    def _route_key_for(self, points):
        """What the cached route was drawn from, so a corrected fix redraws it."""
        return '|'.join('%.5f,%.5f' % (p['lat'], p['lon']) for p in points)

    def _thin(self, points):
        """Evenly spaced waypoints when a round has more doors than a URL should carry."""
        if len(points) <= MAX_WAYPOINTS:
            return points
        step = len(points) / float(MAX_WAYPOINTS - 1)
        kept = [points[int(i * step)] for i in range(MAX_WAYPOINTS - 1)]
        return kept + [points[-1]]

    def _road_route(self, points, force=False):
        """The day's path along the roads, or nothing and a reason.

        Cached on the sheet: the first desk to open it pays the round trip, everyone
        after reads the stored line. A failure is remembered too, so a routing service
        that is down is asked once rather than on every form open — `force` (the Redraw
        button) is what asks again.
        """
        self.ensure_one()
        if len(points) < 2:
            return None
        key = self._route_key_for(points)
        if not force and self.route_key == key and self.route_state in ('ok', 'failed', 'off'):
            return json.loads(self.route_geometry) if self.route_geometry else None

        url = self._routing_url()
        if not url:
            self.sudo().write({'route_state': 'off', 'route_geometry': False,
                               'route_km': 0.0, 'route_key': key})
            return None

        coordinates = ';'.join('%s,%s' % (p['lon'], p['lat']) for p in self._thin(points))
        try:
            response = requests.get(
                url + coordinates,
                params={'overview': 'full', 'geometries': 'geojson'},
                timeout=ROUTING_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
            if payload.get('code') != 'Ok' or not payload.get('routes'):
                raise ValueError(payload.get('message') or payload.get('code'))
            route = payload['routes'][0]
            # GeoJSON is lon,lat; Leaflet wants lat,lon.
            line = [[lat, lon] for lon, lat in route['geometry']['coordinates']]
        except Exception as error:   # noqa: BLE001  — a map must never break the sheet
            _logger.info("day sheet %s: no road route (%s)", self.id, error)
            self.sudo().write({'route_state': 'failed', 'route_geometry': False,
                               'route_km': 0.0, 'route_key': key})
            return None

        self.sudo().write({
            'route_state': 'ok', 'route_geometry': json.dumps(line),
            'route_km': round((route.get('distance') or 0.0) / 1000.0, 1),
            'route_key': key,
        })
        return line

    def action_route_refresh(self):
        """Ask the routing service again — for a day drawn while it was down."""
        self.ensure_one()
        track = self.get_day_track(force_route=True)
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'type': 'success' if track['road_km'] else 'warning',
                'message': (_('Route redrawn: %s km by road', track['road_km'])
                            if track['road_km']
                            else _('The routing service did not answer; the map shows '
                                   'straight lines between the fixes.')),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def _track_may_read_deliveries(self):
        """Delivery fixes are manager-only (`groups=` on lab.delivery).

        An executive sees their own doors, which they walked to themselves; where the
        parcels were handed over is a manager's reading of the round, and the field
        groups say so. Honour that here rather than leaking it through a map.
        """
        return self.env.su or self.env.user.has_group(
            'lab_fieldwork.group_fieldwork_manager') or self.env.user.has_group(
            'base.group_system')

    def _track_point(self, kind, lat, lon, when, label, **extra):
        point = {
            'kind': kind,
            'lat': lat,
            'lon': lon,
            'at': self._local_hm(when) or '',
            'sort': when or fields.Datetime.now(),
            'label': label or '',
        }
        point.update(extra)
        return point

    def get_day_track(self, force_route=False):
        """Every fix of the day, in order, with the clinic pins to judge them by.

        Returned as plain data rather than rendered HTML: the map is drawn in the
        browser, and the same numbers feed the caption above it.
        """
        self.ensure_one()
        points = []
        clinics = {}
        visits = self._visits()
        located = 0

        for index, visit in enumerate(visits, start=1):
            clinic = visit.partner_id
            pinned = has_fix(clinic.partner_latitude, clinic.partner_longitude)
            if pinned and clinic.id not in clinics:
                clinics[clinic.id] = {
                    'id': clinic.id,
                    'name': clinic.display_name or '',
                    'lat': clinic.partner_latitude,
                    'lon': clinic.partner_longitude,
                }
            arrived = has_fix(visit.gps_lat, visit.gps_lon)
            left = has_fix(visit.out_gps_lat, visit.out_gps_lon)
            common = dict(seq=index, clinic_id=clinic.id if pinned else False,
                          res_model='lab.visit', res_id=visit.id)
            if arrived and left and self._same_place(visit):
                # One doorstep, both times.
                points.append(self._track_point(
                    'door', visit.gps_lat, visit.gps_lon, visit.check_in,
                    clinic.display_name, state=visit.gps_state or 'nopin',
                    metres=round(visit.distance_m or 0.0),
                    left_at=self._local_hm(visit.check_out) or '',
                    note=_('arrived and left'), **common))
            else:
                if arrived:
                    points.append(self._track_point(
                        'in', visit.gps_lat, visit.gps_lon, visit.check_in,
                        clinic.display_name, state=visit.gps_state or 'nopin',
                        metres=round(visit.distance_m or 0.0),
                        left_at='', note=_('arrived'), **common))
                if left:
                    points.append(self._track_point(
                        'out', visit.out_gps_lat, visit.out_gps_lon,
                        visit.check_out, clinic.display_name,
                        state=visit.out_gps_state or 'nopin',
                        metres=round(visit.out_distance_m or 0.0),
                        left_at='', note=_('left'), **common))
            located += bool(arrived or left)

        if self._track_may_read_deliveries() and 'lab.delivery' in self.env:
            start, end = self._day_bounds_utc()
            deliveries = self.env['lab.delivery'].sudo().search([
                ('executive_id', '=', self.user_id.id),
                ('state', '=', 'delivered'),
                ('delivered_datetime', '>=', start),
                ('delivered_datetime', '<', end),
            ])
            for delivery in deliveries:
                if not has_fix(delivery.delivered_lat, delivery.delivered_lon):
                    continue
                points.append(self._track_point(
                    'delivery', delivery.delivered_lat, delivery.delivered_lon,
                    delivery.delivered_datetime,
                    delivery.partner_id.display_name or delivery.name,
                    seq=0, state=delivery.delivered_gps_state or 'nopin',
                    metres=round(delivery.delivered_distance_m or 0.0),
                    clinic_id=False, res_model='lab.delivery', res_id=delivery.id,
                    left_at='', note=_('delivered %s', delivery.name)))

        points.sort(key=lambda p: p['sort'])
        for point in points:
            del point['sort']

        geometry = self._road_route(points, force=force_route)
        trail, trail_km = self._track_trail()
        return {
            'points': points,
            'clinics': list(clinics.values()),
            'geometry': geometry or [],
            'trail': trail,
            'trail_km': trail_km,
            'road_km': self.route_km if geometry else 0.0,
            'route_state': self.route_state,
            'bounds': self._track_bounds(points, clinics.values(), geometry, trail),
            'straight_km': self._track_straight_km(points),
            'odo_km': round(self.km or 0.0, 1),
            'located': located,
            'visits': len(visits),
            'user': self.user_id.display_name,
            'date_label': self.date_label or '',
            'tiles': self.env['ir.config_parameter'].sudo().get_param(
                'lab_fieldwork.map_tiles',
                'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'),
            'attribution': self.env['ir.config_parameter'].sudo().get_param(
                'lab_fieldwork.map_attribution',
                '&copy; OpenStreetMap contributors'),
        }

    def _same_place(self, visit):
        """True when the fix taken on leaving is the doorstep it arrived at."""
        metres = metres_between(visit.gps_lat, visit.gps_lon,
                                visit.out_gps_lat, visit.out_gps_lon)
        return metres is not None and metres <= SAME_PLACE_METRES

    def _track_bounds(self, points, clinics, geometry=None, trail=None):
        """South-west and north-east corners, never a zero-sized box.

        A day with one fix has no spread of its own; fitting a map to it zooms to the
        maximum and shows a roof. A small box around the point shows the town.
        """
        if not points and not trail:
            # Clinic pins alone are not a track: a day with no fix must not be
            # framed as though it had been walked. A recorded route IS one, though —
            # a day spent driving with no clinic reached is exactly the day worth
            # being able to look at.
            return False
        lats = [p['lat'] for p in points] + [c['lat'] for c in clinics]
        lons = [p['lon'] for p in points] + [c['lon'] for c in clinics]
        for segment in (trail or []):
            for lat, lon, _label in segment:
                lats.append(lat)
                lons.append(lon)
        # A road route can swing wide of every door it joins; frame the line, not
        # just its ends.
        for lat, lon in (geometry or []):
            lats.append(lat)
            lons.append(lon)
        south, north = min(lats), max(lats)
        west, east = min(lons), max(lons)
        if north - south < FALLBACK_SPAN_DEG:
            middle = (north + south) / 2
            south, north = middle - FALLBACK_SPAN_DEG, middle + FALLBACK_SPAN_DEG
        if east - west < FALLBACK_SPAN_DEG:
            middle = (east + west) / 2
            west, east = middle - FALLBACK_SPAN_DEG, middle + FALLBACK_SPAN_DEG
        return [[south, west], [north, east]]

    def _track_trail(self):
        """The recorded route between the doors, split at the gaps.

        Returned as a LIST OF SEGMENTS rather than one line, and that is the point. A
        browser only watches while its page is open, so a locked phone or a switched
        app leaves a hole in the record. Joining the two ends of that hole would draw a
        road nobody can say was taken — the one thing a tracking map must never do. A
        gap stays a gap, and the map shows it as one.

        Distance is the sum WITHIN segments only: what crossed a gap is unknown, and
        adding a straight line across it would put a number on a guess.
        """
        pings = self.env['lab.location.ping'].sudo().search(
            [('user_id', '=', self.user_id.id), ('date', '=', self.date)], order='ts')
        if not pings:
            return [], 0.0
        segments, current = [], []
        metres_total, previous = 0.0, None
        for ping in pings:
            local = fields.Datetime.context_timestamp(
                self.with_context(tz=self.user_id.tz or 'UTC'), ping.ts)
            if previous is not None:
                waited = (ping.ts - previous.ts).total_seconds()
                if waited > TRAIL_GAP_SECONDS:
                    if len(current) > 1:
                        segments.append(current)
                    current = []
                else:
                    step = metres_between(previous.latitude, previous.longitude,
                                          ping.latitude, ping.longitude)
                    metres_total += step or 0.0
            current.append([ping.latitude, ping.longitude, local.strftime('%H:%M')])
            previous = ping
        if len(current) > 1:
            segments.append(current)
        return segments, round(metres_total / 1000.0, 1)

    def _track_straight_km(self, points):
        """Great-circle distance along the track.

        Deliberately NOT presented as the distance travelled: it is the crow's flight
        between consecutive doors, and the odometer on the trip is the real figure. It
        is here because a straight-line total far ABOVE the odometer is impossible and
        one far below it is worth a question.
        """
        total = 0.0
        for before, after in zip(points, points[1:]):
            metres = metres_between(before['lat'], before['lon'],
                                    after['lat'], after['lon'])
            total += metres or 0.0
        return round(total / 1000.0, 1)
