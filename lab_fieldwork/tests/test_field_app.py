# -*- coding: utf-8 -*-
"""The Lab app (models/field_app.py, controllers/field_app.py): a phone provisions
itself through tracking_state, browsers stand down for it, its positions come in
through the Traccar door without the Traccar switch, and the APK is kept by Field
Work settings and served from the download page. (client, 2026-10-20: live GPS
without the third-party Traccar Client app)"""
import base64
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import HttpCase, tagged

APK = b'PK\x03\x04' + b'\0' * 64


@tagged('post_install', '-at_install')
class TestFieldApp(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env(su=True)
        ref = env.ref
        cls.exec_user = env['res.users'].create({
            'login': 'field_app_exec', 'password': 'field_app_exec_pw',
            'name': 'Field App Exec', 'tz': 'Asia/Kolkata',
            'group_ids': [(4, ref('lab_fieldwork.group_fieldwork_executive').id),
                          (4, ref('base.group_user').id)]})
        cls.fw_admin = env['res.users'].create({
            'login': 'field_app_admin', 'name': 'Field App Admin',
            'group_ids': [(4, ref('lab_fieldwork.group_fieldwork_admin').id),
                          (4, ref('base.group_user').id)]})
        cls.employee = env['hr.employee'].create({
            'name': 'Field App Exec', 'user_id': cls.exec_user.id, 'company_id': env.company.id})
        env['ir.config_parameter'].set_str('lab_fieldwork.track_live', 'True')
        cls.App = env['lab.field.app']

    def _on_duty(self):
        return self.env['hr.attendance'].sudo().create({
            'employee_id': self.employee.id,
            'check_in': fields.Datetime.now() - timedelta(hours=1)})

    def _state(self, **kwargs):
        return self.env['lab.my.day'].with_user(self.exec_user).tracking_state(**kwargs)

    def _pings(self):
        return self.env['lab.location.ping'].sudo().search(
            [('user_id', '=', self.exec_user.id)], order='ts')

    # ------------------------------------------------------------ provisioning
    def test_the_app_provisions_itself_and_its_page_stands_down(self):
        self._on_duty()
        state = self._state(client='app', app_version='1.0.0')
        native = state['native']
        self.assertTrue(native and len(native['device_id']) == 16, "a device id, made on the spot")
        self.assertEqual(native['path'], '/lab_fieldwork/gps')
        self.assertEqual((native['interval_s'], native['min_move_m'], native['heartbeat_s']),
                         (90, 50, 300))
        self.assertFalse(state['enabled'], "inside the app the page never watches by itself")
        self.assertTrue(state['app_mode'])
        self.assertIsNone(state['app'], "no Traccar instructions inside the app")
        me = self.exec_user.sudo()
        self.assertTrue(me.fw_native_app)
        self.assertEqual(me.fw_native_app_version, '1.0.0')
        self.assertFalse(me.fw_gps_app, "the Traccar switch stays off")
        # the same id every time; a new build is noted; junk in the version is not kept
        again = self._state(client='app', app_version='1.1.0<script>')
        self.assertEqual(again['native']['device_id'], native['device_id'])
        self.assertEqual(me.fw_native_app_version, '1.1.0script')
        self.assertEqual(state['native_app']['version'], '1.0.0')
        self.assertIsNone(state['native_app']['last_fix_age_s'], "nothing posted yet")

    def test_the_app_is_handed_the_recorder_only_while_the_day_runs(self):
        """Start my day starts recording, End the day stops it (client, 2026-10-20)."""
        state = self._state(client='app', app_version='1.0.1')
        self.assertIsNone(state['native'], "no day running: nothing to record with")
        self.assertTrue(state['app_mode'] and not state['enabled'],
                        "...and the page never watches by itself inside the app")
        self.assertTrue(self.exec_user.sudo().fw_native_app, "provisioned all the same")
        self._on_duty()
        self.assertTrue(self._state(client='app', app_version='1.0.1')['native'])
        self.env['hr.attendance'].sudo().search(
            [('employee_id', '=', self.employee.id)]).write({'check_out': fields.Datetime.now()})
        self.assertIsNone(self._state(client='app', app_version='1.0.1')['native'])

    def test_a_browser_watches_until_the_app_is_heard_from(self):
        self._on_duty()
        self.assertTrue(self._state()['enabled'])
        self.assertFalse(self._state()['app_live'])
        self._state(client='app', app_version='1.0.0')
        self.assertTrue(self._state()['enabled'], "installed is not the same as running")
        Ping = self.env['lab.location.ping'].with_user(self.exec_user) \
            .with_context(fw_ping_source='app')
        kept = Ping.record([{'latitude': 11.1, 'longitude': 76.1, 'ts': fields.Datetime.to_string(
            fields.Datetime.now() - timedelta(minutes=2))}])
        self.assertEqual(kept, 1)
        state = self._state()
        self.assertFalse(state['enabled'], "the app is posting: this browser stands down")
        self.assertTrue(state['app_live'] and state['app_mode'])
        self.assertLessEqual(state['native_app']['last_fix_age_s'], 200)
        # ...and only while it is: a position from an hour ago is not live
        self._pings().write({'ts': fields.Datetime.now() - timedelta(hours=1)})
        state = self._state()
        self.assertTrue(state['enabled'])
        self.assertFalse(state['app_live'])

    def test_an_excused_phone_or_a_switched_off_lab_gets_no_service(self):
        self._on_duty()
        self.exec_user.sudo().fw_location_exception = True
        state = self._state(client='app', app_version='1.0.0')
        self.assertIsNone(state['native'], "an excused phone is never watched, app or browser")
        self.assertFalse(state['enabled'], "...and inside the app the page never watches either")
        self.exec_user.sudo().fw_location_exception = False
        self.env['ir.config_parameter'].sudo().set_str('lab_fieldwork.track_live', 'False')
        self.assertIsNone(self._state(client='app', app_version='1.0.0')['native'])

    # ------------------------------------------------------------- the GPS door
    def test_the_app_posts_without_the_traccar_switch_and_hears_when_the_day_ends(self):
        self._on_duty()
        device = self._state(client='app', app_version='1.0.0')['native']['device_id']
        when = (fields.Datetime.now() - timedelta(minutes=1)).replace(microsecond=0)
        body = {'device_id': device, 'locations': [{
            'timestamp': when.isoformat() + 'Z',
            'coords': {'latitude': 11.12, 'longitude': 76.12, 'speed': 4.0, 'accuracy': 9},
            'battery': {'level': 0.7}}]}
        r = self.url_open('/lab_fieldwork/gps', json=body)
        self.assertEqual((r.status_code, r.text), (200, 'OK 1'))
        self.assertEqual(r.headers.get('X-Lab-Track'), 'on', "the day is running: carry on")
        ping = self._pings()
        self.assertEqual((ping.source, ping.battery), ('app', 70.0))
        # the day ended on the office computer: the next report-in tells the phone
        self.env['hr.attendance'].sudo().search(
            [('employee_id', '=', self.employee.id)]).write({'check_out': fields.Datetime.now()})
        r = self.url_open('/lab_fieldwork/gps', json={'device_id': device, 'locations': []})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get('X-Lab-Track'), 'off')

    # ------------------------------------------------------------- the APK
    def test_settings_keep_the_apk_and_the_page_serves_it(self):
        self.assertIsNone(self.App._info())
        Settings = self.env['lab.fieldwork.settings'].with_user(self.fw_admin)
        with self.assertRaises(UserError):
            Settings.create({'fw_app_apk': base64.b64encode(b'hello').decode(),
                             'fw_app_version': '1.0.0'}).action_save()
        Settings.create({'fw_app_apk': base64.b64encode(APK).decode(),
                         'fw_app_apk_filename': 'adl.apk',
                         'fw_app_version': ' v1.0.0 '}).action_save()
        info = self.App._info()
        self.assertEqual((info['version'], info['url']), ('v1.0.0', '/lab_fieldwork/app'))
        self.assertEqual(bytes(self.App._attachment().raw), APK)
        self.assertEqual(self._state()['app_download']['version'], 'v1.0.0',
                         "My Day is told there is an app to offer")
        # the page and the file, as an executive
        self.authenticate('field_app_exec', 'field_app_exec_pw')
        page = self.url_open('/lab_fieldwork/app')
        self.assertEqual(page.status_code, 200)
        self.assertIn('v1.0.0', page.text)
        self.assertIn('/lab_fieldwork/app/download', page.text)
        apk = self.url_open('/lab_fieldwork/app/download')
        self.assertEqual(apk.status_code, 200)
        self.assertEqual(apk.content, APK)
        self.assertIn('android.package-archive', apk.headers.get('Content-Type', ''))
        self.assertIn('adl-field-v1.0.0.apk', apk.headers.get('Content-Disposition', ''))
        # the version alone can change, keeping the file; the settings read it back
        Settings.create({'fw_app_version': '1.0.1'}).action_save()
        self.assertEqual(self.App._version(), '1.0.1')
        self.assertEqual(bytes(self.App._attachment().raw), APK)
        defaults = Settings.default_get(['fw_app_version', 'fw_app_info', 'fw_app_url'])
        self.assertEqual(defaults['fw_app_version'], '1.0.1')
        self.assertIn('1.0.1', defaults['fw_app_info'])
        self.assertTrue(defaults['fw_app_url'].endswith('/lab_fieldwork/app'))

    def test_without_an_apk_the_page_says_so(self):
        self.authenticate('field_app_exec', 'field_app_exec_pw')
        page = self.url_open('/lab_fieldwork/app')
        self.assertEqual(page.status_code, 200)
        self.assertIn('not available yet', page.text)
        self.assertEqual(self.url_open('/lab_fieldwork/app/download').status_code, 404)
        self.assertIsNone(self._state()['app_download'], "nothing to offer on My Day")
        defaults = self.env['lab.fieldwork.settings'].with_user(self.fw_admin) \
            .default_get(['fw_app_info'])
        self.assertIn('No app', defaults['fw_app_info'])
