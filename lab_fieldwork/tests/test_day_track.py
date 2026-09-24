# -*- coding: utf-8 -*-
"""The day's track: what the map on the day sheet is allowed to say.

The map is a reading of fixes the phone already recorded, so what matters here is
that it never says more than the records do: a visit with no fix contributes no
mark, a clinic with no pin is not measured against one, the marks come back in the
order the day happened, and the delivery fixes — which `lab.delivery` restricts to
managers with `groups=` — do not reach an executive through a map.
"""
import json
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged

from .test_daily_flow import DailyFlowCase, LAT, LON


@tagged('post_install', '-at_install')
class TestDayTrack(DailyFlowCase):

    def _at(self, hour, minute=0):
        """A naive UTC datetime inside this sheet's day."""
        return fields.Datetime.to_datetime(
            '%s %02d:%02d:00' % (self.today, hour, minute))

    def _located_visit(self, hour, lat=LAT, lon=LON, out=True, **vals):
        visit = self._visit(gps=False, **vals)
        writes = {'check_in': self._at(hour), 'gps_lat': lat, 'gps_lon': lon}
        if out:
            writes.update(check_out=self._at(hour, 30),
                          out_gps_lat=lat, out_gps_lon=lon)
        visit.write(writes)
        return visit

    # ------------------------------------------------------------- the marks
    def test_each_door_becomes_one_mark_in_the_order_it_happened(self):
        self._located_visit(11)
        self._located_visit(9)
        track = self._sheet().get_day_track()
        self.assertEqual([p['kind'] for p in track['points']], ['door', 'door'])
        times = [p['at'] for p in track['points']]
        self.assertEqual(times, sorted(times), "the track is the day in order")
        self.assertEqual(track['visits'], 2)
        self.assertEqual(track['located'], 2)

    def test_arriving_and_leaving_the_same_doorstep_is_one_mark_with_both_times(self):
        """Two marks on one spot hide each other; the lower cannot even be clicked."""
        self._located_visit(9)
        point = self._sheet().get_day_track()['points'][0]
        self.assertEqual(point['kind'], 'door')
        # The stamps are UTC; the map reads in the executive's own clock, so a
        # 09:00 fix in Kerala is 14:30. An approver judges a round by local time.
        self.assertEqual(point['at'], '14:30')
        self.assertEqual(point['left_at'], '15:00', "both times ride on the one mark")

    def test_leaving_from_somewhere_else_keeps_both_marks(self):
        """A fix taken streets away on the way out is a second place, not jitter."""
        visit = self._located_visit(9)
        visit.write({'out_gps_lat': LAT + 0.01, 'out_gps_lon': LON + 0.01})
        kinds = [p['kind'] for p in self._sheet().get_day_track()['points']]
        self.assertEqual(kinds, ['in', 'out'])

    def test_a_visit_without_a_fix_draws_nothing_and_counts_as_unlocated(self):
        self._visit(gps=False)
        track = self._sheet().get_day_track()
        self.assertEqual(track['points'], [])
        self.assertEqual(track['visits'], 1)
        self.assertEqual(track['located'], 0,
                         "a day typed at a desk must not look like a round")
        self.assertFalse(track['bounds'], "nothing to fit a map to")

    def test_null_island_is_not_a_location(self):
        """0,0 is what a wrapper sends when it has no fix — never a mark."""
        self._located_visit(10, lat=0.0, lon=0.0)
        self.assertEqual(self._sheet().get_day_track()['points'], [])

    def test_a_fix_away_from_the_clinic_carries_its_distance(self):
        # ~1.3 km north of the pin: far enough that the phone was not at the door.
        self._located_visit(10, lat=LAT + 0.012, out=False)
        point = self._sheet().get_day_track()['points'][0]
        self.assertEqual(point['state'], 'far')
        self.assertGreater(point['metres'], 500)
        self.assertTrue(point['clinic_id'], "there is a pin to measure against")

    def test_an_unpinned_clinic_is_not_measured_against_one(self):
        bare = self.env['res.partner'].create({'name': 'Unpinned Clinic'})
        self._located_visit(10, partner_id=bare.id, out=False)
        track = self._sheet().get_day_track()
        self.assertEqual(track['points'][0]['state'], 'nopin')
        self.assertFalse(track['points'][0]['clinic_id'])
        self.assertFalse([c for c in track['clinics'] if c['id'] == bare.id],
                         "a clinic with no pin cannot be drawn")

    def test_the_clinic_pin_is_sent_once_however_often_it_is_visited(self):
        self._located_visit(9)
        self._located_visit(14)
        track = self._sheet().get_day_track()
        self.assertEqual(len(track['clinics']), 1)
        self.assertEqual(track['clinics'][0]['id'], self.clinic.id)

    # ------------------------------------------------------------- the frame
    def test_a_single_fix_still_gets_a_box_to_show_its_town(self):
        """Fitting a map to one point zooms to a roof; the box is widened."""
        self._located_visit(10, out=False)
        south_west, north_east = self._sheet().get_day_track()['bounds']
        self.assertGreater(north_east[0] - south_west[0], 0.0)
        self.assertGreater(north_east[1] - south_west[1], 0.0)
        self.assertLessEqual(south_west[0], LAT)
        self.assertGreaterEqual(north_east[0], LAT)

    def test_the_straight_line_total_is_the_crows_flight_not_the_odometer(self):
        self._located_visit(9, out=False)
        self._located_visit(11, lat=LAT + 0.09, out=False)       # ~10 km north
        track = self._sheet().get_day_track()
        self.assertGreater(track['straight_km'], 8)
        self.assertLess(track['straight_km'], 12)

    # ------------------------------------------------------------- who sees what
    def test_an_executive_sees_their_own_doors(self):
        self._located_visit(10)
        self._located_visit(12)
        track = self._sheet().with_user(self.exec_user).get_day_track()
        self.assertEqual(len(track['points']), 2)

    def test_delivery_fixes_stay_on_the_managers_side_of_the_field_groups(self):
        """`lab.delivery.delivered_lat` is manager-only; a map must not leak it."""
        if 'lab.delivery' not in self.env:
            self.skipTest('lab_delivery is not installed here')
        # A pickup, which needs only the clinic it was collected from — an
        # outbound box would need an order, which is not what this test is about.
        delivery = self.env['lab.delivery'].sudo().create({
            'partner_id': self.clinic.id, 'executive_id': self.exec_user.id,
            'direction': 'in'})
        delivery.write({
            'state': 'delivered', 'delivered_datetime': self._at(12),
            'delivered_lat': LAT, 'delivered_lon': LON})
        sheet = self._sheet()

        as_manager = sheet.with_user(self.ops_user).get_day_track()
        self.assertIn('delivery', {p['kind'] for p in as_manager['points']},
                      "the desk that signs the day may see where the parcels went")

        as_executive = sheet.with_user(self.exec_user).get_day_track()
        self.assertNotIn('delivery', {p['kind'] for p in as_executive['points']})

    def test_the_track_is_scoped_to_this_sheets_day_and_person(self):
        other_person = self._located_visit(10, user=self.mkt_user)
        yesterday = self._visit(gps=False)
        yesterday.write({
            'date': self.today - timedelta(days=1),
            'check_in': self._at(10) - timedelta(days=1),
            'gps_lat': LAT, 'gps_lon': LON})
        mine = self._located_visit(11, out=False)

        track = self._sheet().get_day_track()
        self.assertEqual([p['res_id'] for p in track['points']], [mine.id])
        self.assertNotIn(other_person.id, [p['res_id'] for p in track['points']])


@tagged('post_install', '-at_install')
class TestRoadRoute(DailyFlowCase):
    """Following the roads between the fixes, and never at the cost of the sheet.

    The routing service is somebody else's server: it can be slow, down, or turned
    off. None of those may cost an approver the day sheet, so what is pinned here is
    the fallback — straight lines and an honest caption — as much as the happy path.
    Nothing here touches the network: `_routing_url` is off under the test runner
    unless a test opts in, and the call itself is patched.
    """

    ROUTE = {'code': 'Ok', 'routes': [{
        'distance': 4321.0,
        'geometry': {'coordinates': [[76.2673, 9.9312], [76.27, 9.94], [76.2773, 9.9412]]},
    }]}

    def setUp(self):
        super().setUp()
        self.env['ir.config_parameter'].sudo().set_param(
            'lab_fieldwork.routing_url', 'https://routing.test/route/v1/driving/')

    def _sheet_with_two_doors(self):
        visit = self._visit(gps=False)
        visit.write({'check_in': fields.Datetime.now() - timedelta(hours=2),
                     'gps_lat': LAT, 'gps_lon': LON})
        second = self._visit(gps=False)
        second.write({'check_in': fields.Datetime.now() - timedelta(hours=1),
                      'gps_lat': LAT + 0.01, 'gps_lon': LON + 0.01})
        return self._sheet().with_context(lab_test_routing=True)

    def _answer(self, payload=None, status=200, boom=None):
        """A stand-in for the routing service."""
        class Response:
            def raise_for_status(inner):
                if status != 200:
                    raise IOError('HTTP %s' % status)

            def json(inner):
                return payload if payload is not None else self.ROUTE

        def fake_get(*args, **kwargs):
            if boom:
                raise boom
            return Response()
        return patch('odoo.addons.lab_fieldwork.models.day_track.requests.get', fake_get)

    # ------------------------------------------------------------- the happy path
    def test_the_line_follows_the_roads_and_carries_their_distance(self):
        sheet = self._sheet_with_two_doors()
        with self._answer():
            track = sheet.get_day_track()
        self.assertEqual(track['route_state'], 'ok')
        self.assertEqual(track['road_km'], 4.3)
        self.assertEqual(len(track['geometry']), 3)
        self.assertEqual(track['geometry'][0], [9.9312, 76.2673],
                         "GeoJSON is lon,lat and the map wants lat,lon")

    def test_the_road_route_is_worked_out_once_and_then_read_from_the_sheet(self):
        sheet = self._sheet_with_two_doors()
        calls = []

        def counting_get(*args, **kwargs):
            calls.append(args)
            class Response:
                def raise_for_status(inner):
                    pass
                def json(inner):
                    return self.ROUTE
            return Response()

        with patch('odoo.addons.lab_fieldwork.models.day_track.requests.get', counting_get):
            sheet.get_day_track()
            sheet.get_day_track()
            self.assertEqual(len(calls), 1, "two desks opening one sheet ask once")
            sheet.get_day_track(force_route=True)
            self.assertEqual(len(calls), 2, "Redraw asks again")

    def test_a_corrected_fix_redraws_the_route(self):
        sheet = self._sheet_with_two_doors()
        with self._answer():
            sheet.get_day_track()
        first_key = sheet.route_key
        sheet._visits()[0].write({'gps_lat': LAT + 0.05})
        with self._answer():
            sheet.get_day_track()
        self.assertNotEqual(sheet.route_key, first_key,
                            "the stored route belongs to the fixes it was drawn from")

    # ------------------------------------------------------------- when it is not there
    def test_a_routing_service_that_is_down_leaves_the_day_readable(self):
        sheet = self._sheet_with_two_doors()
        with self._answer(boom=IOError('connection refused')):
            track = sheet.get_day_track()
        self.assertEqual(track['route_state'], 'failed')
        self.assertEqual(track['geometry'], [], "the map falls back to straight lines")
        self.assertGreater(track['straight_km'], 0, "and still says how far apart they were")
        self.assertTrue(track['points'], "the doors are drawn whatever the router says")

    def test_a_service_that_answers_with_a_refusal_is_not_treated_as_a_route(self):
        sheet = self._sheet_with_two_doors()
        with self._answer(payload={'code': 'NoRoute', 'message': 'no route found'}):
            track = sheet.get_day_track()
        self.assertEqual(track['route_state'], 'failed')
        self.assertFalse(track['geometry'])

    def test_a_failure_is_remembered_so_a_dead_service_is_not_hammered(self):
        sheet = self._sheet_with_two_doors()
        calls = []

        def failing_get(*args, **kwargs):
            calls.append(args)
            raise IOError('down')

        with patch('odoo.addons.lab_fieldwork.models.day_track.requests.get', failing_get):
            sheet.get_day_track()
            sheet.get_day_track()
            sheet.get_day_track()
        self.assertEqual(len(calls), 1,
                         "every form open must not re-ask a service that is down")

    def test_emptying_the_parameter_turns_road_routing_off(self):
        self.env['ir.config_parameter'].sudo().set_param('lab_fieldwork.routing_url', '')
        sheet = self._sheet_with_two_doors()
        with self._answer(boom=AssertionError('must not be called')):
            track = sheet.get_day_track()
        self.assertEqual(track['route_state'], 'off')
        self.assertFalse(track['geometry'])

    def test_the_suite_itself_never_calls_out(self):
        """The guard the other twelve tests rely on."""
        sheet = self._sheet_with_two_doors().with_context(lab_test_routing=False)
        with self._answer(boom=AssertionError('must not be called')):
            sheet.get_day_track()

    # ------------------------------------------------------------- the frame
    def test_the_map_frames_the_road_not_just_its_ends(self):
        """A road can swing wide of every door it joins."""
        sheet = self._sheet_with_two_doors()
        wide = {'code': 'Ok', 'routes': [{'distance': 9000.0, 'geometry': {
            'coordinates': [[76.2673, 9.9312], [76.50, 10.20], [76.2773, 9.9412]]}}]}
        with self._answer(payload=wide):
            track = sheet.get_day_track()
        (south, west), (north, east) = track['bounds']
        self.assertGreaterEqual(north, 10.20, "the detour is inside the frame")
        self.assertGreaterEqual(east, 76.50)
