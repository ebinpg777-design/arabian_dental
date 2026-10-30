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
from .location_live import JITTER_M, MAX_KMH, classify_modes, epoch, road_step

_logger = logging.getLogger(__name__)

DEFAULT_ROUTING_URL = 'https://router.project-osrm.org/route/v1/driving/'
ROUTING_TIMEOUT = 8
ROAD_WAYPOINTS = 100          # fixes per request; the public server takes 200
ROAD_REQUESTS_PER_CALL = 6    # a first look at a long day; the rest on the next look
DETOUR_FACTOR = 1.5           # by road this much longer than the straight gap...
DETOUR_SLACK_M = 150.0        # ...plus this: a fix snapped to the wrong road
FAILED_RETRY_SECONDS = 600
ROAD_OPEN_DAYS = 7            # a day this recent whose trip is not approved yet keeps being measured
ROAD_TEAM_ASK_SECONDS = 60    # the overview asks for a person's newest stretch this often at most


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


def matching_url(env):
    """Where whole stretches are map-matched: `lab_fieldwork.matching_url`, else the
    route URL's match service when the lab runs a routing server of its own (the
    public demo server allows ten points a match), else ''. Under the test runner
    as routing_url."""
    if odoo.modules.module.current_test and not env.context.get('lab_test_routing'):
        return ''
    parameter = env['ir.config_parameter'].sudo().search(
        [('key', '=', 'lab_fieldwork.matching_url')], limit=1)
    if parameter:
        return (parameter.value or '').strip()
    route = routing_url(env)
    if route and route != DEFAULT_ROUTING_URL and '/route/v1/' in route:
        return route.replace('/route/v1/', '/match/v1/')
    return ''


MATCH_RADIUS_M = 100.0         # a fix is looked for on a road within this - NOT within the accuracy the
                               # phone claims: at "6-15 m" the matcher threw half of a real ride away
                               # (43 of 85 fixes, 2026-10-26); at 100 m it kept all but the two off the roads
LONG_GAP_M = 300.0             # a step the matcher left straight and this long is asked of the router
MATCH_DETOUR_FACTOR = 3.0      # a matched leg this much longer than its gap, plus...
MATCH_DETOUR_SLACK_M = 300.0   # ...this, is drawn straight: a loop the matcher invented


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
    def get_road_trail(self, user_id=None, day=None, since=None):
        """One person's day along the roads, for the map (field_live.js loadRoad):
        {'state': ok|failed|off, 'legs': as get_live_map's or None when roads are
        off, 'complete': whether every step has its road yet, 'through': the time
        of the last vertex, 'vertices': how many the whole day has}. With `since` -
        the time the browser already holds the trail through - only the vertices
        after it travel (see _road_answer)."""
        me = self.env.user
        user = self.env['res.users'].browse(int(user_id)) if user_id else me
        if user != me and not self._is_manager():
            raise AccessError(_("Only field managers can see where others are."))
        day = fields.Date.to_date(day) if day else self._today_for(me)
        return self._road_answer(user, day, since)

    @api.model
    def get_road_trails(self, day=None, since=None):
        """Everybody's day along the roads in one call, for the overview (live_map.js
        loadRoads): {user id: get_road_trail's answer} for each id in `since`, which
        maps it to the time the browser holds that person's trail through, or null.
        The overview drew the fixes joined straight and only the person in focus
        followed the roads; on winding roads the straight lines cut every corner and
        read as a fault (client, 2026-10-26: "tracking is not in road")."""
        if not self._is_manager():
            raise AccessError(_("Only field managers can see where others are."))
        day = fields.Date.to_date(day) if day else self._today_for(self.env.user)
        Users = self.env['res.users'].with_context(active_test=False)
        out = {}
        for user_id, held in (since or {}).items():
            user = Users.browse(int(user_id))
            if user.exists():
                out[str(user.id)] = self._road_answer(user, day, held)
        return out

    @api.model
    def _road_answer(self, user, day, since=None):
        """get_road_trail's answer. A first look (no `since`) asks the service for
        up to ROAD_REQUESTS_PER_CALL stretches, as the person in focus always did; a
        look that only wants what is new asks for one stretch at most, and none
        within ROAD_TEAM_ASK_SECONDS of the last - the job fills the rest every
        five minutes - so an overview of ten people re-read after every batch of
        positions keeps the routing service's load bounded."""
        budget = ROAD_REQUESTS_PER_CALL
        if since is not None:
            road = self.env['lab.location.road'].sudo().search(
                [('user_id', '=', user.id), ('date', '=', day)], limit=1)
            # write_date is the database's clock (cr.now()), so it is read against that
            recent = road.write_date and \
                (self.env.cr.now() - road.write_date).total_seconds() < ROAD_TEAM_ASK_SECONDS
            budget = 0 if recent else 1
        answer = self._road_legs(user, day, budget=budget)
        legs = answer['legs']
        if legs is None:
            answer.update({'through': None, 'vertices': 0})
            return answer
        answer['vertices'] = sum(len(leg) for leg in legs)
        answer['through'] = max((leg[-1][2] for leg in legs if leg), default=None)
        if since is not None:
            cut = [[v for v in leg if v[2] > since] for leg in legs]
            answer['legs'] = [leg for leg in cut if leg]
        return answer

    @api.model
    def _road_legs(self, user, day, budget=ROAD_REQUESTS_PER_CALL):
        url = routing_url(self.env) if self._road_trail_enabled() else ''
        if not url:
            return {'state': 'off', 'legs': None, 'complete': True}
        match_url = matching_url(self.env)
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
        # One linked trail (2026-10-24): the road across a silence is asked too.
        for leg in ([points] if points else []):
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
                # known for THIS step: a late fix between two read ones changes the step
                if road_step(cache, a, b) is not None or jump:
                    run = None
                    continue
                if run is None:
                    run = [a]
                    runs.append(run)
                run.append(b)

        # Ask for the roads not known yet - a bounded number of requests a call.
        now = fields.Datetime.now()
        state, complete, asked = 'ok', True, 0
        if runs and budget and not (road and self._hold_road(road)):
            # (no row at all: somebody is creating it this very moment - _for lost the
            # race to a transaction not committed yet; the next look finds it)
            # Somebody else - the overview, the person's own focus look, the job - is
            # asking for this trail this very moment: let them, and draw what is known.
            # Two writers used to collide ("could not serialize access", 2026-10-26)
            # and Odoo retried the whole request a second and a half later.
            budget, complete = 0, False
        if road.state == 'failed' and road.failed_at and \
                now - road.failed_at < timedelta(seconds=FAILED_RETRY_SECONDS):
            state, complete = 'failed', bool(not runs)
        else:
            for run in runs:
                for start in range(0, len(run) - 1, ROAD_WAYPOINTS - 1):
                    if asked >= budget:
                        complete = False
                        break
                    asked += 1
                    try:
                        cache.update(self._ask_roads(url, run[start:start + ROAD_WAYPOINTS], match_url))
                    except Exception as error:   # noqa: BLE001 - a map must never break the board
                        _logger.info("road trail %s %s: no roads (%s)", user.login, day, error)
                        state, complete = 'failed', False
                        break
                if state == 'failed' or not complete:
                    break
            if (asked or state == 'failed') and road:
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
            last = first
            for b in wps[1:]:
                mode = mode_of.get(id(b), 'still')
                when, speed = epoch(b[0]), b[3]
                geometry = road_step(cache, last, b)
                if geometry == []:
                    continue        # the map-matcher put this fix off every road: not drawn
                last = b
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
    def _hold_road(self, road):
        """Take the row for this transaction, or say that another one holds it."""
        try:
            with self.env.cr.savepoint():
                self.env.cr.execute("SELECT id FROM lab_location_road WHERE id = %s FOR UPDATE NOWAIT", [road.id])
            return True
        except psycopg2.errors.LockNotAvailable:
            return False

    @api.model
    def _cron_fill_roads(self):
        """The roads of everyone recording today, a bounded number of requests each,
        every five minutes: the team view draws a stretch the phone crossed with no
        signal along its road from what is known here (get_live_map), and asks
        nothing itself. The person in focus still asks for the rest at once
        (get_road_trail). (client, 2026-10-23)

        Yesterday too, and each day's km read again after (2026-10-24): a stretch
        the phone did not record counts its road, not the straight line across
        (mode_km), and the app sends the whole day again when it ends - so the last
        stretches of a day, and anything that arrived late, are measured as well.

        And every day of the last ROAD_OPEN_DAYS whose trip is not approved yet
        (2026-10-30): a road is held for the two fixes it was asked for, so a day
        whose fixes change after - a phone sending it again days later, the trail
        read anew (drop_headless) - had its new stretches counted straight for good
        once it was older than yesterday. Shajimon, 26-28 Oct: 13 stretches never
        asked, 4-9 km a day under his odometer. A complete day asks nothing more."""
        if not (self._road_trail_enabled() and routing_url(self.env)):
            return
        Ping = self.sudo()
        Users = self.env['res.users'].sudo().with_context(active_test=False)
        since = fields.Date.today() - timedelta(days=ROAD_OPEN_DAYS)
        open_days = {(trip.user_id.id, trip.date) for trip in self.env['lab.trip'].sudo().search(
            [('date', '>=', since), ('state', 'in', ('open', 'closed'))])}
        self.env.cr.execute("SELECT DISTINCT user_id, date FROM lab_location_ping WHERE date >= %s ORDER BY date",
                            [since])
        filled = 0
        for user_id, day in self.env.cr.fetchall():
            user = Users.browse(user_id)
            if day < Ping._today_for(user) - timedelta(days=1) and (user_id, day) not in open_days:
                continue
            Ping._road_legs(user, day)
            Ping._refresh_day_km(user, day)
            filled += 1
        _logger.info("road trail: %d day(s) of trail brought up to date", filled)

    @api.model
    def _ask_roads(self, url, chunk, match_url=None):
        """{fix time: [[lat, lon], ...]} - the road from each fix of `chunk` to the
        next: map-matched as one trace where the lab has a matcher (_ask_match),
        else routed step by step (_ask_route). A matcher that fails - too big a
        trace, down - is answered by the router."""
        if match_url:
            try:
                found = self._ask_match(match_url, chunk)
                return self._route_long_gaps(url, chunk, found)
            except Exception as error:   # noqa: BLE001 - the router still knows the roads
                _logger.info("road trail: match of %d fixes failed (%s), routing instead", len(chunk), error)
        return self._ask_route(url, chunk)

    @api.model
    def _route_long_gaps(self, url, chunk, found):
        """The steps the matcher left straight - a long silence between two fixes is
        no trace to match (Shajimon, 2026-10-27: 10 km across the fields after the
        phone went quiet) - asked of the router, each on its own, as before the
        matcher: the road between the two fixes, or straight when it is a detour."""
        for a, b in zip(chunk, chunk[1:]):
            key = str(epoch(b[0]))
            road = found.get(key)
            if road is None or len(road) != 1 or _straight(a, b) < LONG_GAP_M:
                continue
            try:
                found.update(self._ask_route(url, [a, b]))
            except Exception as error:   # noqa: BLE001 - straight stays straight
                _logger.info("road trail: route of a %d m gap failed (%s)", _straight(a, b), error)
        return found

    @api.model
    def _ask_match(self, url, chunk):
        """{fix time: [[lat, lon], ...]} by OSRM's match service, the whole chunk as
        one trace with MATCH_RADIUS_M round each fix and its time: the matcher
        picks the roads consistent with the trace, and a fix it finds on no road
        - 500 m into a field, across the river - is an outlier, stored as [] so
        the trail, the km and the day sheet pass it by (2026-10-26: "triangles and
        straight lines and map crossing rivers"). The leg from one matched fix to
        the next is keyed by the latter's time; a matched leg far longer than the
        gap it bridges is a loop the matcher invented, drawn straight."""
        coordinates = ';'.join('%s,%s' % (p[2], p[1]) for p in chunk)
        radiuses = ';'.join(str(int(MATCH_RADIUS_M)) for _p in chunk)
        timestamps = ';'.join(str(epoch(p[0])) for p in chunk)
        response = requests.get(
            url + coordinates,
            params={'overview': 'false', 'geometries': 'geojson', 'steps': 'true', 'gaps': 'ignore',
                    'tidy': 'false', 'radiuses': radiuses, 'timestamps': timestamps},
            timeout=ROUTING_TIMEOUT)
        payload = response.json() if response.content else {}
        if payload.get('code') == 'NoMatch':
            # nothing of the trace sits on a road the matcher knows: straight, all of it
            return {str(epoch(b[0])): [_vertex(b[1], b[2])] for b in chunk[1:]}
        response.raise_for_status()
        if payload.get('code') != 'Ok':
            raise ValueError(payload.get('message') or payload.get('code'))
        tracepoints = payload.get('tracepoints') or []
        matchings = payload.get('matchings') or []
        found = {}
        matched = None             # index of the last fix found on a road
        for i, point in enumerate(tracepoints[:len(chunk)]):
            key = str(epoch(chunk[i][0]))
            if not point:
                if i:
                    found[key] = []            # off every road: not on the trail
                continue
            if matched is None:
                if i:
                    found[key] = [_vertex(chunk[i][1], chunk[i][2])]
                matched = i
                continue
            before = tracepoints[matched]
            same = point.get('matchings_index') == before.get('matchings_index') \
                and point.get('waypoint_index') == (before.get('waypoint_index') or 0) + 1
            leg = None
            if same and point.get('matchings_index') is not None:
                legs = (matchings[point['matchings_index']] or {}).get('legs') or []
                index = before.get('waypoint_index') or 0
                leg = legs[index] if index < len(legs) else None
            vertices = []
            if leg:
                for step in leg.get('steps') or []:
                    for lon, lat in (step.get('geometry') or {}).get('coordinates') or []:
                        vertex = _vertex(lat, lon)
                        if not vertices or vertices[-1] != vertex:
                            vertices.append(vertex)
                gap = _straight(chunk[matched], chunk[i])
                if (leg.get('distance') or 0.0) > MATCH_DETOUR_FACTOR * gap + MATCH_DETOUR_SLACK_M:
                    vertices = []
            found[key] = vertices if len(vertices) > 1 else [_vertex(chunk[i][1], chunk[i][2])]
            matched = i
        return found

    @api.model
    def _ask_route(self, url, chunk):
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
