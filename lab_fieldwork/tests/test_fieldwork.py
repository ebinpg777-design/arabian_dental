# -*- coding: utf-8 -*-
from datetime import timedelta

from psycopg2.errors import UniqueViolation as IntegrityError

from unittest.mock import patch

from odoo import fields
from odoo.addons.lab_fieldwork.models import desk as desk_model
from odoo.addons.lab_fieldwork.models.local_day import local_now
from odoo.tools import mute_logger
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged

LAT, LON = 9.98160, 76.57790


@tagged('post_install', '-at_install')
class TestFieldwork(TransactionCase):
    """The rules an ordinary user will hit, and the access model they must not slip."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        G = cls.env.ref
        cls.exec_user = cls.env['res.users'].create({
            'name': 'Field Exec', 'login': 'fw_exec',
            'group_ids': [(6, 0, [G('base.group_user').id,
                                  G('lab_fieldwork.group_fieldwork_executive').id])]})
        cls.other_user = cls.env['res.users'].create({
            'name': 'Other Exec', 'login': 'fw_other',
            'group_ids': [(6, 0, [G('base.group_user').id,
                                  G('lab_fieldwork.group_fieldwork_executive').id])]})
        cls.manager = cls.env['res.users'].create({
            'name': 'Field Manager', 'login': 'fw_mgr',
            'group_ids': [(6, 0, [G('base.group_user').id,
                                  G('lab_fieldwork.group_fieldwork_manager').id])]})
        cls.admin_user = cls.env['res.users'].create({
            'name': 'Field Admin', 'login': 'fw_admin',
            'group_ids': [(6, 0, [G('base.group_user').id,
                                  G('lab_fieldwork.group_fieldwork_admin').id])]})
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Test Clinic', 'is_clinic': True,
            'partner_latitude': LAT, 'partner_longitude': LON,
            'visit_radius_m': 200.0})

    def _visit(self, user=None, **vals):
        return self.env['lab.visit'].create(dict({
            'partner_id': self.clinic.id,
            'user_id': (user or self.exec_user).id,
        }, **vals))

    # ------------------------------------------------------------------ the loop
    def test_the_whole_day_in_five_steps(self):
        """Plan, arrive, record, close — the only sequence a user must learn."""
        v = self._visit()
        self.assertEqual(v.state, 'planned')
        v.do_check_in(LAT + 0.0002, LON)          # ~22 m away
        self.assertEqual(v.state, 'open')
        self.assertEqual(v.gps_state, 'ok')
        v.outcome = 'order'
        v.do_check_out(LAT, LON)
        self.assertEqual(v.state, 'done')
        self.assertTrue(v.minutes >= 0)

    def test_cannot_close_without_saying_what_happened(self):
        v = self._visit()
        v.do_check_in(LAT, LON)
        with self.assertRaises(UserError):
            v.do_check_out(LAT, LON)

    def test_far_check_in_is_recorded_not_refused(self):
        """Blocking the check-in would only mean the visit goes unrecorded, which is
        worse than an explained one."""
        v = self._visit()
        v.do_check_in(LAT + 0.05, LON)            # ~5.5 km
        self.assertEqual(v.state, 'open')
        self.assertEqual(v.gps_state, 'far')
        v.outcome = 'followup'
        with self.assertRaises(UserError):        # …but it must be explained to close
            v.do_check_out(LAT, LON)
        v.gps_reason = 'Doctor met at their other branch'
        v.do_check_out(LAT, LON)
        self.assertEqual(v.state, 'done')

    def test_a_visit_cannot_be_started_without_a_location(self):
        """Reversed on 2026-09-05. This module used to record a locationless
        visit and show it as 'nofix', on the reasoning that a lost visit beats
        an unexplained one. The live data settled it the other way: day sheets
        of thirty visits stamped inside one minute with zero km travelled.
        (client, 2026-09-05)"""
        v = self._visit()
        with self.assertRaises(UserError):
            v.do_check_in(False, False)
        self.assertEqual(v.state, 'planned', "and nothing was half-started")
        self.assertFalse(v.check_in)

    def test_null_island_is_not_a_location(self):
        """A wrapper that answers 0,0 rather than refusing must fail the same
        way a denied permission does, or the check is decoration."""
        v = self._visit()
        with self.assertRaises(UserError):
            v.do_check_in(0.0, 0.0)
        self.assertEqual(v.state, 'planned')

    def test_unpinned_clinic_says_so(self):
        clinic = self.env['res.partner'].create({'name': 'Unpinned', 'is_clinic': True})
        v = self._visit(partner_id=clinic.id)
        v.do_check_in(LAT, LON)
        self.assertEqual(v.gps_state, 'nopin')

    def test_payment_needs_a_mode(self):
        v = self._visit()
        v.do_check_in(LAT, LON)
        v.write({'outcome': 'payment', 'collected': 500.0})
        with self.assertRaises(UserError):
            v.do_check_out(LAT, LON)
        v.pay_mode = 'cash'
        v.do_check_out(LAT, LON)
        self.assertEqual(v.state, 'done')

    # ------------------------------------------------------------------ cases
    def test_a_visit_carries_many_cases(self):
        product = self.env['product.product'].create({'name': 'Aligner', 'list_price': 100.0})
        v = self._visit()
        v.do_check_in(LAT, LON)
        for qty in (1, 2, 3):
            self.env['sale.order'].with_context(default_visit_id=v.id).create({
                'partner_id': self.clinic.id,
                'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': qty})]})
        self.assertEqual(v.order_count, 3)
        self.assertEqual(v.order_value, 600.0)
        self.assertEqual(v.outcome, 'order', "taking a case sets the outcome for you")

    # ------------------------------------------------------------------ locking
    def test_a_closed_visit_is_read_only_in_the_server(self):
        v = self._visit()
        v.do_check_in(LAT, LON)
        v.outcome = 'absent'
        v.do_check_out(LAT, LON)
        with self.assertRaises(UserError):
            v.with_user(self.exec_user).write({'purpose': 'payment'})
        # the note stays open so a supervisor can still record what emerged later
        v.with_user(self.exec_user).write({'note': 'doctor called back'})
        self.assertEqual(v.note, 'doctor called back')

    # ------------------------------------------------------------------ beats
    def test_planning_a_beat_creates_the_round(self):
        beat = self.env['lab.beat'].create({
            'name': 'Test Beat', 'weekday': str(fields.Date.context_today(self).weekday()),
            'user_id': self.exec_user.id, 'partner_ids': [(6, 0, self.clinic.ids)]})
        beat.action_plan()
        visits = self.env['lab.visit'].search([('beat_id', '=', beat.id)])
        self.assertEqual(len(visits), 1)
        self.assertEqual(visits.user_id, self.exec_user)

    def test_planning_twice_does_not_duplicate_the_round(self):
        """A manager unsure whether they already planned will press it again."""
        beat = self.env['lab.beat'].create({
            'name': 'Test Beat 2', 'weekday': str(fields.Date.context_today(self).weekday()),
            'user_id': self.exec_user.id, 'partner_ids': [(6, 0, self.clinic.ids)]})
        beat.action_plan()
        beat.action_plan()
        self.assertEqual(
            self.env['lab.visit'].search_count([('beat_id', '=', beat.id)]), 1)

    def test_a_beat_with_no_clinics_says_so(self):
        beat = self.env['lab.beat'].create({
            'name': 'Empty', 'weekday': '0', 'user_id': self.exec_user.id})
        with self.assertRaises(UserError):
            beat.action_plan()

    # ------------------------------------------------------------------ trips
    def test_trip_computes_distance_and_claim(self):
        trip = self.env['lab.trip'].create({
            'user_id': self.exec_user.id, 'odo_start': 1000.0, 'odo_end': 1042.0,
            'vehicle': 'bike', 'rate': 5.0})
        self.assertEqual(trip.distance, 42.0)
        self.assertEqual(trip.amount, 210.0)

    def test_closing_reading_below_opening_is_refused(self):
        with self.assertRaises(ValidationError):
            self.env['lab.trip'].create({
                'user_id': self.exec_user.id, 'odo_start': 1000.0, 'odo_end': 900.0})

    @mute_logger('odoo.sql_db')
    def test_only_one_trip_per_person_per_day(self):
        """One record per person per day removes a whole class of confusion: there is
        never a question of which trip today's kilometres belong to."""
        day = fields.Date.context_today(self)
        self.env['lab.trip'].create({'user_id': self.exec_user.id, 'date': day})
        with self.assertRaises(IntegrityError):
            self.env['lab.trip'].create({'user_id': self.exec_user.id, 'date': day})
            self.env.flush_all()

    def test_an_executive_cannot_undo_a_signed_claim(self):
        """Reset had no check: an approved claim went back to draft, took a new
        odometer and was resubmitted with the old approver's name still on it.
        (2026-09-15)"""
        trip = self.env['lab.trip'].create({
            'user_id': self.exec_user.id, 'odo_start': 1000.0, 'odo_end': 1042.0,
            'state': 'closed'})
        trip.with_user(self.manager).action_approve()
        self.assertEqual(trip.approved_by, self.manager)
        with self.assertRaises(UserError):
            trip.with_user(self.exec_user).action_reset()
        with self.assertRaises(UserError):
            trip.with_user(self.exec_user).action_cancel()
        self.assertEqual(trip.state, 'approved')
        trip.with_user(self.manager).action_reset()
        self.assertEqual(trip.state, 'draft')
        self.assertFalse(trip.approved_by, "a claim back in draft is unsigned")

    # ------------------------------------------------------------------ targets
    def test_achievement_is_measured_not_typed(self):
        product = self.env['product.product'].create({'name': 'Plate', 'list_price': 50.0})
        target = self.env['lab.target'].create({
            'user_id': self.exec_user.id, 'goal_value': 100.0, 'state': 'open'})
        v = self._visit()
        v.do_check_in(LAT, LON)
        self.env['sale.order'].with_context(default_visit_id=v.id).create({
            'partner_id': self.clinic.id,
            'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': 1})]})
        v.do_check_out(LAT, LON)
        # Stored figures follow the visits when refreshed - which the screens
        # do before reading them, and a cron does hourly. (2026-09-15)
        target._refresh_done()
        self.assertEqual(target.done_value, 50.0)
        self.assertEqual(target.progress, 50.0)

    def test_the_hourly_job_brings_running_targets_up_to_date(self):
        today = local_now(self.env).date()
        target = self.env['lab.target'].create({
            'user_id': self.exec_user.id, 'goal_value': 100.0, 'state': 'open',
            'month': today.replace(day=1)})
        self.assertEqual(target.done_visits, 0)
        v = self._visit(date=today)
        v.do_check_in(LAT, LON)
        v.outcome = 'followup'
        v.do_check_out(LAT, LON)
        self.env['lab.target']._cron_refresh_done()
        self.assertEqual(target.done_visits, 1,
                         "the visit reached the target without anyone editing it")

    # ------------------------------------------------------------------ access
    def test_an_executive_sees_only_their_own_visits(self):
        mine = self._visit(user=self.exec_user)
        theirs = self._visit(user=self.other_user)
        seen = self.env['lab.visit'].with_user(self.exec_user).search([])
        self.assertIn(mine, seen)
        self.assertNotIn(theirs, seen, "an executive must not see a colleague's round")

    def test_a_manager_sees_the_team(self):
        mine = self._visit(user=self.exec_user)
        theirs = self._visit(user=self.other_user)
        seen = self.env['lab.visit'].with_user(self.manager).search([])
        self.assertIn(mine, seen)
        self.assertIn(theirs, seen)

    def test_an_executive_cannot_change_their_own_target(self):
        """A target you can edit is not a target."""
        target = self.env['lab.target'].create({
            'user_id': self.exec_user.id, 'goal_value': 100.0})
        with self.assertRaises(AccessError):
            target.with_user(self.exec_user).write({'goal_value': 1.0})

    def test_an_executive_cannot_write_a_beat(self):
        beat = self.env['lab.beat'].create({
            'name': 'Read Only Beat', 'weekday': '0', 'user_id': self.exec_user.id})
        with self.assertRaises(AccessError):
            beat.with_user(self.exec_user).write({'name': 'changed'})

    def test_an_executive_cannot_delete_a_visit(self):
        """Records are cancelled, never removed: a deleted visit is an unexplained gap
        in someone's day."""
        v = self._visit(user=self.exec_user)
        with self.assertRaises(AccessError):
            v.with_user(self.exec_user).unlink()

    # ------------------------------------------------------------------ my day
    def test_my_day_returns_the_whole_screen_in_one_call(self):
        self._visit(user=self.exec_user)
        data = self.env['lab.my.day'].with_user(self.exec_user).get_day()
        # Everything the base screen draws must be in the one call. Downstream modules
        # are allowed to add their own sections to it — lab_delivery contributes the
        # bag of deliveries — so this pins what may never go missing rather than
        # forbidding what may be added.
        self.assertLessEqual({'date', 'date_label', 'greeting', 'user_name',
                              'currency_id', 'summary', 'cash', 'trip',
                              'target', 'visits', 'suggestions', 'attendance'},
                             set(data))
        # The same rule for the summary: lab_delivery adds the dispatches
        # delivered, so this pins what must be there, not what may join it.
        self.assertLessEqual({'planned', 'done', 'cases', 'value', 'collected',
                              'cases_collected', 'reworks_collected'},
                             set(data['summary']))
        self.assertGreaterEqual(len(data['visits']), 1)

    def test_my_day_is_scoped_by_the_record_rules(self):
        """No user filter in the query — the rules do it, so the same call is right for
        a manager standing in for someone."""
        self._visit(user=self.other_user)
        data = self.env['lab.my.day'].with_user(self.exec_user).get_day()
        self.assertFalse([v for v in data['visits']
                          if v['clinic'] and v['id'] and v['state'] == 'planned'
                          and v['id'] in self.env['lab.visit']
                          .with_user(self.other_user).search([]).ids])

    # ------------------------------------------------------------------ menus
    def _menu_names(self, user):
        """What the web client actually renders. ir.ui.menu.search() does NOT apply the
        group filter, so a test written against search() passes while the real menu tree
        leaks — load_menus is the only honest check."""
        root = self.env.ref('lab_fieldwork.menu_fieldwork_root')
        menus = self.env['ir.ui.menu'].with_user(user).load_menus(False)
        names = []

        def walk(mid):
            entry = menus.get(mid)
            if not entry:
                return
            for child in entry.get('children', []):
                child_entry = menus.get(child)
                if child_entry:
                    names.append(child_entry['name'])
                    walk(child)
        walk(root.id)
        return names

    def test_an_executive_sees_only_their_own_flat_menu(self):
        """The whole point of the module: every entry is one tap, no submenu on a
        screen used at a clinic door.

        The list grew from six to eight when deliveries and incentives stopped being
        applications of their own and moved in here — which is the right home for
        them, since the same person carrying the box on the same round is the one
        reading this menu. What must NOT change is the shape: flat, and all "My …".
        """
        names = self._menu_names(self.exec_user)
        # Membership, not equality: other modules hang their own "My ..." items
        # here (collections, deliveries, incentives), and this test cannot know
        # them - asserting THEIR names here made this module's own suite fail on
        # any database without them installed. What it owns is that ITS entries
        # are there and the shape holds.
        for expected in ('My Day', 'My Day Sheets', 'My Visits', 'My Cases',
                         'My Travel', 'My Cash', 'My Target', 'My New Clinics'):
            self.assertIn(expected, names)
        self.assertTrue(all(n.startswith('My ') for n in names),
                        "an executive's menu is only ever their own work")

    def test_a_manager_sees_the_team_grouped(self):
        """The SHARED half of the two manager desks (2026-08-31): what both the
        Operational and the Marketing Manager see through the base group. The
        desk-specific queues (Daily Updates / Travel vs Activities / New
        Clinics) are asserted per role in test_daily_flow."""
        names = self._menu_names(self.manager)
        for expected in ('Approvals', 'All Day Sheets',
                         'Operations', 'Visits', 'Case Slips',
                         'Planning', 'Clinics',
                         'Analysis', 'Clinic Coverage'):
            self.assertIn(expected, names)
        self.assertNotIn('Configuration', names,
                         "policy belongs to the administrator, not the manager it binds")
        # The desk screens are role-owned, never shared: the base group alone
        # (a legacy state no picker offers) opens neither. (2026-08-31)
        self.assertNotIn('Ops Desk', names)
        self.assertNotIn('Marketing Desk', names)

    def test_a_manager_does_not_get_the_executives_own_work_menus(self):
        """The roles are parallel, not stacked.

        This is the one the XML alone cannot deliver: dropping `implied_ids` from the
        data file does not unlink what is already stored, so without the migration the
        security file reads as though the roles are separate while every manager still
        sees My Day. Assert on the rendered menu, which is what the user actually gets.
        """
        names = self._menu_names(self.manager)
        for own_work in ('My Day', 'My Visits', 'My Cases', 'My Travel', 'My Cash',
                         'My Target'):
            self.assertNotIn(own_work, names,
                             "a manager does not work a beat")
        self.assertFalse(
            self.manager.has_group('lab_fieldwork.group_fieldwork_executive'),
            "Manager must not imply Executive")

    def test_someone_with_no_field_role_sees_no_app(self):
        outsider = self.env['res.users'].create({
            'name': 'Outsider', 'login': 'fw_outsider',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('base.group_system').id])]})
        self.assertEqual(self._menu_names(outsider), [],
                         "holding Settings must not open the field force")

    # ------------------------------------------------------- view expressions
    def test_no_view_domain_uses_something_py_js_cannot_evaluate(self):
        """View domains are evaluated in the BROWSER by py_js, not by Python.

        py_js implements `strftime` on its date objects but not `replace`, so
        `context_today().replace(day=1)` raises "Function.prototype.apply was called on
        undefined" at render time — after install, after view validation and after the
        whole server test suite have all passed. The only way to catch it server-side is
        to look for the constructs it cannot evaluate.
        """
        unsupported = ('.replace(', '.combine(', '.fromordinal(', '.isocalendar(')
        views = self.env['ir.ui.view'].search([
            ('model', 'in', ['lab.visit', 'lab.trip', 'lab.beat', 'lab.target']),
        ])
        offenders = []
        for view in views:
            arch = view.arch_db or ''
            for token in unsupported:
                # only inside evaluated attributes; Python data files may use them freely
                for attr in ('domain="', 'context="', 'invisible="', 'readonly="',
                             'required="'):
                    idx = 0
                    while True:
                        start = arch.find(attr, idx)
                        if start == -1:
                            break
                        end = arch.find('"', start + len(attr))
                        if token in arch[start:end]:
                            offenders.append((view.name, token))
                        idx = end
        self.assertFalse(offenders, "py_js cannot evaluate these: %s" % offenders)

    # ------------------------------------------------- cash into the float
    def _float_for(self, user, amount=5000.0):
        journal = self.env['account.journal'].search(
            [('type', '=', 'cash'), ('company_id', '=', self.env.company.id)], limit=1)
        if not journal and self.env['account.account'].sudo().search_count(
                [('company_ids', 'in', self.env.company.id)]):
            # Every cash journal on the staging database is ARCHIVED, so the
            # search above finds none and this used to skip - taking ten cash
            # tests with it, silently, while reporting green. The accounts are
            # there (465 of them), so a journal can simply be made.
            # (client, 2026-09-12)
            journal = self.env['account.journal'].sudo().create({
                'name': 'Test Cash', 'type': 'cash', 'code': 'TSTCS',
                'company_id': self.env.company.id})
        if not journal:
            # A bare database with no chart of accounts at all: the whole float
            # mechanism sits on accounting and none of it is this module's to
            # assert in that state. (2026-08-31)
            self.skipTest('no chart of accounts on this database')
        mgr = self.env.ref('petty_cash.group_petty_cash_manager')
        if not self.env.user.has_group('petty_cash.group_petty_cash_manager'):
            self.env.user.sudo().write({'group_ids': [(4, mgr.id)]})
        alloc = self.env['petty.cash.allocation'].create({
            'partner_id': user.partner_id.id, 'journal_id': journal.id,
            'amount_limit': amount})
        alloc.action_submit_request()
        alloc.action_allocate()
        for tx in alloc.transaction_ids.filtered(lambda t: t.state != 'posted'):
            # petty_cash refuses to post an allocation without the account the
            # money comes FROM (the fixture predates that guard): the cash
            # journal's own account is exactly what a real allocation names.
            if tx.type in ('allocation', 'return') and not tx.counter_account_id:
                tx.counter_account_id = journal.default_account_id
            if tx.state == 'draft':
                tx.action_submit()
            if tx.state == 'submitted':
                tx.action_approve()
            if tx.state == 'approved':
                tx.action_post()
        return alloc

    def _cash_visit(self, amount=1500.0, mode='cash'):
        v = self._visit(user=self.exec_user)
        v.do_check_in(LAT, LON)
        v.write({'outcome': 'payment', 'collected': amount, 'pay_mode': mode})
        v.do_check_out(LAT, LON)
        return v

    def test_cash_collected_lands_in_the_executives_float(self):
        """The accountant needs to see where the money is: it is with this person."""
        alloc = self._float_for(self.exec_user)
        self.exec_user.invalidate_recordset()
        opening = alloc.amount_balance
        v = self._cash_visit(1500.0)
        v.action_cash_to_float()
        alloc.invalidate_recordset()
        self.assertTrue(v.cash_banked)
        self.assertEqual(alloc.amount_balance, opening + 1500.0)
        self.assertEqual(alloc.amount_collected, 1500.0)
        self.assertEqual(v.cash_txn_id.visit_id, v, "the movement points back at the visit")

    # ------------------------------------------- cash with no visit behind it
    def _collect(self, amount=800.0, user=None, partner=None, **vals):
        """The Cash In screen, filled in and submitted."""
        who = user or self.exec_user
        # Recorded BY that person, as the screen is: the wizard refuses to bank
        # cash into somebody else's accountability, so a fixture running as the
        # test user would only ever exercise that guard.
        wiz = self.env['lab.collect.cash'].with_user(who).create(dict({
            'user_id': who.id,
            'partner_id': (partner or self.clinic).id,
            'amount': amount,
        }, **vals))
        return wiz.action_record(), wiz

    def test_cash_can_be_recorded_without_inventing_a_visit(self):
        """Money arrives ahead of the paperwork: a doctor settles an old bill
        while the executive is there for something else. Recording it used to
        mean creating a visit that never happened - a false call in the day
        sheet to get a true number into the cash box. (client, 2026-09-12)"""
        alloc = self._float_for(self.exec_user)
        self.exec_user.invalidate_recordset()
        opening = alloc.amount_balance
        before = self.env['lab.visit'].search_count([])
        self._collect(800.0)
        alloc.invalidate_recordset()
        self.assertEqual(alloc.amount_balance, opening + 800.0)
        self.assertEqual(alloc.amount_collected, 800.0)
        self.assertEqual(self.env['lab.visit'].search_count([]), before,
                         "and no visit was invented to carry it")

    def test_the_money_counts_in_the_day_the_way_a_visits_payment_does(self):
        """The point of the feature: it is the same money, so it belongs in
        the same figures - the day's total, and the collection target the
        executive is judged on. (client, 2026-09-12)"""
        today = fields.Date.context_today(self.env.user)
        day = self.env['lab.my.day'].with_user(self.exec_user).get_day()
        before = day['summary']['collected']
        self._collect(750.0)
        day = self.env['lab.my.day'].with_user(self.exec_user).get_day()
        self.assertEqual(day['summary']['collected'], before + 750.0)
        target = self.env['lab.target'].create({
            'user_id': self.exec_user.id, 'month': today.replace(day=1)})
        self.assertEqual(target.done_collect, 750.0,
                         "and towards the target they are judged on")

    def test_the_movement_is_a_field_collection_with_no_visit(self):
        alloc = self._float_for(self.exec_user)
        self.exec_user.invalidate_recordset()
        self._collect(800.0)
        txn = alloc.transaction_ids.filtered(lambda t: t.type == 'collection')
        self.assertEqual(len(txn), 1)
        self.assertFalse(txn.visit_id, "there was no visit, and it does not pretend")
        self.assertEqual(txn.state, 'posted', "the money is in, not waiting to be")
        self.assertEqual(txn.partner_id, self.clinic)

    def test_it_counts_in_the_float_the_same_way_a_visits_cash_does(self):
        """The accountant sees one kind of collection, however it was entered."""
        alloc = self._float_for(self.exec_user)
        self.exec_user.invalidate_recordset()
        self._cash_visit(1500.0).action_cash_to_float()
        self._collect(500.0)
        alloc.invalidate_recordset()
        self.assertEqual(alloc.amount_collected, 2000.0)
        self.assertEqual(alloc.collection_count, 2)

    def test_cash_is_still_recorded_for_somebody_with_no_float(self):
        """Nobody on this database has a petty cash float - 0 allocations -
        so refusing to record the money until accounts allocates one would
        leave the day's figure wrong to protect a cash box nobody keeps. The
        money is recorded; only the float movement waits. (client, 2026-09-12)

        Since 2026-09-18 a float opens itself on first use; this is the lab
        that has switched that off and left floats to accounts.
        """
        self.env['ir.config_parameter'].sudo().set_param(
            'lab_fieldwork.auto_float', 'False')
        other = self.env['res.users'].create(
            {'name': 'Floatless', 'login': 'no_float_exec',
             'group_ids': [(4, self.env.ref('base.group_user').id),
                           (4, self.env.ref(
                               'lab_fieldwork.group_fieldwork_executive').id)]})
        self._collect(800.0, user=other)
        rows = self.env['lab.cash.collection'].sudo().search(
            [('user_id', '=', other.id)])
        self.assertEqual(len(rows), 1, "the money is on the record")
        self.assertEqual(rows.amount, 800.0)
        self.assertFalse(rows.cash_banked, "and waiting for a float to exist")

    def test_nothing_is_recorded_for_nothing(self):
        self._float_for(self.exec_user)
        self.exec_user.invalidate_recordset()
        with self.assertRaises(UserError):
            self._collect(0.0)

    def test_an_executive_cannot_bank_cash_in_somebody_elses_name(self):
        """Recording a collection moves money into a person's accountability,
        so it may only be moved into your own."""
        mate = self.env['res.users'].create(
            {'name': 'Other Exec', 'login': 'other_exec',
             'group_ids': [(4, self.env.ref('base.group_user').id),
                           (4, self.env.ref(
                               'lab_fieldwork.group_fieldwork_executive').id)]})
        self._float_for(mate)
        mate.invalidate_recordset()
        wiz = self.env['lab.collect.cash'].with_user(self.exec_user).create({
            'user_id': mate.id, 'partner_id': self.clinic.id, 'amount': 800.0})
        with self.assertRaises(UserError):
            wiz.action_record()

    def test_collected_cash_counts_as_allocated_to_the_holder(self):
        alloc = self._float_for(self.exec_user)
        self.exec_user.invalidate_recordset()
        advanced = alloc.amount_allocated
        self._cash_visit(1500.0).action_cash_to_float()
        alloc.invalidate_recordset()
        self.assertEqual(alloc.amount_allocated, advanced + 1500.0)
        self.assertEqual(
            alloc.amount_balance,
            alloc.amount_allocated - alloc.amount_utilized - alloc.amount_returned)

    def test_a_cheque_does_not_go_into_the_float(self):
        """It reaches the bank directly; booking it here would count it twice."""
        self._float_for(self.exec_user)
        self.exec_user.invalidate_recordset()
        v = self._cash_visit(1500.0, mode='cheque')
        with self.assertRaises(UserError):
            v.action_cash_to_float()

    def test_banking_the_same_cash_twice_does_nothing(self):
        alloc = self._float_for(self.exec_user)
        self.exec_user.invalidate_recordset()
        v = self._cash_visit(1500.0)
        v.action_cash_to_float()
        first = v.cash_txn_id
        v.action_cash_to_float()
        alloc.invalidate_recordset()
        self.assertEqual(v.cash_txn_id, first)
        self.assertEqual(alloc.amount_collected, 1500.0)

    def test_cash_in_the_float_cannot_be_rewritten_by_the_executive(self):
        """Once the money is in the float, the collection is what the movement
        was raised from; changing it afterwards made the two disagree.
        (2026-09-15)"""
        self._float_for(self.exec_user)
        self.exec_user.invalidate_recordset()
        self._collect(800.0)
        collection = self.env['lab.cash.collection'].search(
            [('user_id', '=', self.exec_user.id)], order='id desc', limit=1)
        self.assertTrue(collection.cash_banked)
        elsewhere = self.env['res.partner'].create({'name': 'Elsewhere Clinic'})
        for vals in ({'amount': 80.0}, {'pay_mode': 'online'},
                     {'date': collection.date - timedelta(days=3)},
                     {'partner_id': elsewhere.id}):
            with self.assertRaises(UserError):
                collection.with_user(self.exec_user).write(vals)
        self.assertEqual(collection.amount, 800.0)
        collection.with_user(self.exec_user).write({'note': 'The March bill'})
        self.assertEqual(collection.note, 'The March bill')

    # ------------------------------------------------------------ performance
    def test_performance_row_combines_effort_output_and_discipline(self):
        product = self.env['product.product'].create({'name': 'Perf', 'list_price': 100.0})
        v = self._visit(user=self.exec_user)
        v.do_check_in(LAT + 0.0002, LON)
        self.env['sale.order'].with_context(default_visit_id=v.id).create({
            'partner_id': self.clinic.id,
            'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': 2})]})
        v.do_check_out(LAT, LON)
        self.env['lab.performance'].flush_model()
        rows = self.env['lab.performance'].search([('user_id', '=', self.exec_user.id)])
        self.assertTrue(rows)
        self.assertEqual(sum(rows.mapped('done')), 1)
        self.assertEqual(sum(rows.mapped('cases')), 1)
        self.assertEqual(sum(rows.mapped('case_value')), 200.0)
        self.assertEqual(max(rows.mapped('location_ok')), 100.0)

    def test_performance_location_ratio_ignores_visits_with_no_fix(self):
        for coords in ((LAT, LON), (LAT + 0.05, LON)):
            v = self._visit(user=self.other_user)
            v.do_check_in(*coords)
            v.outcome = 'absent'
            if v.gps_state == 'far':
                v.gps_reason = 'elsewhere'
            v.do_check_out(LAT, LON)
        # A locationless visit can no longer be CREATED (2026-09-05: the check-in
        # refuses without a fix), but thousands recorded before that rule are on
        # file, and the end-of-day job still closes forgotten ones without a
        # position. The report has to keep counting them, so the row is built the
        # way those arise rather than through a path that is now closed.
        legacy = self._visit(user=self.other_user)
        legacy.do_check_in(LAT, LON)
        legacy.outcome = 'absent'
        legacy.do_check_out(LAT, LON)
        legacy.sudo().write({'gps_lat': 0.0, 'gps_lon': 0.0})
        self.assertEqual(legacy.gps_state, 'nofix')
        rows = self.env['lab.performance'].search([('user_id', '=', self.other_user.id)])
        self.assertEqual(sum(rows.mapped('at_clinic')), 1)
        self.assertEqual(sum(rows.mapped('away')), 1)
        self.assertEqual(sum(rows.mapped('no_fix')), 1)
        self.assertAlmostEqual(max(rows.mapped('location_ok')), 50.0, places=1)

    # ------------------------------------------------------------- coverage
    def test_coverage_bands_follow_days_since_the_last_visit(self):
        self.env['lab.coverage'].flush_model()
        row = self.env['lab.coverage'].search([('partner_id', '=', self.clinic.id)])
        self.assertEqual(len(row), 1)
        self.assertIn(row.status, ('never', 'current', 'due', 'overdue', 'at_risk'))

    def test_coverage_is_clinics_not_the_address_book(self):
        """`is_clinic` had no writer but three default contexts, so it was TRUE for 0
        partners out of 7,278 and every clinic screen was either empty or counting
        vendors and staff. (client, 2026-08-29)"""
        vendor = self.env['res.partner'].create({'name': 'Wire Supplier Ltd'})
        self.env['lab.coverage'].flush_model()
        rows = self.env['lab.coverage'].search([('partner_id', 'in', (
            self.clinic.id, vendor.id))])
        self.assertIn(self.clinic, rows.mapped('partner_id'))
        self.assertNotIn(vendor, rows.mapped('partner_id'),
                         "a supplier is not a clinic and must not be on a coverage report")

    def test_confirming_an_order_says_the_customer_is_a_clinic(self):
        """The one moment the answer is certain: the lab has taken work from them."""
        walk_in = self.env['res.partner'].create({'name': 'Dr New Practice'})
        self.assertFalse(walk_in.is_clinic)
        order = self.env['sale.order'].create({
            'partner_id': walk_in.id,
            'order_line': [(0, 0, {'product_id': self._product().id, 'product_uom_qty': 1})],
        })
        order.action_confirm()
        walk_in.invalidate_recordset()
        self.assertTrue(walk_in.is_clinic)

    def test_a_clinic_that_stopped_ordering_is_named_as_such(self):
        """The manager's rescue alert asked for a VISIT status and matched nothing for
        ever, while clinics stopped ringing. Ordering is the evidence this lab has."""
        Order = self.env['sale.order']
        old = Order.create({
            'partner_id': self.clinic.id,
            'order_line': [(0, 0, {'product_id': self._product().id,
                                   'product_uom_qty': 1})],
        })
        old.action_confirm()
        # dated into the previous quarter, with nothing since
        old.write({'date_order': fields.Datetime.now() - timedelta(days=120)})
        self.env['lab.coverage'].flush_model()
        row = self.env['lab.coverage'].search([('partner_id', '=', self.clinic.id)])
        self.assertEqual(row.order_state, 'stopped')
        self.assertGreater(row.value_drop, 0,
                           "the alert leads with the money, so it has to be carried")

    def test_cash_cannot_cross_companies(self):
        """Said plainly, rather than letting the ORM raise its generic crossover error
        naming a 'Draft Payment' the user never asked for."""
        self._float_for(self.exec_user)
        self.exec_user.invalidate_recordset()
        other_co = self.env['res.company'].create({'name': 'Other Co'})
        v = self._visit(user=self.exec_user)
        v.do_check_in(LAT, LON)
        v.write({'outcome': 'payment', 'collected': 1000.0, 'pay_mode': 'cash'})
        v.sudo().write({'company_id': other_co.id})
        # Check-out tries by itself (2026-09-18), cannot, and says why on the
        # visit instead of refusing to close it.
        v.do_check_out(LAT, LON)
        self.assertFalse(v.cash_banked)
        self.assertTrue(any('between companies' in (m.body or '')
                            for m in v.message_ids), "said plainly, on the visit")
        with self.assertRaises(UserError):
            v.action_cash_to_float()

    # ------------------------------------------------------- control tower
    def test_an_unpinned_clinic_is_counted_not_swallowed(self):
        """A day where GPS could not be checked must not report a clean zero.

        Every clinic in the live database is unpinned, so every visit computes
        gps_state 'nopin'. That value belonged to neither count, so the sheet said
        "0 away from clinic" for a day where nothing whatsoever had been verified,
        and the manager read the zero as a pass.
        """
        self.clinic.sudo().write({'partner_latitude': 0.0, 'partner_longitude': 0.0})
        v = self._visit(user=self.exec_user)
        v.do_check_in(LAT, LON)
        v.write({'outcome': 'followup'})
        v.do_check_out(LAT, LON)
        self.assertEqual(v.gps_state, 'nopin',
                         "an unpinned clinic cannot be judged either way")

        # The sheet is raised by the day's own flow, not by a visit, so build it
        # here and let it read the day back.
        sheet = self.env['lab.daily.update'].sudo().create({
            'user_id': self.exec_user.id, 'date': v.date})
        sheet._collect_facts()
        sheet.invalidate_recordset()

        self.assertEqual(sheet.gps_far_count, 0,
                         "not-checked is not the same as away-from-clinic")
        self.assertEqual(sheet.gps_nopin_count, 1,
                         "the unchecked visit must be counted somewhere")
        self.assertIn('not pinned', (sheet.anomaly_note or '').lower(),
                      "and said in words, so a zero is never read as a pass")

    def test_status_tells_the_four_quiet_mornings_apart(self):
        """No visits booked is not one situation, it is four.

        On duty with nothing planned, finished for the day, never linked to an
        employee, and genuinely off all used to render "off today" — so the board
        could not be acted on. Each must now say which it is.
        """
        Desk = self.env['lab.desk'].with_user(self.manager)

        def status_of(user):
            rows = Desk.get_ops_desk()['rows']
            row = next((r for r in rows if r['id'] == user.id), None)
            return row and row['status']

        # The fixture user carries no employee record, so make one: this test is
        # about what the desk says once HR has done its part.
        emp = self.env['hr.employee'].sudo().search(
            [('user_id', '=', self.exec_user.id)], limit=1)
        if not emp:
            emp = self.env['hr.employee'].sudo().create({
                'name': self.exec_user.name or 'Field Executive',
                'user_id': self.exec_user.id,
                'company_id': self.exec_user.company_id.id,
            })

        # 1. no visits, never started -> genuinely off
        if emp.attendance_state == 'checked_in':
            emp._attendance_action_change()
        self.assertEqual(status_of(self.exec_user), 'off')

        # 2. no visits, but the day HAS been started -> on duty, not "off today"
        emp._attendance_action_change()
        self.assertEqual(emp.attendance_state, 'checked_in')
        self.assertEqual(status_of(self.exec_user), 'on_duty',
                         "an executive standing on duty must never read as off")

        # 3. started and ended, still nothing booked -> the day is finished
        emp._attendance_action_change()
        self.assertEqual(emp.attendance_state, 'checked_out')
        self.assertEqual(status_of(self.exec_user), 'finished')

        # 4. no employee record at all -> a fault for HR, not a day off
        emp.write({'user_id': False})
        self.exec_user.invalidate_recordset()
        self.assertEqual(status_of(self.exec_user), 'no_setup',
                         "somebody who cannot start must not be filed under 'off'")

    def test_old_phone_clients_may_still_ask_for_mobile(self):
        """The app fires res.users.search_read with 'mobile' on every login.

        Odoo 19 dropped the field; the packaged phone client cannot be patched. The
        exact call the app makes must come back answered, not refused.
        """
        rows = self.env['res.users'].search_read(
            [('id', '=', self.exec_user.id)], ['name', 'mobile'])
        self.assertEqual(len(rows), 1)
        self.assertIn('mobile', rows[0])
        self.exec_user.partner_id.sudo().phone = '+91 90000 00000'
        self.assertEqual(self.exec_user.mobile, '+91 90000 00000',
                         "the compat field answers with the number Odoo 19 keeps")

    def test_finishing_your_calls_is_not_ending_your_day(self):
        """The two the board got wrong on the live floor.

        ASLAM had made his one booked call and was still clocked in; the desk said
        "day finished". Johnson had started his day and not yet reached a clinic;
        the desk said "not started". Both read the visit list and ignored the fact
        that the man was on duty.
        """
        Desk = self.env['lab.desk'].with_user(self.manager)

        def status_of(user):
            rows = Desk.get_ops_desk()['rows']
            row = next((r for r in rows if r['id'] == user.id), None)
            return row and row['status']

        emp = self.env['hr.employee'].sudo().search(
            [('user_id', '=', self.exec_user.id)], limit=1)
        if not emp:
            emp = self.env['hr.employee'].sudo().create({
                'name': self.exec_user.name or 'Field Executive',
                'user_id': self.exec_user.id,
                'company_id': self.exec_user.company_id.id,
            })
        if emp.attendance_state != 'checked_in':
            emp._attendance_action_change()

        # Johnson's case: on duty, calls booked, none reached yet.
        v = self._visit(user=self.exec_user)
        self.assertEqual(status_of(self.exec_user), 'on_duty',
                         "a day that has been started is not 'not started'")

        # ASLAM's case: every booked call made, still clocked in.
        v.do_check_in(LAT, LON)
        self.assertEqual(status_of(self.exec_user), 'at_clinic')
        v.write({'outcome': 'followup'})
        v.do_check_out(LAT, LON)
        self.assertEqual(v.state, 'done')
        self.assertEqual(status_of(self.exec_user), 'visits_done',
                         "calls finished while still on duty is not a finished day")

        # Only clocking out ends the day.
        emp._attendance_action_change()
        self.assertEqual(emp.attendance_state, 'checked_out')
        self.assertEqual(status_of(self.exec_user), 'finished')

    def test_control_tower_derives_a_status_nobody_types(self):
        """Every field on the tower is derived. The moment a person can type their own
        status, the screen stops being a measurement."""
        # An executive HR never set up reads 'no_setup' whatever the visit list
        # says, so this test - which is about the visit-derived states - first
        # gives its executive the employee record field work requires.
        if not self.env['hr.employee'].sudo().search(
                [('user_id', '=', self.exec_user.id)], limit=1):
            self.env['hr.employee'].sudo().create({
                'name': self.exec_user.name or 'Field Executive',
                'user_id': self.exec_user.id,
                'company_id': self.exec_user.company_id.id,
            })
        v = self._visit(user=self.exec_user)
        tower = self.env['lab.desk'].with_user(self.manager).get_ops_desk()
        row = next(r for r in tower['rows'] if r['id'] == self.exec_user.id)
        self.assertEqual(row['status'], 'idle', "planned but not started")

        v.do_check_in(LAT, LON)
        row = next(r for r in self.env['lab.desk']
                   .with_user(self.manager).get_ops_desk()['rows']
                   if r['id'] == self.exec_user.id)
        self.assertEqual(row['status'], 'at_clinic')
        self.assertEqual(row['at_clinic'], self.clinic.display_name)

        v.outcome = 'order'
        v.do_check_out(LAT, LON)
        row = next(r for r in self.env['lab.desk']
                   .with_user(self.manager).get_ops_desk()['rows']
                   if r['id'] == self.exec_user.id)
        self.assertEqual(row['status'], 'finished')
        self.assertEqual(row['done'], 1)

    def test_control_tower_names_cash_that_is_only_in_a_pocket(self):
        """The number a manager most needs and no report shows."""
        v = self._visit(user=self.exec_user)
        v.do_check_in(LAT, LON)
        v.write({'outcome': 'payment', 'collected': 750.0, 'pay_mode': 'cash'})
        v.do_check_out(LAT, LON)
        tower = self.env['lab.desk'].with_user(self.manager).get_ops_desk()
        row = next(r for r in tower['rows'] if r['id'] == self.exec_user.id)
        self.assertEqual(row['unbanked'], 750.0)
        # Since 2026-09-18 the cash is in the executive's float by itself, so
        # it is "on the road", not undeposited: the panel names them, and the
        # alert fires once the lab's policy says it has been out too long.
        held = next(r for r in tower['cash_held'] if r['user_id'] == self.exec_user.id)
        self.assertEqual(held['amount'], 750.0)
        # Other people on this database may be past the policy already; the
        # claim is about THIS person: not named until the policy says so.
        def named(tower):
            return any(self.exec_user.name in a['detail'] for a in tower['alerts']
                       if a['key'] == 'cash_overdue')
        self.assertFalse(named(tower))
        self.env['ir.config_parameter'].sudo().set_param('lab_fieldwork.cash_ceiling', '500')
        self.assertTrue(named(self.env['lab.desk'].with_user(self.manager).get_ops_desk()))

    def test_control_tower_only_lists_people_who_can_work_a_beat(self):
        """Derived from the group, so it cannot drift from who can use the app. The
        manager is not on it — a manager does not work a beat."""
        ids = [r['id'] for r in
               self.env['lab.desk'].with_user(self.manager).get_ops_desk()['rows']]
        self.assertIn(self.exec_user.id, ids)
        self.assertNotIn(self.manager.id, ids)

    # ------------------------------------------------------- suggestions
    def test_my_day_suggests_clinics_from_the_executives_own_beat(self):
        """Coverage is only worth measuring if the person who can act on it is offered
        the action, and that person is standing outside a clinic."""
        quiet = self.env['res.partner'].create({
            'name': 'Quiet Clinic', 'is_clinic': True})
        self.env['lab.beat'].create({
            'name': 'Test Round', 'user_id': self.exec_user.id, 'weekday': '0',
            'partner_ids': [(6, 0, [quiet.id])]})
        data = self.env['lab.my.day'].with_user(self.exec_user).get_day()
        self.assertIn(quiet.id, [s['partner_id'] for s in data['suggestions']],
                      "a never-visited clinic on my own beat is worth a stop")

    def test_adding_a_stop_twice_does_not_make_two_visits(self):
        """Pressing twice on a slow connection is the normal case, not the exception."""
        MyDay = self.env['lab.my.day'].with_user(self.exec_user)
        first = MyDay.add_stop(self.clinic.id)
        second = MyDay.add_stop(self.clinic.id)
        self.assertEqual(first, second)

    def test_a_stop_can_only_be_added_for_a_clinic(self):
        someone = self.env['res.partner'].create({'name': 'Not a Clinic'})
        with self.assertRaises(UserError):
            self.env['lab.my.day'].with_user(self.exec_user).add_stop(someone.id)

    # ------------------------------------------------------- smart buttons
    def test_every_smart_button_action_opens_something(self):
        """A stat button that raises is worse than no button: the number invites the
        click. This walks all of them rather than trusting the arch."""
        v = self._visit(user=self.exec_user)
        v.do_check_in(LAT, LON)
        v.write({'outcome': 'payment', 'collected': 200.0, 'pay_mode': 'cash'})
        v.do_check_out(LAT, LON)
        beat = self.env['lab.beat'].create({
            'name': 'Buttons', 'user_id': self.exec_user.id, 'weekday': '1',
            'partner_ids': [(6, 0, [self.clinic.id])]})
        trip = self.env['lab.trip'].create({'user_id': self.exec_user.id})
        target = self.env['lab.target'].create({
            'user_id': self.exec_user.id,
            'month': fields.Date.context_today(self.env.user).replace(day=1)})

        checks = [
            (v, ['action_view_cases', 'action_view_clinic_history', 'action_view_clinic']),
            (beat, ['action_view_visits', 'action_view_clinics', 'action_view_coverage']),
            (trip, ['action_view_visits', 'action_view_cases', 'action_view_collections']),
            (target, ['action_view_visits', 'action_view_cases',
                      'action_view_performance']),
            (self.clinic, ['action_view_visits', 'action_view_cases',
                           'action_view_collections', 'action_view_coverage']),
        ]
        for record, methods in checks:
            for method in methods:
                action = getattr(record, method)()
                self.assertEqual(action.get('type'), 'ir.actions.act_window',
                                 "%s.%s" % (record._name, method))
                self.assertTrue(action.get('res_model'),
                                "%s.%s" % (record._name, method))

    def test_the_trip_counts_what_the_days_travel_produced(self):
        """A trip that brought back cases and one that brought back none must not look
        identical on the form a manager approves."""
        v = self._visit(user=self.exec_user)
        v.do_check_in(LAT, LON)
        v.write({'outcome': 'payment', 'collected': 500.0, 'pay_mode': 'cash'})
        v.do_check_out(LAT, LON)
        trip = self.env['lab.trip'].create({'user_id': self.exec_user.id})
        self.assertEqual(trip.visit_count, 1)
        self.assertEqual(trip.collected_total, 500.0)

    # ------------------------------------------------------- settings
    def test_the_administrator_can_actually_open_their_own_settings(self):
        """The role that owns the configuration must be able to reach it.

        res.config.settings is gated on base.group_system, so a Field Work Administrator
        cannot open it and the Configuration menu quietly disappeared for the one role
        it was written for — the menu existed, the group was right, and load_menus
        dropped it because the action's model was unreachable.
        """
        names = self._menu_names(self.admin_user)
        self.assertIn('Configuration', names)
        self.assertIn('Settings', names)

        cfg = self.env['lab.fieldwork.settings'].with_user(self.admin_user).create({})
        self.assertEqual(cfg.fw_visit_radius_m, 300.0, "defaults must be readable")
        cfg.fw_visit_radius_m = 450.0
        cfg.action_save()
        self.assertEqual(
            self.env['ir.config_parameter'].sudo().get_param(
                'lab_fieldwork.visit_radius_m'), '450.0')

    def test_the_administrator_cannot_reach_every_other_apps_settings(self):
        """Granting this role res.config.settings would have fixed the menu and opened
        every other app's configuration at the same time."""
        with self.assertRaises(AccessError):
            self.env['res.config.settings'].with_user(self.admin_user).check_access('write')

    def test_changing_a_coverage_band_actually_changes_the_report(self):
        """The bands are baked into a SQL view's CASE, so a saved setting that does not
        rebuild the view is a setting that silently does nothing."""
        v = self._visit(user=self.exec_user,
                        date=fields.Date.context_today(self.env.user) - timedelta(days=45))
        v.do_check_in(LAT, LON)
        v.outcome = 'order'
        v.do_check_out(LAT, LON)
        row = self.env['lab.coverage'].search([('partner_id', '=', self.clinic.id)])
        self.assertEqual(row.status, 'due', "45 days with the default 30/60/90 bands")

        settings = self.env['lab.fieldwork.settings'].with_user(
            self.admin_user).create({})
        settings.fw_overdue_days = 40
        settings.action_save()
        self.addCleanup(self.env['lab.coverage'].init)
        row = self.env['lab.coverage'].search([('partner_id', '=', self.clinic.id)])
        self.assertEqual(row.status, 'overdue', "40-day band must reach the report")

    def test_the_tower_still_shows_todays_work_after_a_role_change(self):
        """Otherwise the screen lies in the most confusing way available: move somebody
        off the role at lunchtime and their morning vanishes while the alerts they caused
        stay, so a manager reads "cash not in any float" above a list containing nobody
        who could be holding it."""
        v = self._visit(user=self.exec_user)
        v.do_check_in(LAT, LON)
        v.write({'outcome': 'payment', 'collected': 300.0, 'pay_mode': 'cash'})
        v.do_check_out(LAT, LON)
        self.exec_user.write({'group_ids': [
            (3, self.env.ref('lab_fieldwork.group_fieldwork_executive').id)]})

        # The cash is in their float by itself (2026-09-18); the alert about it
        # is the policy one, and it must still name a person on the screen.
        self.env['ir.config_parameter'].sudo().set_param('lab_fieldwork.cash_ceiling', '100')
        tower = self.env['lab.desk'].with_user(self.manager).get_ops_desk()
        ids = [r['id'] for r in tower['rows']]
        self.assertIn(self.exec_user.id, ids,
                      "today's work must stay on the screen that alerts about it")
        alert = next(a for a in tower['alerts'] if a['key'] == 'cash_overdue')
        self.assertIn(self.exec_user.name, alert['detail'])

    def test_the_towers_totals_cannot_disagree_with_its_alerts(self):
        """The totals are summed from the rows, so a user missing from the roster takes
        their numbers with them — the screen then reports nothing collected directly
        above an alert saying cash is unaccounted for."""
        Tower = self.env['lab.desk'].with_user(self.manager)
        # Measured against a baseline, never an absolute: this database carries real
        # field work and a hard-coded total is a test that only passes on an empty one.
        before = Tower.get_ops_desk()['totals']

        v = self._visit(user=self.exec_user)
        v.do_check_in(LAT, LON)
        v.write({'outcome': 'payment', 'collected': 640.0, 'pay_mode': 'cash'})
        v.do_check_out(LAT, LON)
        self.exec_user.action_archive()

        after = Tower.get_ops_desk()['totals']
        self.assertEqual(after['collected'] - before['collected'], 640.0)
        self.assertEqual(after['unbanked'] - before['unbanked'], 640.0,
                         "the alert and the total must come from the same day")

    def test_no_action_ships_a_domain_the_browser_cannot_parse(self):
        """Action domains are parsed in the BROWSER.

        `%(module.xml_id)d` is substituted in some Odoo data fields but NOT in an
        act_window domain: the literal text is stored, the server never looks at it,
        install and the whole suite pass, and py_js throws "Token cannot be parsed" the
        first time a user opens the menu. Use eval + ref() instead. Parsing every domain
        and context as Python is the cheapest way to catch the whole family.
        """
        import ast
        actions = self.env['ir.actions.act_window'].search([]).filtered(
            lambda a: (self.env['ir.model.data'].search([
                ('model', '=', 'ir.actions.act_window'), ('res_id', '=', a.id),
                ('module', '=', 'lab_fieldwork')], limit=1)))
        self.assertTrue(actions, "the module must own some window actions")
        for action in actions:
            for attr in ('domain', 'context'):
                raw = action[attr]
                if not raw or raw in ('{}', '[]'):
                    continue
                self.assertNotIn('%(', raw,
                                 "%s.%s is not substituted server-side" % (action.name, attr))
                try:
                    ast.parse(raw, mode='eval')
                except SyntaxError as exc:
                    self.fail("%s.%s is not parseable: %s" % (action.name, attr, exc))

    # ------------------------------------------------------- field health (admin)
    def _far_visits(self, n, metres_offset):
        made = []
        for i in range(n):
            v = self._visit(user=self.exec_user)
            v.do_check_in(LAT + metres_offset, LON)
            v.outcome = 'followup'    # an OUTCOME; 'round' is a purpose
            if v.gps_state == 'far':
                v.gps_reason = 'testing'
            v.do_check_out(LAT, LON)
            made.append(v)
        return made

    def test_health_says_the_radius_is_too_tight_when_the_evidence_says_so(self):
        """The screen's whole point: everywhere else a distant check-in reads as a
        person's failure. Here the same data is read as evidence about the rule."""
        self.clinic.visit_radius_m = 0.0     # fall back to the configured default
        self.env['ir.config_parameter'].sudo().set_param(
            'lab_fieldwork.visit_radius_m', 100)
        self._far_visits(18, 0.0045)          # ~500 m out
        self._far_visits(4, 0.0002)           # ~22 m, comfortably inside

        health = self.env['lab.desk'].with_user(self.admin_user).get_admin_desk()
        radius = health['radius']
        self.assertEqual(radius['verdict'], 'tight')
        self.assertGreater(radius['far_pct'], 25)
        self.assertTrue(radius['suggested'] % 50 == 0,
                        "a recommendation must be a number somebody would actually set")
        self.assertGreaterEqual(radius['suggested'], 100)

    def test_health_will_not_judge_a_radius_on_too_little_evidence(self):
        """Three check-ins is an anecdote. Recommending a policy change from it would
        make the screen worse than silent."""
        # The calibration reads every check-in in the database from the last
        # LOOKBACK_DAYS, and a live database has plenty. Narrow the window to
        # today so the evidence is this test's own, and assert how much of it
        # there is - otherwise this passes for the wrong reason the day
        # somebody adds a fixture.
        self._far_visits(3, 0.0045)
        with patch.object(desk_model, 'LOOKBACK_DAYS', 0):
            radius = self.env['lab.desk'].with_user(
                self.admin_user).get_admin_desk()['radius']
        self.assertLess(radius['total'], 20, "the point is too little evidence")
        self.assertEqual(radius['verdict'], 'unknown')
        self.assertEqual(radius['suggested'], 0, "no recommendation without evidence")

    def test_health_lists_setup_gaps_that_produce_no_error(self):
        """Each of these stops a feature working silently — an unpinned clinic reports
        'clinic not pinned' for ever and nothing anywhere complains."""
        self.env['res.partner'].create({'name': 'Unpinned Clinic', 'is_clinic': True})
        gaps = self.env['lab.desk'].with_user(
            self.admin_user).get_admin_desk()['gaps']
        keys = [g['key'] for g in gaps]
        self.assertIn('unpinned', keys)
        for gap in gaps:
            self.assertTrue(gap['model'], "a gap with no action is a gap nobody closes")
            self.assertTrue(gap['label'])
            self.assertGreater(gap['count'], 0)

    def test_health_ratio_does_not_divide_by_zero(self):
        """A lab that reimburses nothing is not infinitely efficient."""
        roi = self.env['lab.desk'].with_user(
            self.admin_user).get_admin_desk()['roi']
        self.assertIsInstance(roi['ratio'], float)
        self.assertGreaterEqual(roi['ratio'], 0.0)

    def test_health_trend_has_one_point_per_month(self):
        # The month trend now lives on the marketing desk ('months').
        trend = self.env['lab.desk'].with_user(
            self.admin_user).get_marketing_desk()['months']
        self.assertEqual(len(trend), 6)
        self.assertEqual(set(trend[0]), {'label', 'visits', 'cases', 'value'})

    def test_only_the_administrator_gets_the_command_center(self):
        """Field Health's replacement (2026-08-31) keeps its ownership: the
        screen that judges the rules belongs to whoever can change them."""
        self.assertIn('Command Center', self._menu_names(self.admin_user))
        self.assertNotIn('Command Center', self._menu_names(self.manager))
        self.assertNotIn('Command Center', self._menu_names(self.exec_user))

    # ------------------------------------------------------- phone / mobile
    def _wa_field(self):
        """The field a WhatsApp chat opens on: the WhatsApp Number where
        lab_whatsapp adds one, the phone where it does not."""
        return 'whatsapp_number' if 'whatsapp_number' in self.clinic._fields else 'phone'

    def test_whatsapp_number_gets_a_country_code(self):
        """wa.me silently opens an empty chat for a number with no country code rather
        than reporting an error, so a ten-digit Indian number must be completed here."""
        india = self.env.ref('base.in')
        self.clinic.write({'country_id': india.id, self._wa_field(): '9847012345'})
        v = self._visit(user=self.exec_user)
        self.assertEqual(v.whatsapp_number, '919847012345')

    def test_whatsapp_number_does_not_double_the_country_code(self):
        india = self.env.ref('base.in')
        self.clinic.write({'country_id': india.id})
        for written, expected in (('+91 98470 12345', '919847012345'),
                                  ('09847012345', '919847012345'),
                                  ('919847012345', '919847012345')):
            self.clinic[self._wa_field()] = written
            v = self._visit(user=self.exec_user)
            self.assertEqual(v.whatsapp_number, expected, written)

    def test_the_whatsapp_button_never_opens_on_the_landline(self):
        """Where the clinic has a WhatsApp Number field, its phone is not used."""
        if self._wa_field() == 'phone':
            self.assertTrue(True, "no WhatsApp Number field on this install")
            return
        self.clinic.write({'phone': '0484 2345678', 'whatsapp_number': False})
        v = self._visit(user=self.exec_user)
        self.assertEqual(v.call_number, '0484 2345678')
        self.assertFalse(v.whatsapp_number)

    def test_no_phone_means_no_call_button(self):
        self.clinic.write({'phone': False, 'country_id': False})
        v = self._visit(user=self.exec_user)
        self.assertFalse(v.call_number)
        self.assertFalse(v.whatsapp_number)

    def test_the_named_contact_is_rung_before_the_front_desk(self):
        """An executive rings the doctor they came to see."""
        self.clinic.phone = '0484 2345678'
        doctor = self.env['res.partner'].create({
            'name': 'Dr Nair', 'parent_id': self.clinic.id, 'phone': '9847099999'})
        v = self._visit(user=self.exec_user, contact_id=doctor.id)
        self.assertEqual(v.call_number, '9847099999')

    def test_directions_work_even_when_the_clinic_is_not_pinned(self):
        """No pin is not the same as no directions — the address still routes, and a
        missing map button is exactly when somebody is lost.

        geo:, not https://www.google.com/maps/... - the field app is a plain WebView
        wrapper with no browser behind it, and a bare WebView cannot resolve the
        intent:// URI Android's App Links rewrite the https form into on tap.
        (client, 2026-08-29)
        """
        unpinned = self.env['res.partner'].create({
            'name': 'Unpinned', 'is_clinic': True, 'street': 'MG Road', 'city': 'Kochi'})
        v = self._visit(user=self.exec_user, partner_id=unpinned.id)
        self.assertTrue(v.map_url.startswith('geo:0,0?q='))
        self.assertNotIn(' ', v.map_url, "an unescaped address breaks the link")
        self.assertEqual(v.map_address, 'MG Road Kochi',
                         "the address is also shown as plain text - the fallback for "
                         "a wrapper that cannot open the geo: link either")

        pinned = self._visit(user=self.exec_user)
        self.assertIn('%s,%s' % (LAT, LON), pinned.map_url)
        self.assertTrue(pinned.map_url.startswith('geo:%s,%s?q=' % (LAT, LON)))

    def test_my_day_carries_what_a_phone_needs_to_act(self):
        self.clinic.write({'phone': '9847012345',
                           'country_id': self.env.ref('base.in').id})
        self._visit(user=self.exec_user)
        data = self.env['lab.my.day'].with_user(self.exec_user).get_day()
        card = data['visits'][0]
        for key in ('map_url', 'call', 'whatsapp'):
            self.assertIn(key, card)
        self.assertTrue(card['map_url'])
        self.assertTrue(card['call'])

    # ------------------------------------------------------- case registration
    def _product(self, name='Hawley Retainer'):
        if not getattr(self, '_cached_product', None):
            self._cached_product = self.env['product.product'].create({
                'name': name, 'type': 'consu', 'sale_ok': True, 'list_price': 1500.0})
        return self._cached_product

    def _case(self, visit, patient='Anu Raj', submit=True, **vals):
        """Lines are created WITH the case: a slip with no work on it cannot be saved,
        so the two-step creation this used to do is no longer legal."""
        case = self.env['lab.case'].create(dict({
            'visit_id': visit.id, 'patient': patient,
            'line_ids': [(0, 0, {'product_id': self._product().id,
                                 'ul': 'upper', 'quantity': 1.0})],
        }, **vals))
        if submit:
            case.action_submit()
        return case

    def _open_visit(self, user=None):
        v = self._visit(user=user or self.exec_user)
        v.do_check_in(LAT, LON)
        v.outcome = 'order'
        return v

    def test_patient_key_survives_how_people_actually_write_names(self):
        """Duplicate detection is only as good as this. Titles, order, initials,
        punctuation and case all vary between two entries of the same child."""
        from odoo.addons.lab_fieldwork.models.lab_case import patient_key
        same = ['Anu Raj', 'anu  raj', 'RAJ ANU', 'Mr. Anu Raj', 'Baby Anu Raj',
                'Anu   Raj.', 'A. Anu Raj']
        keys = {patient_key(n) for n in same}
        self.assertEqual(len(keys), 1, "these are all the same patient: %s" % keys)
        self.assertNotEqual(patient_key('Anu Raj'), patient_key('Arun Raj'),
                            "siblings must not be merged")
        self.assertFalse(patient_key(''))

    def test_finishing_the_visit_creates_orders_awaiting_verification(self):
        v = self._open_visit()
        case = self._case(v, 'Meera Nair')
        v.do_check_out(LAT, LON)

        case.invalidate_recordset()
        self.assertEqual(case.state, 'registered')
        order = case.sale_order_id
        self.assertTrue(order)
        self.assertEqual(order.verification_state, 'to_verify',
                         "a case registered in the field is not a confirmed order")
        self.assertEqual(order.patient, 'Meera Nair')
        self.assertEqual(order.partner_id, self.clinic)
        self.assertEqual(order.visit_id, v)
        self.assertEqual(order.order_line.ul, 'upper', "U/L must reach the order line")

    def test_finishing_twice_does_not_create_two_orders(self):
        """The one thing this whole model exists to prevent."""
        v = self._open_visit()
        case = self._case(v)
        v.do_check_out(LAT, LON)
        first = case.sale_order_id
        # Reopening and closing again is a normal correction, not an edge case.
        v.action_reset()
        v.do_check_in(LAT, LON)
        v.outcome = 'order'
        v.do_check_out(LAT, LON)
        case.invalidate_recordset()
        self.assertEqual(case.sale_order_id, first)
        self.assertEqual(len(v.order_ids), 1)

    def test_a_case_handed_over_after_check_out_still_becomes_an_order(self):
        """Add Case stays on a finished visit, but orders were only raised at
        check-out - so a late slip sat Submitted for ever. (2026-09-15)"""
        v = self._open_visit()
        v.do_check_out(LAT, LON)
        case = self._case(v, 'Late Lakshmi')
        self.assertEqual(case.state, 'registered')
        self.assertTrue(case.sale_order_id)
        self.assertEqual(case.sale_order_id.visit_id, v)
        self.assertEqual(v.state, 'done', "and the visit was not reopened for it")

    def test_quick_entry_on_a_finished_visit_registers_the_cases(self):
        v = self._open_visit()
        v.do_check_out(LAT, LON)
        wiz = self.env['lab.case.quick.entry'].create({
            'partner_id': self.clinic.id, 'executive_id': self.exec_user.id,
            'entry_date': v.date,
            'line_ids': [(0, 0, {'patient': 'Quick Late',
                                 'product_id': self._product().id, 'ul': 'upper'})]})
        action = wiz.action_create_cases()
        cases = self.env['lab.case'].search(action['domain'])
        self.assertEqual(cases.visit_id, v, "the finished visit was reused")
        self.assertEqual(cases.state, 'registered')
        self.assertTrue(cases.sale_order_id)

    def test_an_order_adds_to_what_the_visit_already_recorded(self):
        """A visit that delivered and also took a case is both."""
        v = self._visit()
        v.do_check_in(LAT, LON)
        v.write({'outcome': 'delivered'})
        self.env['sale.order'].with_context(default_visit_id=v.id).create({
            'partner_id': self.clinic.id,
            'order_line': [(0, 0, {'product_id': self._product().id,
                                   'product_uom_qty': 1})]})
        self.assertEqual(v.outcome, 'delivered', "the delivery is not written over")
        codes = v.outcome_ids.mapped('code')
        self.assertIn('order', codes)
        self.assertIn('delivered', codes)

    def test_a_reset_visit_forgets_how_it_was_closed(self):
        v = self._open_visit()
        v.do_check_out(LAT + 0.0001, LON)
        v.sudo().write({'auto_closed': True, 'auto_close_failed': True})
        v.action_reset()
        self.assertEqual((v.out_gps_lat, v.out_gps_lon), (0.0, 0.0))
        self.assertFalse(v.auto_closed)
        self.assertFalse(v.auto_close_failed)

    def test_a_second_slip_for_the_same_patient_is_flagged(self):
        v = self._open_visit()
        first = self._case(v, 'Anu Raj')
        v.do_check_out(LAT, LON)

        v2 = self._open_visit()
        second = self._case(v2, 'anu raj', submit=False)   # same child, typed differently
        second.invalidate_recordset()
        self.assertTrue(second.duplicate_ids,
                        "the earlier order must be found despite the spelling")
        self.assertIn('Anu Raj', second.duplicate_warning,
                      "the warning must name the EARLIER spelling, which is the one the "
                      "executive would recognise — not their own keystrokes")
        self.assertTrue(first.sale_order_id)

    def test_a_suspected_duplicate_blocks_check_out_until_acknowledged(self):
        """Never silently dropped and never silently duplicated — the executive is still
        at the counter and is the only person who can tell which it is."""
        v = self._open_visit()
        self._case(v, 'Anu Raj')
        v.do_check_out(LAT, LON)

        v2 = self._open_visit()
        dup = self._case(v2, 'Anu Raj', submit=False)
        with self.assertRaises(UserError):
            dup.action_submit()
        self.assertEqual(dup.state, 'draft')
        with self.assertRaises(UserError):
            v2.do_check_out(LAT, LON)
        self.assertEqual(v2.state, 'open',
                         "the visit stays open so it can be fixed on the spot")

        dup.duplicate_ack = True
        dup.action_submit()
        v2.do_check_out(LAT, LON)
        self.assertEqual(v2.state, 'done')
        self.assertTrue(dup.sale_order_id)

    def test_duplicates_are_found_across_branches_of_the_same_clinic(self):
        """A clinic with three branch contacts is still one doctor, and a case entered
        against a different contact is exactly the duplicate this is looking for."""
        branch = self.env['res.partner'].create({
            'name': 'Test Clinic — Annexe', 'is_clinic': True,
            'parent_id': self.clinic.id, 'type': 'delivery'})
        v = self._open_visit()
        self._case(v, 'Kiran Menon')
        v.do_check_out(LAT, LON)

        v2 = self._visit(user=self.exec_user, partner_id=branch.id)
        v2.do_check_in(LAT, LON)
        v2.outcome = 'order'
        dup = self._case(v2, 'Kiran Menon', submit=False)
        dup.invalidate_recordset()
        self.assertTrue(dup.duplicate_ids, "same doctor, different branch contact")

    def test_a_different_patient_is_not_flagged(self):
        v = self._open_visit()
        self._case(v, 'Anu Raj')
        v.do_check_out(LAT, LON)
        v2 = self._open_visit()
        other = self._case(v2, 'Arun Raj')
        other.invalidate_recordset()
        self.assertFalse(other.duplicate_ids)
        v2.do_check_out(LAT, LON)
        self.assertTrue(other.sale_order_id)

    def test_a_slip_with_no_work_on_it_cannot_even_be_saved(self):
        """Caught on save, not at check-out: an empty slip that survives to the end of
        the visit has already cost the executive the trip back through the form, and by
        then the doctor has gone."""
        v = self._open_visit()
        with self.assertRaises(ValidationError):
            self.env['lab.case'].create({'visit_id': v.id, 'patient': 'Empty Slip'})

    def test_the_last_line_cannot_be_taken_off_a_slip(self):
        v = self._open_visit()
        case = self._case(v, 'Stripped', submit=False)
        with self.assertRaises(ValidationError):
            case.line_ids.unlink()

    def test_an_unsubmitted_slip_stops_the_visit_finishing(self):
        """Never silently swept into an order: the executive either finished it or did
        not, and only they know which."""
        v = self._open_visit()
        case = self._case(v, 'Half Entered', submit=False)
        with self.assertRaises(UserError):
            v.do_check_out(LAT, LON)
        self.assertEqual(v.state, 'open', "they are still at the counter")
        case.action_submit()
        v.do_check_out(LAT, LON)
        self.assertEqual(v.state, 'done')

    def test_submitting_locks_the_slip_and_reopening_unlocks_it(self):
        v = self._open_visit()
        case = self._case(v, 'Submitted Slip')
        self.assertEqual(case.state, 'submitted')
        with self.assertRaises(UserError):
            case.with_user(self.exec_user).patient = 'Changed'
        case.action_reset_to_draft()
        case.with_user(self.exec_user).patient = 'Changed'
        self.assertEqual(case.state, 'draft')

    def test_a_registered_slip_cannot_be_reopened(self):
        v = self._open_visit()
        case = self._case(v, 'Already Ordered')
        v.do_check_out(LAT, LON)
        case.invalidate_recordset()
        with self.assertRaises(UserError):
            case.action_reset_to_draft()

    def test_a_cancelled_slip_is_skipped(self):
        v = self._open_visit()
        good = self._case(v, 'Real Case')
        dead = self._case(v, 'Mistake')
        dead.action_cancel()
        v.do_check_out(LAT, LON)
        good.invalidate_recordset()
        dead.invalidate_recordset()
        self.assertTrue(good.sale_order_id)
        self.assertFalse(dead.sale_order_id)

    def test_a_registered_slip_stays_locked(self):
        """It produced an order somebody is verifying; changing it now would make the
        slip and the order disagree with nothing to say which is right."""
        v = self._open_visit()
        case = self._case(v)
        v.do_check_out(LAT, LON)
        case.invalidate_recordset()
        with self.assertRaises(UserError):
            case.with_user(self.exec_user).patient = 'Someone Else'
        case.with_user(self.exec_user).note = 'still writable'

    def test_a_case_cannot_be_registered_before_arriving(self):
        v = self._visit(user=self.exec_user)
        with self.assertRaises(UserError):
            v.action_new_case()

    def test_the_duplicate_window_is_configurable(self):
        """A lab that sees the same child every fortnight needs a different window from
        one that sees them twice a year, and the default cannot be right for both."""
        v = self._open_visit()
        self._case(v, 'Window Test')
        v.do_check_out(LAT, LON)

        cfg = self.env['lab.fieldwork.settings'].with_user(self.admin_user).create({})
        cfg.fw_duplicate_days = 0
        cfg.action_save()

        v2 = self._open_visit()
        later = self._case(v2, 'Window Test', submit=False)
        later.invalidate_recordset()
        # A zero-day window still catches the same day, which is the case people
        # actually hit: the same slip entered twice on one visit.
        self.assertTrue(later.duplicate_ids)
        self.assertEqual(self.env['lab.case']._duplicate_window(), 0)

    def test_the_visit_reports_slips_before_check_out_and_orders_after(self):
        """The Cases button pointed at sale orders, which do not exist until check-out —
        so it read zero for the whole time the executive was registering cases."""
        v = self._open_visit()
        self._case(v, 'Counting Test')
        v.invalidate_recordset()
        self.assertEqual(v.case_count, 1)
        self.assertEqual(v.case_ready_count, 1, "submitted, waiting for check-out")
        self.assertEqual(v.case_draft_count, 0)
        self.assertEqual(v.order_count, 0, "no order exists yet, by design")

        card = [c for c in self.env['lab.my.day'].with_user(self.exec_user)
                .get_day()['visits'] if c['id'] == v.id][0]
        self.assertEqual(card['cases'], 1, "My Day must not report zero here")

        v.do_check_out(LAT, LON)
        v.invalidate_recordset()
        self.assertEqual(v.order_count, 1)
        self.assertEqual(v.case_ready_count, 0)
        card = [c for c in self.env['lab.my.day'].with_user(self.exec_user)
                .get_day()['visits'] if c['id'] == v.id][0]
        self.assertEqual(card['cases'], 1)

    # ------------------------------------------------------- ordered cases are history
    def test_a_case_that_made_an_order_cannot_be_deleted(self):
        """The order would still exist, with nothing to say where it came from."""
        v = self._open_visit()
        case = self._case(v, 'Kept Forever')
        v.do_check_out(LAT, LON)
        case.invalidate_recordset()
        self.assertTrue(case.sale_order_id)
        with self.assertRaises(UserError):
            case.with_user(self.exec_user).unlink()
        self.assertTrue(case.exists())

    def test_not_even_a_manager_may_delete_an_ordered_case(self):
        """An integrity rule, not a permission level — seniority does not make an
        orphaned order acceptable. The manager bypasses the edit lock, not this."""
        v = self._open_visit()
        case = self._case(v, 'Manager Test')
        v.do_check_out(LAT, LON)
        case.invalidate_recordset()
        with self.assertRaises(UserError):
            case.with_user(self.manager).unlink()
        self.assertTrue(case.exists())

    def test_an_unordered_slip_can_still_be_deleted(self):
        """Deleting the slip is the intended way to resolve a duplicate, so this must
        keep working — the guard is about ordered cases only."""
        v = self._open_visit()
        case = self._case(v, 'Typed By Mistake')
        case.with_user(self.exec_user).unlink()
        self.assertFalse(case.exists())

    def test_a_cancelled_slip_can_still_be_deleted(self):
        v = self._open_visit()
        case = self._case(v, 'Cancelled Slip')
        case.action_cancel()
        case.unlink()
        self.assertFalse(case.exists())

    def test_the_visit_cannot_be_deleted_out_from_under_a_registered_case(self):
        """visit_id is ondelete='cascade' — a Postgres foreign key. The rows would go
        without lab.case.unlink() ever running, bypassing the guard entirely."""
        v = self._open_visit()
        case = self._case(v, 'Cascade Test')
        v.do_check_out(LAT, LON)
        with self.assertRaises(UserError):
            v.with_user(self.manager).unlink()
        self.assertTrue(v.exists())
        self.assertTrue(case.exists())

    def test_lines_of_an_ordered_case_cannot_be_changed_or_removed(self):
        """The slip is protected and its lines were not: the case could not be edited,
        but a line could be deleted straight out of it."""
        v = self._open_visit()
        case = self._case(v, 'Line Test')
        v.do_check_out(LAT, LON)
        case.invalidate_recordset()
        line = case.line_ids[0]
        with self.assertRaises(UserError):
            line.with_user(self.exec_user).unlink()
        with self.assertRaises(UserError):
            line.with_user(self.exec_user).quantity = 99
        with self.assertRaises(UserError):
            self.env['lab.case.line'].with_user(self.exec_user).create({
                'case_id': case.id, 'product_id': self._product().id, 'ul': 'lower'})
        self.assertTrue(line.exists())

    # ------------------------------------------------- one executive, one set of records
    def test_an_executive_cannot_hand_their_work_to_a_colleague(self):
        """The record rules stop an executive READING somebody else's work; they do not
        stop them pushing their own onto a colleague. The write passes the rule, the
        record leaves their list, and it lands on someone else without that person or
        their manager being asked."""
        v = self._visit(user=self.exec_user)
        with self.assertRaises(UserError):
            v.with_user(self.exec_user).user_id = self.other_user
        self.assertEqual(v.user_id, self.exec_user)

    def test_an_executive_cannot_create_work_in_someone_elses_name(self):
        for model, vals in (
            ('lab.visit', {'partner_id': self.clinic.id, 'user_id': self.other_user.id}),
            ('lab.trip', {'user_id': self.other_user.id}),
            ('lab.beat', {'name': 'Not Mine', 'weekday': '0',
                            'user_id': self.other_user.id}),
        ):
            with self.assertRaises(UserError, msg=model):
                self.env[model].with_user(self.exec_user).create(vals)

    def test_a_manager_may_still_assign_work_to_anyone(self):
        """The rule is about an executive's reach, not about locking the model down."""
        v = self.env['lab.visit'].with_user(self.manager).create({
            'partner_id': self.clinic.id, 'user_id': self.exec_user.id})
        self.assertEqual(v.user_id, self.exec_user)
        v.user_id = self.other_user
        self.assertEqual(v.user_id, self.other_user)

    def test_the_dropdown_offers_only_what_the_server_will_accept(self):
        """Offering a list of every user and then refusing the save is worse than not
        offering the choice."""
        v = self._visit(user=self.exec_user)
        self.assertEqual(v.with_user(self.exec_user).allowed_user_ids, self.exec_user)
        mine = v.with_user(self.manager).allowed_user_ids
        self.assertIn(self.exec_user, mine)
        self.assertIn(self.other_user, mine)

    def test_an_executive_sees_coverage_only_for_their_own_round(self):
        """Coverage was the one report with no record rule, so an executive could read
        every clinic in the lab — including what other people's rounds are worth."""
        mine = self.env['res.partner'].create({'name': 'My Clinic', 'is_clinic': True})
        theirs = self.env['res.partner'].create({'name': 'Their Clinic', 'is_clinic': True})
        self.env['lab.beat'].create({
            'name': 'Mine', 'user_id': self.exec_user.id, 'weekday': '0',
            'partner_ids': [(6, 0, [mine.id])]})
        self.env['lab.beat'].create({
            'name': 'Theirs', 'user_id': self.other_user.id, 'weekday': '1',
            'partner_ids': [(6, 0, [theirs.id])]})

        seen = self.env['lab.coverage'].with_user(self.exec_user).search([]).partner_id
        self.assertIn(mine, seen)
        self.assertNotIn(theirs, seen, "another executive's round is not their business")
        self.assertIn(theirs,
                      self.env['lab.coverage'].with_user(self.manager).search([]).partner_id)

    def test_a_never_visited_clinic_still_reaches_its_own_executive(self):
        """Scoped through the beat's clinics rather than coverage.beat_id: a clinic that
        has never been visited carries no beat, and those are exactly the ones the
        'worth a stop' suggestions are for."""
        fresh = self.env['res.partner'].create({'name': 'Never Seen', 'is_clinic': True})
        self.env['lab.beat'].create({
            'name': 'Fresh Round', 'user_id': self.exec_user.id, 'weekday': '2',
            'partner_ids': [(6, 0, [fresh.id])]})
        row = self.env['lab.coverage'].with_user(self.exec_user).search(
            [('partner_id', '=', fresh.id)])
        self.assertTrue(row, "a never-visited clinic on my beat must be visible to me")
        self.assertEqual(row.status, 'never')

    def test_the_day_is_named_in_words_not_iso(self):
        """An ISO date is a machine's answer. The screen is opened at a clinic door by
        someone checking they are on the right day."""
        data = self.env['lab.my.day'].with_user(self.exec_user).get_day()
        self.assertNotEqual(data['date_label'], data['date'])
        self.assertRegex(data['date_label'], r'[A-Za-z]{3,}')
        self.assertIn(data['greeting'].split()[0], ('Good',))
        tower = self.env['lab.desk'].with_user(self.manager).get_ops_desk()
        self.assertNotEqual(tower['date_label'], tower['date'])

    # ------------------------------------------------------------ attendance
    def _employee_for(self, user):
        return self.env['hr.employee'].create({
            'name': user.name, 'user_id': user.id,
            'company_id': self.env.company.id})

    def test_field_work_is_locked_until_the_day_is_started(self):
        """The whole point: attendance is not a report filed afterwards, it is the gate."""
        self._employee_for(self.exec_user)
        v = self._visit(user=self.exec_user)
        with self.assertRaises(UserError):
            v.with_user(self.exec_user).do_check_in(LAT, LON)
        self.assertEqual(v.state, 'planned', "a refused check-in must not half-open it")

        trip = self.env['lab.trip'].with_user(self.exec_user).create({'odo_start': 100})
        with self.assertRaises(UserError):
            trip.action_start()

        self.env['lab.my.day'].with_user(self.exec_user).attendance_toggle()
        v.with_user(self.exec_user).do_check_in(LAT, LON)
        self.assertEqual(v.state, 'open')

    def test_one_button_starts_and_ends_the_day(self):
        emp = self._employee_for(self.exec_user)
        MyDay = self.env['lab.my.day'].with_user(self.exec_user)

        self.assertEqual(MyDay.get_day()['attendance']['state'], 'checked_out')
        after_in = MyDay.attendance_toggle(latitude=LAT, longitude=LON)
        self.assertEqual(after_in['state'], 'checked_in')
        self.assertTrue(after_in['since'])

        att = self.env['hr.attendance'].search(
            [('employee_id', '=', emp.id)], order='id desc', limit=1)
        self.assertAlmostEqual(att.in_latitude, LAT, places=4,
                               msg="the field fix belongs on the attendance too")

        after_out = MyDay.attendance_toggle(latitude=LAT, longitude=LON)
        self.assertEqual(after_out['state'], 'checked_out')
        self.assertTrue(att.check_out)
        self.assertAlmostEqual(att.out_latitude, LAT, places=4)

    def test_attendance_lands_on_odoos_own_record_not_a_parallel_one(self):
        """Payroll, leave and every HR report read hr.attendance. A second record of
        when somebody started work would be a second answer to the same question."""
        emp = self._employee_for(self.exec_user)
        before = self.env['hr.attendance'].search_count([('employee_id', '=', emp.id)])
        self.env['lab.my.day'].with_user(self.exec_user).attendance_toggle()
        self.assertEqual(
            self.env['hr.attendance'].search_count([('employee_id', '=', emp.id)]),
            before + 1)

    def test_someone_with_no_employee_record_is_told_what_is_missing(self):
        """"Access denied" on a phone at a clinic door is how an executive learns to stop
        using the app, not how they learn to mark attendance."""
        v = self._visit(user=self.other_user)
        with self.assertRaises(UserError) as caught:
            v.with_user(self.other_user).do_check_in(LAT, LON)
        self.assertIn('employee', str(caught.exception).lower())
        self.assertEqual(
            self.env['lab.my.day'].with_user(self.other_user)
            .get_day()['attendance']['state'], 'no_employee')

    def test_the_gate_can_be_switched_off(self):
        """A lab that does not run attendance must not be locked out of its own app."""
        cfg = self.env['lab.fieldwork.settings'].with_user(self.admin_user).create({})
        cfg.fw_require_attendance = False
        cfg.action_save()
        v = self._visit(user=self.exec_user)
        v.with_user(self.exec_user).do_check_in(LAT, LON)
        self.assertEqual(v.state, 'open')

    def test_the_gate_is_not_bypassed_by_env_user_being_superuser(self):
        """`env.user` hands back the user record in SUPERUSER mode, so a bypass tested
        on that recordset is always true. The gate silently never fired."""
        self._employee_for(self.exec_user)
        v = self._visit(user=self.exec_user)
        with self.assertRaises(UserError):
            v.with_user(self.exec_user).do_check_in(LAT, LON)
        # …while genuine sudo still works, because the caller's env carries the flag.
        v.sudo().do_check_in(LAT, LON)
        self.assertEqual(v.state, 'open')

    def test_switching_a_true_by_default_setting_off_actually_sticks(self):
        """`ir.config_parameter.set_param` DELETES the key when handed False, and a
        missing key reads as the default — so unticking anything defaulted to True did
        nothing at all, on either settings page."""
        Param = self.env['ir.config_parameter'].sudo()
        field = 'fw_require_attendance'
        # Each page is reached by the role that owns it: the field-work page by the
        # Field Work Administrator, General Settings by a system administrator.
        pages = (('lab.fieldwork.settings', self.admin_user, 'action_save'),
                 ('res.config.settings', self.env.ref('base.user_admin'), 'execute'))
        for model, who, save in pages:
            cfg = self.env[model].with_user(who).create({})
            if 'group_mrp_routings' in cfg._fields:
                # Core MRP's set_values ARCHIVES every routing operation when the
                # wizard says routings-off while the saving user has the group —
                # 13 CPU-minutes on this database, rolled back but still burned,
                # and nothing to do with what this test asserts. Keep the truth:
                # the routings are in use.
                cfg.group_mrp_routings = True
            cfg[field] = False
            getattr(cfg, save)()
            self.assertEqual(
                Param.get_param('lab_fieldwork.require_attendance'), 'False',
                "%s must store 'off' as a value, not an absence" % model)
            self.assertFalse(self.env.user._fw_attendance_required(), model)

            cfg = self.env[model].with_user(who).create({})
            if 'group_mrp_routings' in cfg._fields:
                cfg.group_mrp_routings = True
            cfg[field] = True
            getattr(cfg, save)()
            self.assertTrue(self.env.user._fw_attendance_required(), model)

    def test_the_attendance_card_shows_a_local_clock_time(self):
        """A raw UTC stamp is hours off whatever the phone's own clock says, which reads
        as the app being wrong; and the card only ever shows today, so the date says
        nothing."""
        self._employee_for(self.exec_user)
        MyDay = self.env['lab.my.day'].with_user(self.exec_user)
        MyDay.attendance_toggle()
        since = MyDay.get_day()['attendance']['since']
        self.assertRegex(since, r'^\d{2}:\d{2}$')

    def test_an_open_visit_saves_without_an_outcome(self):
        """The outcome is often not knowable while the executive is still at the counter.
        Requiring it to SAVE stops them recording the note, the photo and the cash they
        already have."""
        self._employee_for(self.exec_user)
        self.env['lab.my.day'].with_user(self.exec_user).attendance_toggle()
        v = self._visit(user=self.exec_user)
        v.with_user(self.exec_user).do_check_in(LAT, LON)
        self.assertEqual(v.state, 'open')
        self.assertFalse(v.outcome)

        v.with_user(self.exec_user).write({'note': 'Doctor busy, waiting',
                                           'collected': 500.0, 'pay_mode': 'cash'})
        self.assertEqual(v.collected, 500.0, "the visit must be recordable meanwhile")

        # …and it is still mandatory at the moment it becomes knowable.
        with self.assertRaises(UserError):
            v.with_user(self.exec_user).do_check_out(LAT, LON)
        v.with_user(self.exec_user).outcome = 'payment'
        v.with_user(self.exec_user).do_check_out(LAT, LON)
        self.assertEqual(v.state, 'done')

    def test_no_view_makes_a_field_required_that_only_an_action_needs(self):
        """`required` in an arch means "cannot save"; a rule that belongs to a button
        must live in the button's method, or the record becomes unsavable in between."""
        arch = self.env['lab.visit'].get_view(
            self.env.ref('lab_fieldwork.view_visit_form').id, 'form')['arch']
        self.assertNotIn('required="state == \'open\'"', arch,
                         "nothing should be unsavable merely because a visit is open")

    # ------------------------------------------------------- call the doctor first
    def test_the_call_doctor_flag_reaches_the_order(self):
        """The executive is the only person who hears the doctor be vague about a
        prescription. If the flag stops at the slip, production never learns."""
        v = self._open_visit()
        case = self._case(v, 'Needs A Call', submit=False,
                          needs_doctor_call=True,
                          doctor_call_note='Confirm the shade on 21')
        case.action_submit()
        v.do_check_out(LAT, LON)
        case.invalidate_recordset()

        order = case.sale_order_id
        self.assertTrue(order.is_pending_work,
                        "the lab's own Call Doctor flag must be set")
        self.assertIn('Confirm the shade on 21', order.instruction,
                      "a flag with no question is 'ring somebody about something'")

    def test_the_flag_is_off_unless_it_is_set(self):
        v = self._open_visit()
        case = self._case(v, 'Ordinary Case')
        v.do_check_out(LAT, LON)
        case.invalidate_recordset()
        self.assertFalse(case.sale_order_id.is_pending_work)

    def test_the_flag_alone_still_says_something_useful(self):
        """Somebody who ticks the box and types nothing must not produce a silent flag."""
        v = self._open_visit()
        case = self._case(v, 'Flag Only', submit=False, needs_doctor_call=True)
        case.action_submit()
        v.do_check_out(LAT, LON)
        case.invalidate_recordset()
        self.assertIn('Call the doctor', case.sale_order_id.instruction)

    def test_a_saved_photo_is_a_size_string_not_its_bytes(self):
        """The contract the camera widget depends on.

        Under bin_size — which is what the web client uses — a stored binary field hands
        back "73.12 Kb", NOT base64. Treating it as base64 produced
        `data:image/png;base64,73.12 Kb` and a broken image on every saved photo, while a
        freshly captured one displayed perfectly, so it only failed after saving.

        Pinned here so nobody "simplifies" the widget back to reading the value directly.
        """
        import base64
        import io
        from PIL import Image
        # A REAL image: fields.Image decodes what it is given and refuses anything else,
        # so a handful of PNG-looking bytes is rejected outright.
        buf = io.BytesIO()
        Image.new('RGB', (24, 24), (200, 40, 90)).save(buf, format='PNG')
        png = base64.b64encode(buf.getvalue())
        trip = self.env['lab.trip'].create({
            'user_id': self.exec_user.id, 'photo_start': png})

        sized = trip.with_context(bin_size=True).read(['photo_start'])[0]['photo_start']
        # "91.00 bytes" for a small image, "73.12 Kb" for a photo — the UNIT varies, so
        # the assertion is on the shape (a number and a unit), not on one spelling.
        self.assertRegex(sized.decode() if isinstance(sized, bytes) else sized,
                         r'^[\d.]+\s*\w+$',
                         "the client receives a SIZE, not the bytes")

        raw = trip.with_context(bin_size=False).read(['photo_start'])[0]['photo_start']
        self.assertGreater(len(raw), 100, "the bytes are still there, fetched by URL")

    # ------------------------------------------------------- end of day
    def _at_hour(self, hour):
        """Pretend the cutoff has passed, without waiting for the evening."""
        return self.env['lab.day.closer'].with_context(
            fw_test_hour=hour)

    def test_a_visit_with_an_outcome_is_closed_when_the_day_ends(self):
        """They told us what happened and forgot the last tap. Nothing is invented."""
        self.env['ir.config_parameter'].sudo().set_param(
            'lab_fieldwork.auto_close_hour', 0)
        v = self._visit(user=self.exec_user)
        v.do_check_in(LAT, LON)
        v.outcome = 'order'
        self.env['lab.day.closer']._cron_close_day()
        v.invalidate_recordset()
        self.assertEqual(v.state, 'done')
        self.assertTrue(v.auto_closed, "it must be distinguishable from a real Finish")
        self.assertTrue(v.check_out)

    def test_a_visit_with_no_outcome_is_left_open_on_purpose(self):
        """Closing it would mean writing down an outcome nobody gave."""
        self.env['ir.config_parameter'].sudo().set_param(
            'lab_fieldwork.auto_close_hour', 0)
        v = self._visit(user=self.exec_user)
        v.do_check_in(LAT, LON)
        self.env['lab.day.closer']._cron_close_day()
        v.invalidate_recordset()
        self.assertEqual(v.state, 'open')
        self.assertTrue(v.auto_close_failed, "and it must be visible, not silent")

    def test_a_trip_with_a_closing_reading_is_submitted(self):
        Param = self.env['ir.config_parameter'].sudo()
        Param.set_param('lab_fieldwork.auto_close_hour', 0)
        # The odometer-photo rule is a different feature; it would block action_start
        # here and the test is not about it.
        Param.set_param('lab_fieldwork.require_odo_photo', 'False')
        self._employee_for(self.exec_user)
        self.env['lab.my.day'].with_user(self.exec_user).attendance_toggle()
        trip = self.env['lab.trip'].create({
            'user_id': self.exec_user.id, 'odo_start': 100.0})
        trip.with_user(self.exec_user).action_start()
        trip.odo_end = 140.0
        self.env['lab.day.closer']._cron_close_day()
        trip.invalidate_recordset()
        self.assertEqual(trip.state, 'closed')
        self.assertTrue(trip.auto_closed)

    def test_a_trip_with_no_closing_reading_is_never_guessed_at(self):
        """A distance nobody drove would go straight into a travel claim. An open claim
        gets chased; an invented one gets paid."""
        Param = self.env['ir.config_parameter'].sudo()
        Param.set_param('lab_fieldwork.auto_close_hour', 0)
        # The odometer-photo rule is a different feature; it would block action_start
        # here and the test is not about it.
        Param.set_param('lab_fieldwork.require_odo_photo', 'False')
        self._employee_for(self.exec_user)
        self.env['lab.my.day'].with_user(self.exec_user).attendance_toggle()
        trip = self.env['lab.trip'].create({
            'user_id': self.exec_user.id, 'odo_start': 100.0})
        trip.with_user(self.exec_user).action_start()
        self.env['lab.day.closer']._cron_close_day()
        trip.invalidate_recordset()
        self.assertEqual(trip.state, 'open')
        self.assertEqual(trip.distance, 0.0, "no distance was invented")
        self.assertTrue(trip.auto_close_failed)

    def test_anyone_still_on_duty_is_signed_out(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'lab_fieldwork.auto_close_hour', 0)
        emp = self._employee_for(self.exec_user)
        self.env['lab.my.day'].with_user(self.exec_user).attendance_toggle()
        self.assertEqual(emp.attendance_state, 'checked_in')

        self.env['lab.day.closer']._cron_close_day()
        emp.invalidate_recordset()
        self.assertEqual(emp.attendance_state, 'checked_out')
        att = self.env['hr.attendance'].search(
            [('employee_id', '=', emp.id)], order='id desc', limit=1)
        self.assertEqual(att.out_mode, 'auto_check_out',
                         "payroll must see this was not a person pressing a button")

    def test_nothing_happens_before_the_cutoff(self):
        """The hour is read in the executive's own timezone, so a single scheduled hour
        would close one region's day in the middle of their afternoon."""
        self.env['ir.config_parameter'].sudo().set_param(
            'lab_fieldwork.auto_close_hour', 23)
        v = self._visit(user=self.exec_user)
        v.do_check_in(LAT, LON)
        v.outcome = 'order'
        self.env['lab.day.closer']._cron_close_day()
        v.invalidate_recordset()
        self.assertEqual(v.state, 'open')

    def test_the_whole_thing_can_be_switched_off(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'lab_fieldwork.auto_close_day', 'False')
        self.env['ir.config_parameter'].sudo().set_param(
            'lab_fieldwork.auto_close_hour', 0)
        v = self._visit(user=self.exec_user)
        v.do_check_in(LAT, LON)
        v.outcome = 'order'
        self.env['lab.day.closer']._cron_close_day()
        v.invalidate_recordset()
        self.assertEqual(v.state, 'open')

    def test_running_twice_changes_nothing_the_second_time(self):
        """Hourly means it runs again ten minutes later."""
        self.env['ir.config_parameter'].sudo().set_param(
            'lab_fieldwork.auto_close_hour', 0)
        v = self._visit(user=self.exec_user)
        v.do_check_in(LAT, LON)
        v.outcome = 'order'
        first = self.env['lab.day.closer']._cron_close_day()
        second = self.env['lab.day.closer']._cron_close_day()
        self.assertGreaterEqual(first, 1)
        self.assertEqual(second, 0)


@tagged('post_install', '-at_install')
class TestSupervisorsSeeEveryDay(TransactionCase):
    """The back-dating window is for the person writing a round up, not for the person
    checking it. A manager reviewing last month's beat was clamped to three days ago and
    could not open the day they were asking about. (client, 2026-08-27)"""

    EXEC = 'lab_fieldwork.group_fieldwork_executive'

    def _user(self, login, groups):
        return self.env['res.users'].create({
            'name': login, 'login': login,
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id]
                           + [self.env.ref(g).id for g in groups])]})

    def _asked_for(self, user, days_back):
        wanted = fields.Date.context_today(user) - timedelta(days=days_back)
        return wanted, self.env['lab.my.day'].with_user(user).get_day(str(wanted))

    def test_an_executive_is_still_held_to_the_window(self):
        user = self._user('day_exec', [self.EXEC])
        wanted, day = self._asked_for(user, 120)
        self.assertNotEqual(str(day['day']), str(wanted))
        self.assertIsNotNone(day['backdate_days'])

    def test_a_manager_an_admin_and_the_board_may_open_any_day(self):
        for login, group in (
                ('day_mgr', 'lab_fieldwork.group_fieldwork_manager'),
                ('day_adm', 'lab_fieldwork.group_fieldwork_admin')):
            user = self._user(login, [self.EXEC, group])
            wanted, day = self._asked_for(user, 120)
            self.assertEqual(str(day['day']), str(wanted), group)
            self.assertTrue(day['any_day'], group)

    def test_the_day_stepper_is_still_offered_when_there_is_no_limit(self):
        """The control is hidden when there is no window to step through, so "no limit"
        has to say so explicitly or it reads as "no days back at all"."""
        user = self._user('day_mgr2', [self.EXEC,
                                       'lab_fieldwork.group_fieldwork_manager'])
        _wanted, day = self._asked_for(user, 1)
        self.assertTrue(day['backdate_days'] or day['any_day'],
                        "the stepper must render for someone with no limit")

    def test_nobody_may_look_forward(self):
        user = self._user('day_mgr3', [self.EXEC,
                                       'lab_fieldwork.group_fieldwork_manager'])
        today = fields.Date.context_today(user)
        day = self.env['lab.my.day'].with_user(user).get_day(
            str(today + timedelta(days=5)))
        self.assertEqual(str(day['day']), str(today),
                         "tomorrow's visits are planned, not recorded")


@tagged('post_install', '-at_install')
class TestNewClinics(TransactionCase):
    """The doors a round opened this month. An executive is judged on the work they
    bring back and the clinics they open, and the lab could see the first everywhere and
    the second nowhere. (client, 2026-08-27)"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.NC = cls.env['lab.new.clinics']
        cls.mine = cls.env['crm.team'].create({'name': 'NC Mine'})
        cls.other = cls.env['crm.team'].create({'name': 'NC Other'})
        cls.exec_user = cls.env['res.users'].create({
            'name': 'NC Exec', 'login': 'nc_exec',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('lab_fieldwork.group_fieldwork_executive').id])]})
        cls.mine.user_id = cls.exec_user.id

    def _clinic(self, name, team):
        return self.env['res.partner'].create({'name': name, 'team_id': team.id})

    def test_a_clinic_opened_this_month_is_listed(self):
        clinic = self._clinic('NC Fresh', self.mine)
        data = self.NC.get_new_clinics()
        self.assertIn(clinic.id, [r['id'] for r in data['rows']])
        self.assertGreaterEqual(data['total'], 1)

    def test_an_executive_sees_their_own_routes_only(self):
        mine = self._clinic('NC Mine Clinic', self.mine)
        theirs = self._clinic('NC Their Clinic', self.other)
        self.env.registry.clear_cache()
        data = self.NC.with_user(self.exec_user).get_new_clinics()
        ids = [r['id'] for r in data['rows']]
        self.assertTrue(data['scoped'])
        self.assertIn(mine.id, ids)
        self.assertNotIn(theirs.id, ids, "another round's doors are not theirs")

    def test_a_manager_sees_the_company(self):
        theirs = self._clinic('NC Everyone', self.other)
        manager = self.env['res.users'].create({
            'name': 'NC Mgr', 'login': 'nc_mgr',
            'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('lab_fieldwork.group_fieldwork_manager').id])]})
        self.env.registry.clear_cache()
        data = self.NC.with_user(manager).get_new_clinics()
        self.assertFalse(data['scoped'])
        self.assertIn(theirs.id, [r['id'] for r in data['rows']])

    def test_a_door_opened_and_never_used_reads_differently(self):
        """A clinic that has since sent work is not the same achievement as one that
        has not, so the screen separates them."""
        quiet = self._clinic('NC Quiet', self.mine)
        data = self.NC.get_new_clinics()
        row = next(r for r in data['rows'] if r['id'] == quiet.id)
        self.assertFalse(row['ordering'])
        self.assertEqual(row['orders'], 0)

    def test_the_month_stepper_never_walks_into_the_future(self):
        data = self.NC.get_new_clinics('2099-01')
        today = fields.Date.context_today(self.env.user)
        self.assertEqual(data['month'], today.strftime('%Y-%m'))

    def test_the_trend_puts_one_month_in_context(self):
        data = self.NC.get_new_clinics()
        self.assertEqual(len(data['trend']), 6)
        self.assertEqual(data['trend'][-1]['month'], data['month'])

    def test_the_drill_opens_the_same_set(self):
        self._clinic('NC Drill', self.mine)
        data = self.NC.get_new_clinics()
        action = self.NC.open_clinics(data['month'])
        self.assertEqual(action['res_model'], 'res.partner')
        self.assertEqual(
            self.env['res.partner'].search_count(action['domain']), data['total'])

    def test_a_doctor_added_under_a_clinic_is_not_a_new_door(self):
        clinic = self._clinic('NC Parent', self.mine)
        doctor = self.env['res.partner'].create({
            'name': 'NC Doctor', 'parent_id': clinic.id, 'team_id': self.mine.id})
        data = self.NC.get_new_clinics()
        ids = [r['id'] for r in data['rows']]
        self.assertIn(clinic.id, ids)
        self.assertNotIn(doctor.id, ids, "a contact under a clinic is not a clinic")
        self.assertEqual(self.NC.count_new_clinics(), data['total'])

    def test_the_month_arrows_reach_the_server(self):
        """The screen calls get_new_clinics([month]). Without @api.model the
        month arrived as record ids and every arrow reloaded this month."""
        from odoo.service.model import call_kw
        last = (local_now(self.env).date().replace(day=1)
                - timedelta(days=1)).strftime('%Y-%m')
        data = call_kw(self.NC, 'get_new_clinics', [last], {})
        self.assertEqual(data['month'], last)


@tagged('post_install', '-at_install')
class TestTowerTrend(TransactionCase):
    """The fortnight strip on the Control Tower.

    A day's numbers say nothing on their own — nine visits is a good day or a
    collapse depending on the fortnight — so the tower carries the fourteen days
    behind whatever day is on screen, and the strip doubles as the day picker.
    (client, 2026-08-27)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tower = cls.env['lab.desk']
        cls.today = fields.Date.context_today(cls.env['res.users'])
        cls.team = cls.env['crm.team'].create({'name': 'Trend Route'})
        cls.exec_user = cls.env['res.users'].create({
            'name': 'Trend Exec', 'login': 'trend_exec',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('lab_fieldwork.group_fieldwork_executive').id])]})
        cls.team.user_id = cls.exec_user.id
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Trend Clinic', 'team_id': cls.team.id})

    def _visit(self, days_back, collected=0.0):
        return self.env['lab.visit'].create({
            'user_id': self.exec_user.id,
            'partner_id': self.clinic.id,
            'date': self.today - timedelta(days=days_back),
            'state': 'done',
            'collected': collected,
        })

    def test_the_strip_is_a_fortnight_ending_on_the_day_shown(self):
        trend = self.Tower.get_ops_desk()['trend']
        self.assertEqual(len(trend), 14)
        self.assertEqual(trend[-1]['day'], fields.Date.to_string(self.today))
        self.assertTrue(trend[-1]['is_day'])

    def test_exactly_one_bar_is_marked_as_the_day_on_screen(self):
        trend = self.Tower.get_ops_desk()['trend']
        self.assertEqual(sum(1 for t in trend if t['is_day']), 1)

    def test_each_bar_counts_the_visits_finished_that_day(self):
        # Differences, not totals: the strip counts every visit in the database
        # and this one has real ones on these days.
        def bars():
            return {t['day']: t['visits']
                    for t in self.Tower.get_ops_desk()['trend']}

        def day(n):
            return fields.Date.to_string(self.today - timedelta(days=n))

        before = bars()
        for _i in range(3):
            self._visit(2)
        self._visit(5)
        after = bars()
        self.assertEqual(after[day(2)] - before[day(2)], 3)
        self.assertEqual(after[day(5)] - before[day(5)], 1)
        self.assertEqual(after[day(3)] - before[day(3)], 0,
                         "a day nothing was recorded on does not move")

    def test_the_collected_lens_sums_the_money_taken(self):
        self._visit(1, collected=120.0)
        self._visit(1, collected=80.0)
        by_day = {t['day']: t for t in self.Tower.get_ops_desk()['trend']}
        row = by_day[fields.Date.to_string(self.today - timedelta(days=1))]
        self.assertEqual(row['collected'], 200.0)

    def test_a_visit_that_is_not_done_is_not_counted(self):
        self.env['lab.visit'].create({
            'user_id': self.exec_user.id, 'partner_id': self.clinic.id,
            'date': self.today, 'state': 'planned'})
        by_day = {t['day']: t for t in self.Tower.get_ops_desk()['trend']}
        self.assertEqual(by_day[fields.Date.to_string(self.today)]['visits'], 0)

    def test_work_older_than_the_fortnight_is_outside_the_strip(self):
        """Asserted as a DIFFERENCE, not as a total.

        Summing the whole strip and expecting zero only holds on an empty
        database; against the real one it counts the team's genuine fortnight
        and fails at 7 != 0. What the test actually means is that a visit 20
        days back changes nothing on a 14-day strip. (2026-08-31)
        """
        before = {t['day']: t['visits'] for t in self.Tower.get_ops_desk()['trend']}
        self._visit(20)
        after = {t['day']: t['visits'] for t in self.Tower.get_ops_desk()['trend']}
        self.assertNotIn(fields.Date.to_string(self.today - timedelta(days=20)),
                         after, "a day 20 back is not on a fortnight strip")
        self.assertEqual(before, after,
                         "and nothing already on the strip moved because of it")

    def test_reading_a_past_day_moves_the_whole_fortnight_with_it(self):
        past = fields.Date.to_string(self.today - timedelta(days=6))
        trend = self.Tower.get_ops_desk(past)['trend']
        self.assertEqual(trend[-1]['day'], past)
        self.assertTrue(trend[-1]['is_day'])
        # …and it ends there, so the strip never shows days after the one being read.
        self.assertNotIn(fields.Date.to_string(self.today),
                         [t['day'] for t in trend])

    def test_sunday_is_flagged_so_a_closed_day_does_not_read_as_a_cliff(self):
        trend = self.Tower.get_ops_desk()['trend']
        for row in trend:
            self.assertEqual(
                row['weekend'],
                fields.Date.to_date(row['day']).weekday() == 6)
        self.assertEqual(sum(1 for t in trend if t['weekend']), 2)

    def test_the_tower_sends_the_real_today_so_the_picker_can_unpin(self):
        past = fields.Date.to_string(self.today - timedelta(days=3))
        data = self.Tower.get_ops_desk(past)
        self.assertEqual(data['today'], fields.Date.to_string(self.today))
        self.assertFalse(data['is_today'])


@tagged('post_install', '-at_install')
class TestWidgetsShowNewClinics(TransactionCase):
    """Both field screens carry the month's door count. (client, 2026-08-27)"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.team = cls.env['crm.team'].create({'name': 'Doors Route'})
        cls.exec_user = cls.env['res.users'].create({
            'name': 'Doors Exec', 'login': 'doors_exec',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('lab_fieldwork.group_fieldwork_executive').id])]})
        cls.team.user_id = cls.exec_user.id

    def test_the_cheap_count_agrees_with_the_full_screen(self):
        NC = self.env['lab.new.clinics']
        self.env['res.partner'].create({'name': 'Doors A', 'team_id': self.team.id})
        self.assertEqual(NC.count_new_clinics(), NC.get_new_clinics()['total'])

    def test_the_count_is_scoped_the_same_way_the_screen_is(self):
        NC = self.env['lab.new.clinics'].with_user(self.exec_user)
        other = self.env['crm.team'].create({'name': 'Doors Other'})
        self.env['res.partner'].create({'name': 'Doors Mine', 'team_id': self.team.id})
        self.env['res.partner'].create({'name': 'Doors Theirs', 'team_id': other.id})
        self.assertEqual(NC.count_new_clinics(), NC.get_new_clinics()['total'])
        self.assertNotIn('Doors Theirs',
                         [r['name'] for r in NC.get_new_clinics()['rows']])

    def test_the_marketing_desk_carries_the_number(self):
        """The doors are marketing's scoreboard, so their count rides that desk."""
        data = self.env['lab.desk'].get_marketing_desk()
        self.assertIn('new_clinics', data)
        self.assertEqual(data['new_clinics'],
                         self.env['lab.new.clinics'].count_new_clinics())

    def test_my_day_carries_the_number(self):
        data = self.env['lab.my.day'].with_user(self.exec_user).get_day()
        self.assertIn('new_clinics', data)
        self.assertEqual(
            data['new_clinics'],
            self.env['lab.new.clinics'].with_user(self.exec_user).count_new_clinics())


@tagged('post_install', '-at_install')
class TestDoctorsOnMyRound(TransactionCase):
    """The Doctors / Clinics cards on My Day: the executive's own routes, their
    own stars, and the button that turns a card into today's visit.
    (client, 2026-09-02)"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Day = cls.env['lab.my.day']
        cls.mine = cls.env['crm.team'].create({'name': 'Doc Mine'})
        cls.other = cls.env['crm.team'].create({'name': 'Doc Other'})
        cls.exec_user = cls.env['res.users'].create({
            'name': 'Doc Exec', 'login': 'doc_exec',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('lab_fieldwork.group_fieldwork_executive').id])]})
        cls.mine.user_id = cls.exec_user.id
        cls.here = cls.env['res.partner'].create({
            'name': 'DR HERE', 'is_clinic': True, 'team_id': cls.mine.id,
            'street': '12 Market Road', 'city': 'Kochi'})
        cls.away = cls.env['res.partner'].create({
            'name': 'DR AWAY', 'is_clinic': True, 'team_id': cls.other.id,
            'city': 'Kollam'})

    def _rows(self, **kw):
        self.env.registry.clear_cache()
        return self.Day.with_user(self.exec_user).get_clinics(**kw)

    # ----------------------------------------------------------------- scope
    def test_only_the_clinics_on_my_own_routes(self):
        data = self._rows()
        ids = [r['id'] for r in data['rows']]
        self.assertIn(self.here.id, ids)
        self.assertNotIn(self.away.id, ids,
                         "another round's doctor is not this executive's list")

    def test_every_clinic_on_the_route_is_listed_not_a_first_page(self):
        """An executive hunting for a doctor must not have to guess whether the
        name is missing or merely past the end of a page. The list stopped at 60
        and said "search to narrow it"; the biggest real route holds 341.
        (client, 2026-09-17)"""
        self.env['res.partner'].create([
            {'name': 'DR BULK %03d' % n, 'is_clinic': True, 'team_id': self.mine.id}
            for n in range(80)])
        data = self._rows()
        self.assertFalse(data['more'], "a route of this size is not truncated")
        self.assertEqual(
            len([r for r in data['rows'] if r['name'].startswith('DR BULK')]), 80,
            "every clinic on the route comes back, not the first page of them")
        self.assertIn(self.here.id, [r['id'] for r in data['rows']])

    def test_a_partner_on_the_route_who_never_ordered_is_listed_too(self):
        """PLKD had 238 partners and Field Work showed 101: only partners flagged
        as clinics were listed, and that flag is set when an order is confirmed,
        so every doctor on the route who had not ordered yet was missing.
        (client, 2026-09-17)"""
        newcomer = self.env['res.partner'].create({
            'name': 'DR NEWCOMER', 'team_id': self.mine.id})
        self.assertFalse(newcomer.is_clinic, "never ordered, so never flagged")
        data = self._rows()
        self.assertIn(newcomer.id, [r['id'] for r in data['rows']])
        on_route = self.env['res.partner'].search_count([('team_id', '=', self.mine.id)])
        self.assertEqual(len(data['rows']), on_route,
                         "the panel shows what the Contacts list shows for the route")
        self.assertTrue(self.Day.with_user(self.exec_user).add_stop(newcomer.id),
                        "and a stop can be added for them")

    def test_a_person_with_no_route_is_told_so(self):
        nobody = self.env['res.users'].create({
            'name': 'Doc Routeless', 'login': 'doc_routeless',
            'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref(
                    'lab_fieldwork.group_fieldwork_executive').id])]})
        data = self.Day.with_user(nobody).get_clinics()
        self.assertTrue(data['no_route'])
        self.assertEqual(data['rows'], [])

    def test_the_card_carries_what_the_card_needs(self):
        row = next(r for r in self._rows()['rows'] if r['id'] == self.here.id)
        for key in ('name', 'address', 'open_receivable', 'open_orders',
                    'favourite', 'map_url', 'route'):
            self.assertIn(key, row)
        self.assertIn('Market Road', row['address'])
        self.assertIn('Kochi', row['address'])

    def test_search_narrows_by_town_as_well_as_name(self):
        self.assertTrue(any(r['id'] == self.here.id
                            for r in self._rows(query='Kochi')['rows']))
        self.assertFalse(self._rows(query='Nowhere-at-all')['rows'])

    # ------------------------------------------------------------ favourites
    def test_a_star_is_personal_and_survives_a_reload(self):
        Fav = self.env['lab.clinic.favourite'].with_user(self.exec_user)
        self.assertTrue(Fav.toggle(self.here.id))
        self.assertIn(self.here.id, Fav.my_partner_ids())
        row = next(r for r in self._rows()['rows'] if r['id'] == self.here.id)
        self.assertTrue(row['favourite'])
        self.assertFalse(Fav.toggle(self.here.id), "tapping again unstars it")
        self.assertNotIn(self.here.id, Fav.my_partner_ids())

    def test_a_double_tap_does_not_explode(self):
        Fav = self.env['lab.clinic.favourite'].with_user(self.exec_user)
        self.assertTrue(Fav.toggle(self.here.id))
        self.assertFalse(Fav.toggle(self.here.id))
        self.assertTrue(Fav.toggle(self.here.id))

    def test_one_executives_stars_are_not_anothers(self):
        mate = self.env['res.users'].create({
            'name': 'Doc Mate', 'login': 'doc_mate',
            'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref(
                    'lab_fieldwork.group_fieldwork_executive').id])]})
        self.mine.write({'member_ids': [(4, mate.id)]})
        self.env['lab.clinic.favourite'].with_user(
            self.exec_user).toggle(self.here.id)
        self.env.registry.clear_cache()
        self.assertEqual(
            self.env['lab.clinic.favourite'].with_user(
                mate).my_partner_ids(), [],
            "a favourites list you did not choose is not a favourites list")

    def test_favourites_come_first(self):
        for name in ('DR AAA', 'DR ZZZ'):
            self.env['res.partner'].create({
                'name': name, 'is_clinic': True, 'team_id': self.mine.id})
        self.env['lab.clinic.favourite'].with_user(
            self.exec_user).toggle(self.here.id)     # 'DR HERE', mid-alphabet
        rows = self._rows()['rows']
        self.assertEqual(rows[0]['id'], self.here.id,
                         "the star outranks the alphabet")
        self.assertTrue(rows[0]['favourite'])
        self.assertFalse(any(r['favourite'] for r in rows[1:]))

    def test_the_favourites_filter_shows_only_those(self):
        self.env['lab.clinic.favourite'].with_user(
            self.exec_user).toggle(self.here.id)
        data = self._rows(favourites_only=True)
        self.assertEqual([r['id'] for r in data['rows']], [self.here.id])
        self.assertEqual(data['favourites'], 1)

    # ---------------------------------------------------------- a new visit
    def test_a_card_becomes_todays_visit(self):
        visit_id = self.Day.with_user(self.exec_user).add_stop(self.here.id)
        visit = self.env['lab.visit'].sudo().browse(visit_id)
        self.assertEqual(visit.partner_id, self.here)
        self.assertEqual(visit.user_id, self.exec_user)
        self.assertEqual(visit.date, fields.Date.context_today(self.exec_user))
        row = next(r for r in self._rows()['rows'] if r['id'] == self.here.id)
        self.assertTrue(row['visited_today'],
                        "the card says it is already on the round")

    def test_pressing_twice_does_not_make_two_visits(self):
        first = self.Day.with_user(self.exec_user).add_stop(self.here.id)
        second = self.Day.with_user(self.exec_user).add_stop(self.here.id)
        self.assertEqual(first, second)

    # ------------------------------------------------------------- the money
    def test_open_receivable_is_the_countback_not_a_residual(self):
        if 'lab.collection.performance' not in self.env:
            self.skipTest('collections is not installed here')
        partners = self.here | self.away
        mine = self.Day.sudo()._clinic_open_money(partners)
        debits = self.env['lab.collection.performance'].sudo()._open_debits(
            self.env.company, partner_ids=partners.ids)
        expected = {}
        for debit in debits:
            expected[debit['partner_id']] = expected.get(
                debit['partner_id'], 0.0) + debit['open']
        self.assertEqual(
            {k: round(v, 2) for k, v in expected.items() if round(v, 2)},
            {k: v for k, v in mine.items() if v})


@tagged('post_install', '-at_install')
class TestLocationIsMandatory(TransactionCase):
    """A visit is refused without a position, at BOTH ends, and the record
    carries where it started and where it finished. (client, 2026-09-05)

    The module's original call was the opposite — record it, flag it 'nofix',
    never block — and the live data settled the argument: day sheets arrived
    with thirty-odd visits whose check-in and check-out fell inside the same
    minute against zero kilometres of travel. That is a day typed at a desk,
    and a record nobody can trust costs more than a missing one.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Fix Clinic', 'is_clinic': True,
            'partner_latitude': LAT, 'partner_longitude': LON})
        cls.user = cls.env['res.users'].create({
            'name': 'Fix Exec', 'login': 'fix_exec',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('lab_fieldwork.group_fieldwork_executive').id])]})

    def _visit(self):
        return self.env['lab.visit'].create({
            'partner_id': self.clinic.id, 'user_id': self.user.id,
            'date': fields.Date.context_today(self.env.user)})

    # ------------------------------------------------------------- the start
    def test_a_good_fix_starts_the_visit(self):
        v = self._visit()
        v.do_check_in(LAT, LON)
        self.assertEqual(v.state, 'open')
        self.assertEqual(v.gps_state, 'ok')

    def test_no_fix_is_refused_with_an_instruction(self):
        v = self._visit()
        with self.assertRaises(UserError) as caught:
            v.do_check_in(False, False)
        self.assertIn('location', str(caught.exception).lower(),
                      "the message must say what to switch on")

    # ------------------------------------------------------------ the finish
    def test_finishing_needs_its_own_fix(self):
        v = self._visit()
        v.do_check_in(LAT, LON)
        v.outcome = 'order'
        with self.assertRaises(UserError):
            v.do_check_out(False, False)
        self.assertEqual(v.state, 'open', "and the visit stays open")

    def test_the_finish_location_is_kept_and_judged(self):
        v = self._visit()
        v.do_check_in(LAT, LON)
        v.outcome = 'order'
        v.do_check_out(LAT, LON)
        self.assertEqual(v.state, 'done')
        self.assertAlmostEqual(v.out_gps_lat, LAT, places=5)
        self.assertAlmostEqual(v.out_gps_lon, LON, places=5)
        self.assertEqual(v.out_gps_state, 'ok')

    def test_a_visit_closed_two_towns_away_is_visible(self):
        """The whole point: same place is a visit, far apart is not, and the
        record has to be able to tell the difference."""
        v = self._visit()
        v.do_check_in(LAT, LON)
        v.outcome = 'order'
        v.do_check_out(LAT + 0.05, LON)          # ~5.5 km
        self.assertEqual(v.gps_state, 'ok', "it started at the door")
        self.assertEqual(v.out_gps_state, 'far', "and ended nowhere near it")
        self.assertGreater(v.out_distance_m, 1000)

    def test_both_ends_are_judged_by_the_same_rule(self):
        far = self._visit()
        far.do_check_in(LAT + 0.05, LON)
        far.outcome = 'followup'
        far.gps_reason = 'other branch'
        far.do_check_out(LAT + 0.05, LON)
        self.assertEqual(far.gps_state, far.out_gps_state,
                         "the same distance must read the same at both ends")
        self.assertAlmostEqual(far.distance_m, far.out_distance_m, places=1)

    # ------------------------------------------------- what the page shows
    def test_the_page_can_open_both_fixes_on_a_map(self):
        v = self._visit()
        v.do_check_in(LAT, LON)
        self.assertTrue(v.gps_map_url.startswith('geo:'))
        self.assertIn(str(round(LAT, 4)), v.gps_map_url)
        self.assertFalse(v.out_gps_map_url, "there is no finish to open yet")
        v.outcome = 'order'
        v.do_check_out(LAT, LON)
        self.assertTrue(v.out_gps_map_url.startswith('geo:'))

    # ------------------------------------------------------- the exception
    def test_the_end_of_day_job_can_still_close_what_was_left_open(self):
        """The cron runs at midnight on a server with no phone in its hand.
        Requiring a fix there would leave every forgotten visit open for ever,
        and its visits are already stamped auto_closed for the supervisor."""
        v = self._visit()
        v.do_check_in(LAT, LON)
        v.outcome = 'absent'
        v.with_context(fw_auto_close=True).do_check_out()
        self.assertEqual(v.state, 'done')
        self.assertTrue(v.auto_closed)
        self.assertEqual(v.out_gps_state, 'nofix',
                         "and it says plainly that nobody was there to be located")


@tagged('post_install', '-at_install')
class TestLocationIsVisibleNotMasked(TransactionCase):
    """A recorded location must read as recorded. (client, 2026-09-08)

    0 of this lab's 2,178 clinics carry coordinates, and _judge_fix tested the
    clinic's pin before the phone's fix — so every visit said "clinic not
    pinned" whether or not a position had been captured, and the lab
    concluded that nothing was being captured at all.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.unpinned = cls.env['res.partner'].create({
            'name': 'Unpinned Clinic', 'is_clinic': True})
        cls.user = cls.env['res.users'].create({
            'name': 'Loc Exec', 'login': 'loc_exec',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('lab_fieldwork.group_fieldwork_executive').id])]})
        cls.boss = cls.env['res.users'].create({
            'name': 'Loc Boss', 'login': 'loc_boss',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('lab_fieldwork.group_fieldwork_manager').id])]})

    def _visit(self, clinic=None):
        return self.env['lab.visit'].create({
            'partner_id': (clinic or self.unpinned).id, 'user_id': self.user.id,
            'date': fields.Date.context_today(self.env.user)})

    # ---------------------------------------------------------- the masking
    def test_a_fix_at_an_unpinned_clinic_reads_as_recorded(self):
        v = self._visit()
        v.do_check_in(LAT, LON)
        self.assertEqual(v.gps_state, 'nopin', "recorded, nothing to compare to")
        self.assertAlmostEqual(v.gps_lat, LAT, places=5)

    def test_no_fix_reads_as_no_fix_even_when_the_clinic_is_unpinned(self):
        """The two must never look the same; before, they did."""
        v = self._visit()
        v.do_check_in(LAT, LON)
        v.sudo().write({'gps_lat': 0.0, 'gps_lon': 0.0})    # legacy row
        self.assertEqual(v.gps_state, 'nofix')

    def test_the_label_says_the_capture_happened(self):
        labels = dict(self.env['lab.visit']._fields['gps_state'].selection)
        self.assertIn('Recorded', labels['nopin'])

    # ----------------------------------------------------------- the report
    def _sheet_html(self, visit):
        sheet = self.env['lab.daily.update'].create({
            'user_id': self.user.id, 'date': visit.date})
        sheet.invalidate_recordset(['timeline_html'])
        return sheet.timeline_html or ''

    def test_the_sheet_shows_both_ends_of_a_visit(self):
        v = self._visit()
        v.do_check_in(LAT, LON)
        v.outcome = 'absent'
        v.do_check_out(LAT + 0.0001, LON)
        html = self._sheet_html(v)
        self.assertEqual(html.count('maps?q='), 2, "a start pin and a finish pin")
        self.assertIn('1 of 1 carry a location', html)

    def test_a_visit_closed_far_away_says_so_in_the_line(self):
        v = self._visit()
        v.do_check_in(LAT, LON)
        v.outcome = 'absent'
        v.do_check_out(LAT + 0.05, LON)          # ~5.5 km
        html = self._sheet_html(v)
        self.assertIn('moved 5.', html)
        self.assertIn('finished away from where they started', html)

    def test_a_visit_with_no_fix_counts_as_unlocated(self):
        v = self._visit()
        v.do_check_in(LAT, LON)
        v.outcome = 'absent'
        v.do_check_out(LAT, LON)
        v.sudo().write({'gps_lat': 0.0, 'gps_lon': 0.0,
                        'out_gps_lat': 0.0, 'out_gps_lon': 0.0})
        html = self._sheet_html(v)
        self.assertIn('0 of 1 carry a location', html)
        self.assertNotIn('maps?q=', html)

    # ---------------------------------------------------------- the pin
    def test_a_manager_pins_the_clinic_from_a_real_visit(self):
        v = self._visit()
        v.do_check_in(LAT, LON)
        self.assertFalse(self.unpinned.is_geolocated)
        v.with_user(self.boss).action_pin_clinic_here()
        self.assertTrue(self.unpinned.is_geolocated)
        self.assertAlmostEqual(self.unpinned.partner_latitude, LAT, places=5)
        v.invalidate_recordset(['gps_state', 'distance_m'])
        self.assertEqual(v.gps_state, 'ok',
                         "the same visit is now measured against the pin")

    def test_an_executive_cannot_pin(self):
        v = self._visit()
        v.do_check_in(LAT, LON)
        with self.assertRaises(UserError):
            v.with_user(self.user).action_pin_clinic_here()

    def test_a_visit_with_no_position_cannot_pin_anything(self):
        v = self._visit()
        v.do_check_in(LAT, LON)
        v.sudo().write({'gps_lat': 0.0, 'gps_lon': 0.0})
        with self.assertRaises(UserError):
            v.with_user(self.boss).action_pin_clinic_here()
        self.assertFalse(self.unpinned.is_geolocated)


class TestCasesCounted(TransactionCase):
    """The executive's own count of cases, typed at the door. (client, 2026-09-08)

    A hand count, distinct from the registered case slips: the slips come
    one by one and often later, the count is one number said at the counter.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Counting Clinic', 'is_clinic': True})
        cls.user = cls.env['res.users'].create({
            'name': 'Count Exec', 'login': 'count_exec',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('lab_fieldwork.group_fieldwork_executive').id])]})

    def _visit(self):
        return self.env['lab.visit'].create({
            'partner_id': self.clinic.id, 'user_id': self.user.id,
            'date': fields.Date.context_today(self.env.user)})

    def _sheet(self, visit):
        sheet = self.env['lab.daily.update'].create({
            'user_id': self.user.id, 'date': visit.date})
        sheet.action_refresh()
        sheet.invalidate_recordset(['timeline_html'])
        return sheet

    def test_the_executive_types_the_count_on_their_own_open_visit(self):
        v = self._visit()
        self.assertEqual(v.cases_counted, 0, "nothing said yet")
        v.do_check_in(LAT, LON)
        v.with_user(self.user).write({'cases_counted': 5})
        self.assertEqual(v.cases_counted, 5)

    def test_a_negative_count_is_refused_by_the_database(self):
        from psycopg2.errors import CheckViolation
        v = self._visit()
        with self.assertRaises(CheckViolation), mute_logger('odoo.sql_db'):
            v.write({'cases_counted': -1})
            self.env.flush_all()

    def test_the_count_survives_finishing_and_is_summed_on_the_sheet(self):
        v = self._visit()
        v.do_check_in(LAT, LON)
        v.write({'cases_counted': 3, 'outcome': 'absent'})
        v.do_check_out(LAT, LON)
        self.assertEqual(v.cases_counted, 3)
        sheet = self._sheet(v)
        self.assertEqual(sheet.cases_counted, 3)
        self.assertIn('3 cases', sheet.timeline_html)

    def test_one_case_is_singular_and_none_is_silent(self):
        v = self._visit()
        v.do_check_in(LAT, LON)
        v.write({'cases_counted': 1, 'outcome': 'absent'})
        v.do_check_out(LAT, LON)
        html = self._sheet(v).timeline_html
        self.assertIn('1 case', html)
        self.assertNotIn('1 cases', html)
        w = self._visit()
        w.do_check_in(LAT, LON)
        w.outcome = 'absent'
        w.do_check_out(LAT, LON)
        w.sudo().write({'date': w.date - timedelta(days=1)})
        self.assertNotIn('cases', self._sheet(w).timeline_html)

    def test_the_executive_gets_the_big_widget_on_the_form(self):
        arch = self.env['lab.visit'].with_user(self.user).get_view(
            view_type='form')['arch']
        self.assertIn('name="cases_counted"', arch)
        self.assertIn('widget="fw_count"', arch)


class TestReworksCollected(TransactionCase):
    """Rework(s) Collected as an outcome, with its count. (client, 2026-09-08)

    The count is subordinate to the chip: shown while it is lit, cleared when
    it goes out, so the day sheet never sums a number nobody can see.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Rework Clinic', 'is_clinic': True})
        cls.user = cls.env['res.users'].create({
            'name': 'Rework Exec', 'login': 'rework_exec',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('lab_fieldwork.group_fieldwork_executive').id])]})
        cls.rework = cls.env.ref('lab_fieldwork.outcome_rework')
        cls.order = cls.env.ref('lab_fieldwork.outcome_order')

    def _visit(self):
        v = self.env['lab.visit'].create({
            'partner_id': self.clinic.id, 'user_id': self.user.id,
            'date': fields.Date.context_today(self.env.user)})
        v.do_check_in(LAT, LON)
        return v

    def _sheet(self, visit):
        sheet = self.env['lab.daily.update'].create({
            'user_id': self.user.id, 'date': visit.date})
        sheet.action_refresh()
        sheet.invalidate_recordset(['timeline_html'])
        return sheet

    def test_the_outcome_exists_on_both_sides(self):
        self.assertEqual(self.rework.code, 'rework')
        self.assertEqual(self.rework.name, 'Rework(s) Collected')
        self.assertIn('rework', dict(self.env['lab.visit']._fields['outcome'].selection))
        self.assertLess(self.rework.sequence, self.env.ref(
            'lab_fieldwork.outcome_delivered').sequence, "right after Case(s) Collected")

    def test_the_chip_lights_the_flag_and_the_count_is_kept(self):
        v = self._visit()
        self.assertFalse(v.has_rework_outcome)
        v.with_user(self.user).write({'outcome_ids': [(4, self.rework.id)]})
        self.assertTrue(v.has_rework_outcome)
        self.assertEqual(v.outcome, 'rework', "the primary follows the only chip")
        v.with_user(self.user).write({'reworks_collected': 2})
        self.assertEqual(v.reworks_collected, 2)
        v.do_check_out(LAT, LON)
        self.assertEqual(v.reworks_collected, 2)

    def test_the_count_dies_with_the_chip(self):
        v = self._visit()
        v.write({'outcome_ids': [(6, 0, [self.order.id, self.rework.id])],
                 'reworks_collected': 3})
        self.assertEqual(v.reworks_collected, 3)
        self.assertEqual(v.outcome, 'order', "Case(s) Collected sorts first")
        v.write({'outcome_ids': [(3, self.rework.id)]})
        self.assertFalse(v.has_rework_outcome)
        self.assertEqual(v.reworks_collected, 0, "cleared, not left to be summed unseen")

    def test_a_count_without_the_chip_is_dropped_on_arrival(self):
        v = self._visit()
        v.write({'reworks_collected': 4})
        self.assertEqual(v.reworks_collected, 0)

    def test_a_negative_count_is_refused(self):
        from psycopg2.errors import CheckViolation
        v = self._visit()
        v.write({'outcome_ids': [(4, self.rework.id)]})
        with self.assertRaises(CheckViolation), mute_logger('odoo.sql_db'):
            v.write({'reworks_collected': -2})
            self.env.flush_all()

    def test_the_sheet_sums_and_says_it(self):
        v = self._visit()
        v.write({'outcome_ids': [(6, 0, [self.order.id, self.rework.id])],
                 'cases_counted': 3, 'reworks_collected': 2})
        v.do_check_out(LAT, LON)
        sheet = self._sheet(v)
        self.assertEqual(sheet.reworks_collected, 2)
        self.assertIn('3 cases · 2 reworks', sheet.timeline_html)
        w = self._visit()
        w.write({'outcome_ids': [(4, self.rework.id)], 'reworks_collected': 1,
                 'date': w.date - timedelta(days=1)})
        w.do_check_out(LAT, LON)
        html = self._sheet(w).timeline_html
        self.assertIn('1 rework', html)
        self.assertNotIn('1 reworks', html)

    def test_the_executive_form_shows_the_count_only_under_the_chip(self):
        arch = self.env['lab.visit'].with_user(self.user).get_view(
            view_type='form')['arch']
        self.assertIn('name="reworks_collected" widget="fw_count"', arch)
        self.assertIn('invisible="not has_rework_outcome"', arch)


class TestDoorCountsOnMyDay(TransactionCase):
    """The executive's widget shows today's hand counts. (client, 2026-09-08)

    Counted over every visit of the day, open ones included: the number is
    typed at the counter and must be seen there, not only after check-out.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Door Count Clinic', 'is_clinic': True})
        cls.user = cls.env['res.users'].create({
            'name': 'Door Exec', 'login': 'door_exec',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('lab_fieldwork.group_fieldwork_executive').id])]})
        cls.rework = cls.env.ref('lab_fieldwork.outcome_rework')

    def _visit(self):
        v = self.env['lab.visit'].create({
            'partner_id': self.clinic.id, 'user_id': self.user.id,
            'date': fields.Date.context_today(self.user)})
        v.do_check_in(LAT, LON)
        return v

    def test_the_widget_sums_todays_hand_counts_open_visits_included(self):
        finished = self._visit()
        finished.write({'outcome_ids': [(4, self.rework.id)],
                        'cases_counted': 3, 'reworks_collected': 1})
        finished.do_check_out(LAT, LON)
        still_open = self._visit()
        still_open.write({'cases_counted': 2})
        summary = self.env['lab.my.day'].with_user(self.user).get_day()['summary']
        self.assertEqual(summary['cases_collected'], 5, "3 finished + 2 at the counter")
        self.assertEqual(summary['reworks_collected'], 1)
        self.assertEqual(summary['cases'], 0, "registered orders are a different number")

    def test_a_day_with_nothing_typed_shows_zeros_not_blanks(self):
        self._visit()
        summary = self.env['lab.my.day'].with_user(self.user).get_day()['summary']
        self.assertEqual((summary['cases_collected'], summary['reworks_collected']), (0, 0))


class TestHasFix(TransactionCase):
    """The one test every fix goes through. (2026-09-09)"""

    def test_none_and_null_island_are_no_fix_and_everything_else_is(self):
        from odoo.addons.lab_fieldwork.models.lab_visit import has_fix, metres_between
        self.assertFalse(has_fix(None, None))
        self.assertFalse(has_fix(0.0, 0.0))
        self.assertFalse(has_fix(None, LON))
        self.assertTrue(has_fix(LAT, LON))
        self.assertTrue(has_fix(0.0, LON), "the equator is a place")
        self.assertIsNone(metres_between(0.0, 0.0, LAT, LON))
        self.assertAlmostEqual(metres_between(LAT, LON, LAT, LON), 0.0)


class TestTrackRefresh(TransactionCase):
    """Refreshing one job on the track panel. (client, 2026-09-09)

    The detail carries the list line as it stands now and the time it was
    read, so the screen can patch the row in place and say "as of".
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.executive = cls.env['res.users'].create({
            'name': 'Track Exec', 'login': 'track_exec',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('lab_fieldwork.group_fieldwork_executive').id,
                cls.env.ref('sales_team.group_sale_salesman').id])]})
        cls.route = cls.env['crm.team'].create({
            'name': 'Track Route', 'user_id': cls.executive.id})
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Track Clinic', 'is_clinic': True, 'team_id': cls.route.id})
        product = cls.env['product.product'].create({
            'name': 'Track Appliance', 'type': 'consu', 'list_price': 400.0})
        cls.order = cls.env['sale.order'].create({
            'partner_id': cls.clinic.id, 'patient': 'Track Patient',
            'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': 1})]})
        cls.order.action_confirm()

    def test_the_detail_carries_the_list_line_and_the_moment_it_was_read(self):
        me = self.env['lab.my.day'].with_user(self.executive)
        rows = me.track_search('Track Patient')['rows']
        self.assertEqual([r['id'] for r in rows], [self.order.id])
        detail = me.track_detail(self.order.id)
        self.assertTrue(detail['ok'])
        self.assertEqual(detail['row'], rows[0], "the same line the search draws")
        self.assertRegex(detail['read_at'], r'^\d\d:\d\d$')

    def test_a_job_off_the_route_stays_refused(self):
        stranger = self.env['res.users'].create({
            'name': 'Other Route', 'login': 'track_other',
            'group_ids': [(6, 0, self.executive.group_ids.ids)]})
        detail = self.env['lab.my.day'].with_user(stranger).track_detail(self.order.id)
        self.assertFalse(detail['ok'])
        self.assertNotIn('row', detail)

    def test_a_carrier_tracks_the_box_in_their_bag_off_their_route(self):
        """Deliveries cross routes, so the box in a carrier's hand is theirs to
        track wherever its clinic is. (client, 2026-09-15)"""
        carrier = self.env['res.users'].create({
            'name': 'Track Carrier', 'login': 'track_carrier',
            'group_ids': [(6, 0, self.executive.group_ids.ids)]})
        me = self.env['lab.my.day'].with_user(carrier)
        self.assertFalse(me.track_detail(self.order.id)['ok'])
        if 'lab.delivery' not in self.env:
            self.assertEqual(me._track_carried_ids(), [],
                             "nothing is carried where deliveries do not exist")
            return
        self.env['lab.delivery'].create({
            'sale_order_id': self.order.id, 'executive_id': carrier.id,
            'scheduled_date': fields.Datetime.now(), 'state': 'assigned'})
        rows = me.track_search('Track Patient')['rows']
        self.assertIn(self.order.id, [r['id'] for r in rows])
        self.assertTrue(me.track_detail(self.order.id)['ok'])

    def test_the_panel_works_where_the_delivery_module_is_absent(self):
        """The guard used to call the very model it guards against. (2026-09-09)

        On a lab with no lab_delivery there is no delivery_ids field AND no
        lab.delivery model, so the fallback recordset could not be built and
        both the list and the detail raised KeyError.
        """
        me = self.env['lab.my.day'].with_user(self.executive)
        with patch.object(type(self.env['lab.my.day']), '_track_deliveries',
                          lambda self, order: None):
            rows = me.track_search('Track Patient')['rows']
            self.assertEqual([r['id'] for r in rows], [self.order.id])
            self.assertTrue(rows[0]['stage'], "a stage is still reported")
            detail = me.track_detail(self.order.id)
            self.assertTrue(detail['ok'])
            self.assertTrue(detail['steps'], "the journey still has steps")

    def test_the_panel_names_who_made_it_and_who_finished_it(self):
        """Two separate fields, two separate people. (client, 2026-09-09)"""
        maker = self.env['res.users'].create({
            'name': 'Bench Maker', 'login': 'track_maker'})
        finisher = self.env['res.users'].create({
            'name': 'Bench Finisher', 'login': 'track_finisher'})
        product = self.env['product.product'].create({
            'name': 'Track Made Thing', 'is_storable': True})
        mo = self.env['mrp.production'].create({
            'product_id': product.id, 'product_qty': 1.0})
        mo.write({'sale_id': self.order.id} if 'sale_id' in mo._fields else {})
        wc = self.env['mrp.workcenter'].create({'name': 'Track Bench'})
        wo = self.env['mrp.workorder'].create({
            'name': 'Polish', 'production_id': mo.id, 'workcenter_id': wc.id,
            'product_uom_id': product.uom_id.id})
        wo.write({'bench_user_id': maker.id, 'finisher_user_id': finisher.id})

        people = self.env['lab.my.day']._track_people(mo)
        self.assertEqual(len(people), 1)
        self.assertEqual(people[0]['technician'], 'Bench Maker')
        self.assertEqual(people[0]['finisher'], 'Bench Finisher')
        self.assertEqual(people[0]['operation'], 'Polish')

    def test_an_operation_nobody_has_touched_is_not_listed(self):
        product = self.env['product.product'].create({
            'name': 'Untouched Thing', 'is_storable': True})
        mo = self.env['mrp.production'].create({
            'product_id': product.id, 'product_qty': 1.0})
        wc = self.env['mrp.workcenter'].create({'name': 'Empty Bench'})
        self.env['mrp.workorder'].create({
            'name': 'Nobody', 'production_id': mo.id, 'workcenter_id': wc.id,
            'product_uom_id': product.uom_id.id})
        self.assertEqual(self.env['lab.my.day']._track_people(mo), [],
                         "a step with no name says nothing")

    def test_the_names_survive_a_lab_without_manufacturing(self):
        """This module does not depend on mrp, and must not reach for it."""
        me = self.env['lab.my.day']
        with patch.object(type(me), '_track_productions', lambda self, order: None):
            detail = me.with_user(self.executive).track_detail(self.order.id)
            self.assertTrue(detail['ok'])
            self.assertEqual(detail['people'], [])
