# -*- coding: utf-8 -*-
"""The trail along the roads (client, 2026-10-23: "show the map through roads instead
of straight lines"). Nothing here touches the network: the routing service is a
function, as in test_day_track."""
import math
from datetime import timedelta
from unittest.mock import patch

from freezegun import freeze_time

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .test_live_track import LiveTrackCase
from ..models import location_live, location_road
from ..models.lab_visit import metres_between

ROUTING = 'https://routing.test/route/v1/driving/'
MATCHING = 'https://routing.test/match/v1/driving/'
NORTH = 1 / 111_320.0


def fake_osrm(detours=(), fail=False):
    """A routing service in a function: the road from each coordinate to the next is
    the straight line with a bend east of its middle, 1.1 times as long - except the
    steps in `detours` (by index), five times as long, as a fix snapped to the wrong
    road comes back. Returns (the function, the calls it took)."""
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, params))
        if fail:
            raise OSError('no route to host')
        coords = [tuple(float(x) for x in c.split(',')) for c in url[len(ROUTING):].split(';')]
        legs = []
        for i, (a, b) in enumerate(zip(coords, coords[1:])):
            straight = metres_between(a[1], a[0], b[1], b[0]) or 0.0
            mid = [(a[0] + b[0]) / 2 + 0.0003, (a[1] + b[1]) / 2]
            legs.append({
                'distance': straight * (5.0 if i in detours else 1.1),
                'steps': [{'geometry': {'coordinates': [list(a), mid]}},
                          {'geometry': {'coordinates': [mid, list(b)]}}],
            })

        class Response:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {'code': 'Ok',
                        'routes': [{'legs': legs, 'distance': sum(l['distance'] for l in legs)}],
                        'waypoints': [{'location': list(c)} for c in coords]}
        return Response()
    return fake_get, calls


def fake_services(outliers=(), match_fails=False):
    """A routing server of our own in a function: the route service as fake_osrm's,
    and the match service, which finds every fix on a road but the `outliers` (by
    index in the trace) and gives the leg from each matched fix to the next - the
    straight line with a bend east, 1.1 times as long. `match_fails` answers the
    match service with 400 TooBig, as the public demo server does."""
    calls = []
    route_get, _route_calls = fake_osrm()

    class Response:
        def __init__(self, status, body):
            self.status_code, self._body, self.content = status, body, b'x'

        def raise_for_status(self):
            if self.status_code != 200:
                raise OSError('HTTP %s' % self.status_code)

        def json(self):
            return self._body

    def fake_get(url, params=None, timeout=None):
        if '/match/' not in url:
            calls.append((url, params))
            return route_get(url, params=params, timeout=timeout)
        calls.append((url, params))
        if match_fails:
            return Response(400, {'code': 'TooBig', 'message': 'Too many trace coordinates'})
        coords = [tuple(float(x) for x in c.split(',')) for c in url.split('/driving/')[1].split(';')]
        tracepoints, legs, waypoint = [], [], 0
        last = None
        for i, c in enumerate(coords):
            if i in outliers:
                tracepoints.append(None)
                continue
            if last is not None:
                a, b = coords[last], c
                straight = metres_between(a[1], a[0], b[1], b[0]) or 0.0
                mid = [(a[0] + b[0]) / 2 + 0.0003, (a[1] + b[1]) / 2]
                legs.append({'distance': straight * 1.1,
                             'steps': [{'geometry': {'coordinates': [list(a), mid]}},
                                       {'geometry': {'coordinates': [mid, list(b)]}}]})
            tracepoints.append({'matchings_index': 0, 'waypoint_index': waypoint, 'location': list(c)})
            waypoint += 1
            last = i
        return Response(200, {'code': 'Ok', 'matchings': [{'legs': legs, 'confidence': 0.9}],
                              'tracepoints': tracepoints})
    return fake_get, calls


def fake_osrm_longer(factor):
    """A routing service whose road between two fixes is `factor` times the straight
    line - a bend east at the middle - and says so in its distance."""
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, params))
        coords = [tuple(float(x) for x in c.split(',')) for c in url[len(ROUTING):].split(';')]
        legs = []
        for a, b in zip(coords, coords[1:]):
            straight = metres_between(a[1], a[0], b[1], b[0]) or 0.0
            east = math.sqrt(max(0.0, (factor * straight / 2) ** 2 - (straight / 2) ** 2))
            mid = [(a[0] + b[0]) / 2 + east / (111_320.0 * math.cos(math.radians(a[1]))), (a[1] + b[1]) / 2]
            legs.append({'distance': straight * factor,
                         'steps': [{'geometry': {'coordinates': [list(a), mid]}},
                                   {'geometry': {'coordinates': [mid, list(b)]}}]})

        class Response:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {'code': 'Ok', 'routes': [{'legs': legs}],
                        'waypoints': [{'location': list(c)} for c in coords]}
        return Response()
    return fake_get, calls


@tagged('post_install', '-at_install')
class TestRoadTrail(LiveTrackCase):

    def setUp(self):
        super().setUp()
        day = fields.Date.context_today(self.exec_user)
        self._freezer = freeze_time(day.strftime('%Y-%m-%d') + ' 05:30:00')
        self._freezer.start()
        self.addCleanup(self._freezer.stop)
        self.env['ir.config_parameter'].sudo().set_str('lab_fieldwork.routing_url', ROUTING)
        self.env['ir.config_parameter'].sudo().set_str('lab_fieldwork.matching_url', '')
        self.Ping = self.env['lab.location.ping'].with_user(self.ops_user) \
            .with_context(lab_test_routing=True)
        self.now = fields.Datetime.now() - timedelta(minutes=30)
        self.env['hr.attendance'].sudo().create({
            'employee_id': self.employee.id, 'check_in': self.now - timedelta(minutes=30),
            'check_out': False})

    def _drive(self, start_step, steps, metres_each=200.0, seconds_each=20, speed=36.0):
        """`steps` fixes north of 10N 76E, from step `start_step`, as the executive."""
        self.env['lab.location.ping'].with_user(self.exec_user).record([
            {'latitude': 10.0 + (start_step + i) * metres_each * NORTH, 'longitude': 76.0,
             'accuracy_m': 8, 'speed_kmh': speed,
             'ts': fields.Datetime.to_string(self.now + timedelta(seconds=(start_step + i) * seconds_each))}
            for i in range(steps)])

    def _patched(self, fake):
        return patch('odoo.addons.lab_fieldwork.models.location_road.requests.get', fake)

    def test_the_trail_is_drawn_along_the_roads_and_each_step_is_asked_once(self):
        self._drive(0, 6)
        fake, calls = fake_osrm()
        with self._patched(fake):
            road = self.Ping.get_road_trail(user_id=self.exec_user.id)
        self.assertEqual((road['state'], road['complete'], len(calls)), ('ok', True, 1))
        self.assertEqual(len(calls[0][0][len(ROUTING):].split(';')), 6, "every fix a waypoint")
        [leg] = road['legs']
        # the first fix, then for each of the 5 steps the bend and the fix: 11 vertices
        self.assertEqual(len(leg), 11)
        self.assertEqual(leg[0][4], 'still')
        self.assertEqual({v[4] for v in leg[1:]}, {'vehicle'})
        self.assertEqual(leg[1][2], leg[2][2], "the bend carries the time of the fix its step ends at")
        self.assertAlmostEqual(leg[1][1], 76.0003, 6, "the bend is east of the line")
        self.assertGreater(leg[2][2], leg[0][2])
        with self._patched(fake):
            again = self.Ping.get_road_trail(user_id=self.exec_user.id)
        self.assertEqual(len(calls), 1, "read from the cache, the service is not asked again")
        self.assertEqual(again['legs'], road['legs'])
        record = self.env['lab.location.road'].sudo().search([('user_id', '=', self.exec_user.id)])
        self.assertEqual((len(record), record.state, record.request_count), (1, 'ok', 1))

    def _age_road(self, seconds):
        """The person's road record written `seconds` ago (the clock is frozen).
        Flushed first: a pending write would set write_date again on its way out."""
        self.env.flush_all()
        self.env.cr.execute("UPDATE lab_location_road SET write_date = write_date - %s * interval '1 second'"
                            " WHERE user_id = %s", [seconds, self.exec_user.id])
        self.env['lab.location.road'].invalidate_model()

    def test_the_overview_reads_everyone_whole_and_then_only_what_is_new(self):
        """The overview asks for the whole team in one call, holding each person's
        trail through a time: only the vertices after it travel (2026-10-26)."""
        uid = str(self.exec_user.id)
        self._drive(0, 6)
        fake, calls = fake_osrm()
        with self._patched(fake):
            answer = self.Ping.get_road_trails(since={uid: None})[uid]
        self.assertEqual((answer['state'], answer['complete'], len(calls)), ('ok', True, 1))
        [leg] = answer['legs']
        self.assertEqual((len(leg), answer['vertices'], answer['through']), (11, 11, leg[-1][2]))
        self._drive(6, 2)
        self._age_road(120)
        with self._patched(fake):
            fresh = self.Ping.get_road_trails(since={uid: answer['through']})[uid]
        self.assertEqual(len(calls), 2, "asked for the new stretch alone")
        [new] = fresh['legs']
        self.assertEqual(len(new), 4, "two steps: the bend and the fix of each")
        self.assertTrue(all(v[2] > answer['through'] for v in new))
        self.assertEqual((fresh['vertices'], fresh['through']), (15, new[-1][2]))
        with self._patched(fake):
            same = self.Ping.get_road_trails(since={uid: fresh['through']})[uid]
        self.assertEqual((same['legs'], same['vertices'], len(calls)), ([], 15, 2), "nothing new: nothing travels")
        with self.assertRaises(AccessError):
            self.Ping.with_user(self.exec_user).get_road_trails(since={uid: None})

    def test_the_overview_asks_for_a_persons_new_stretch_once_a_minute_at_most(self):
        uid = str(self.exec_user.id)
        self._drive(0, 6)
        fake, calls = fake_osrm()
        with self._patched(fake):
            answer = self.Ping.get_road_trails(since={uid: None})[uid]
        self._drive(6, 2)
        with self._patched(fake):
            soon = self.Ping.get_road_trails(since={uid: answer['through']})[uid]
        self.assertEqual(len(calls), 1, "just asked for this person: not again within the minute")
        self.assertEqual((soon['complete'], len(soon['legs'][0])), (False, 2), "the new steps straight for now")
        self._age_road(61)
        with self._patched(fake):
            later = self.Ping.get_road_trails(since={uid: answer['through']})[uid]
        self.assertEqual((len(calls), later['complete'], len(later['legs'][0])), (2, True, 4))

    def test_the_day_sheet_map_follows_the_roads_and_leaves_out_a_jump(self):
        """The day sheet's map read the raw fixes joined straight; it now reads the
        trail Field Live reads - along the roads, without a jump the phone came
        straight back from (2026-10-26)."""
        self._drive(0, 6)
        far = 10.0 + 5 * 200 * NORTH            # where fix 5 is
        self.env['lab.location.ping'].with_user(self.exec_user).record([
            {'latitude': far, 'longitude': 76.0045, 'accuracy_m': 7, 'speed_kmh': 3.0,
             'ts': fields.Datetime.to_string(self.now + timedelta(seconds=110))},
            {'latitude': far + NORTH, 'longitude': 76.0, 'accuracy_m': 7, 'speed_kmh': 0.0,
             'ts': fields.Datetime.to_string(self.now + timedelta(seconds=130))}])
        fake, calls = fake_osrm()
        with self._patched(fake):
            self.Ping.get_road_trail(user_id=self.exec_user.id)
        day = fields.Date.context_today(self.exec_user)
        Sheet = self.env['lab.daily.update'].sudo()
        sheet = Sheet.search([('user_id', '=', self.exec_user.id), ('date', '=', day)], limit=1) \
            or Sheet.create({'user_id': self.exec_user.id, 'date': day})
        track = sheet.get_day_track()
        back = self.env['lab.location.ping'].sudo().search(
            [('user_id', '=', self.exec_user.id)], order='ts desc', limit=1)
        self.assertEqual(back.speed_kmh, 0.0, "the phone said 0 km/h: that is its speed, not the jump's")
        [segment] = track['trail']
        lons = [round(v[1], 4) for v in segment]
        self.assertLess(max(lons), 76.001, "the jump 500 m east is not on the day sheet")
        self.assertEqual(lons.count(76.0003), 5, "each of the five steps runs by its road's bend")
        self.assertEqual(len(segment), 12, "first fix, five steps of bend and fix, the fix back")
        self.assertEqual(track['trail_km'], round(5 * 200 * 1.1 / 1000, 1), "measured along the roads")

    def _outlier_drive(self):
        """Seven fixes north, the fourth 400 m east of the line - riding, and reading
        a speed that bears the jump out, so the trail's own rules keep it: only a
        map-matcher can tell it sits on no road."""
        self._drive(0, 3)
        east = 400 / (111_320.0 * math.cos(math.radians(10.0)))
        self.env['lab.location.ping'].with_user(self.exec_user).record([
            {'latitude': 10.0 + 3 * 200 * NORTH, 'longitude': 76.0 + east, 'accuracy_m': 8,
             'speed_kmh': 60.0, 'motion': 'vehicle',
             'ts': fields.Datetime.to_string(self.now + timedelta(seconds=60))}])
        self._drive(4, 3)

    def test_a_matched_trail_leaves_a_fix_on_no_road_out(self):
        """With a routing server of our own the whole stretch is map-matched: a fix the
        matcher finds on no road - 400 m into a field, across the river - is stored as
        an empty road, and the trail, the km and the day sheet pass it by (2026-10-26:
        "triangles and straight lines and map crossing rivers")."""
        self.env['ir.config_parameter'].sudo().set_str('lab_fieldwork.matching_url', MATCHING)
        self._outlier_drive()
        user = self.exec_user
        day = fields.Date.context_today(user)
        self.assertEqual(len(self.Ping.sudo()._day_points(user, day)), 7, "the trail's own rules keep it")
        fake, calls = fake_services(outliers=(3,))
        with self._patched(fake):
            road = self.Ping.get_road_trail(user_id=user.id)
        self.assertEqual([('/match/' in u) for u, _p in calls], [True], "matched as one trace, routed nowhere")
        self.assertEqual((road['state'], road['complete']), ('ok', True))
        [leg] = road['legs']
        self.assertLess(max(v[1] for v in leg), 76.001, "the fix in the field is not drawn")
        self.assertEqual(len(leg), 11, "first fix, then bend and fix for the five legs between matched fixes")
        steps = self.Ping.sudo()._road_steps(user, day)
        outlier = self.Ping.sudo()._day_points(user, day)[3]
        self.assertEqual(steps[str(location_live.epoch(outlier[0]))], [], "known: off every road")
        km = location_live.mode_km(self.Ping.sudo()._day_points(user, day), steps=steps)
        # 1,200 m along the bent roads is 1,248 m; with the spike, 894 m more
        self.assertTrue(1.2 < km['travel'] < 1.3, "the spike never counted: %s" % km)
        Sheet = self.env['lab.daily.update'].sudo()
        sheet = Sheet.search([('user_id', '=', user.id), ('date', '=', day)], limit=1) \
            or Sheet.create({'user_id': user.id, 'date': day})
        [segment] = sheet.get_day_track()['trail']
        self.assertLess(max(v[1] for v in segment), 76.001, "nor on the day sheet")
        self.assertEqual(len(segment), 11)

    def test_a_long_silence_the_matcher_leaves_straight_is_asked_of_the_router(self):
        """The matcher has no trace across a long silence; the router has the road
        between its two ends (Shajimon, 2026-10-27: 10 km drawn across the fields)."""
        self.env['ir.config_parameter'].sudo().set_str('lab_fieldwork.matching_url', MATCHING)
        self._drive(0, 3)
        self._drive(40, 3)          # 7 km on, 12 minutes later: the phone heard nothing between
        fake, calls = fake_services()
        Ping = self.Ping.sudo()
        day = fields.Date.context_today(self.exec_user)
        with self._patched(fake), patch.object(type(Ping), '_ask_match', lambda self, url, chunk: {
                str(location_live.epoch(b[0])): [location_road._vertex(b[1], b[2])] for b in chunk[1:]}):
            road = self.Ping.get_road_trail(user_id=self.exec_user.id)
        routed = [u for u, _p in calls if '/route/' in u]
        self.assertEqual(len(routed), 1, "the one long gap, and nothing else, asked of the router")
        steps = Ping._road_steps(self.exec_user, day)
        gap_end = Ping._day_points(self.exec_user, day)[3]
        self.assertEqual(len(steps[str(location_live.epoch(gap_end[0]))]), 3, "the gap along its road: fix, bend, fix")

    def test_a_matcher_that_fails_is_answered_by_the_router(self):
        self.env['ir.config_parameter'].sudo().set_str('lab_fieldwork.matching_url', MATCHING)
        self._drive(0, 6)
        fake, calls = fake_services(match_fails=True)
        with self._patched(fake):
            road = self.Ping.get_road_trail(user_id=self.exec_user.id)
        self.assertEqual([('/match/' in u) for u, _p in calls], [True, False], "the matcher refused, the router answered")
        [leg] = road['legs']
        self.assertEqual((road['state'], len(leg)), ('ok', 11))

    def test_the_match_service_follows_a_routing_server_of_our_own(self):
        icp = self.env['ir.config_parameter'].sudo()
        env = self.env(context=dict(self.env.context, lab_test_routing=True))
        icp.search([('key', '=', 'lab_fieldwork.matching_url')]).unlink()
        icp.set_str('lab_fieldwork.routing_url', location_road.DEFAULT_ROUTING_URL)
        self.assertEqual(location_road.matching_url(env), '', "the public demo server matches ten points: not used")
        icp.set_str('lab_fieldwork.routing_url', 'http://127.0.0.1:5000/route/v1/driving/')
        self.assertEqual(location_road.matching_url(env), 'http://127.0.0.1:5000/match/v1/driving/')
        icp.set_str('lab_fieldwork.matching_url', 'https://matcher.example/match/v1/driving/')
        self.assertEqual(location_road.matching_url(env), 'https://matcher.example/match/v1/driving/')
        icp.set_str('lab_fieldwork.matching_url', '')
        self.assertEqual(location_road.matching_url(env), '', "set empty: off")

    def test_a_fix_snapped_to_the_wrong_road_is_drawn_straight(self):
        self._drive(0, 5)
        fake, calls = fake_osrm(detours={2})
        with self._patched(fake):
            [leg] = self.Ping.get_road_trail(user_id=self.exec_user.id)['legs']
        # 4 steps: three with a bend (2 vertices each), the detour one straight (1)
        self.assertEqual(len(leg), 1 + 2 + 2 + 1 + 2)
        bends = [v for v in leg if abs(v[1] - 76.0003) < 1e-6]
        self.assertEqual(len(bends), 3)

    def test_new_fixes_are_asked_for_on_their_own(self):
        self._drive(0, 4)
        fake, calls = fake_osrm()
        with self._patched(fake):
            self.Ping.get_road_trail(user_id=self.exec_user.id)
            self._drive(4, 3)
            road = self.Ping.get_road_trail(user_id=self.exec_user.id)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(calls[1][0][len(ROUTING):].split(';')), 4,
                         "the last known fix and the three new ones")
        self.assertEqual(len(road['legs'][0]), 1 + 6 * 2)

    def test_a_service_that_does_not_answer_is_asked_once_in_ten_minutes(self):
        self._drive(0, 4)
        fake, calls = fake_osrm(fail=True)
        with self._patched(fake):
            road = self.Ping.get_road_trail(user_id=self.exec_user.id)
            self.assertEqual((road['state'], road['complete'], len(calls)), ('failed', False, 1))
            self.assertEqual(len(road['legs'][0]), 4, "drawn straight, as before")
            self.assertEqual(self.Ping.get_road_trail(user_id=self.exec_user.id)['state'], 'failed')
            self.assertEqual(len(calls), 1, "an outage is not asked again on every poll")
        later = fields.Datetime.now() + timedelta(minutes=11)   # from the frozen clock
        self._freezer.stop()
        self._freezer = freeze_time(later)
        self._freezer.start()
        good, calls2 = fake_osrm()
        with self._patched(good):
            road = self.Ping.get_road_trail(user_id=self.exec_user.id)
        self.assertEqual((road['state'], len(calls2)), ('ok', 1))

    def test_roads_can_be_switched_off_and_the_executive_sees_only_themselves(self):
        self._drive(0, 3)
        fake, calls = fake_osrm()
        self.env['ir.config_parameter'].sudo().set_str('lab_fieldwork.road_trail', 'False')
        with self._patched(fake):
            self.assertEqual(self.Ping.get_road_trail(user_id=self.exec_user.id)['state'], 'off')
        self.env['ir.config_parameter'].sudo().set_str('lab_fieldwork.road_trail', 'True')
        self.env['ir.config_parameter'].sudo().set_str('lab_fieldwork.routing_url', '')
        with self._patched(fake):
            self.assertEqual(self.Ping.get_road_trail(user_id=self.exec_user.id)['state'], 'off')
        self.assertEqual(calls, [])
        self.assertEqual(location_road.routing_url(self.env), '', "off under the test runner by default")
        mine = self.env['lab.location.ping'].with_user(self.exec_user)
        with self.assertRaises(AccessError):
            mine.get_road_trail(user_id=self.ops_user.id)
        self.assertEqual(mine.get_road_trail()['state'], 'off', "my own day: allowed")

    # ------------------------------------------- a stretch crossed with no signal
    def _blind_day(self):
        """Three fixes 200 m and 20 s apart, then 900 m crossed in 150 s with nothing
        recorded - a blind stretch - and three fixes more."""
        fixes, metres, when = [], 0.0, self.now
        for _i in range(3):
            fixes.append((metres, when))
            metres, when = metres + 200.0, when + timedelta(seconds=20)
        metres, when = metres + 700.0, when + timedelta(seconds=130)
        for _i in range(3):
            fixes.append((metres, when))
            metres, when = metres + 200.0, when + timedelta(seconds=20)
        self.env['lab.location.ping'].with_user(self.exec_user).record([
            {'latitude': 10.0 + m * NORTH, 'longitude': 76.0, 'accuracy_m': 8, 'speed_kmh': 36.0,
             'ts': fields.Datetime.to_string(t)} for m, t in fixes])

    def test_a_blind_stretch_is_drawn_along_its_road_once_the_road_is_known(self):
        """Client, 2026-10-23: "why these dots ... the map should be realistic"."""
        self._blind_day()
        [ex] = self.Ping.get_live_map(user_ids=[self.exec_user.id])['executives']
        self.assertIn('blind', {v[4] for leg in ex['legs'] for v in leg},
                      "no road known yet: the dotted straight line, as before")
        fake, calls = fake_osrm()
        with self._patched(fake):
            self.env['lab.location.ping'].with_context(lab_test_routing=True)._cron_fill_roads()
        self.assertEqual(len(calls), 1, "the five-minute job asked for the day's roads")
        [ex] = self.Ping.get_live_map(user_ids=[self.exec_user.id])['executives']
        vertices = [v for leg in ex['legs'] for v in leg]
        self.assertNotIn('blind', {v[4] for v in vertices}, "drawn as the drive it was")
        bends = [v for v in vertices if abs(v[1] - 76.0003) < 1e-6]
        self.assertEqual(len(bends), 1, "the road in the blind stretch only; the rest as recorded")
        with self._patched(fake):
            road = self.Ping.get_road_trail(user_id=self.exec_user.id)
        self.assertEqual(len(calls), 1, "the person in focus reads the same roads")
        self.assertNotIn('blind', {v[4] for leg in road['legs'] for v in leg})

    def test_the_job_asks_nothing_when_roads_are_off(self):
        self._blind_day()
        fake, calls = fake_osrm()
        self.env['ir.config_parameter'].sudo().set_str('lab_fieldwork.road_trail', 'False')
        with self._patched(fake):
            self.env['lab.location.ping'].with_context(lab_test_routing=True)._cron_fill_roads()
        self.assertEqual(calls, [])
        [ex] = self.Ping.get_live_map(user_ids=[self.exec_user.id])['executives']
        self.assertIn('blind', {v[4] for leg in ex['legs'] for v in leg})

    # ---------------------------------------- fixes that reach the server late
    def test_fixes_arriving_late_do_not_reuse_a_road_that_set_off_elsewhere(self):
        """A phone back in coverage sends the fixes it took meanwhile (2026-10-24):
        they land between two fixes whose road was already read. The step ending at
        the later fix now starts at a late one, so its stored road - set off from
        the earlier fix - is asked again rather than drawn running back there."""
        self._drive(0, 3)            # 0, 200, 400 m
        self._drive(5, 2)            # 1000, 1200 m: the phone silent for 600 m
        fake, calls = fake_osrm()
        with self._patched(fake):
            first = self.Ping.get_road_trail(user_id=self.exec_user.id)
        self.assertEqual((len(calls), len(first['legs'][0])), (1, 1 + 4 * 2))
        self._drive(3, 2)            # 600 and 800 m, taken meanwhile, arriving now
        with self._patched(fake):
            road = self.Ping.get_road_trail(user_id=self.exec_user.id)
        self.assertEqual(len(calls), 2, "one request: the late steps and the one now starting elsewhere")
        self.assertEqual(len(calls[1][0][len(ROUTING):].split(';')), 4,
                         "from the last read fix through the two late ones to the next")
        [leg] = road['legs']
        self.assertEqual(len(leg), 1 + 6 * 2, "every step along its own road")
        for v, w in zip(leg, leg[1:]):
            self.assertGreaterEqual(w[0], v[0] - 1e-9, "the trail only ever heads north: nothing runs back")

    # ------------------------------------------- the km of a stretch not recorded
    def test_a_stretch_not_recorded_counts_its_road_not_the_straight_line(self):
        """Client, 2026-10-24: "the KM showing also wrong... a big problem on
        reimbursing the KM allowance". A gap measured as the straight line across it
        under-counted every one."""
        t0 = fields.Datetime.now()
        pts = [(t0, 10.0, 76.0, 36.0), (t0 + timedelta(seconds=20), 10.0 + 200 * NORTH, 76.0, 36.0),
               (t0 + timedelta(seconds=380), 10.0 + 3200 * NORTH, 76.0, 36.0)]
        modes = location_live.classify_modes(pts)
        self.assertEqual(modes[2], 'blind')
        road = [[10.0 + 200 * NORTH, 76.0], [10.0 + 1700 * NORTH, 76.0 + 2000 / 109_600.0],
                [10.0 + 3200 * NORTH, 76.0]]
        steps = {str(location_live.epoch(pts[2][0])): road}
        straight = location_live.mode_km(pts, modes)
        along = location_live.mode_km(pts, modes, steps)
        self.assertAlmostEqual(straight['blind'], 3.0, 1)
        self.assertAlmostEqual(along['blind'], location_live.road_metres(road) / 1000.0, 1)
        self.assertGreater(along['travel'], straight['travel'] + 1.5, "5 km by road, not 3 across")
        self.assertEqual(along['blind_road'], along['blind'])
        # a one-vertex entry is a fix snapped to the wrong road: the straight line stands
        snapped = {str(location_live.epoch(pts[2][0])): [[10.0 + 3200 * NORTH, 76.0]]}
        self.assertEqual(location_live.mode_km(pts, modes, snapped), straight | {'blind_road': 0.0})
        # never shorter than the straight line, whatever the cache says
        short = {str(location_live.epoch(pts[2][0])): [[10.0 + 200 * NORTH, 76.0], [10.0 + 1000 * NORTH, 76.0]]}
        self.assertAlmostEqual(location_live.mode_km(pts, modes, short)['blind'], 3.0, 1)

    def test_the_job_measures_a_gap_on_the_trip_until_it_is_approved(self):
        self._blind_day()
        day = self.env['lab.location.ping']._today_for(self.exec_user)
        trip = self.env['lab.trip'].sudo().create({
            'user_id': self.exec_user.id, 'date': day, 'vehicle': 'bike', 'odo_start': 100, 'odo_end': 103})
        self.assertAlmostEqual(trip.gps_km, 1.7, 1, "0.8 km driven, 0.9 km across the gap")
        fake, _calls = fake_osrm_longer(1.3)
        with self._patched(fake):
            self.env['lab.location.ping'].with_context(lab_test_routing=True)._cron_fill_roads()
        self.assertAlmostEqual(trip.gps_km, (0.8 + 0.9) * 1.3, 1, "the drive and the gap along their roads")
        # approved: a signed figure is not changed under the people who signed it
        trip.sudo().state = 'approved'
        before = trip.gps_km
        later = self.now + timedelta(minutes=12)
        self.env['lab.location.ping'].with_user(self.exec_user).record([
            {'latitude': 10.0 + m * NORTH, 'longitude': 76.0, 'accuracy_m': 8, 'speed_kmh': 36.0,
             'ts': fields.Datetime.to_string(later + timedelta(seconds=s))}
            for m, s in ((4000.0, 0), (6000.0, 200), (6200.0, 220))])
        with self._patched(fake):
            self.env['lab.location.ping'].with_context(lab_test_routing=True)._cron_fill_roads()
        self.assertEqual(trip.gps_km, before)

    def test_a_drive_counts_its_road_and_a_snap_beside_it_does_not(self):
        """Straight lines cut every corner: 119 km on a day the vehicle read 128."""
        t0 = fields.Datetime.now()
        pts = [(t0, 10.0, 76.0, 36.0), (t0 + timedelta(seconds=20), 10.0 + 200 * NORTH, 76.0, 36.0)]
        modes = location_live.classify_modes(pts)
        self.assertEqual(modes[1], 'vehicle')
        key = str(location_live.epoch(pts[1][0]))
        bend = [[10.0, 76.0], [10.0 + 100 * NORTH, 76.0 + 60 / 109_600.0], [10.0 + 200 * NORTH, 76.0]]
        km = location_live.mode_km(pts, modes, {key: bend})['vehicle']
        self.assertAlmostEqual(km, location_live.road_metres(bend) / 1000.0, 2)
        self.assertGreater(km, 0.2)
        around = [[10.0, 76.0], [10.0, 76.0 + 300 / 109_600.0], [10.0 + 200 * NORTH, 76.0 + 300 / 109_600.0],
                  [10.0 + 200 * NORTH, 76.0]]
        self.assertAlmostEqual(location_live.mode_km(pts, modes, {key: around})['vehicle'], 0.2, 2,
                               "800 m around the block for a 200 m step: the road beside it")
