# -*- coding: utf-8 -*-
"""The Lab app (models/field_app.py, controllers/field_app.py): a phone provisions
itself through tracking_state, browsers stand down for it, its positions come in
through the Traccar door without the Traccar switch, and the APK is kept by Field
Work settings and served from the download page. (client, 2026-10-20: live GPS
without the third-party Traccar Client app)"""
import base64
import json
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
        # the phone itself, as the app from 1.0.8 says it (2026-10-24); tidied, capped
        self.assertFalse(me.fw_native_device)
        self.env['lab.my.day'].with_user(self.exec_user).tracking_state(
            client='app', app_version='1.0.0', app_device='  vivo  V2250 \u00b7 Android 14 ' + 'x' * 80)
        self.assertEqual(me.fw_native_device, ('vivo V2250 \u00b7 Android 14 ' + 'x' * 80)[:64])
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
        self.assertIn('adl-pro-v1.0.0.apk', apk.headers.get('Content-Disposition', ''))
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


def _ipa(devices=('00008030-001A2B3C4D5E6F70', '00008110-000A1B2C3D4E5F60'), version='1.0.0',
         bundle='com.arabiandentallab.field', all_devices=False):
    """A minimal iPhone build: Info.plist (binary, as Xcode writes it) and a signed
    profile - here only the XML plist inside its envelope, which is all that is read."""
    import io
    import plistlib
    import zipfile
    from datetime import datetime
    info = plistlib.dumps({'CFBundleIdentifier': bundle, 'CFBundleShortVersionString': version,
                           'CFBundleVersion': '7', 'CFBundleDisplayName': 'ADL Pro'},
                          fmt=plistlib.FMT_BINARY)
    provision = {'ExpirationDate': datetime(2027, 10, 21, 9, 0, 0), 'Name': 'ADL Pro Ad Hoc'}
    if devices:
        provision['ProvisionedDevices'] = list(devices)
    if all_devices:
        provision['ProvisionsAllDevices'] = True
    envelope = b'0\x82\x10\x00signed-data' + plistlib.dumps(provision) + b'\x00\x01signature'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('Payload/ADLPro.app/Info.plist', info)
        archive.writestr('Payload/ADLPro.app/embedded.mobileprovision', envelope)
        archive.writestr('Payload/ADLPro.app/ADLPro', b'\xcf\xfa\xed\xfe binary')
    return buffer.getvalue()


IPHONE = ('Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 '
          '(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1')


@tagged('post_install', '-at_install')
class TestFieldAppIphone(HttpCase):
    """The iPhone on the same link (client, 2026-10-21: "create the iOS app also, with a
    download button on the same link")."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env(su=True)
        cls.exec_user = env['res.users'].create({
            'login': 'field_ios_exec', 'password': 'field_ios_exec_pw', 'name': 'Field iOS Exec',
            'group_ids': [(4, env.ref('lab_fieldwork.group_fieldwork_executive').id),
                          (4, env.ref('base.group_user').id)]})
        cls.fw_admin = env['res.users'].create({
            'login': 'field_ios_admin', 'name': 'Field iOS Admin',
            'group_ids': [(4, env.ref('lab_fieldwork.group_fieldwork_admin').id),
                          (4, env.ref('base.group_user').id)]})
        cls.App = env['lab.field.app']

    def _settings(self, **vals):
        Settings = self.env['lab.fieldwork.settings'].with_user(self.fw_admin)
        Settings.create(vals).action_save()
        return Settings

    def _page(self, agent=IPHONE):
        self.authenticate('field_ios_exec', 'field_ios_exec_pw')
        return self.url_open('/lab_fieldwork/app', headers={'User-Agent': agent})

    def test_the_build_is_read_and_refused_when_it_cannot_install_from_a_link(self):
        from ..models.field_app import read_ipa
        meta = read_ipa(_ipa(), self.env)
        self.assertEqual((meta['bundle_id'], meta['version'], meta['devices'], meta['expires']),
                         ('com.arabiandentallab.field', '1.0.0', 2, '2027-10-21'))
        self.assertTrue(read_ipa(_ipa(devices=(), all_devices=True), self.env)['all_devices'])
        with self.assertRaises(UserError):
            read_ipa(b'hello', self.env)
        with self.assertRaises(UserError):
            read_ipa(APK, self.env)
        with self.assertRaises(UserError, msg="an App Store / TestFlight build lists no iPhones"):
            read_ipa(_ipa(devices=()), self.env)

    def test_an_uploaded_build_installs_from_the_same_page(self):
        build = _ipa()
        self._settings(fw_ios_ipa=base64.b64encode(build).decode(), fw_ios_ipa_filename='adl.ipa')
        info = self.App._ios_info()
        self.assertEqual((info['kind'], info['version'], info['devices']), ('ipa', '1.0.0', '2'))
        self.assertTrue(info['install_url'].startswith('itms-services://?action=download-manifest&url=https%3A')
                        or info['install_url'].startswith('itms-services://?action=download-manifest&url=http%3A'))
        token = self.App._param('ios_token')
        # what the iPhone's installer fetches, without a login
        manifest = self.url_open('/lab_fieldwork/app/ios/%s/manifest.plist' % token)
        self.assertEqual(manifest.status_code, 200)
        import plistlib
        item = plistlib.loads(manifest.content)['items'][0]
        self.assertEqual(item['metadata']['bundle-identifier'], 'com.arabiandentallab.field')
        self.assertEqual(item['metadata']['bundle-version'], '1.0.0')
        self.assertTrue(item['assets'][0]['url'].endswith('/lab_fieldwork/app/ios/%s/ADLPro.ipa' % token))
        got = self.url_open('/lab_fieldwork/app/ios/%s/ADLPro.ipa' % token)
        self.assertEqual((got.status_code, got.content), (200, build))
        self.assertEqual(self.url_open('/lab_fieldwork/app/ios/wrong-token/manifest.plist').status_code, 404)
        self.assertEqual(self.url_open('/lab_fieldwork/app/ios/wrong-token/ADLPro.ipa').status_code, 404)
        # the page, on an iPhone: the iPhone button, not the APK
        page = self._page()
        self.assertIn('Install on iPhone', page.text)
        self.assertIn('itms-services://', page.text)
        self.assertNotIn('Download the app', page.text)
        # a new build is a new link: the one sent with the old build stops working
        self._settings(fw_ios_ipa=base64.b64encode(_ipa(version='1.0.1')).decode())
        self.assertEqual(self.url_open('/lab_fieldwork/app/ios/%s/manifest.plist' % token).status_code, 404)
        self.assertEqual(self.App._latest_version('ios'), '1.0.1')

    def test_a_testflight_link_and_removing_the_build(self):
        with self.assertRaises(UserError):
            self._settings(fw_ios_link='http://testflight.apple.com/join/ABC')
        self._settings(fw_ios_link='https://testflight.apple.com/join/ABC')
        info = self.App._ios_info()
        self.assertEqual((info['kind'], info['install_url']), ('link', 'https://testflight.apple.com/join/ABC'))
        self.assertIn('testflight.apple.com/join/ABC', self._page().text)
        # an uploaded build wins over the link; removed, the link is back
        self._settings(fw_ios_ipa=base64.b64encode(_ipa()).decode(),
                       fw_ios_link='https://testflight.apple.com/join/ABC')
        self.assertEqual(self.App._ios_info()['kind'], 'ipa')
        token = self.App._param('ios_token')
        self._settings(fw_ios_remove=True, fw_ios_link='https://testflight.apple.com/join/ABC')
        self.assertEqual(self.App._ios_info()['kind'], 'link')
        self.assertEqual(self.url_open('/lab_fieldwork/app/ios/%s/manifest.plist' % token).status_code, 404)

    def test_without_an_iphone_app_the_page_shows_the_home_screen_way(self):
        page = self._page()
        self.assertIn('Add to Home Screen', page.text)
        self.assertNotIn('itms-services', page.text)
        # a computer sees both phones; an Android phone only its own
        desktop = self._page('Mozilla/5.0 (X11; Linux x86_64) Chrome/130.0')
        self.assertIn('Android phones', desktop.text)
        self.assertIn('iPhone', desktop.text)
        android = self._page('Mozilla/5.0 (Linux; Android 14; RMX3085) Chrome/130.0 Mobile')
        self.assertNotIn('Add to Home Screen', android.text)

    def test_each_phone_hears_of_its_own_newer_build(self):
        from ..models.field_app import platform_of
        self.assertEqual(platform_of(IPHONE), 'ios')
        self.assertEqual(platform_of(IPHONE + ' ADLField/1.0.0'), 'ios')
        self.assertEqual(platform_of('Mozilla/5.0 (Linux; Android 14) ADLField/1.0.1'), 'android')
        self.App._set_version('1.0.1')
        self._settings(fw_ios_ipa=base64.b64encode(_ipa(version='1.0.0')).decode())
        self.assertEqual((self.App._latest_version('android'), self.App._latest_version('ios')), ('1.0.1', '1.0.0'))
        # inside the iPhone app the page offers the iPhone update, never the APK
        self._settings(fw_ios_ipa=base64.b64encode(_ipa(version='1.0.2')).decode())
        page = self._page(IPHONE + ' ADLField/1.0.0')
        self.assertIn('You are using the app', page.text)
        self.assertIn('Version <b>1.0.2</b> is out', page.text)
        self.assertIn('itms-services://', page.text)
        self.assertNotIn('/lab_fieldwork/app/download', page.text)


@tagged('post_install', '-at_install')
class TestFieldStaffStartOnMyDay(HttpCase):
    """ADL Pro opens on My Day, not on Odoo's app screen and a tap on Field Work.
    (client, 2026-10-22: "check the delay in connecting ADL Pro app")"""

    APP = 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/130.0 Mobile ADLField/1.0.1'
    BROWSER = 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/130.0 Mobile'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env(su=True)
        executive = env.ref('lab_fieldwork.group_fieldwork_executive')
        cls.exec_user = env['res.users'].create({
            'login': 'start_exec', 'password': 'start_exec_pw1', 'name': 'Start Exec',
            'group_ids': [(4, executive.id), (4, env.ref('base.group_user').id)]})
        cls.office_user = env['res.users'].create({
            'login': 'start_office', 'password': 'start_office_pw1', 'name': 'Start Office',
            'group_ids': [(4, executive.id), (4, env.ref('sales_team.group_sale_manager').id)]})
        cls.my_day = env.ref('lab_fieldwork.action_my_day')

    def _home(self, login, agent):
        self.authenticate(login, login + '_pw1')
        reply = self.url_open('/web/session/get_session_info', data=json.dumps(
            {'jsonrpc': '2.0', 'method': 'call', 'params': {}}),
            headers={'Content-Type': 'application/json', 'User-Agent': agent})
        return reply.json()['result'].get('home_action_id')

    def test_an_executive_opens_on_my_day_in_the_app_and_in_a_browser(self):
        self.assertEqual(self._home('start_exec', self.APP), self.my_day.id)
        self.assertEqual(self._home('start_exec', self.BROWSER), self.my_day.id)

    def test_office_staff_keep_odoos_start_screen_in_a_browser(self):
        self.assertFalse(self._home('start_office', self.BROWSER))
        self.assertEqual(self._home('start_office', self.APP), self.my_day.id,
                         "inside the field app, the field app's page")

    def test_a_home_action_set_on_the_user_still_wins(self):
        own = self.env.ref('lab_fieldwork.action_visit_my')
        self.exec_user.sudo().write({'action_id': own.id})
        self.assertEqual(self._home('start_exec', self.APP), own.id)
