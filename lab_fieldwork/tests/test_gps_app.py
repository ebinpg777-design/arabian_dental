# -*- coding: utf-8 -*-
"""Positions from the free Traccar Client app (controllers/gps.py): both of its
protocols, the device switch, and the trail's own rules still holding.
(client, 2026-10-19: "able to track using the free Traccar Client app")"""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError
import gzip
import json

from odoo.tests import HttpCase, tagged

from ..controllers import gps


@tagged('post_install', '-at_install')
class TestGpsApp(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env(su=True)
        ref = env.ref
        cls.exec_user = env['res.users'].create({
            'login': 'gps_app_exec', 'name': 'GPS App Exec', 'tz': 'Asia/Kolkata',
            'group_ids': [(4, ref('lab_fieldwork.group_fieldwork_executive').id),
                          (4, ref('base.group_user').id)]})
        cls.fw_admin = env['res.users'].create({
            'login': 'gps_app_admin', 'name': 'GPS App Admin',
            'group_ids': [(4, ref('lab_fieldwork.group_fieldwork_admin').id),
                          (4, ref('base.group_user').id)]})
        cls.employee = env['hr.employee'].create({
            'name': 'GPS App Exec', 'user_id': cls.exec_user.id, 'company_id': env.company.id})
        env['ir.config_parameter'].set_str('lab_fieldwork.track_live', 'True')

    def _on_duty(self):
        return self.env['hr.attendance'].sudo().create({
            'employee_id': self.employee.id,
            'check_in': fields.Datetime.now() - timedelta(hours=1)})

    def _switch_on(self):
        self.exec_user.with_user(self.fw_admin).with_context(fw_value=True).action_fw_tracking_app()
        return self.exec_user.sudo().fw_gps_device_id

    def _pings(self):
        return self.env['lab.location.ping'].sudo().search(
            [('user_id', '=', self.exec_user.id)], order='ts')

    def test_parsers(self):
        when = fields.Datetime.now().replace(microsecond=0)
        epoch = int((when - fields.Datetime.to_datetime('1970-01-01 00:00:00')).total_seconds())
        device, pings = gps.parse_query({'id': 'ABCDEF0123456789', 'lat': '11.1', 'lon': '76.1',
                                         'timestamp': str(epoch), 'speed': '10', 'batt': '81'})
        self.assertEqual(device, 'ABCDEF0123456789')
        self.assertAlmostEqual(pings[0]['speed_kmh'], 18.52, places=2, msg="knots -> km/h")
        self.assertEqual(pings[0]['ts'], fields.Datetime.to_string(when))
        self.assertEqual(pings[0]['battery'], 81.0)
        device, pings = gps.parse_body({'locations': [
            {'device_id': 'X' * 16, 'location': {'timestamp': '2026-10-19T09:00:00Z', 'coords': {
                'latitude': 11.2, 'longitude': 76.2, 'speed': 5, 'accuracy': 8}, 'battery': {'level': 0.4}}},
            {'device_id': 'X' * 16, 'location': {'timestamp': epoch * 1000, 'coords': {
                'latitude': 11.3, 'longitude': 76.3}}}]})
        self.assertEqual((device, len(pings)), ('X' * 16, 2))
        self.assertEqual(pings[0]['ts'], '2026-10-19 09:00:00', "ISO with Z -> naive UTC")
        self.assertAlmostEqual(pings[0]['speed_kmh'], 18.0, msg="m/s -> km/h")
        self.assertAlmostEqual(pings[0]['battery'], 40.0, msg="0..1 -> percent")
        self.assertEqual(pings[1]['ts'], fields.Datetime.to_string(when), "epoch milliseconds")
        self.assertIsNone(pings[1]['speed_kmh'], "no speed sent stays unknown, not zero")

    def test_a_gzipped_batch_is_taken_like_a_plain_one(self):
        """The app gzips its batches from 1.0.4: six times less on the mobile
        connection, and the door undoes it - Werkzeug does not. (2026-10-23)"""
        device = self._switch_on()
        self._on_duty()
        when = fields.Datetime.now() - timedelta(minutes=2)
        body = {'device_id': device, 'location': {
            'timestamp': when.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'coords': {'latitude': 11.13, 'longitude': 76.13, 'speed': 8, 'accuracy': 6}}}
        r = self.url_open('/lab_fieldwork/gps', data=gzip.compress(json.dumps(body).encode()),
                          headers={'Content-Type': 'application/json', 'Content-Encoding': 'gzip'})
        self.assertEqual((r.status_code, r.text), (200, 'OK 1'))
        self.assertEqual(len(self._pings()), 1)
        r = self.url_open('/lab_fieldwork/gps', data=b'not gzip at all',
                          headers={'Content-Type': 'application/json', 'Content-Encoding': 'gzip'})
        self.assertEqual(r.status_code, 400)
        # a bomb: 4 KB that would inflate to 3 MB is refused before it does
        bomb = gzip.compress(b'0' * (3 * 1024 * 1024))
        self.assertLess(len(bomb), 8 * 1024)
        zipped = {'Content-Type': 'application/json', 'Content-Encoding': 'gzip'}
        r = self.url_open('/lab_fieldwork/gps', data=bomb, headers=zipped)
        self.assertEqual((r.status_code, r.text), (413, 'too big'))
        self.assertEqual(len(self._pings()), 1, "and nothing of it was kept")
        # a valid gzip header over broken deflate data, and a stream cut short
        r = self.url_open('/lab_fieldwork/gps', headers=zipped,
                          data=b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03' + b'\xff' * 16)
        self.assertEqual((r.status_code, r.text), (400, 'bad gzip'))
        r = self.url_open('/lab_fieldwork/gps', headers=zipped,
                          data=gzip.compress(json.dumps(body).encode())[:-20])
        self.assertEqual(r.status_code, 400)
        # a plain body past the cap is refused before it is read
        r = self.url_open('/lab_fieldwork/gps', data=b'0' * (2 * 1024 * 1024 + 1),
                          headers={'Content-Type': 'application/json'})
        self.assertEqual(r.status_code, 413)

    def test_a_body_of_another_shape_is_refused_never_an_error(self):
        """The door is public: a stray `5` or `{"location": "x"}` is answered, never a
        500 with a traceback in the log. (review, 2026-10-23)"""
        for data in ('5', '"abc"', 'true', '[' * 100000, '{"location": "x"}',
                     '{"location": {"coords": "x"}}', '{"locations": [{"location": [1, 2]}]}',
                     '{"location": {"battery": "x"}}', '{"locations": 7}'):
            r = self.url_open('/lab_fieldwork/gps', data=data, headers={'Content-Type': 'application/json'})
            self.assertIn(r.status_code, (400, 403), data[:40])

    def test_the_phones_activity_rides_along(self):
        self.assertEqual(gps.parse_activity({'type': 'in_vehicle', 'confidence': 80}), 'vehicle')
        self.assertEqual(gps.parse_activity({'type': 'on_bicycle', 'confidence': 70}), 'vehicle')
        self.assertEqual(gps.parse_activity({'type': 'walking'}), 'foot')
        self.assertEqual(gps.parse_activity('still'), 'still')
        self.assertIsNone(gps.parse_activity({'type': 'in_vehicle', 'confidence': 20}), "a guess is no answer")
        self.assertIsNone(gps.parse_activity({'type': 'tilting', 'confidence': 90}))
        self.assertIsNone(gps.parse_activity(None))
        _device, pings = gps.parse_body({'device_id': 'X' * 16, 'location': {
            'timestamp': '2026-10-23T05:00:00Z', 'activity': {'type': 'on_foot', 'confidence': 77},
            'coords': {'latitude': 11.0, 'longitude': 76.0, 'accuracy': 5}}})
        self.assertEqual(pings[0]['motion'], 'foot')

    def test_the_phone_says_when_location_was_turned_off(self):
        """Client, 2026-10-24: "if any time location off in the phone settings then
        that information needed"."""
        device = self._switch_on()
        self._on_duty()
        off = fields.Datetime.now() - timedelta(minutes=20)
        on = fields.Datetime.now() - timedelta(minutes=5)
        body = {'device_id': device, 'locations': [], 'events': [
            {'type': 'location_off', 'timestamp': off.strftime('%Y-%m-%dT%H:%M:%SZ')},
            {'type': 'location_on', 'timestamp': on.strftime('%Y-%m-%dT%H:%M:%SZ')},
            {'type': 'nonsense', 'timestamp': on.strftime('%Y-%m-%dT%H:%M:%SZ')}]}
        for _again in range(2):          # an answer lost on the way: the batch comes again
            r = self.url_open('/lab_fieldwork/gps', json=body)
            self.assertEqual(r.status_code, 200)
        events = self.env['lab.location.event'].sudo().search([('user_id', '=', self.exec_user.id)], order='ts')
        self.assertEqual(events.mapped('kind'), ['location_off', 'location_on'], "each once, the unknown skipped")
        self.assertEqual(gps.parse_events({'events': 'x'}), [])
        self.assertEqual(gps.parse_events([1, 2]), [])
        # ADL Pro 1.0.13: battery saver and the satellites
        stamp = on.strftime('%Y-%m-%dT%H:%M:%SZ')
        self.assertEqual([e['kind'] for e in gps.parse_events({'events': [
            {'type': kind, 'timestamp': stamp} for kind in ('gps_saver_on', 'gps_saver_off', 'gps_silent')]})],
            ['gps_saver_on', 'gps_saver_off', 'gps_silent'])

    def test_only_an_administrator_switches_the_app_and_the_id_is_secret(self):
        with self.assertRaises(AccessError):
            self.exec_user.with_user(self.exec_user).with_context(fw_value=True).action_fw_tracking_app()
        device = self._switch_on()
        self.assertTrue(device and len(device) >= 16)
        self.exec_user.with_user(self.fw_admin).action_fw_new_device_id()
        self.assertNotEqual(self.exec_user.sudo().fw_gps_device_id, device, "a new ID replaces the old")

    def test_the_app_posts_on_duty_and_the_trail_keeps_it(self):
        device = self._switch_on()
        # off duty: answered OK (or the app retries for ever) but nothing kept
        now = fields.Datetime.now()
        r = self.url_open('/lab_fieldwork/gps?id=%s&lat=11.12&lon=76.12&speed=0' % device)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(self._pings(), "the app may run all day; off duty nothing is kept")
        self.assertTrue(self.exec_user.sudo().fw_last_heard_at,
                        "heard from all the same: which silence it is, when one comes (2026-10-24)")
        self._on_duty()
        first = now - timedelta(minutes=3)
        stamp = int((first - fields.Datetime.to_datetime('1970-01-01 00:00:00')).total_seconds())
        r = self.url_open('/lab_fieldwork/gps?id=%s&lat=11.12&lon=76.12&speed=10&batt=77&timestamp=%s'
                          % (device, stamp))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.text, 'OK 1')
        ping = self._pings()
        self.assertEqual((ping.source, ping.battery), ('app', 77.0))
        self.assertAlmostEqual(ping.speed_kmh, 18.5, places=1)
        # JSON (Traccar Client v9), a later position 1 km on - still in the past: a fix
        # from after "now" is outside the running day and is never kept
        later = now - timedelta(minutes=1)
        body = {'device_id': device, 'location': {
            'timestamp': later.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'coords': {'latitude': 11.129, 'longitude': 76.12, 'speed': 8, 'accuracy': 6},
            'battery': {'level': 0.75}}}
        r = self.url_open('/lab_fieldwork/gps', json=body)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(self._pings()), 2)
        me = self.exec_user.sudo()
        self.assertAlmostEqual(me.fw_last_lat, 11.129, places=4)
        self.assertEqual(me.fw_last_battery, 75.0)
        # the browser stands down and My Day gets the settings
        state = self.env['lab.my.day'].with_user(self.exec_user).tracking_state()
        self.assertFalse(state['enabled'], "no second GPS watch in the browser")
        self.assertTrue(state['app_mode'])
        self.assertEqual(state['app']['device_id'], device)
        self.assertTrue(state['app']['server_url'].endswith('/lab_fieldwork/gps'))
        self.assertLess(state['app']['last_app_fix_age_s'], 600)
        live = self.env['lab.location.ping'].with_user(self.exec_user).get_live_map(user_ids=[self.exec_user.id])
        self.assertEqual(live['executives'][0]['last']['source'], 'app')

    def test_unknown_or_switched_off_devices_are_refused(self):
        self.assertEqual(self.url_open('/lab_fieldwork/gps?id=short&lat=1&lon=1').status_code, 403)
        self.assertEqual(self.url_open('/lab_fieldwork/gps?id=NOPE0123456789AB&lat=1&lon=1').status_code, 403)
        device = self._switch_on()
        self.exec_user.with_user(self.fw_admin).with_context(fw_value=False).action_fw_tracking_app()
        self.assertEqual(self.url_open('/lab_fieldwork/gps?id=%s&lat=1&lon=1' % device).status_code, 403,
                         "switched off: the phone is no longer accepted")
        self.assertEqual(self.url_open('/lab_fieldwork/gps', data='{bad', headers={
            'Content-Type': 'application/json'}).status_code, 400)

    def test_an_excused_phone_is_never_kept(self):
        device = self._switch_on()
        self._on_duty()
        self.exec_user.sudo().fw_location_exception = True
        r = self.url_open('/lab_fieldwork/gps?id=%s&lat=11.12&lon=76.12' % device)
        self.assertEqual((r.status_code, r.text), (200, 'OK 0'))
        self.assertFalse(self._pings())
