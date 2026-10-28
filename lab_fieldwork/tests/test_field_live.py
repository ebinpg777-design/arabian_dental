# -*- coding: utf-8 -*-
"""Field Live (models/field_live.py): the manager's board - who may open it, what it
carries per executive, the alerts it raises, the phones it reports, and the push
that moves a marker without a poll. (client, 2026-10-20: the three desks removed, a
real-time dashboard for the managers and the CEO in their place)"""
from datetime import timedelta

from freezegun import freeze_time

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestFieldLive(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env(su=True)
        ref = env.ref
        cls.exec_user = env['res.users'].create({
            'login': 'fl_exec', 'name': 'FL Exec', 'tz': 'Asia/Kolkata',
            'group_ids': [(4, ref('lab_fieldwork.group_fieldwork_executive').id),
                          (4, ref('base.group_user').id)]})
        cls.manager = env['res.users'].create({
            'login': 'fl_manager', 'name': 'FL Manager', 'tz': 'Asia/Kolkata',
            'group_ids': [(4, ref('lab_fieldwork.group_fieldwork_ops_manager').id),
                          (4, ref('base.group_user').id)]})
        cls.employee = env['hr.employee'].create({
            'name': 'FL Exec', 'user_id': cls.exec_user.id, 'company_id': env.company.id})
        cls.clinic = env['res.partner'].create({
            'name': 'FL Clinic', 'is_company': True,
            'partner_latitude': 11.1, 'partner_longitude': 76.1})
        env['ir.config_parameter'].set_str('lab_fieldwork.track_live', 'True')
        cls.Live = env['lab.field.live']

    def setUp(self):
        super().setUp()
        # 11:00 in Kolkata on the real date, for every test: the board reads the
        # clock everywhere (the day a visit belongs to, a day not started by ten,
        # the pulse, "no position for 45 min") and the suite runs at any hour -
        # after midnight IST an attendance two hours old is yesterday's.
        day = fields.Date.context_today(self.exec_user)
        self._freezer = freeze_time(day.strftime('%Y-%m-%d') + ' 05:30:00')
        self._freezer.start()
        self.addCleanup(self._freezer.stop)

    def _on_duty(self, hours_ago=1):
        return self.env['hr.attendance'].sudo().create({
            'employee_id': self.employee.id,
            'check_in': fields.Datetime.now() - timedelta(hours=hours_ago)})

    def _ping(self, minutes_ago, lat=11.1, lon=76.1, battery=80.0, speed=30.0):
        Ping = self.env['lab.location.ping'].with_user(self.exec_user)
        return Ping.record([{
            'latitude': lat, 'longitude': lon, 'speed_kmh': speed, 'battery': battery,
            'ts': fields.Datetime.to_string(fields.Datetime.now() - timedelta(minutes=minutes_ago))}])

    def _board(self):
        return self.Live.with_user(self.manager).get_board()

    def _me(self, board):
        return next(ex for ex in board['executives'] if ex['user_id'] == self.exec_user.id)

    def _alerts(self, board, key, user=None):
        return [a for a in board['alerts'] if a['key'] == key
                and (user is None or a['user_id'] == user.id)]

    def test_location_turned_off_in_the_phone_is_told_on_the_board(self):
        """Client, 2026-10-24: "if any time location off in the phone settings then
        that information needed"."""
        self._on_duty(hours_ago=2)
        self._ping(90)
        Event = self.env['lab.location.event']
        off = fields.Datetime.now() - timedelta(minutes=60)
        Event._record(self.exec_user, [{'kind': 'location_off', 'ts': fields.Datetime.to_string(off)}])
        board = self._board()
        me = self._me(board)
        self.assertIn('location_off', [e['kind'] for e in me['story']])
        self.assertEqual([b['kind'] for b in me['bands'] if b['kind'] == 'locoff'], ['locoff'])
        self.assertTrue(self._alerts(board, 'location_off', self.exec_user), "off now: the manager is told")
        Event._record(self.exec_user, [{'kind': 'location_on',
                                        'ts': fields.Datetime.to_string(off + timedelta(minutes=30))}])
        board = self._board()
        self.assertFalse(self._alerts(board, 'location_off', self.exec_user))
        locoff = [b for b in self._me(board)['bands'] if b['kind'] == 'locoff']
        self.assertEqual(locoff[0]['to_epoch'] - locoff[0]['from_epoch'], 30 * 60)

    def test_battery_saver_taking_the_gps_away_is_told_on_the_board(self):
        """ADL Pro 1.0.13 (2026-10-28): a phone placing itself by Wi-Fi and towers records
        nothing where the mobile data drops - the saver switching the GPS off is why."""
        self._on_duty(hours_ago=2)
        self._ping(30)
        Event = self.env['lab.location.event']
        on = fields.Datetime.now() - timedelta(minutes=40)
        Event._record(self.exec_user, [{'kind': 'gps_saver_on', 'ts': fields.Datetime.to_string(on)},
                                       {'kind': 'gps_silent', 'ts': fields.Datetime.to_string(on + timedelta(minutes=6))}])
        board = self._board()
        self.assertTrue(self._alerts(board, 'gps_saver', self.exec_user), "the manager is told")
        self.assertTrue({'gps_saver_on', 'gps_silent'} <= {e['kind'] for e in self._me(board)['story']})
        Event._record(self.exec_user, [{'kind': 'gps_saver_off',
                                        'ts': fields.Datetime.to_string(on + timedelta(minutes=20))}])
        self.assertFalse(self._alerts(self._board(), 'gps_saver', self.exec_user), "off again: no alert")

    # ------------------------------------------------------------------ access
    def test_only_a_manager_opens_the_board(self):
        with self.assertRaises(AccessError):
            self.Live.with_user(self.exec_user).get_board()
        board = self._board()
        for key in ('executives', 'clinics', 'kpis', 'alerts', 'phones', 'leaders', 'currency'):
            self.assertIn(key, board)
        self.assertTrue(board['config']['push'], "positions are pushed, the poll is the fallback")
        self.assertEqual(board['config']['live_ms'], 60000)

    # ------------------------------------------------------------------ the person
    def test_an_executive_carries_facts_a_phone_and_a_story(self):
        self._on_duty()
        self._ping(10, battery=72.0)
        self._ping(3, lat=11.11, lon=76.11, battery=70.0)
        visit = self.env['lab.visit'].with_user(self.exec_user).create({
            'partner_id': self.clinic.id, 'date': fields.Date.context_today(self.exec_user)})
        visit.sudo().write({'check_in': fields.Datetime.now() - timedelta(minutes=8),
                            'gps_lat': 11.1, 'gps_lon': 76.1, 'gps_state': 'ok', 'state': 'open'})
        board = self._board()
        me = self._me(board)
        self.assertTrue(me['on_duty'])
        self.assertEqual(me['facts']['planned'], 1)
        self.assertEqual(me['phone_app']['battery'], 70.0)
        self.assertFalse(me['phone_app']['on_app'], "a browser phone, so far")
        self.assertFalse(me['phone_app']['silent'])
        kinds = [event['kind'] for event in me['story']]
        self.assertEqual(kinds[0], 'start', "the story opens with the day starting")
        self.assertIn('visit_in', kinds)
        door = next(e for e in me['story'] if e['kind'] == 'visit_in')
        self.assertEqual((door['title'], door['state'], door['detail']), ('FL Clinic', 'ok', 'at the door'))
        self.assertEqual(me['avatar'], '/web/image/res.users/%d/avatar_128' % self.exec_user.id)
        kpis = board['kpis']
        self.assertGreaterEqual(kpis['duty'], 1)
        self.assertGreaterEqual(kpis['at_clinic'], 1)
        self.assertEqual(me['status'], 'at_clinic')
        self.assertEqual(len(kpis['pulse']), 14, "07:00 to 20:00, one bar an hour")
        # the pulse covers the working day: a check-in made while this test runs at
        # night (the suite after 20:00 IST) is rightly off it
        local_hour = fields.Datetime.context_timestamp(
            self.env['res.users'].with_user(self.exec_user).browse(self.exec_user.id),
            fields.Datetime.now() - timedelta(minutes=8)).hour
        on_pulse = sum(p['count'] for p in kpis['pulse'])
        if 7 <= local_hour <= 20:
            self.assertEqual(on_pulse, 1, "one check-in on the pulse")
            self.assertEqual(next(p['count'] for p in kpis['pulse'] if p['hour'] == local_hour), 1)
        else:
            self.assertEqual(on_pulse, 0)

    def test_the_phone_on_the_app_is_told_apart(self):
        self._on_duty()
        self.env['lab.my.day'].with_user(self.exec_user).tracking_state(client='app', app_version='1.0.0')
        self.env['lab.location.ping'].with_user(self.exec_user).with_context(fw_ping_source='app').record([{
            'latitude': 11.1, 'longitude': 76.1, 'battery': 55.0,
            'ts': fields.Datetime.to_string(fields.Datetime.now() - timedelta(minutes=2))}])
        board = self._board()
        phone = next(p for p in board['phones'] if p['user_id'] == self.exec_user.id)
        self.assertTrue(phone['on_app'])
        self.assertEqual((phone['app_version'], phone['source'], phone['battery']), ('1.0.0', 'app', 55.0))
        self.assertEqual(board['kpis']['on_app'], 1)

    # ------------------------------------------------------------------ the alerts
    def test_a_phone_silent_on_duty_is_an_alert_and_names_the_app(self):
        self._on_duty(hours_ago=2)
        self._ping(45)                                  # heard from, then nothing
        board = self._board()
        alert = self._alerts(board, 'silent', self.exec_user)
        self.assertEqual(len(alert), 1)
        self.assertEqual(alert[0]['level'], 'danger')
        self.assertIn('no position for 45 min', alert[0]['detail'])
        self.assertNotIn('ADL Pro', alert[0]['detail'], "a browser phone: no app to blame")
        self.assertTrue(self._me(board)['phone_app']['silent'])
        self.assertEqual(board['kpis']['silent'], 1)
        self.env['lab.my.day'].with_user(self.exec_user).tracking_state(client='app', app_version='1.0.0')
        board = self._board()
        self.assertIn('ADL Pro', self._alerts(board, 'silent', self.exec_user)[0]['detail'])
        # which silence (2026-10-24): not heard from at all - no network, or the app
        # stopped; heard from but holding no position - no GPS fix
        self.assertEqual(self._me(board)['phone_app']['reason'], 'network')
        self.assertIn('not heard from', self._alerts(board, 'silent', self.exec_user)[0]['detail'])
        self.env['lab.location.ping'].sudo()._heard(self.exec_user)
        board = self._board()
        alert = self._alerts(board, 'silent', self.exec_user)[0]
        self.assertEqual(alert['title'], 'No GPS fix')
        self.assertIn('holds no position', alert['detail'])
        self.assertEqual(self._me(board)['phone_app']['reason'], 'gps')
        self.assertEqual(self._me(board)['status_label'], 'no GPS fix')
        self.assertLess(self._me(board)['phone_app']['heard_age_s'], 60)
        # off duty, silence is nobody's business
        self.env['hr.attendance'].sudo().search([('employee_id', '=', self.employee.id)]).write(
            {'check_out': fields.Datetime.now()})
        self.assertFalse(self._alerts(self._board(), 'silent', self.exec_user))

    def test_a_dying_battery_and_a_long_open_visit_are_alerts(self):
        self._on_duty()
        self._ping(2, battery=9.0)
        visit = self.env['lab.visit'].with_user(self.exec_user).create({
            'partner_id': self.clinic.id, 'date': fields.Date.context_today(self.exec_user)})
        visit.sudo().write({'check_in': fields.Datetime.now() - timedelta(minutes=100),
                            'gps_lat': 11.1, 'gps_lon': 76.1, 'gps_state': 'ok', 'state': 'open'})
        board = self._board()
        self.assertEqual(self._alerts(board, 'battery', self.exec_user)[0]['title'], 'Battery 9%')
        long_visit = self._alerts(board, 'long_visit', self.exec_user)
        self.assertEqual(len(long_visit), 1)
        self.assertIn('FL Clinic', long_visit[0]['title'])
        self.assertGreaterEqual(board['kpis']['alerts'], 2)
        self.assertEqual(board['alerts'][0]['level'], 'warning', "the worst first")

    def test_a_day_not_started_by_ten_is_said_only_after_ten(self):
        # 05:30 UTC is 11:00 in Kolkata; 03:00 UTC is 08:30
        day = fields.Date.context_today(self.exec_user)
        self.env['lab.visit'].with_user(self.exec_user).create({'partner_id': self.clinic.id, 'date': day})
        with freeze_time(day.strftime('%Y-%m-%d') + ' 03:00:00'):
            self.assertFalse(self._alerts(self._board(), 'late', self.exec_user))
        with freeze_time(day.strftime('%Y-%m-%d') + ' 05:30:00'):
            late = self._alerts(self._board(), 'late', self.exec_user)
        self.assertEqual(len(late), 1)
        self.assertIn('1 visit(s) planned', late[0]['detail'])
        self.assertEqual(self._board()['kpis']['not_started'], 1)

    # ------------------------------------------------------------------ the push
    def test_every_batch_is_announced_to_the_manager_group(self):
        self._on_duty()
        self.env.cr.precommit.data.pop('bus.bus.values', None)
        self._ping(1, lat=11.12, lon=76.12, speed=42.0)
        sent = [(channel, kind, payload) for channel, kind, payload
                in self.env.cr.precommit.data.get('bus.bus.values', [])
                if kind == 'lab_fieldwork.live']
        self.assertEqual(len(sent), 1, "one announcement per batch")
        channel, _kind, payload = sent[0]
        self.assertEqual(channel[1:], ('res.groups', self.env.ref('lab_fieldwork.group_fieldwork_manager').id),
                         "the manager group is the channel")
        self.assertEqual((payload['user_id'], payload['lat'], payload['lon'], payload['speed_kmh']),
                         (self.exec_user.id, 11.12, 76.12, 42.0))
        self.assertEqual(payload['kept'], 1)
        # nothing kept (off duty): nothing announced
        self.env['hr.attendance'].sudo().search([('employee_id', '=', self.employee.id)]).write(
            {'check_out': fields.Datetime.now()})
        self.env.cr.precommit.data.pop('bus.bus.values', None)
        self.env['lab.location.ping'].with_user(self.exec_user).record([{
            'latitude': 11.2, 'longitude': 76.2,
            'ts': fields.Datetime.to_string(fields.Datetime.now() + timedelta(minutes=5))}])
        self.assertFalse([k for _c, k, _p in self.env.cr.precommit.data.get('bus.bus.values', [])
                          if k == 'lab_fieldwork.live'])

    # ------------------------------------------------------------------ the menus
    def test_field_live_is_the_managers_first_menu_and_the_desks_are_gone(self):
        menus = self.env['ir.ui.menu'].with_user(self.manager)._visible_menu_ids()
        self.assertIn(self.env.ref('lab_fieldwork.menu_fw_field_live').id, menus)
        exec_menus = self.env['ir.ui.menu'].with_user(self.exec_user)._visible_menu_ids()
        self.assertNotIn(self.env.ref('lab_fieldwork.menu_fw_field_live').id, exec_menus)
        for gone in ('menu_fw_ops_desk', 'menu_fw_marketing_desk', 'menu_fw_admin_desk',
                     'action_ops_desk', 'action_marketing_desk', 'action_admin_desk',
                     'menu_fw_ops_live_map'):
            self.assertFalse(self.env.ref('lab_fieldwork.%s' % gone, raise_if_not_found=False),
                             '%s should be gone' % gone)

    # ------------------------------------------------------------------ the reports
    def test_the_day_comes_as_bands_and_a_split(self):
        day = fields.Date.context_today(self.exec_user)
        if True:
            self._on_duty(hours_ago=2)                       # 09:00
            self._ping(5)
            visit = self.env['lab.visit'].with_user(self.exec_user).create({
                'partner_id': self.clinic.id, 'date': day})
            visit.sudo().write({'check_in': fields.Datetime.now() - timedelta(minutes=60),
                                'check_out': fields.Datetime.now() - timedelta(minutes=20),
                                'gps_lat': 11.1, 'gps_lon': 76.1, 'gps_state': 'ok', 'state': 'done'})
            board = self._board()
            me = self._me(board)
            kinds = [band['kind'] for band in me['bands']]
            self.assertEqual(kinds[0], 'duty')
            self.assertIn('visit', kinds)
            duty = next(b for b in me['bands'] if b['kind'] == 'duty')
            self.assertGreaterEqual(duty['to_epoch'] - duty['from_epoch'], 2 * 3600 - 60)
            self.assertTrue(board['day_start_epoch'] < duty['from_epoch'] < board['day_end_epoch'])
            at_door = next(b for b in me['bands'] if b['kind'] == 'visit')
            self.assertEqual((at_door['label'], at_door['open'], at_door['state']), ('FL Clinic', False, 'ok'))
            self.assertEqual(at_door['to_epoch'] - at_door['from_epoch'], 40 * 60)
            split = me['split']
            self.assertEqual(split['clinic'], 40)
            self.assertGreaterEqual(split['duty'], 119)
            self.assertEqual(split['road'], split['duty'] - 40 - split['stopped'] - split['unrecorded'])
            self.assertGreaterEqual(board['kpis']['clinic_minutes'], 40)
            self.assertEqual(len(board['routes']), 1)
            self.assertEqual((board['routes'][0]['people'], board['routes'][0]['visits_done']), (1, 1))

    def test_the_fortnight_and_the_month_read_the_sheets(self):
        day = fields.Date.context_today(self.exec_user)
        Sheet = self.env['lab.daily.update'].sudo()
        seeded = {}
        for back, visits, collected in ((1, 3, 500.0), (3, 2, 250.0)):
            when = day - timedelta(days=back)
            sheet = Sheet.create({'user_id': self.exec_user.id, 'date': when})
            # the sheet assembles itself from the day's (empty) records: write the
            # figures a real day would have left
            sheet.write({'visit_done_count': visits, 'visit_count': visits, 'case_count': 1,
                         'collected': collected, 'km': 10.0 * back, 'gps_km': 9.0 * back,
                         'is_clean': back == 1, 'state': 'approved'})
            seeded[fields.Date.to_string(when)] = (visits, collected)
        board = self._board()
        trend = board['trend']
        self.assertEqual(len(trend['days']), 14)
        self.assertEqual(trend['days'][-1]['date'], fields.Date.to_string(day))
        self.assertTrue(trend['days'][-1]['live'], "today comes from the live board")
        by_date = {row['date']: row for row in trend['days']}
        for date, (visits, collected) in seeded.items():
            self.assertEqual((by_date[date]['visits'], by_date[date]['collected'], by_date[date]['people']),
                             (visits, collected, 1))
        me = next(p for p in trend['people'] if p['user_id'] == self.exec_user.id)
        cells = dict(zip([d['date'] for d in trend['days']], me['days']))
        yesterday = fields.Date.to_string(day - timedelta(days=1))
        self.assertEqual((cells[yesterday]['visits'], cells[yesterday]['clean']), (3, True))
        self.assertFalse(cells[fields.Date.to_string(day - timedelta(days=3))]['clean'])
        self.assertIsNone(cells[fields.Date.to_string(day - timedelta(days=2))], "no sheet, no dot")
        in_month = [(v, c) for d, (v, c) in seeded.items() if fields.Date.to_date(d) >= day.replace(day=1)]
        month = [row for row in board['month'] if row['user_id'] == self.exec_user.id]
        if in_month:
            self.assertEqual(len(month), 1)
            self.assertEqual((month[0]['days'], month[0]['visits'], month[0]['collected']),
                             (len(in_month), sum(v for v, _c in in_month), sum(c for _v, c in in_month)))
        else:
            self.assertFalse(month)
