# -*- coding: utf-8 -*-
"""Route duties: who works which sales route on which days, beyond the standing
membership - and what that widens, moves and refuses. Run as the people
concerned, never as admin."""
from datetime import timedelta

from psycopg2.errors import CheckViolation

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged('post_install', '-at_install')
class TestRouteDuty(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        G = cls.env.ref
        base = G('base.group_user').id

        def user(login, *groups):
            return cls.env['res.users'].create({
                'name': login.replace('_', ' ').title(), 'login': login,
                'tz': 'Asia/Kolkata',
                'group_ids': [(6, 0, [base] + [G(g).id for g in groups])]})

        cls.exec_a = user('rd_exec_a', 'lab_fieldwork.group_fieldwork_executive')
        cls.exec_b = user('rd_exec_b', 'lab_fieldwork.group_fieldwork_executive')
        cls.exec_c = user('rd_exec_c', 'lab_fieldwork.group_fieldwork_executive')
        cls.manager = user('rd_manager', 'lab_fieldwork.group_fieldwork_ops_manager')
        Team = cls.env['crm.team']
        cls.route_a = Team.create({'name': 'RD Route A', 'user_id': cls.exec_a.id})
        cls.route_b = Team.create({'name': 'RD Route B', 'user_id': cls.exec_b.id})
        cls.route_c = Team.create({'name': 'RD Route C'})           # nobody standing
        Partner = cls.env['res.partner']
        cls.clinic_a1 = Partner.create({'name': 'RD Clinic A1', 'is_clinic': True,
                                        'is_company': True, 'team_id': cls.route_a.id})
        cls.clinic_a2 = Partner.create({'name': 'RD Clinic A2', 'is_clinic': True,
                                        'is_company': True, 'team_id': cls.route_a.id})
        cls.clinic_b1 = Partner.create({'name': 'RD Clinic B1', 'is_clinic': True,
                                        'is_company': True, 'team_id': cls.route_b.id})
        cls.Duty = cls.env['lab.route.duty']
        cls.today = cls.Duty._today()

    def _as(self, user):
        return self.Duty.with_user(user)

    def _visit(self, user, clinic, date):
        return self.env['lab.visit'].create({
            'partner_id': clinic.id, 'user_id': user.id, 'date': date})

    # ------------------------------------------------------------ assigning
    def test_a_manager_puts_many_people_on_many_routes_at_once(self):
        res = self._as(self.manager).assign(
            [self.exec_c.id, self.exec_a.id], [self.route_a.id, self.route_c.id],
            self.today, self.today + timedelta(days=2), 'regular')
        # c on A and C, a on C; a on A is skipped - it is their own route
        self.assertEqual(res['count'], 3)
        self.assertEqual(len(res['skipped']), 1)
        self.assertIn('already works', res['skipped'][0])
        duties = self.Duty.search([('id', 'in', res['ids'])])
        self.assertEqual(set(duties.mapped('state')), {'active'})
        self.assertEqual(set(duties.mapped('requester_id').ids), {self.manager.id})
        # asking the same again adds nothing
        again = self._as(self.manager).assign(
            [self.exec_c.id], [self.route_a.id], self.today, self.today, 'regular')
        self.assertEqual(again['count'], 0)
        self.assertIn('already on', again['skipped'][0])

    def test_a_duty_widens_my_routes_for_its_days_only(self):
        self._as(self.manager).assign(
            [self.exec_c.id], [self.route_a.id], self.today, self.today + timedelta(days=1))
        self.assertIn(self.route_a, self.Duty._routes_on(self.exec_c, self.today))
        self.assertIn(self.route_a, self.Duty._routes_on(self.exec_c, self.today + timedelta(days=1)))
        self.assertNotIn(self.route_a, self.Duty._routes_on(self.exec_c, self.today + timedelta(days=2)))
        self.assertNotIn(self.route_b, self.Duty._routes_on(self.exec_c, self.today))
        # the user field every screen reads
        self.exec_c.invalidate_recordset(['fw_route_ids'])
        self.assertIn(self.route_a, self.exec_c.fw_route_ids)
        # and the partner restriction: c reads A's clinics today, never B's
        seen = self.env['res.partner'].with_user(self.exec_c).search(
            [('id', 'in', (self.clinic_a1 | self.clinic_a2 | self.clinic_b1).ids)])
        self.assertEqual(seen, self.clinic_a1 | self.clinic_a2)
        # My Day's clinic list follows
        rows = self.env['lab.my.day'].with_user(self.exec_c).get_clinics()['rows']
        self.assertEqual({r['name'] for r in rows}, {'RD Clinic A1', 'RD Clinic A2'})

    def test_nobody_on_a_route_reads_nothing_and_the_roster_says_so(self):
        seen = self.env['res.partner'].with_user(self.exec_c).search(
            [('id', 'in', (self.clinic_a1 | self.clinic_b1).ids)])
        self.assertFalse(seen)
        roster = self._as(self.manager).get_roster(self.today, self.today + timedelta(days=6))
        key = '%d|%s' % (self.route_c.id, self.today.isoformat())
        if self.today.weekday() != 6:
            self.assertIn(key, roster['uncovered'])
        self._as(self.manager).assign([self.exec_c.id], [self.route_c.id], self.today, self.today)
        roster = self._as(self.manager).get_roster(self.today, self.today + timedelta(days=6))
        self.assertNotIn(key, roster['uncovered'])
        chips = roster['cells'][key]
        self.assertEqual([(c['user_id'], c['kind']) for c in chips], [(self.exec_c.id, 'regular')])

    # ------------------------------------------------------------ handing over
    def test_an_executive_hands_over_and_the_colleague_accepts_with_the_visits(self):
        tomorrow = self.today + timedelta(days=1)
        planned_today = self._visit(self.exec_a, self.clinic_a1, self.today)
        planned_later = self._visit(self.exec_a, self.clinic_a2, tomorrow + timedelta(days=1))
        on_b = self._visit(self.exec_b, self.clinic_b1, self.today)
        res = self._as(self.exec_a).request_transfer(
            [self.route_a.id], self.exec_c.id, self.today, tomorrow, 'cover', 'wedding')
        duty = self.Duty.browse(res['ids'])
        self.assertEqual(duty.state, 'pending')
        self.assertEqual(duty.origin_user_id, self.exec_a)
        self.assertEqual(duty.requester_id, self.exec_a)
        # the request is not a duty yet: c sees nothing of A
        self.assertNotIn(self.route_a, self.Duty._routes_on(self.exec_c, self.today))
        self.assertEqual(planned_today.user_id, self.exec_a)
        # c answers on My Day
        card = self.Duty.with_user(self.exec_c).get_my_routes()
        self.assertEqual([q['from'] for q in card['requests']], [self.exec_a.name])
        self._as(self.exec_c).browse(duty.id).action_accept()
        self.assertEqual(duty.state, 'active')
        self.assertIn(self.route_a, self.Duty._routes_on(self.exec_c, self.today))
        self.assertEqual(planned_today.user_id, self.exec_c, "the planned visit went with the round")
        self.assertEqual(planned_later.user_id, self.exec_a, "outside the days: untouched")
        self.assertEqual(on_b.user_id, self.exec_b, "another route: untouched")
        self.assertEqual(duty.moved_visit_ids, planned_today)
        # a's card knows, c's card shows the route as covered
        mine = self.Duty.with_user(self.exec_a).get_my_routes()
        self.assertEqual([(o['to'], o['state']) for o in mine['outgoing']], [(self.exec_c.name, 'active')])
        theirs = self.Duty.with_user(self.exec_c).get_my_routes()
        self.assertEqual([(r['name'], r['kind'], r['for']) for r in theirs['rows']],
                         [('RD Route A', 'cover', self.exec_a.name)])
        # the live board names the route for c today
        self.assertIn('RD Route A', self.env['lab.field.live']._route_of(self.exec_c, self.today))
        # c ends it early: the visit comes back
        self._as(self.exec_c).browse(duty.id).action_cancel()
        self.assertEqual(duty.state, 'cancelled')
        self.assertEqual(planned_today.user_id, self.exec_a)
        self.assertFalse(duty.moved_visit_ids)
        self.assertNotIn(self.route_a, self.Duty._routes_on(self.exec_c, self.today))

    def test_an_assist_adds_the_colleague_and_moves_nothing(self):
        planned = self._visit(self.exec_a, self.clinic_a1, self.today)
        res = self._as(self.exec_a).request_transfer(
            [self.route_a.id], self.exec_c.id, self.today, None, 'assist')
        duty = self.Duty.browse(res['ids'])
        self._as(self.exec_c).browse(duty.id).action_accept()
        self.assertIn(self.route_a, self.Duty._routes_on(self.exec_c, self.today))
        self.assertIn(self.route_a, self.Duty._routes_on(self.exec_a, self.today))
        self.assertEqual(planned.user_id, self.exec_a)
        self.assertFalse(duty.moved_visit_ids)

    def test_declining_leaves_everything_as_it_was(self):
        res = self._as(self.exec_a).request_transfer(
            [self.route_a.id], self.exec_c.id, self.today, None, 'cover')
        duty = self.Duty.browse(res['ids'])
        self._as(self.exec_c).browse(duty.id).action_decline()
        self.assertEqual(duty.state, 'declined')
        self.assertNotIn(self.route_a, self.Duty._routes_on(self.exec_c, self.today))
        # a manager may ask again
        self._as(self.manager).browse(duty.id).action_reopen()
        self.assertEqual(duty.state, 'pending')

    def test_only_the_colleague_or_a_manager_answers(self):
        res = self._as(self.exec_a).request_transfer(
            [self.route_a.id], self.exec_c.id, self.today, None, 'cover')
        duty = self.Duty.browse(res['ids'])
        with self.assertRaises(AccessError):
            self._as(self.exec_a).browse(duty.id).action_accept()
        self._as(self.manager).browse(duty.id).action_accept()
        self.assertEqual(duty.state, 'active')
        with self.assertRaises(UserError):
            self._as(self.manager).browse(duty.id).action_accept()   # not pending any more

    # ------------------------------------------------------------ what is refused
    def test_an_executive_cannot_assign_or_hand_over_what_is_not_theirs(self):
        with self.assertRaises(UserError):
            self._as(self.exec_a).create({
                'user_id': self.exec_c.id, 'team_id': self.route_a.id, 'kind': 'regular',
                'date_from': self.today, 'date_to': self.today})
        with self.assertRaises(UserError):     # B is not a's route
            self._as(self.exec_a).request_transfer(
                [self.route_b.id], self.exec_c.id, self.today, None, 'cover')
        with self.assertRaises(UserError):     # asking oneself
            self._as(self.exec_a).request_transfer(
                [self.route_a.id], self.exec_a.id, self.today, None, 'cover')
        res = self._as(self.exec_a).request_transfer(
            [self.route_a.id], self.exec_c.id, self.today, None, 'cover')
        duty = self._as(self.exec_a).browse(res['ids'])
        duty.write({'note': 'reworded'})                 # their own open request: fine
        with self.assertRaises(UserError):
            duty.write({'user_id': self.exec_b.id})      # the rest is the buttons' job
        with self.assertRaises(UserError):
            self._as(self.exec_b).browse(duty.id).write({'note': 'not mine'})
        with self.assertRaises(ValidationError):
            self._as(self.manager).create({
                'user_id': self.exec_c.id, 'team_id': self.route_a.id, 'kind': 'cover',
                'date_from': self.today, 'date_to': self.today})   # cover for whom?
        # the database keeps the dates in order (a CHECK, so last: it fails the cursor)
        with mute_logger('odoo.sql_db'), self.assertRaises(CheckViolation), self.env.cr.savepoint():
            self._as(self.manager).create({
                'user_id': self.exec_c.id, 'team_id': self.route_a.id,
                'date_from': self.today, 'date_to': self.today - timedelta(days=1)})

    def test_a_colleague_reads_only_the_duties_that_concern_them(self):
        res = self._as(self.exec_a).request_transfer(
            [self.route_a.id], self.exec_c.id, self.today, None, 'cover')
        duty = self.Duty.browse(res['ids'])
        self.assertTrue(self._as(self.exec_c).search([('id', '=', duty.id)]))
        self.assertTrue(self._as(self.exec_a).search([('id', '=', duty.id)]))
        self.assertFalse(self._as(self.exec_b).search([('id', '=', duty.id)]))

    # ------------------------------------------------------------ planning and the roster
    def test_the_beat_plans_straight_to_the_cover(self):
        beat = self.env['lab.beat'].create({
            'name': 'RD Beat A', 'user_id': self.exec_a.id,
            'weekday': str(self.today.weekday()),
            'partner_ids': [(6, 0, (self.clinic_a1 | self.clinic_a2).ids)]})
        self._as(self.manager).assign(
            [self.exec_c.id], [self.route_a.id], self.today, self.today, 'cover',
            origin_user_id=self.exec_a.id)
        beat.action_plan()
        visits = self.env['lab.visit'].search([('beat_id', '=', beat.id), ('date', '=', self.today)])
        self.assertEqual(len(visits), 2)
        self.assertEqual(set(visits.mapped('user_id').ids), {self.exec_c.id})

    def test_the_roster_shows_away_heavy_and_requests(self):
        week_end = self.today + timedelta(days=6)
        self._as(self.manager).assign(
            [self.exec_c.id], [self.route_a.id], self.today, self.today, 'cover',
            origin_user_id=self.exec_a.id, note='leave')
        extra = [self.env['crm.team'].create({'name': 'RD Route X%d' % i}).id for i in range(3)]
        self._as(self.manager).assign([self.exec_c.id], extra, self.today, self.today)
        self._as(self.exec_b).request_transfer(
            [self.route_b.id], self.exec_a.id, week_end, None, 'assist')
        roster = self._as(self.manager).get_roster(self.today, week_end)
        key_a = '%d|%s' % (self.route_a.id, self.today.isoformat())
        chips = {c['user_id']: c for c in roster['cells'][key_a]}
        self.assertTrue(chips[self.exec_a.id]['away'])
        self.assertEqual(chips[self.exec_a.id]['covered_by'], self.exec_c.name)
        self.assertEqual(chips[self.exec_c.id]['kind'], 'cover')
        # Scoped to this test's people: the database may carry other duties.
        ours = {self.exec_a.id, self.exec_b.id, self.exec_c.id}
        self.assertEqual([(a['user'], a['covered_by']) for a in roster['away']
                          if a['user_id'] in ours],
                         [(self.exec_a.name, self.exec_c.name)])
        self.assertIn('%d|%s' % (self.exec_c.id, self.today.isoformat()), roster['heavy'])
        self.assertEqual([(q['user'], q['state']) for q in roster['requests']
                          if q['user_id'] in ours],
                         [(self.exec_a.name, 'pending')])
        self.assertGreaterEqual(roster['kpis']['pending'], 1)
        self.assertGreaterEqual(roster['kpis']['away'], 1)
        with self.assertRaises(AccessError):
            self._as(self.exec_a).get_roster(self.today, week_end)

    def test_the_dialog_offers_the_right_people_first(self):
        self._as(self.manager).assign([self.exec_c.id], [self.route_a.id],
                                      self.today - timedelta(days=10), self.today - timedelta(days=9))
        options = self._as(self.exec_a).dialog_options('handover')
        self.assertEqual([r['name'] for r in options['routes']], ['RD Route A'])
        people = {p['id']: p for p in options['people']}
        self.assertNotIn(self.exec_a.id, people)
        self.assertIn(self.route_a.id, people[self.exec_c.id]['knows'], "c worked A before")
        self.assertIn(self.route_b.id, people[self.exec_b.id]['standing'])
        with self.assertRaises(AccessError):
            self._as(self.exec_a).dialog_options('assign')

    def test_moving_a_duty_keeps_its_length(self):
        res = self._as(self.manager).assign(
            [self.exec_c.id], [self.route_a.id], self.today, self.today + timedelta(days=2))
        duty = self.Duty.browse(res['ids'])
        self._as(self.manager).move_duty(duty.id, self.route_c.id, self.today + timedelta(days=7))
        self.assertEqual(duty.team_id, self.route_c)
        self.assertEqual(duty.date_from, self.today + timedelta(days=7))
        self.assertEqual(duty.date_to, self.today + timedelta(days=9))

    def test_the_turn_of_the_day_runs_once(self):
        self.env['ir.config_parameter'].sudo().set_str('lab_fieldwork.duty_cache_day', '')
        self.assertTrue(self.Duty._cron_turn_of_day())
        self.assertFalse(self.Duty._cron_turn_of_day())
