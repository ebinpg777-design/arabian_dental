# -*- coding: utf-8 -*-
"""The trail along the roads (client, 2026-10-23: "show the map through roads instead
of straight lines").

A fix every ten seconds is a point every 50-200 m, and a line from each to the next
cuts every corner. OSRM's route service - the one the day sheet already asks for the
road between two doors - is asked for the road from each fix to the next, the whole
trail at once up to ROAD_WAYPOINTS fixes a request, and the roads it returns are
drawn instead of the straight lines. Fixes closer than JITTER_M to the last waypoint
are skipped (a phone standing still is one place); a stretch nobody could have driven
(over MAX_KMH) is never asked for; and a leg the service makes far longer than the
gap it bridges is a fix snapped to the wrong road - that leg is drawn straight. (The
phone's heading was tried as a bearing hint and made it worse: 3 wrong snaps became
5 on the same morning.)

Cached per person and day (`lab.location.road`): every step is asked for once, the
day's new fixes alone on the next look, so a manager's screen costs one small request
per poll. A service that does not answer is remembered for FAILED_RETRY_SECONDS so an
outage is not asked again on every poll, and the trail is drawn straight wherever no
road is known - the picture is never worse than the plain one.
"""
import json
import logging
from datetime import timedelta

import psycopg2
import requests

import odoo.modules.module
from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from .lab_visit import metres_between
from .location_live import JITTER_M, MAX_KMH, classify_modes, epoch, split_legs

_logger = logging.getLogger(__name__)

DEFAULT_ROUTING_URL = 'https://router.project-osrm.org/route/v1/driving/'
ROUTING_TIMEOUT = 8
ROAD_WAYPOINTS = 100          # fixes per request; the public server takes 200
ROAD_REQUESTS_PER_CALL = 6    # a first look at a long day; the rest on the next look
DETOUR_FACTOR = 1.5           # by road this much longer than the straight gap...
DETOUR_SLACK_M = 150.0        # ...plus this: a fix snapped to the wrong road
FAILED_RETRY_SECONDS = 600


def routing_url(env):
    """Where the roads are asked: `lab_fieldwork.routing_url`, the day sheet's own
    setting (models/day_track.py explains the blank-means-off reading), or '' - and
    always '' under the test runner unless a test says `lab_test_routing` and patches
    the call itself."""
    if odoo.modules.module.current_test and not env.context.get('lab_test_routing'):
        return ''
    parameter = env['ir.config_parameter'].sudo().search(
        [('key', '=', 'lab_fieldwork.routing_url')], limit=1)
    url = parameter.value if parameter else DEFAULT_ROUTING_URL
    return (url or '').strip()


def _straight(a, b):
    return metres_between(a[1], a[2], b[1], b[2]) or 0.0


def _vertex(lat, lon):
    """A vertex as it is stored and compared: six decimals, a tenth of a metre."""
    return [round(lat, 6), round(lon, 6)]


class LabLocationRoad(models.Model):
    _name = 'lab.location.road'
    _description = 'Field Trail Along the Roads'
    _rec_name = 'date'

    user_id = fields.Many2one('res.users', string='Executive', required=True, index=True,
                              ondelete='cascade')
    date = fields.Date(required=True, index=True)
    company_id = fields.Many2one('res.company', required=True,
                                 default=lambda self: self.env.company)
    steps = fields.Text(help="JSON: the road from each fix to the next, keyed by the "
                             "fix's time.")
    accuracy_key = fields.Integer(help="The trail accuracy limit the roads were read with.")
    state = fields.Selection(
        [('ok', 'Drawn'), ('failed', 'The routing service did not answer')],
        default='ok', required=True)
    failed_at = fields.Datetime()
    request_count = fields.Integer()

    _user_day_unique = models.Constraint(
        'UNIQUE(user_id, date)', "One road trail per person and day.")

    @api.model
    def _for(self, user, day):
        """This person's road record for the day, made if there is none yet. Two
        managers focusing the same person at once both get here: the loser of the
        race reads what the winner made."""
        domain = [('user_id', '=', user.id), ('date', '=', day)]
        road = self.search(domain, limit=1)
        if road:
            return road
        try:
            with self.env.cr.savepoint():
                return self.create({'user_id': user.id, 'date': day,
                                    'company_id': user.company_id.id})
        except psycopg2.IntegrityError:
            return self.search(domain, limit=1)


class LabLocationPingRoad(models.Model):
    _inherit = 'lab.location.ping'

    @api.model
    def _road_trail_enabled(self):
        return self.env['ir.config_parameter'].sudo().get_str(
            'lab_fieldwork.road_trail', 'True') in ('True', 'true', '1')

    @api.model
    def get_road_trail(self, user_id=None, day=None):
        """One person's day along the roads, for the map in focus (field_live.js
        loadRoad): {'state': ok|failed|off, 'legs': as get_live_map's or None when
        roads are off, 'complete': whether every step has its road yet}."""
        me = self.env.user
        user = self.env['res.users'].browse(int(user_id)) if user_id else me
        if user != me and not self._is_manager():
            raise AccessError(_("Only field managers can see where others are."))
        day = fields.Date.to_date(day) if day else self._today_for(me)
        return self._road_legs(user, day)

    @api.model
    def _road_legs(self, user, day):
        url = routing_url(self.env) if self._road_trail_enabled() else ''
        if not url:
            return {'state': 'off', 'legs': None, 'complete': True}
        points = self._day_points(user, day)
        modes = classify_modes(points)
        mode_of = {id(p): m for p, m in zip(points, modes)}
        limit = self._trail_accuracy()
        road = self.env['lab.location.road'].sudo()._for(user, day)
        cache = {}
        if road.steps and road.accuracy_key == limit:
            try:
                cache = json.loads(road.steps)
            except ValueError:
                cache = {}

        # The waypoints: a leg per recording gap, jitter skipped; and the runs of
        # consecutive steps whose road is not known yet.
        legs_wp, runs = [], []
        for leg in split_legs(points)[0]:
            wps = [leg[0]]
            for p in leg[1:]:
                if _straight(wps[-1], p) >= JITTER_M:
                    wps.append(p)
            if len(wps) < 2:
                continue
            legs_wp.append(wps)
            run = None
            for a, b in zip(wps, wps[1:]):
                secs = max(1.0, (b[0] - a[0]).total_seconds())
                jump = _straight(a, b) / secs * 3.6 > MAX_KMH
                if str(epoch(b[0])) in cache or jump:
                    run = None
                    continue
                if run is None:
                    run = [a]
                    runs.append(run)
                run.append(b)

        # Ask for the roads not known yet - a bounded number of requests a call.
        now = fields.Datetime.now()
        state, complete, asked = 'ok', True, 0
        if road.state == 'failed' and road.failed_at and \
                now - road.failed_at < timedelta(seconds=FAILED_RETRY_SECONDS):
            state, complete = 'failed', bool(not runs)
        else:
            for run in runs:
                for start in range(0, len(run) - 1, ROAD_WAYPOINTS - 1):
                    if asked >= ROAD_REQUESTS_PER_CALL:
                        complete = False
                        break
                    asked += 1
                    try:
                        cache.update(self._ask_roads(url, run[start:start + ROAD_WAYPOINTS]))
                    except Exception as error:   # noqa: BLE001 - a map must never break the board
                        _logger.info("road trail %s %s: no roads (%s)", user.login, day, error)
                        state, complete = 'failed', False
                        break
                if state == 'failed' or not complete:
                    break
            if asked or state == 'failed':
                road.write({
                    'steps': json.dumps(cache, separators=(',', ':')), 'accuracy_key': limit,
                    'state': state, 'failed_at': now if state == 'failed' else False,
                    'request_count': road.request_count + asked,
                })

        # Draw: the road where it is known, the straight gap where it is not. Each
        # vertex carries the time, speed and mode of the fix its step ends at, so the
        # replay and the dashed/dotted styling work as on the plain trail.
        legs_out = []
        for wps in legs_wp:
            first = wps[0]
            out = [[first[1], first[2], epoch(first[0]), first[3], 'still']]
            for a, b in zip(wps, wps[1:]):
                mode = mode_of.get(id(b), 'still')
                when, speed = epoch(b[0]), b[3]
                geometry = cache.get(str(when))
                # A stretch the phone crossed with nothing recorded is drawn as the
                # drive it was once its road is known, not as a dotted straight line
                # (client, 2026-10-23: "why these dots ... the map should be realistic").
                if mode == 'blind' and geometry and len(geometry) > 1:
                    mode = 'vehicle'
                for lat, lon in geometry or [_vertex(b[1], b[2])]:
                    if abs(out[-1][0] - lat) < 1e-6 and abs(out[-1][1] - lon) < 1e-6:
                        continue
                    out.append([lat, lon, when, speed, mode])
            legs_out.append(out)
        return {'state': state, 'legs': legs_out, 'complete': complete}

    @api.model
    def _cron_fill_roads(self):
        """The roads of everyone recording today, a bounded number of requests each,
        every five minutes: the team view draws a stretch the phone crossed with no
        signal along its road from what is known here (get_live_map), and asks
        nothing itself. The person in focus still asks for the rest at once
        (get_road_trail). (client, 2026-10-23)"""
        if not (self._road_trail_enabled() and routing_url(self.env)):
            return
        Ping = self.sudo()
        Users = self.env['res.users'].sudo().with_context(active_test=False)
        self.env.cr.execute("SELECT DISTINCT user_id FROM lab_location_ping WHERE date >= %s",
                            [fields.Date.today() - timedelta(days=1)])
        filled = 0
        for (user_id,) in self.env.cr.fetchall():
            user = Users.browse(user_id)
            day = Ping._today_for(user)
            if Ping.search_count([('user_id', '=', user.id), ('date', '=', day)], limit=1):
                Ping._road_legs(user, day)
                filled += 1
        _logger.info("road trail: %d trail(s) of today brought up to date", filled)

    @api.model
    def _ask_roads(self, url, chunk):
        """{fix time: [[lat, lon], ...]} - the road from each fix of `chunk` to the
        next, as OSRM's route service gives it (steps carry the geometry per leg).
        A leg far longer by road than the gap it bridges is a fix snapped to the
        wrong road: that step is the gap itself, straight."""
        coordinates = ';'.join('%s,%s' % (p[2], p[1]) for p in chunk)
        response = requests.get(
            url + coordinates,
            params={'overview': 'false', 'geometries': 'geojson', 'steps': 'true'},
            timeout=ROUTING_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
        if payload.get('code') != 'Ok' or not payload.get('routes'):
            raise ValueError(payload.get('message') or payload.get('code'))
        snapped = [[w['location'][1], w['location'][0]]
                   for w in payload.get('waypoints') or [] if w and w.get('location')]
        found = {}
        for i, leg in enumerate(payload['routes'][0].get('legs') or []):
            if i + 1 >= len(chunk):
                break
            a, b = chunk[i], chunk[i + 1]
            key = str(epoch(b[0]))
            if (leg.get('distance') or 0.0) > DETOUR_FACTOR * _straight(a, b) + DETOUR_SLACK_M:
                found[key] = [_vertex(b[1], b[2])]
                continue
            vertices = []
            for step in leg.get('steps') or []:
                for lon, lat in (step.get('geometry') or {}).get('coordinates') or []:
                    point = _vertex(lat, lon)
                    if not vertices or vertices[-1] != point:
                        vertices.append(point)
            if len(vertices) < 2 and len(snapped) > i + 1:
                vertices = [_vertex(*snapped[i]), _vertex(*snapped[i + 1])]
            found[key] = vertices or [_vertex(b[1], b[2])]
        return found
