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


def at(t0, seconds, north_m, speed=0.0, motion=None):
    """A trail point as _day_fixes gives it, `north_m` north of 10N 76E."""
    return (t0 + timedelta(seconds=seconds), 10.0 + north_m / 111_320.0, 76.0, speed,
            False, 0.0, 10.0, 'app', motion)


def letters(modes):
    return ''.join('.' if m == 'still' else m[0].upper() for m in modes)


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

    def test_the_modes_tell_a_drive_from_a_walk_and_a_blind_stretch(self):
        """A travel claim is about the vehicle: the walk between two doors is not a
        kilometre to reimburse, and a stretch the phone crossed with nothing
        reliable recorded is a straight line under the road. (2026-10-23)"""
        t0 = fields.Datetime.now()
        north = lambda i, metres: 10.0 + i * metres / 111_320.0        # noqa: E731
        pts = []
        # a drive: 300 m every 30 s (36 km/h), 6 steps
        pts += [(t0 + timedelta(seconds=30 * i), north(i, 300), 76.0, 36.0) for i in range(7)]
        # ...a signal: 40 s crawling at 3 km/h inside the drive (two short steps)
        base = pts[-1]
        pts += [(base[0] + timedelta(seconds=20 * i), base[1] + i * 17 / 111_320.0, 76.0, 3.0) for i in (1, 2)]
        # ...then on again
        base = pts[-1]
        pts += [(base[0] + timedelta(seconds=30 * i), base[1] + i * 300 / 111_320.0, 76.0, 36.0) for i in (1, 2, 3)]
        # a walk: 60 m every 40 s (5.4 km/h), 5 steps
        base = pts[-1]
        pts += [(base[0] + timedelta(seconds=40 * i), base[1] + i * 60 / 111_320.0, 76.0, 5.0) for i in range(1, 6)]
        # blind: the next reliable fix 2 km away, 8 minutes later (15 km/h implied)
        base = pts[-1]
        pts += [(base[0] + timedelta(seconds=480), base[1] + 2000 / 111_320.0, 76.0, 0.0)]
        modes = location_live.classify_modes(pts)
        self.assertEqual(modes[0], 'still')
        # points 0-6 the drive, 7-8 the crawl, 9-11 on again, 12-16 the walk, 17 blind
        self.assertEqual(set(modes[1:12]), {'vehicle'}, "the crawl at the signal is part of the drive")
        self.assertEqual(set(modes[12:17]), {'foot'})
        self.assertEqual(modes[17], 'blind')
        km = location_live.mode_km(pts, modes)
        self.assertAlmostEqual(km['vehicle'], 2.73, 1)
        self.assertAlmostEqual(km['foot'], 0.3, 1)
        self.assertAlmostEqual(km['blind'], 2.0, 1)
        self.assertAlmostEqual(km['travel'], km['vehicle'] + km['blind'], 2)
        self.assertAlmostEqual(km['total'], location_live.trail_km(pts), 1, "the total is the trail")
        # a GPS flick: one 15-second "drive" step inside a walk stays a walk
        walk = [(t0 + timedelta(seconds=40 * i), north(i, 60), 76.0, 5.0) for i in range(4)]
        flick = walk[:2] + [(walk[1][0] + timedelta(seconds=15), walk[1][1] + 80 / 111_320.0, 76.0, 19.0)] + \
            [(walk[2][0] + timedelta(seconds=20), walk[2][1] + 80 / 111_320.0, 76.0, 5.0),
             (walk[3][0] + timedelta(seconds=20), walk[3][1] + 80 / 111_320.0, 76.0, 5.0)]
        self.assertEqual(set(location_live.classify_modes(flick)[1:]), {'foot'})

    def test_the_live_map_carries_the_mode_and_counts_only_travel(self):
        user = self.exec_user
        now = fields.Datetime.now() - timedelta(minutes=10)
        self._start_day(user, now - timedelta(minutes=30))
        # a 2.1 km drive at 36 km/h, then a 240 m walk
        path = walk(10.0, 76.0, 8, metres_each=300.0, seconds_each=30, speed=36.0)
        last = path[-1]
        path += [(last[0] + i * 60 / 111_320.0, 76.0, last[2] + 40 * i, 5.0) for i in range(1, 5)]
        self._pings(user, now, path)
        [ex] = self.env['lab.location.ping'].with_user(user).get_live_map()['executives']
        modes = [p[4] for leg in ex['legs'] for p in leg]
        self.assertEqual(modes[0], 'still')
        self.assertEqual(modes[1:8], ['vehicle'] * 7)
        self.assertEqual(modes[8:], ['foot'] * 4)
        self.assertAlmostEqual(ex['km'], 2.1, 1, "travelled: the drive")
        self.assertAlmostEqual(ex['foot_km'], 0.24, 1)
        self.assertAlmostEqual(ex['trail_km'], ex['km'] + ex['foot_km'], 1)

    def test_a_trip_is_judged_against_the_drive_not_the_walk(self):
        user = self.exec_user
        now = fields.Datetime.now() - timedelta(minutes=10)
        self._start_day(user, now - timedelta(minutes=30))
        trip = self.env['lab.trip'].sudo().create({
            'user_id': user.id, 'date': self.env['lab.location.ping']._today_for(user),
            'vehicle': 'bike', 'odo_start': 100, 'odo_end': 102})
        path = walk(10.0, 76.0, 8, metres_each=300.0, seconds_each=30, speed=36.0)
        last = path[-1]
        path += [(last[0] + i * 60 / 111_320.0, 76.0, last[2] + 40 * i, 5.0) for i in range(1, 5)]
        self._pings(user, now, path)
        self.assertAlmostEqual(trip.gps_km, 2.1, 1, "the claim is judged against the drive")

    def test_heartbeats_are_neither_a_walk_nor_a_hurry(self):
        """A heartbeat repeats the last position with a fresh time. A parked phone
        wandering by 20-45 m between heartbeats walked nowhere; and the stretch that
        leaves a run of heartbeats may have begun right after the first of them - a
        600 m walk out of a hospital is a walk, not a 21 km/h drive. (review, 2026-10-23)"""
        t0 = fields.Datetime.now()
        wander = [at(t0, 0, 0)] + [at(t0, 300 * i, (-1) ** i * (10 + i)) for i in range(1, 13)]
        km = location_live.mode_km(wander)
        self.assertEqual((km['foot'], km['travel']), (0.0, 0.0), letters(location_live.classify_modes(wander)))
        for leave in (700, 800):
            out = [at(t0, 0, 0, 4.0), at(t0, 300, 0), at(t0, 600, 0), at(t0, leave, 600, 4.0)]
            km = location_live.mode_km(out)
            self.assertAlmostEqual(km['foot'], 0.6, 1, "left at %d s" % leave)
            self.assertEqual(km['travel'], 0.0)

    def test_traffic_is_not_a_walk_but_a_visit_makes_one(self):
        """Walking pace between two drives with no stop in it is a jam; a walk to a
        clinic has the visit in it. (review, 2026-10-23)"""
        t0 = fields.Datetime.now()
        pts, t, y = [], 0, 0
        for _i in range(4):
            pts.append(at(t0, t, y, 36.0)); t += 10; y += 100           # noqa: E702
        for _i in range(20):                                             # 1 km in 20 min
            pts.append(at(t0, t, y, 3.0)); t += 60; y += 50             # noqa: E702
        for _i in range(3):
            pts.append(at(t0, t, y, 36.0)); t += 10; y += 100           # noqa: E702
        km = location_live.mode_km(pts)
        self.assertEqual(km['foot'], 0.0, letters(location_live.classify_modes(pts)))
        self.assertAlmostEqual(km['travel'], 1.6, 1)
        pts, t, y = [], 0, 0
        for _i in range(5):
            pts.append(at(t0, t, y, 36.0)); t += 10; y += 100           # noqa: E702
        for _i in range(3):
            pts.append(at(t0, t, y, 4.0)); t += 45; y += 55             # noqa: E702
        for _i in range(4):                                              # the visit
            pts.append(at(t0, t, y)); t += 300                          # noqa: E702
        for _i in range(3):
            y -= 55; pts.append(at(t0, t, y, 4.0)); t += 45            # noqa: E702
        for _i in range(3):
            y += 100; pts.append(at(t0, t, y, 36.0)); t += 10          # noqa: E702
        km = location_live.mode_km(pts)
        self.assertAlmostEqual(km['foot'], 0.33, 1, letters(location_live.classify_modes(pts)))

    def test_a_long_silence_ending_elsewhere_is_linked_and_counted(self):
        """Client, 2026-10-24: "not recorded time should not needed. It should be
        linked." An hour with nothing recorded, after which the phone is 8 km on, was
        a journey: counted. One that ends where it began is the phone still there."""
        t0 = fields.Datetime.now()
        pts = [at(t0, 0, 0, 36.0), at(t0, 20, 200, 36.0), at(t0, 3620, 8200, 36.0), at(t0, 3640, 8400, 36.0)]
        modes = location_live.classify_modes(pts)
        self.assertEqual(modes[2], 'blind', letters(modes))
        self.assertAlmostEqual(location_live.mode_km(pts, modes)['travel'], 8.4, 1)
        parked = [at(t0, 0, 0), at(t0, 3600, 40)]
        self.assertEqual(location_live.classify_modes(parked)[1], 'still')
        # 100 km in twenty minutes is a GPS error, not a journey
        wild = [at(t0, 0, 0, 36.0), at(t0, 1200, 100_000, 36.0)]
        self.assertEqual(location_live.mode_km(wild)['travel'], 0.0)

    def test_the_map_draws_one_linked_trail(self):
        user = self.exec_user
        now = fields.Datetime.now() - timedelta(minutes=90)
        self._start_day(user, now - timedelta(minutes=30))
        path = walk(10.0, 76.0, 3, metres_each=200.0, seconds_each=20, speed=36.0)
        path += [(10.0 + 6000 / 111_320.0, 76.0, 3600, 36.0), (10.0 + 6200 / 111_320.0, 76.0, 3620, 36.0)]
        self._pings(user, now, path)
        [ex] = self.env['lab.location.ping'].with_user(user).get_live_map()['executives']
        self.assertEqual(len(ex['legs']), 1, "one trail, no break")
        self.assertEqual(ex['gaps'], [])
        self.assertIn('blind', {p[4] for p in ex['legs'][0]}, "drawn as the journey it was")
        self.assertAlmostEqual(ex['km'], 6.2, 1)

    def test_a_recording_gap_is_never_smoothed_across(self):
        t0 = fields.Datetime.now()
        pts = [at(t0, 0, 0, 36.0), at(t0, 10, 100, 36.0), at(t0, 20, 200, 36.0), at(t0, 30, 300, 36.0),
               at(t0, 60, 340, 4.0), at(t0, 1260, 350, 4.0), at(t0, 1285, 385, 4.0),
               at(t0, 1295, 485, 36.0), at(t0, 1305, 585, 36.0)]
        modes = location_live.classify_modes(pts)
        self.assertEqual(modes[4], 'foot', letters(modes))
        self.assertEqual(modes[5], 'still', "the gap itself is no distance")
        self.assertEqual(modes[6], 'foot', letters(modes))

    def test_the_phones_sensors_decide_first(self):
        """The Lab app 1.0.4 sends Android's activity recognition with each fix: a jam
        is a drive at any pace, and a walk the sensors saw is never smoothed into the
        drive around it. A speed nobody's speedometer reads is ignored."""
        t0 = fields.Datetime.now()
        jam = [at(t0, 60 * i, 50 * i, 3.0, 'vehicle') for i in range(10)]
        self.assertEqual(set(location_live.classify_modes(jam)[1:]), {'vehicle'})
        mixed = [at(t0, 0, 0, 36.0, 'vehicle'), at(t0, 10, 100, 36.0, 'vehicle'),
                 at(t0, 40, 150, 6.0, 'foot'), at(t0, 70, 200, 6.0, 'foot'),
                 at(t0, 80, 300, 36.0, 'vehicle'), at(t0, 90, 400, 36.0, 'vehicle')]
        self.assertEqual(letters(location_live.classify_modes(mixed)), '.VFFVV')
        # "on foot" at 40 km/h is a phone in a hand in a bus
        bus = [at(t0, 0, 0, 40.0, 'foot'), at(t0, 10, 110, 40.0, 'foot'), at(t0, 20, 220, 40.0, 'foot')]
        self.assertEqual(set(location_live.classify_modes(bus)[1:]), {'vehicle'})
        bogus = [at(t0, 0, 0, 4.0), at(t0, 60, 60, 240.0)]
        self.assertEqual(location_live.classify_modes(bogus)[1], 'foot', "240 km/h is a jump, not a reading")

    def test_the_overview_thins_without_losing_a_walk_or_a_blind_stretch(self):
        """Field Live draws 300 points a person. Thinned the plain way, each kept point
        carried the mode of its own last 10-second step, so a blind stretch or a walk
        between two kept points was drawn as road. (review, 2026-10-23)"""
        t0 = fields.Datetime.now()
        pts, t, y = [], 0, 0
        for i in range(1200):
            if i == 1001:
                t += 360; y += 2000                                      # noqa: E702
            elif 501 <= i <= 505:
                t += 45; y += 50                                         # noqa: E702
            elif i:
                t += 10; y += 100                                        # noqa: E702
            walking = 501 <= i <= 505
            pts.append(at(t0, t, y, 4.0 if walking else 36.0, 'foot' if walking else None))
        modes = location_live.classify_modes(pts)
        kept = location_live.thin_runs(pts, modes, 300)
        self.assertLess(len(kept), 320)
        drawn = {}
        for (a, _ma), (b, mode) in zip(kept, kept[1:]):
            if mode != 'still':
                drawn[mode] = drawn.get(mode, 0.0) + location_live.metres_between(a[1], a[2], b[1], b[2]) / 1000
        counted = location_live.mode_km(pts, modes)
        for mode in ('vehicle', 'foot', 'blind'):
            self.assertAlmostEqual(drawn.get(mode, 0.0), counted[mode], 1, mode)

    def test_the_phone_says_what_it_was_doing(self):
        user = self.exec_user
        now = fields.Datetime.now() - timedelta(minutes=10)
        self._start_day(user, now - timedelta(minutes=30))
        Ping = self.env['lab.location.ping'].with_user(user)
        Ping.record([{'latitude': 10.0 + i * 60 / 111_320.0, 'longitude': 76.0, 'accuracy_m': 8,
                      'speed_kmh': 3.0, 'motion': 'vehicle' if i else 'nonsense',
                      'ts': fields.Datetime.to_string(now + timedelta(seconds=60 * i))} for i in range(6)])
        pings = self.env['lab.location.ping'].sudo().search([('user_id', '=', user.id)], order='ts')
        self.assertEqual(pings.mapped('motion'), [False] + ['vehicle'] * 5)
        [ex] = Ping.get_live_map()['executives']
        self.assertAlmostEqual(ex['km'], 0.3, 1, "a jam at 3 km/h the phone knew was a drive")
        self.assertEqual(ex['foot_km'], 0.0)

    def test_a_speed_the_phone_did_not_give_is_never_measured_from_a_tower(self):
        """A browser fix carries no speed: it is the ground covered since the last
        fix that sits on a road - 2 km "covered" from a tower in 30 s read 240 km/h."""
        user = self.exec_user
        now = fields.Datetime.now() - timedelta(minutes=10)
        self._start_day(user, now - timedelta(minutes=30))
        self.env['lab.location.ping'].with_user(user).record([
            {'latitude': lat, 'longitude': 76.0, 'accuracy_m': acc,
             'ts': fields.Datetime.to_string(now + timedelta(seconds=seconds))}
            for lat, acc, seconds in ((10.0, 8, 0), (10.018, 150, 30), (10.00054, 8, 60))])
        last = self.env['lab.location.ping'].sudo().search([('user_id', '=', user.id)], order='ts desc', limit=1)
        self.assertLess(last.speed_kmh, 10.0, "60 m in a minute from the last real fix")
        self.assertFalse(last.overspeed)

    def test_a_network_position_is_where_the_phone_is_but_never_a_road(self):
        """Indoors the phone reports a cell tower's fixed position at "100 m" every
        other fix, between real GPS fixes: drawn, the trail was a star of lines
        radiating from the tower (Abdul Latheef, 2026-10-21/22). Such a fix still
        says where the phone is; it is never part of the trail or its distance."""
        user = self.exec_user
        now = fields.Datetime.now() - timedelta(minutes=10)
        self._start_day(user, now - timedelta(minutes=30))
        tower = (10.01, 76.0)
        fixes = [(10.0, 76.0, 8), tower + (100,), (10.0003, 76.0, 9),
                 tower + (100,), (10.0006, 76.0, 7), tower + (200,)]
        self.env['lab.location.ping'].with_user(user).record([
            {'latitude': lat, 'longitude': lon, 'accuracy_m': acc,
             'ts': fields.Datetime.to_string(now + timedelta(seconds=30 * i))}
            for i, (lat, lon, acc) in enumerate(fixes)])
        [ex] = self.env['lab.location.ping'].with_user(user).get_live_map()['executives']
        self.assertEqual([[p[0] for p in leg] for leg in ex['legs']], [[10.0, 10.0003, 10.0006]])
        self.assertEqual((ex['last']['lat'], ex['last']['accuracy_m']), (10.01, 200.0),
                         "the latest fix is still where the phone is")
        self.assertLess(ex['km'], 0.1, "and the tower is no distance driven")

    def test_a_fix_the_phones_sensors_contradict_is_not_the_trail(self):
        """Indoors a phone lands fixes 500 m to 1.5 km away, each somewhere else,
        while its sensors say still or on foot (Abdul Latheef at Kavanoor,
        2026-10-26 13:03-13:23): nobody still covers 800 m in 20 s. A riding phone
        is believed up to 100 km/h; a phone that says nothing is judged by that alone."""
        t0 = fields.Datetime.now() - timedelta(hours=1)
        drop = location_live.drop_impossible
        still = [at(t0, 0, 0, motion='still'), at(t0, 300, 2, motion='still')]
        # 800 m in 20 s with the sensors saying still, then 900 m elsewhere on foot: gone
        scatter = still + [at(t0, 320, 800, speed=0.0, motion='still'),
                           at(t0, 350, -700, speed=1.5, motion='foot'),
                           at(t0, 380, 5, speed=0.0, motion='still')]
        self.assertEqual(drop(scatter), still + [scatter[-1]])
        # the same ground with the sensors saying vehicle and a speed read: a ride
        ride = still + [at(t0, 300 + 20 * i, 150 * i, speed=27.0, motion='vehicle') for i in range(1, 5)]
        self.assertEqual(drop(ride), ride)
        # 1.5 km in 42 s is a jump whatever the sensors say
        jump = still + [at(t0, 342, 1500, speed=135.0, motion='vehicle'), at(t0, 400, 10, speed=0.0, motion='still')]
        self.assertEqual(drop(jump), still + [jump[-1]])
        # a walk is a walk
        walk = still + [at(t0, 300 + 30 * i, 2 + 40 * i, speed=4.8, motion='foot') for i in range(1, 5)]
        self.assertEqual(drop(walk), walk)
        # a phone that gives neither sensors nor a speed reading: only the jump rule judges it
        mute = [at(t0, 0, 0), at(t0, 20, 300)]
        mute[1] = mute[1][:3] + (round(location_live._step(mute[0], mute[1])[2], 1),) + mute[1][4:]
        self.assertEqual(drop(mute), mute, "54 km/h with nothing said either way is kept")
        # a wrong first fix: the true ones are "fast" from it only until time makes them slow
        wrong = [at(t0, 0, 1000, motion='still'), at(t0, 30, 0, speed=0.0, motion='still'),
                 at(t0, 200, 1, speed=0.0, motion='still'), at(t0, 400, 2, speed=0.0, motion='still')]
        self.assertEqual(drop(wrong), [wrong[0], wrong[2], wrong[3]])

    def test_a_fix_the_phone_never_went_to_is_not_the_trail(self):
        """Standing at a clinic the phone puts a fix 500 m out at "7 m" and is back
        seconds later (ASHIQ, 2026-10-26): a spike on the map and km never driven.
        Such a run - one fix or a few - is dropped; a ride out and back is not."""
        t0 = fields.Datetime.now() - timedelta(hours=1)
        drop = location_live.drop_excursions
        still = [at(t0, 0, 0), at(t0, 300, 2)]
        # one fix 512 m out, back 24 s later; two fixes 1.2 km out, back 15 s later
        trail = still + [at(t0, 547, 512, speed=3.2), at(t0, 571, 1),
                         at(t0, 780, 1221, speed=3.7), at(t0, 793, 1076, speed=2.2),
                         at(t0, 808, 12), at(t0, 1100, 3)]
        self.assertEqual([round((p[1] - 10.0) * 111_320.0) for p in drop(trail)], [0, 2, 1, 12, 3])
        # a ride: the phone reads its speed and records the road in between
        ride = still + [at(t0, 300 + 20 * i, 150 * i, speed=27.0) for i in range(1, 5)] \
            + [at(t0, 380 + 20 * i, 600 - 150 * i, speed=27.0) for i in range(1, 5)]
        self.assertEqual(drop(ride), ride)
        quick = still + [at(t0, 330, 400, speed=30.0), at(t0, 360, 0)]
        self.assertEqual(drop(quick), quick, "one fix out, but the phone said it was riding")
        shop = still + [at(t0, 600, 150, speed=4.0), at(t0, 900, 0)]
        self.assertEqual(drop(shop), shop, "a walk to the shop and back")
        last = still + [at(t0, 330, 500, speed=2.0)]
        self.assertEqual(drop(last), last, "the latest fix is where the phone is: no way back to judge")
        # a phone that gives no speed: the server's speed is the jump itself, no reading
        stay = [at(t0, 0, 0, motion='still'), at(t0, 300, 2, motion='still')]
        jump = at(t0, 330, 500, motion='still')
        jump = jump[:3] + (round(location_live._step(stay[-1], jump)[2], 1),) + jump[4:]
        back = at(t0, 350, 1, motion='still')
        back = back[:3] + (round(location_live._step(jump, back)[2], 1),) + back[4:]
        self.assertEqual(drop(stay + [jump, back]), stay + [back])
        # the same shape while the sensors said "vehicle" is a ride
        ridden = jump[:8] + ('vehicle',)
        self.assertEqual(drop(stay + [ridden, back]), stay + [ridden, back])

    def test_the_map_and_the_km_leave_out_where_the_phone_never_went(self):
        user = self.exec_user
        start = fields.Datetime.now() - timedelta(minutes=15)
        self._start_day(user, start - timedelta(minutes=30))
        out = 10.0 + 500 / 111_320.0
        fixes = [(10.0, 0, 0.0), (10.00002, 300, 0.0), (out, 330, 3.0),
                 (10.00001, 350, 0.0), (10.00003, 600, 0.0)]
        Ping = self.env['lab.location.ping']
        Ping.with_user(user).record([
            {'latitude': lat, 'longitude': 76.0, 'accuracy_m': 7, 'speed_kmh': speed,
             'ts': fields.Datetime.to_string(start + timedelta(seconds=seconds))}
            for lat, seconds, speed in fixes])
        [ex] = Ping.with_user(user).get_live_map()['executives']
        self.assertNotIn(round(out, 6), [round(p[0], 6) for leg in ex['legs'] for p in leg])
        self.assertLess(ex['km'], 0.1, "500 m out and back in 20 s is no distance")
        self.assertTrue(Ping.sudo().search_count([('user_id', '=', user.id), ('latitude', '>', 10.004)]),
                        "the fix itself stays stored")

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
        # 1 km in 60 s = 60 km/h, no speed sent - twice: one such step is a GPS
        # error as often as a hurry, the second in a row is a verdict (2026-10-26)
        self._pings(user, now, [(10.0, 76.0, 0, None), (10.0 + 1000 / 111_320.0, 76.0, 60, None),
                                (10.0 + 2000 / 111_320.0, 76.0, 120, None)])
        first, second = self.env['lab.location.ping'].sudo().search(
            [('user_id', '=', user.id)], order='ts desc', limit=2).sorted('ts')
        self.assertAlmostEqual(first.speed_kmh, 60.0, delta=1.0)
        self.assertFalse(first.overspeed, "one derived step over the limit is not a verdict")
        self.assertTrue(second.overspeed, "the second in a row is")
        self.assertEqual(self.env['lab.location.ping'].sudo()._day_summary(
            user, second.date)['overspeed_count'], 1)
        # a speed the phone itself read over the limit counts at once
        self._pings(user, now + timedelta(seconds=180), [(10.0 + 2100 / 111_320.0, 76.0, 0, 55.0)])
        self.assertTrue(self.env['lab.location.ping'].sudo().search(
            [('user_id', '=', user.id)], order='ts desc', limit=1).overspeed)

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

    def test_the_cron_reads_the_phones_that_went_quiet(self):
        """Halts used to be re-detected on every read of the live map; the batch
        that arrives does it now, and the five-minute cron does the rest - for the
        on-duty phones and for anyone with a halt still open. (2026-10-21)"""
        user = self.exec_user
        now = fields.Datetime.now() - timedelta(minutes=60)
        self._start_day(user, now - timedelta(minutes=30))
        self.env['ir.config_parameter'].sudo().set_str('lab_fieldwork.halt_minutes', '10')
        # fifteen minutes parked, then silence: the batch opened the halt...
        self._pings(user, now, [(10.0, 76.0, s, 0.0) for s in (0, 300, 600, 900)])
        halts = self.env['lab.location.halt'].sudo().search([('user_id', '=', user.id)])
        self.assertEqual(len(halts), 1)
        self.assertFalse(halts.ended_at, "still there as far as the trail knows")
        # ...and the cron, with nothing new, changes nothing (idempotent)
        self.env['lab.location.ping']._cron_close_halts()
        self.assertEqual(self.env['lab.location.halt'].sudo().search_count([('user_id', '=', user.id)]), 1)
        # the phone moves on (2 km in the five minutes since: a ride, not a jump the
        # trail would refuse); the read of the map no longer detects anything, the cron does
        self._pings(user, now + timedelta(minutes=20), [(10.018, 76.0, 0, 30.0)])
        self.env['lab.location.ping']._cron_close_halts()
        halts = self.env['lab.location.halt'].sudo().search([('user_id', '=', user.id)])
        self.assertEqual(len(halts), 1)
        self.assertTrue(halts.ended_at, "the halt was closed")
        self.assertEqual(halts.minutes, 15)
