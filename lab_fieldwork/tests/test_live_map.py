# -*- coding: utf-8 -*-
"""The live layer over the trail: last position, status, arrivals the trail noticed,
halts, speed, the trip's km, and the one call the live map draws from.
(client, 2026-10-19: "implement live GPS in field executive visits and travel")"""
from datetime import timedelta

from freezegun import freeze_time

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .test_live_track import LiveTrackCase
from ..models import location_live


def walk(start_lat, start_lon, steps, metres_each=100.0, seconds_each=30, speed=None):
    """A straight walk north: `steps` fixes `metres_each` apart, `seconds_each` apart."""
    out = []
    for i in range(steps):
        out.append((start_lat + i * metres_each / 111_320.0, start_lon,
                    seconds_each * i, speed))
    return out


@tagged('post_install', '-at_install')
class TestLiveLayer(LiveTrackCase):

    def setUp(self):
        super().setUp()
        # 11:00 in Kolkata on the real date: every scenario here places pings
        # "N minutes ago" and reads the day they fall in, which after midnight IST
        # is yesterday for half of them. (2026-10-21, the suite run at 00:43)
        day = fields.Date.context_today(self.exec_user)
        self._freezer = freeze_time(day.strftime('%Y-%m-%d') + ' 05:30:00')
        self._freezer.start()
        self.addCleanup(self._freezer.stop)

    def _start_day(self, user, when):
        """On duty since `when` and still on duty now (an open attendance)."""
        return self.env['hr.attendance'].sudo().create({
            'employee_id': self.employee.id, 'check_in': when, 'check_out': False})

    def _pings(self, user, when, path):
        """Record `path` ([(lat, lon, +seconds, speed_kmh|None)]) from `when` as `user`."""
        Ping = self.env['lab.location.ping'].with_user(user)
        batch = []
        for lat, lon, offset, speed in path:
            batch.append({'latitude': lat, 'longitude': lon, 'accuracy_m': 10,
                          'ts': fields.Datetime.to_string(when + timedelta(seconds=offset)),
                          **({'speed_kmh': speed} if speed is not None else {})})
        return Ping.record(batch)

    def test_helpers_split_measure_and_thin_the_trail(self):
        t0 = fields.Datetime.now()
        pts = [(t0 + timedelta(seconds=s), 10.0 + s * 1e-5, 76.0, 20.0) for s in (0, 30, 60)]
        pts += [(t0 + timedelta(seconds=s), 10.01 + s * 1e-5, 76.0, 20.0) for s in (2000, 2030)]
        legs, gaps = location_live.split_legs(pts)
        self.assertEqual([len(l) for l in legs], [3, 2], "a 32-minute silence breaks the trail")
        self.assertEqual(len(gaps), 1)
        km = location_live.trail_km(pts)
        # the two legs walk ~66 m and ~33 m; the 1.1 km between them is a gap, not a road
        self.assertGreater(km, 0.05)
        self.assertLess(km, 0.2, "the gap itself (1.1 km) is never counted as distance")
        jumpy = [(t0, 10.0, 76.0, 0.0), (t0 + timedelta(seconds=10), 10.5, 76.0, 0.0)]
        self.assertEqual(location_live.trail_km(jumpy), 0.0, "55 km in ten seconds is a GPS jump")
        thinned = location_live.downsample(list(range(100)), 10)
        self.assertLessEqual(len(thinned), 11)
        self.assertEqual(thinned[-1], 99, "the last point always survives")

    def test_a_batch_writes_the_last_fix_forward_only(self):
        user = self.exec_user
        now = fields.Datetime.now() - timedelta(minutes=10)
        self._start_day(user, now - timedelta(minutes=30))
        self._pings(user, now, [(10.0, 76.0, 0, 30.0), (10.001, 76.0, 60, 25.0)])
        me = user.sudo()
        self.assertEqual((me.fw_last_lat, me.fw_last_speed), (10.001, 25.0))
        first_seen = me.fw_last_ping_at
        # a late batch from before does not move the position back
        self._pings(user, now - timedelta(minutes=5), [(9.9, 76.0, 0, 5.0)])
        self.assertEqual((me.fw_last_lat, me.fw_last_ping_at), (10.001, first_seen))
        ping = self.env['lab.location.ping'].sudo().search(
            [('user_id', '=', user.id)], order='ts desc', limit=1)
        self.assertEqual(ping.speed_kmh, 25.0, "the phone's own speed is kept")

    def test_speed_is_derived_when_the_phone_gives_none_and_flags_the_limit(self):
        user = self.exec_user
        now = fields.Datetime.now() - timedelta(minutes=5)
        self._start_day(user, now - timedelta(minutes=30))
        self.env['ir.config_parameter'].sudo().set_str('lab_fieldwork.speed_limit', '40')
        # 1 km in 60 s = 60 km/h, no speed sent
        self._pings(user, now, [(10.0, 76.0, 0, None), (10.0 + 1000 / 111_320.0, 76.0, 60, None)])
        last = self.env['lab.location.ping'].sudo().search([('user_id', '=', user.id)], order='ts desc', limit=1)
        self.assertAlmostEqual(last.speed_kmh, 60.0, delta=1.0)
        self.assertTrue(last.overspeed)
        self.assertEqual(self.env['lab.location.ping'].sudo()._day_summary(
            user, last.date)['overspeed_count'], 1)

    def test_the_trail_notices_arrival_and_leaving(self):
        user = self.exec_user
        now = fields.Datetime.now() - timedelta(minutes=20)
        self._start_day(user, now - timedelta(minutes=30))
        clinic = self.env['res.partner'].create({
            'name': 'Live Clinic', 'is_clinic': True,
            'partner_latitude': 10.05, 'partner_longitude': 76.05, 'visit_radius_m': 200})
        day = self.env['lab.location.ping']._today_for(user)
        visit = self.env['lab.visit'].sudo().create({
            'partner_id': clinic.id, 'user_id': user.id, 'date': day})
        # on the way: nothing yet
        self._pings(user, now, [(10.0, 76.0, 0, 30.0)])
        self.assertFalse(visit.auto_arrived_at)
        # standing at the clinic while the visit is still only planned
        self._pings(user, now + timedelta(minutes=2), [(10.0501, 76.05, 0, 0.0)])
        self.assertTrue(visit.auto_arrived_at, "the phone inside the radius is noticed")
        state = self.env['lab.my.day'].with_user(user).tracking_state()
        self.assertEqual([n['kind'] for n in state['nudges']], ['arrived'])
        self.assertEqual(state['nudges'][0]['visit_id'], visit.id)
        self.assertIn('km_today', state)
        # they check in; later the phone walks 500 m away while the visit is open
        visit.with_user(user).do_check_in(10.0501, 76.05)
        self.assertEqual(visit.state, 'open')
        self._pings(user, now + timedelta(minutes=10), [(10.0501 + 500 / 111_320.0, 76.05, 0, 4.0)])
        self.assertTrue(visit.auto_left_at)
        state = self.env['lab.my.day'].with_user(user).tracking_state()
        self.assertEqual([n['kind'] for n in state['nudges']], ['left'])
        # back inside: it was a walk to the car, not a departure
        self._pings(user, now + timedelta(minutes=12), [(10.0502, 76.05, 0, 1.0)])
        self.assertFalse(visit.auto_left_at)

    def test_a_long_stop_away_from_clinics_is_a_halt_and_a_clinic_is_not(self):
        user = self.exec_user
        now = fields.Datetime.now() - timedelta(minutes=60)
        self._start_day(user, now - timedelta(minutes=30))
        self.env['ir.config_parameter'].sudo().set_str('lab_fieldwork.halt_minutes', '10')
        clinic = self.env['res.partner'].create({
            'name': 'Halt Clinic', 'is_clinic': True,
            'partner_latitude': 10.2, 'partner_longitude': 76.2, 'visit_radius_m': 200})
        day = self.env['lab.location.ping']._today_for(user)
        self.env['lab.visit'].sudo().create({'partner_id': clinic.id, 'user_id': user.id, 'date': day})
        # 15 minutes still at a tea shop (heartbeats every 5 min), then 15 minutes at the clinic
        path = [(10.0, 76.0, s, 0.0) for s in (0, 300, 600, 900)]
        path += [(10.2, 76.2, s, 0.0) for s in (1200, 1500, 1800, 2100)]
        path += [(10.3, 76.3, 2400, 30.0)]
        self._pings(user, now, path)
        halts = self.env['lab.location.halt'].sudo().search([('user_id', '=', user.id)])
        self.assertEqual(len(halts), 1, "the clinic stop is a visit, the tea shop a halt")
        self.assertEqual((halts.latitude, halts.minutes), (10.0, 15))
        self.assertTrue(halts.ended_at)
        # re-reading the day changes nothing
        self.env['lab.location.ping'].sudo()._detect_halts(user, day)
        self.assertEqual(self.env['lab.location.halt'].sudo().search_count([('user_id', '=', user.id)]), 1)
        self.assertEqual(self.env['lab.location.ping'].sudo()._day_summary(user, day)['halt_minutes'], 15)

    def test_the_trip_carries_the_trails_km_beside_the_claim(self):
        user = self.exec_user
        now = fields.Datetime.now() - timedelta(minutes=30)
        self._start_day(user, now - timedelta(minutes=30))
        day = self.env['lab.location.ping']._today_for(user)
        trip = self.env['lab.trip'].sudo().create({
            'user_id': user.id, 'date': day, 'vehicle': 'bike', 'odo_start': 100, 'odo_end': 110})
        self._pings(user, now, walk(10.0, 76.0, 51, metres_each=100.0, seconds_each=20, speed=18.0))
        self.assertAlmostEqual(trip.gps_km, 5.0, delta=0.2, msg="fifty 100 m steps")
        self.assertGreater(trip.km_variance_pct, 90, "a 10 km claim on a 5 km trail")

    def test_the_live_map_shows_the_team_to_managers_and_only_me_to_me(self):
        user = self.exec_user
        now = fields.Datetime.now() - timedelta(minutes=2)
        self._start_day(user, now - timedelta(minutes=30))
        self._pings(user, now, [(10.0, 76.0, 0, 30.0), (10.001, 76.0, 60, 28.0)])
        Ping = self.env['lab.location.ping']
        data = Ping.with_user(self.ops_user).get_live_map()
        mine = next(e for e in data['executives'] if e['user_id'] == user.id)
        self.assertEqual(mine['status'], 'moving')
        self.assertEqual(mine['last']['speed_kmh'], 28.0)
        self.assertEqual(len(mine['legs']), 1)
        self.assertEqual(len(mine['legs'][0]), 2)
        self.assertTrue(mine['on_duty'])
        self.assertIn('tiles', data)
        self.assertIn('lat', data['home'], "somewhere to open on before anybody has a fix")
        self.assertTrue(data['is_today'])
        # the executive: themselves only, and never somebody else
        own = Ping.with_user(user).get_live_map(user_ids=[user.id])
        self.assertEqual([e['user_id'] for e in own['executives']], [user.id])
        with self.assertRaises(AccessError):
            Ping.with_user(user).get_live_map(user_ids=[self.ops_user.id])
        # the desk board carries the live facts
        board = self.env['lab.desk'].with_user(self.ops_user)._field_board(
            Ping._today_for(self.ops_user))
        row = next(r for r in board['rows'] if r['id'] == user.id)
        self.assertEqual(row['live'], 'moving')
        self.assertEqual(row['where'], 'on the road')
        self.assertIsNotNone(row['last_age_s'])

    def test_status_follows_the_age_of_the_last_fix(self):
        Ping = self.env['lab.location.ping']
        now = fields.Datetime.now()
        fresh_moving = (now - timedelta(minutes=1), 10.0, 76.0, 30.0, False, 0, 10)
        fresh_still = (now - timedelta(minutes=1), 10.0, 76.0, 0.0, False, 0, 10)
        old = (now - timedelta(minutes=12), 10.0, 76.0, 30.0, False, 0, 10)
        gone = (now - timedelta(minutes=45), 10.0, 76.0, 30.0, False, 0, 10)
        none = self.env['lab.visit']
        self.assertEqual(Ping._status_of(self.exec_user, fresh_moving, none, True, True, now)[0], 'moving')
        self.assertEqual(Ping._status_of(self.exec_user, fresh_still, none, True, True, now)[0], 'stopped')
        self.assertEqual(Ping._status_of(self.exec_user, old, none, True, True, now)[0], 'stopped')
        self.assertEqual(Ping._status_of(self.exec_user, gone, none, True, True, now)[0], 'offline')
        self.assertEqual(Ping._status_of(self.exec_user, gone, none, False, True, now)[0], 'off')
        self.assertEqual(Ping._status_of(self.exec_user, None, none, True, True, now)[0], 'offline')
        self.assertEqual(Ping._status_of(self.exec_user, None, none, False, True, now)[0], 'off')
        self.assertEqual(Ping._status_of(self.exec_user, fresh_moving, none, True, False, now)[0], 'off')
