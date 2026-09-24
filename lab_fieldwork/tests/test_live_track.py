# -*- coding: utf-8 -*-
"""The trail between the doors: what the phone is allowed to record, and when.

This feature records where a person was, so almost every test here is a test that it
records LESS than it is asked to. The phone is not trusted: it posts whatever it has,
including fixes taken before the day started, after it ended, from a cell tower two
towns away, and eighty copies of a desk it was left on. The server is what decides, and
these tests are the statement of what it decides.

The privacy boundary is the attendance window and nothing else. If one test in this file
mattered more than the rest it is the pair that proves a fix taken one minute before
Start my day, and one taken a minute after End the day, are both thrown away.
"""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .test_daily_flow import DailyFlowCase, LAT, LON


@tagged('post_install', '-at_install')
class LiveTrackCase(DailyFlowCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pings = cls.env['lab.location.ping']
        cls.employee = cls.env['hr.employee'].sudo().create({
            'name': 'Flow Exec', 'user_id': cls.exec_user.id,
            'company_id': cls.env.company.id})
        cls.env['ir.config_parameter'].sudo().set_param(
            'lab_fieldwork.track_live', 'True')

    # ------------------------------------------------------------- fixtures
    def _at(self, hour, minute=0):
        """A naive UTC datetime on the sheet's day."""
        return fields.Datetime.to_datetime(
            '%s %02d:%02d:00' % (self.today, hour, minute))

    def _on_duty(self, start_hour=3, end_hour=12):
        """An attendance covering the day. Hours are UTC; the executive is on IST,
        so 03:30–12:30 UTC is a 09:00–18:00 working day in Kerala."""
        return self.env['hr.attendance'].sudo().create({
            'employee_id': self.employee.id,
            'check_in': self._at(start_hour, 30),
            'check_out': self._at(end_hour, 30) if end_hour else False,
        })

    def _post(self, rows, user=None):
        """Send a batch the way the phone sends it, as the executive."""
        return self.pings.with_user(user or self.exec_user).record([
            {'latitude': lat, 'longitude': lon,
             'ts': fields.Datetime.to_string(ts),
             'accuracy_m': accuracy}
            for ts, lat, lon, accuracy in rows
        ])

    def _stored(self, user=None):
        return self.pings.sudo().search([
            ('user_id', '=', (user or self.exec_user).id)], order='ts')

    # ------------------------------------------------- the privacy boundary
    def test_a_fix_taken_on_duty_is_kept(self):
        self._on_duty()
        kept = self._post([(self._at(6, 0), LAT, LON, 10)])
        self.assertEqual(kept, 1)
        self.assertEqual(len(self._stored()), 1)

    def test_a_fix_taken_before_the_day_started_is_thrown_away(self):
        self._on_duty(start_hour=3)
        kept = self._post([(self._at(3, 29), LAT, LON, 10)])
        self.assertEqual(kept, 0, "the day had not been started yet")
        self.assertFalse(self._stored())

    def test_a_fix_taken_after_the_day_ended_is_thrown_away(self):
        self._on_duty(end_hour=12)
        kept = self._post([(self._at(12, 31), LAT, LON, 10)])
        self.assertEqual(kept, 0, "the day was already ended")
        self.assertFalse(self._stored())

    def test_nothing_is_recorded_without_an_attendance_at_all(self):
        kept = self._post([(self._at(6, 0), LAT, LON, 10)])
        self.assertEqual(kept, 0,
                         "no attendance means the executive is not on duty, and a "
                         "phone that keeps posting must not be believed")

    def test_an_open_day_records_up_to_now(self):
        """Started and not yet ended is the normal state all day long."""
        now = fields.Datetime.now()
        self.env['hr.attendance'].sudo().create({
            'employee_id': self.employee.id,
            'check_in': now - timedelta(hours=2), 'check_out': False})
        kept = self._post([(now - timedelta(minutes=5), LAT, LON, 10)])
        self.assertEqual(kept, 1)

    def test_a_gap_between_two_stints_is_not_covered(self):
        """Out for lunch, clocked out: the middle of the day is nobody's business."""
        self._on_duty(start_hour=3, end_hour=7)
        self.env['hr.attendance'].sudo().create({
            'employee_id': self.employee.id,
            'check_in': self._at(9, 30), 'check_out': self._at(12, 30)})
        kept = self._post([
            (self._at(4, 0), LAT, LON, 10),          # first stint — kept
            (self._at(8, 0), LAT + 0.05, LON, 10),   # clocked out — dropped
            (self._at(10, 0), LAT + 0.1, LON, 10),   # second stint — kept
        ])
        self.assertEqual(kept, 2)
        self.assertEqual([p.ts for p in self._stored()],
                         [self._at(4, 0), self._at(10, 0)])

    # ---------------------------------------------------- what is worth keeping
    def test_a_phone_sitting_still_writes_one_row_not_eighty(self):
        self._on_duty()
        # Twenty readings thirty seconds apart: nine and a half minutes on a desk.
        rows = [(self._at(6, 0) + timedelta(seconds=30 * i), LAT, LON, 10)
                for i in range(20)]
        kept = self._post(rows)
        self.assertEqual(kept, 2,
                         "the first fix, then one heartbeat five minutes later — not "
                         "twenty rows from a phone that has not moved")
        self.assertEqual(
            [p.ts for p in self._stored()], [self._at(6, 0), self._at(6, 5)])

    def test_a_phone_that_moved_is_recorded_every_time(self):
        self._on_duty()
        rows = [(self._at(6, 0) + timedelta(minutes=i), LAT + 0.01 * i, LON, 10)
                for i in range(5)]
        self.assertEqual(self._post(rows), 5)

    def test_a_cell_tower_fix_is_not_a_person(self):
        self._on_duty()
        kept = self._post([(self._at(6, 0), LAT, LON, 5000)])
        self.assertEqual(kept, 0,
                         "a fix accurate to five kilometres can land in the wrong "
                         "town, and a wrong town on a map is worse than no map")

    def test_a_fix_already_held_is_not_stored_twice(self):
        self._on_duty()
        self._post([(self._at(6, 0), LAT, LON, 10)])
        again = self._post([(self._at(6, 0), LAT, LON, 10)])
        self.assertEqual(again, 0, "a phone on a bad line retries the same batch")
        self.assertEqual(len(self._stored()), 1)

    def test_nonsense_in_the_batch_is_dropped_without_raising(self):
        """A background POST must never put an error in front of a working executive."""
        self._on_duty()
        kept = self.pings.with_user(self.exec_user).record([
            {'latitude': 'north', 'longitude': LON, 'ts': self._at(6, 0)},
            {'latitude': LAT},
            {'latitude': 0.0, 'longitude': 0.0, 'ts': self._at(6, 1)},
            {'latitude': LAT, 'longitude': LON,
             'ts': fields.Datetime.to_string(self._at(6, 2)), 'accuracy_m': 10},
        ])
        self.assertEqual(kept, 1, "one usable row out of four")

    def test_switching_tracking_off_records_nothing(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'lab_fieldwork.track_live', 'False')
        self._on_duty()
        self.assertEqual(self._post([(self._at(6, 0), LAT, LON, 10)]), 0)

    # ------------------------------------------------------------- who may look
    def test_an_executive_cannot_see_another_executives_trail(self):
        self._on_duty()
        self._post([(self._at(6, 0), LAT, LON, 10)])
        other = self.env['res.users'].create({
            'name': 'Other Exec', 'login': 'other.exec@track',
            'group_ids': [(4, self.g_exec.id),
                          (4, self.env.ref('base.group_user').id)]})
        seen = self.pings.with_user(other).search([])
        self.assertFalse(seen, "one executive's movements are not another's business")

    def test_a_manager_sees_the_teams_trails(self):
        self._on_duty()
        self._post([(self._at(6, 0), LAT, LON, 10)])
        self.assertTrue(self.pings.with_user(self.ops_user).search([]))

    def test_nobody_in_the_field_can_edit_a_recorded_position(self):
        self._on_duty()
        self._post([(self._at(6, 0), LAT, LON, 10)])
        ping = self._stored()[0].with_user(self.exec_user)
        with self.assertRaises(AccessError):
            ping.write({'latitude': LAT + 1})
        with self.assertRaises(AccessError):
            ping.unlink()

    def test_a_manager_cannot_tidy_a_trail_either(self):
        """A route that can be edited afterwards proves nothing at all."""
        self._on_duty()
        self._post([(self._at(6, 0), LAT, LON, 10)])
        ping = self._stored()[0].with_user(self.ops_user)
        with self.assertRaises(AccessError):
            ping.write({'latitude': LAT + 1})

    # ------------------------------------------------------------- the card
    def test_the_card_says_the_route_is_being_recorded(self):
        self._on_duty(end_hour=False)
        state = self.env['lab.my.day'].with_user(self.exec_user).tracking_state()
        self.assertTrue(state['enabled'])
        self.assertTrue(state['on_duty'])
        self.assertTrue(state['notice'], "an executive is told, not tracked quietly")

    def test_the_card_stops_saying_so_once_the_day_is_ended(self):
        self._on_duty(end_hour=12)
        state = self.env['lab.my.day'].with_user(self.exec_user).tracking_state()
        self.assertFalse(state['on_duty'])

    # ------------------------------------------------------------- housekeeping
    def test_the_cron_drops_old_trails_and_keeps_current_ones(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'lab_fieldwork.track_keep_days', '30')
        old = self.today - timedelta(days=40)
        self.pings.sudo().create([
            {'user_id': self.exec_user.id, 'date': old, 'ts': self._at(6, 0),
             'latitude': LAT, 'longitude': LON,
             'company_id': self.env.company.id},
            {'user_id': self.exec_user.id, 'date': self.today,
             'ts': self._at(7, 0), 'latitude': LAT, 'longitude': LON,
             'company_id': self.env.company.id},
        ])
        self.pings._cron_prune()
        remaining = self._stored()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining.date, self.today)


@tagged('post_install', '-at_install')
class TestTrailOnTheMap(LiveTrackCase):
    """What the day sheet draws from the trail."""

    def _trail_pings(self, *offsets):
        """Positions at the given minute offsets from 06:00 UTC, walking east."""
        values = []
        for index, minute in enumerate(offsets):
            values.append({
                'user_id': self.exec_user.id, 'date': self.today,
                'ts': self._at(6, 0) + timedelta(minutes=minute),
                'latitude': LAT, 'longitude': LON + 0.01 * index,
                'company_id': self.env.company.id})
        return self.pings.sudo().create(values)

    def test_the_trail_comes_back_in_the_order_it_happened(self):
        self._trail_pings(0, 5, 10)
        track = self._sheet().get_day_track()
        self.assertEqual(len(track['trail']), 1, "one unbroken stretch")
        times = [point[2] for point in track['trail'][0]]
        self.assertEqual(times, sorted(times))

    def test_a_long_silence_breaks_the_line_rather_than_bridging_it(self):
        # Two stretches, forty minutes apart: the app was closed in between.
        self._trail_pings(0, 5, 10, 50, 55, 60)
        track = self._sheet().get_day_track()
        self.assertEqual(len(track['trail']), 2,
                         "a stretch the phone was not watching is a gap, and drawing "
                         "a line across it would invent a road nobody took")

    def test_the_recorded_distance_does_not_count_the_gap(self):
        # One sheet, read twice: a user has exactly one day sheet per day, and the
        # database enforces it.
        sheet = self._sheet()
        self._trail_pings(0, 5, 10, 50, 55, 60)
        broken = sheet.get_day_track()['trail_km']
        self.pings.sudo().search([]).unlink()
        self._trail_pings(0, 5, 10, 15, 20, 25)
        whole = sheet.get_day_track()['trail_km']
        self.assertLess(broken, whole,
                        "the distance across a gap is unknown, so it is not added")

    def test_a_day_with_a_route_but_no_visits_still_draws(self):
        self._trail_pings(0, 5, 10)
        track = self._sheet().get_day_track()
        self.assertFalse(track['points'], "no clinic was reached")
        self.assertTrue(track['bounds'],
                        "a day spent driving and reaching nobody is exactly the day "
                        "worth being able to look at")

    def test_a_day_with_neither_is_still_blank(self):
        track = self._sheet().get_day_track()
        self.assertFalse(track['trail'])
        self.assertFalse(track['bounds'])

    def test_one_executives_map_never_carries_anothers_trail(self):
        self._trail_pings(0, 5, 10)
        other = self.env['res.users'].create({
            'name': 'Second Exec', 'login': 'second.exec@track',
            'group_ids': [(4, self.g_exec.id),
                          (4, self.env.ref('base.group_user').id)]})
        sheet = self.env['lab.daily.update'].create({
            'user_id': other.id, 'date': self.today})
        self.assertFalse(sheet.get_day_track()['trail'])
