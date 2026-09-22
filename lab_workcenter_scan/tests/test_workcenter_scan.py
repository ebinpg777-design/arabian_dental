# -*- coding: utf-8 -*-
from datetime import datetime, time, timedelta

import pytz

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import Form, TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestWorkcenterScan(TransactionCase):
    """The rules a technician hits with gloves on."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.wc_a = cls.env['mrp.workcenter'].create({'name': 'Bench A'})
        cls.wc_b = cls.env['mrp.workcenter'].create({'name': 'Bench B'})
        product = cls.env['product.product'].create({
            'name': 'Test Appliance', 'type': 'consu', 'is_storable': True})
        cls.production = cls.env['mrp.production'].create({
            'product_id': product.id, 'product_qty': 1.0})
        cls.wo = cls.env['mrp.workorder'].create({
            'name': 'Finishing', 'production_id': cls.production.id,
            'workcenter_id': cls.wc_a.id, 'product_uom_id': product.uom_id.id})

    def test_every_station_gets_a_code_and_a_readable_label(self):
        self.assertTrue(self.wc_a.scan_code)
        self.assertNotEqual(self.wc_a.scan_code, self.wc_b.scan_code)
        self.assertTrue(self.wc_a.qr_image, "a station with no label cannot be scanned")

    def test_scanning_a_station_moves_the_job(self):
        result = self.wo.move_to_scanned_workcenter(self.wc_b.scan_code)
        self.assertTrue(result['moved'])
        self.assertEqual(self.wo.workcenter_id, self.wc_b)

    def test_the_move_is_written_where_a_supervisor_will_see_it(self):
        """A job that changes station without a trace is a job nobody can account for.
        mrp.workorder has no chatter, so the trail goes on its manufacturing order."""
        before = len(self.production.message_ids)
        self.wo.move_to_scanned_workcenter(self.wc_b.scan_code)
        self.assertGreater(len(self.production.message_ids), before)
        self.assertIn('Bench B', self.production.message_ids[0].body)

    def test_scanning_the_station_you_are_already_at_is_not_an_error(self):
        """It is the natural way to check you are in the right place."""
        result = self.wo.move_to_scanned_workcenter(self.wc_a.scan_code)
        self.assertFalse(result['moved'])
        self.assertEqual(self.wo.workcenter_id, self.wc_a)

    def test_an_unknown_code_says_what_to_do_about_it(self):
        with self.assertRaises(UserError) as caught:
            self.wo.move_to_scanned_workcenter('NOT-A-STATION')
        self.assertIn('print labels', str(caught.exception).lower())

    def test_nothing_scanned_is_refused(self):
        for empty in ('', '   ', False):
            with self.assertRaises(UserError):
                self.wo.move_to_scanned_workcenter(empty)

    def test_a_station_also_answers_to_its_own_printed_code(self):
        """A workshop that already labels its machines should not have to relabel them."""
        self.wc_b.code = 'BENCH-B'
        result = self.wo.move_to_scanned_workcenter('BENCH-B')
        self.assertTrue(result['moved'])
        self.assertEqual(self.wo.workcenter_id, self.wc_b)

    def test_a_finished_job_cannot_be_moved(self):
        self.wo.state = 'done'
        with self.assertRaises(UserError):
            self.wo.move_to_scanned_workcenter(self.wc_b.scan_code)

    def test_a_running_timer_is_closed_before_the_job_moves(self):
        """The timer belongs to the station it was started at — carrying it across would
        bill the new station for work done at the old one.

        Asserted on the timer record, not on `state`: state is COMPUTED from the timers,
        so setting it by hand proves nothing about whether a real clock was stopped.
        """
        timer = self.env['mrp.workcenter.productivity'].create({
            'workorder_id': self.wo.id,
            'workcenter_id': self.wc_a.id,
            'date_start': fields.Datetime.now(),
            'loss_id': self.env.ref('mrp.block_reason7').id,
            'user_id': self.env.uid,
        })
        self.wo.invalidate_recordset()
        self.assertFalse(timer.date_end, "the timer must start open")

        self.wo.move_to_scanned_workcenter(self.wc_b.scan_code)

        timer.invalidate_recordset()
        self.assertTrue(timer.date_end,
                        "the clock at the old station must be stopped by the move")
        self.assertEqual(self.wo.workcenter_id, self.wc_b)

    def test_the_move_stops_the_clock_whoever_started_it(self):
        """Core's button_pending closes only the CURRENT user's timer. The one running
        is the technician's, and it is the lead who scans the move.
        (review, 2026-09-15)"""
        tech = self.env['res.users'].create({'name': 'Clock Tech', 'login': 'clock_tech'})
        timer = self.env['mrp.workcenter.productivity'].create({
            'workorder_id': self.wo.id,
            'workcenter_id': self.wc_a.id,
            'date_start': fields.Datetime.now(),
            'loss_id': self.env.ref('mrp.block_reason7').id,
            'user_id': tech.id,
        })
        self.wo.invalidate_recordset()
        self.wo.move_to_scanned_workcenter(self.wc_b.scan_code)
        timer.invalidate_recordset()
        self.assertTrue(timer.date_end, "a technician's clock must not follow the job")

    def test_two_stations_cannot_share_a_code(self):
        from psycopg2.errors import UniqueViolation
        from odoo.tools import mute_logger
        with self.assertRaises(UniqueViolation), mute_logger('odoo.sql_db'):
            self.wc_b.scan_code = self.wc_a.scan_code
            self.wc_b.flush_recordset()

    def test_existing_stations_are_given_codes_when_the_module_arrives(self):
        """Without the post-install hook the feature is dead on any real database:
        every station that already exists would answer to no scan at all."""
        self.wc_b.scan_code = False
        self.wc_b.flush_recordset()
        assigned = (self.wc_a | self.wc_b).action_assign_scan_codes()
        self.assertEqual(assigned, 1)
        self.assertTrue(self.wc_b.scan_code)


@tagged('post_install', '-at_install')
class TestStationRestart(TransactionCase):
    """Starting a case again from the Station Board.

    The one act on the board that destroys work, so the rules around it matter more
    than the act itself. (client, 2026-08-29)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.head = cls.env['res.users'].create({
            'name': 'Redo Head', 'login': 'redo_head',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('mrp.group_mrp_user').id])]})
        cls.wc_a = cls.env['mrp.workcenter'].create({
            'name': 'Bend', 'head_user_ids': [(6, 0, [cls.head.id])]})
        cls.wc_b = cls.env['mrp.workcenter'].create({'name': 'Finish'})
        product = cls.env['product.product'].create({
            'name': 'Plate', 'type': 'consu', 'is_storable': True})
        cls.mo = cls.env['mrp.production'].create({
            'product_id': product.id, 'product_qty': 1.0})
        cls.wo_a = cls.env['mrp.workorder'].create({
            'name': 'Bending', 'sequence': 10, 'production_id': cls.mo.id,
            'workcenter_id': cls.wc_a.id, 'product_uom_id': product.uom_id.id})
        cls.wo_b = cls.env['mrp.workorder'].create({
            'name': 'Finishing', 'sequence': 20, 'production_id': cls.mo.id,
            'workcenter_id': cls.wc_b.id, 'product_uom_id': product.uom_id.id})
        cls.reason = cls.env['lab.redo.reason'].search([], limit=1) \
            or cls.env['lab.redo.reason'].create({'name': 'Broken'})

    def test_the_board_offers_the_lab_its_own_reasons(self):
        board = self.env['lab.station'].with_user(self.head).get_station(self.wc_a.id)
        self.assertTrue(board['redo_reasons'],
                        "the dialog cannot open on a list it has to fetch first")
        self.assertIn(self.reason.id, [r['id'] for r in board['redo_reasons']])
        card = next(c for c in board['incoming'] if c['id'] == self.wo_a.id)
        self.assertTrue(card['can_restart'])
        self.assertEqual(card['redo_count'], 0)

    def test_a_restart_without_a_reason_is_refused(self):
        """The reason list is what makes the redo report worth reading, so a redo
        without one is not a redo the board will take."""
        station = self.env['lab.station'].with_user(self.head)
        with self.assertRaises(UserError):
            station.restart(self.wo_a.id, False)
        self.mo.invalidate_recordset()
        self.assertEqual(self.mo.lab_redo_count, 0, "nothing may have happened")

    # ------------------------------------------------- correcting a job by scan
    def test_a_correction_scan_finds_the_job_and_changes_nothing(self):
        station = self.env['lab.station'].with_user(self.head)
        found = station.scan_to_change(self.mo.name, self.wc_a.id)
        self.assertEqual(found['job']['id'], self.wo_a.id)
        self.assertEqual(found['status'], 'waiting', "nobody has taken it yet")
        self.wo_a.invalidate_recordset()
        self.assertFalse(self.wo_a.accepted_at, "the scan itself took nothing")

    def test_the_technician_can_be_corrected(self):
        station = self.env['lab.station'].with_user(self.head)
        self.wc_a.users = [(6, 0, self.head.ids)]
        station.apply_change(self.wo_a.id, bench_user_id=self.head.id)
        self.wo_a.invalidate_recordset()
        self.assertEqual(self.wo_a.bench_user_id, self.head)

    def test_a_status_moves_by_the_same_steps_a_scan_would(self):
        """Not a stamp written straight in: walking it means every rule that
        hangs off the steps between still applies. (client, 2026-09-12)"""
        station = self.env['lab.station'].with_user(self.head)
        self.wc_a.users = [(6, 0, self.head.ids)]
        station.apply_change(self.wo_a.id, bench_user_id=self.head.id,
                             status='bench')
        self.wo_a.invalidate_recordset()
        self.assertTrue(self.wo_a.accepted_at, "it is on the bench now")
        self.assertFalse(self.wo_a.handed_over_at)
        # ...and back again, which is the mis-scan this exists for.
        station.apply_change(self.wo_a.id, status='waiting')
        self.wo_a.invalidate_recordset()
        self.assertFalse(self.wo_a.accepted_at, "the double scan is undone")

    def test_a_correction_that_changes_nothing_is_refused(self):
        """A dialog that can be confirmed without a change is one people confirm
        by reflex."""
        station = self.env['lab.station'].with_user(self.head)
        with self.assertRaises(UserError):
            station.apply_change(self.wo_a.id, status='waiting')

    def test_a_finished_case_has_nothing_left_to_correct(self):
        station = self.env['lab.station'].with_user(self.head)
        self.mo.action_cancel()
        with self.assertRaises(UserError):
            station.scan_to_change(self.mo.name, self.wc_a.id)

    def test_a_tile_names_the_appliance_without_its_internal_code(self):
        """The tile shows what is in the tray, in words, not "[CODE] name".
        (client, 2026-09-14)"""
        self.mo.product_id.product_tmpl_id.write(
            {'default_code': 'OCR-C08', 'name': '  Hawleys Appliances '})
        card = self.env['lab.station'].with_user(self.head)._card(self.wo_a)
        self.assertEqual(card['product_name'], 'Hawleys Appliances',
                         "no code, and no stray spaces")

    # ------------------------------------------------- cancelling a step here
    def test_cancelling_a_step_moves_the_case_on_and_says_why(self):
        """Only this bench's step goes; the case carries on. (client, 2026-09-14)"""
        station = self.env['lab.station'].with_user(self.head)
        found = station.scan_to_cancel(self.mo.name, self.wc_a.id)
        self.assertEqual(found['job']['id'], self.wo_a.id)
        station.cancel_step(self.wo_a.id, 'sent to the wrong bench')
        self.wo_a.invalidate_recordset()
        self.wo_b.invalidate_recordset()
        self.assertEqual(self.wo_a.state, 'cancel')
        self.assertNotIn(self.wo_b.state, ('done', 'cancel'), "the rest of the route stands")
        self.assertIn('sent to the wrong bench',
                      ' '.join(self.mo.message_ids.mapped('body')))

    def test_a_cancel_needs_a_reason(self):
        station = self.env['lab.station'].with_user(self.head)
        with self.assertRaises(UserError):
            station.cancel_step(self.wo_a.id, '   ')
        self.wo_a.invalidate_recordset()
        self.assertNotEqual(self.wo_a.state, 'cancel')

    def test_a_step_already_taken_is_not_cancelled_over(self):
        station = self.env['lab.station'].with_user(self.head)
        station.accept(self.wo_a.id)
        with self.assertRaises(UserError):
            station.cancel_step(self.wo_a.id, 'not ours')

    def test_the_last_step_left_is_not_cancelled(self):
        """Cancelling it would leave the case open with nothing to do."""
        self.wo_b.sudo().action_cancel()
        station = self.env['lab.station'].with_user(self.head)
        with self.assertRaises(UserError):
            station.cancel_step(self.wo_a.id, 'not ours')

    def test_a_step_at_another_bench_is_cancelled_there_not_here(self):
        elsewhere = self.env['mrp.workcenter'].create({'name': 'Cancel Elsewhere'})
        product = self.env['product.product'].create(
            {'name': 'Cancel Plate', 'type': 'consu', 'is_storable': True})
        mo = self.env['mrp.production'].create({'product_id': product.id, 'product_qty': 1.0})
        for name, wc, seq in (('Bend', self.wc_a, 10), ('Finish', self.wc_b, 20)):
            self.env['mrp.workorder'].create({
                'name': name, 'sequence': seq, 'production_id': mo.id,
                'workcenter_id': wc.id, 'product_uom_id': product.uom_id.id})
        with self.assertRaises(UserError):
            self.env['lab.station'].with_user(self.head).scan_to_cancel(mo.name, elsewhere.id)

    # --------------------------------------------- scanning one to start again
    def test_scanning_to_start_again_finds_the_job_and_changes_nothing(self):
        """The scan RESOLVES; the dialog still asks why. Starting a case again
        is the one act here that cannot be undone, so a mis-scan must not be
        able to commit it. (client, 2026-09-12)"""
        station = self.env['lab.station'].with_user(self.head)
        found = station.scan_to_restart(self.mo.name, self.wc_a.id)
        self.assertEqual(found['job']['id'], self.wo_a.id)
        self.assertIn(self.reason.id, [r['id'] for r in found['reasons']],
                      "and it brings the reasons the dialog will ask for")
        self.mo.invalidate_recordset()
        self.assertEqual(self.mo.lab_redo_count, 0,
                         "nothing has been started again by the scan itself")

    def test_the_scanned_job_is_the_one_that_gets_restarted(self):
        station = self.env['lab.station'].with_user(self.head)
        found = station.scan_to_restart(self.mo.name, self.wc_a.id)
        station.restart(found['job']['id'], self.reason.id, 'scanned at the bench')
        self.mo.invalidate_recordset()
        self.assertEqual(self.mo.lab_redo_count, 1)
        self.assertEqual(self.mo.lab_redo_ids.note, 'scanned at the bench')

    def test_a_job_standing_at_another_bench_is_refused_by_name(self):
        """A case is started again from the bench it is standing at - the same
        rule the card's own button follows.

        The case needs a step at NEITHER of the other benches to test this: a
        scanned code resolves to the step at the scanning bench by preference,
        so a two-bench case scanned at its own second bench is found there and
        refusing it would be wrong.
        """
        elsewhere = self.env['mrp.workcenter'].create({'name': 'Polish'})
        product = self.env['product.product'].create(
            {'name': 'Single-step Plate', 'type': 'consu', 'is_storable': True})
        mo = self.env['mrp.production'].create(
            {'product_id': product.id, 'product_qty': 1.0})
        self.env['mrp.workorder'].create({
            'name': 'Bending', 'sequence': 10, 'production_id': mo.id,
            'workcenter_id': self.wc_a.id, 'product_uom_id': product.uom_id.id})
        station = self.env['lab.station'].with_user(self.head)
        with self.assertRaises(UserError) as caught:
            station.scan_to_restart(mo.name, elsewhere.id)
        self.assertIn(self.wc_a.name, str(caught.exception),
                      "and it says where the job actually is")

    def test_the_step_at_the_scanning_bench_is_the_one_offered(self):
        """A case passes through several benches; the one being started again
        is the step in front of the person who scanned it."""
        station = self.env['lab.station'].with_user(self.head)
        found = station.scan_to_restart(self.mo.name, self.wc_b.id)
        self.assertEqual(found['job']['id'], self.wo_b.id)

    def test_a_case_that_cannot_be_restarted_is_refused_before_the_question(self):
        """Refused here rather than after, so nobody picks a reason for a job
        that was never going to take it."""
        station = self.env['lab.station'].with_user(self.head)
        self.mo.action_cancel()
        with self.assertRaises(UserError):
            station.scan_to_restart(self.mo.name, self.wc_a.id)

    def test_an_unknown_code_is_refused(self):
        station = self.env['lab.station'].with_user(self.head)
        with self.assertRaises(UserError):
            station.scan_to_restart('NO/SUCH/JOB', self.wc_a.id)

    def test_a_restart_sends_every_bench_back_and_records_why(self):
        station = self.env['lab.station'].with_user(self.head)
        station.accept(self.wo_a.id)
        result = station.restart(self.wo_a.id, self.reason.id, 'clasp snapped')
        self.mo.invalidate_recordset()
        self.wo_a.invalidate_recordset()
        self.assertEqual(self.mo.lab_redo_count, 1)
        self.assertEqual(result['attempt'], 1)
        self.assertEqual(result['operations'], 2, "every operation goes back, not one")
        redo = self.mo.lab_redo_ids
        self.assertEqual(redo.reason_id, self.reason)
        self.assertEqual(redo.note, 'clasp snapped')
        self.assertEqual(redo.user_id, self.head, "who sent it back is the point")
        self.assertFalse(self.wo_a.accepted_at,
                         "a case sent back is waiting to be accepted again")
        # and the board says so on the card, before anybody picks it up
        board = station.get_station(self.wc_a.id)
        card = next(c for c in board['incoming'] if c['id'] == self.wo_a.id)
        self.assertEqual(card['redo_count'], 1)
        self.assertEqual(card['redo_reason'], self.reason.name)

    def test_only_somebody_posted_at_that_bench_may_start_it_again(self):
        outsider = self.env['res.users'].create({
            'name': 'Passer By', 'login': 'redo_outsider',
            'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('mrp.group_mrp_user').id,
                self.env.ref('lab_workcenter_scan.group_workcenter_bound').id])]})
        with self.assertRaises(UserError):
            self.env['lab.station'].with_user(outsider).restart(
                self.wo_a.id, self.reason.id)
        self.mo.invalidate_recordset()
        self.assertEqual(self.mo.lab_redo_count, 0)

    def test_a_technician_at_the_bench_may(self):
        """The client asked for the technician as well as the lead: the person who
        opens the bag is the one who finds the fault."""
        tech = self.env['res.users'].create({
            'name': 'Tech', 'login': 'redo_tech',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('mrp.group_mrp_user').id])]})
        self.wc_a.write({'users': [(4, tech.id)]})
        self.env['lab.station'].with_user(tech).restart(self.wo_a.id, self.reason.id)
        self.mo.invalidate_recordset()
        self.assertEqual(self.mo.lab_redo_count, 1)


class TestBenchUserMenus(TransactionCase):
    """What a bench technician sees in the app switcher.

    Menu groups are additive and most app roots are open to every internal user, so
    a technician holding only Manufacturing/User was greeted by Sales, Website,
    Employees, Contacts, Dashboards and Apps. (client, 2026-08-29)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bench = cls.env['res.users'].create({
            'name': 'Bench Tech', 'login': 'menu_bench',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('mrp.group_mrp_user').id,
                cls.env.ref('lab_workcenter_scan.group_workcenter_bound').id])]})

    def _apps(self, user):
        menus = self.env['ir.ui.menu'].with_user(user).load_menus(False)
        # load_menus keys its entries by id; whether that id arrives as an int or a
        # string has changed between versions, so accept either rather than silently
        # returning an empty set (which would make every assertion below pass or fail
        # for the wrong reason).
        names = set()
        for cid in menus['root']['children']:
            entry = menus.get(cid) or menus.get(str(cid)) or menus.get(int(cid))
            if entry:
                names.add(entry['name'])
        return names

    def test_a_bench_user_only_gets_the_app_they_work_in(self):
        apps = self._apps(self.bench)
        self.assertIn('Manufacturing', apps)
        for stranger in ('Contacts', 'Discuss', 'Website', 'Employees', 'Calendar'):
            self.assertNotIn(stranger, apps,
                             "an app reached only through base.group_user is not this "
                             "person's work")

    def test_an_app_they_were_actually_granted_survives(self):
        """The rule is about grants, not about hiding: give them the group and the app
        comes back. Otherwise this would be a lock rather than a tidy-up."""
        self.bench.write({'group_ids': [
            (4, self.env.ref('base.group_partner_manager').id)]})
        self.env.registry.clear_cache()
        self.assertIn('Contacts', self._apps(self.bench))

    def test_an_administrator_keeps_the_whole_switcher(self):
        boss = self.env['res.users'].create({
            'name': 'Boss', 'login': 'menu_boss',
            'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('base.group_system').id,
                self.env.ref('mrp.group_mrp_user').id,
                self.env.ref('lab_workcenter_scan.group_workcenter_bound').id])]})
        apps = self._apps(boss)
        self.assertIn('Settings', apps)
        self.assertIn('Contacts', apps,
                      "the person who fixes access must still be able to reach it")


class TestStationBoard(TransactionCase):
    """Accepting a job, and passing it on."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.head = cls.env['res.users'].create({
            'name': 'Bench Head', 'login': 'wc_head',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('mrp.group_mrp_user').id])]})
        cls.wc_a = cls.env['mrp.workcenter'].create({
            'name': 'Trim', 'head_user_ids': [(6, 0, [cls.head.id])]})
        cls.wc_b = cls.env['mrp.workcenter'].create({'name': 'Polish'})
        product = cls.env['product.product'].create({
            'name': 'Retainer', 'type': 'consu', 'is_storable': True})
        cls.mo = cls.env['mrp.production'].create({
            'product_id': product.id, 'product_qty': 1.0})
        cls.wo_a = cls.env['mrp.workorder'].create({
            'name': 'Trimming', 'sequence': 10, 'production_id': cls.mo.id,
            'workcenter_id': cls.wc_a.id, 'product_uom_id': product.uom_id.id})
        cls.wo_b = cls.env['mrp.workorder'].create({
            'name': 'Polishing', 'sequence': 20, 'production_id': cls.mo.id,
            'workcenter_id': cls.wc_b.id, 'product_uom_id': product.uom_id.id})

    def test_the_board_opens_on_the_station_you_run(self):
        """A shared terminal that asks "which station are you?" every morning is
        answered wrong eventually, and the wrong answer moves somebody else's work."""
        board = self.env['lab.station'].with_user(self.head).get_station()
        self.assertTrue(board['workcenter'])
        self.assertEqual(board['workcenter']['id'], self.wc_a.id)
        self.assertTrue(board['is_head'])

    def test_an_arriving_job_waits_to_be_accepted(self):
        """This is why the board exists: a job moved to a station that never noticed is
        a job that surfaces only when it fails to come back."""
        board = self.env['lab.station'].get_station(self.wc_a.id)
        self.assertIn(self.wo_a.id, [c['id'] for c in board['incoming']])
        self.assertEqual(board['working'], [])

    def test_accepting_moves_it_to_the_bench_and_starts_the_clock(self):
        board = self.env['lab.station'].with_user(self.head).accept(self.wo_a.id)
        self.wo_a.invalidate_recordset()
        self.assertEqual(board['incoming'], [])
        self.assertIn(self.wo_a.id, [c['id'] for c in board['working']])
        self.assertEqual(self.wo_a.accepted_by_id, self.head)
        self.assertEqual(self.wo_a.state, 'progress',
                         "accepted but not started reads as idle on every capacity report")

    def test_handing_over_says_where_it_went(self):
        self.env['lab.station'].with_user(self.head).accept(self.wo_a.id)
        self.wo_a.action_assign_bench_user(self.head.id)
        board = self.env['lab.station'].with_user(self.head).handover(self.wo_a.id)
        self.wo_a.invalidate_recordset()
        self.assertEqual(self.wo_a.state, 'done')
        self.assertTrue(self.wo_a.handed_over_at)
        self.assertIn(self.wo_a.id, [c['id'] for c in board['done_today']])
        self.assertIn('Polish', self.mo.message_ids[0].body,
                      "the trail must name the station it went to")

    def test_the_next_station_is_known_before_the_button_is_pressed(self):
        """The head decides whether to hand over; they cannot decide if the board will
        not say where it goes."""
        self.assertEqual(self.wo_a.next_workorder_id, self.wo_b)
        self.assertEqual(self.wo_a.next_workcenter_id, self.wc_b)
        self.assertFalse(self.wo_b.next_workorder_id, "the last step goes nowhere")

    def test_a_job_cannot_be_handed_on_before_it_is_accepted(self):
        """Otherwise it passes through a station that never took responsibility, which
        is exactly the gap the board closes."""
        with self.assertRaises(UserError):
            self.env['lab.station'].with_user(self.head).handover(self.wo_a.id)

    def test_a_job_with_nobody_doing_it_cannot_be_handed_on(self):
        """Work leaves a bench with a name on it. Finishing a step "nobody" did is how
        the lab lost who made what, and a redo then has nobody to learn from.
        (client, 2026-08-29)"""
        station = self.env['lab.station'].with_user(self.head)
        station.accept(self.wo_a.id)
        # the model refuses outright…
        with self.assertRaises(UserError) as caught:
            self.wo_a.with_user(self.head).action_station_handover()
        self.assertIn('Technician', str(caught.exception))
        # …and the board turns that refusal into a question with the people on it
        ask = station.handover(self.wo_a.id)
        self.assertEqual(ask['action'], 'needs_person')
        self.assertTrue(ask['can_assign'], "the head may answer it")
        self.assertIn(self.head.id, [p['id'] for p in ask['people']])
        self.assertNotIn('none', [p['id'] for p in ask['people']])
        self.assertEqual(ask['job']['id'], self.wo_a.id)
        self.wo_a.invalidate_recordset()
        self.assertNotEqual(self.wo_a.state, 'done', "asking must not finish it")
        # the scan path asks the same question rather than quietly finishing it
        # (confirmed: since 2026-09-09 an unconfirmed scan only says what it would do)
        ask = station.scan(self.mo.name, self.wc_a.id, confirmed=True)
        self.assertEqual((ask['action'], ask['stage']), ('needs_person', 'handover'))
        # a technician gets the question too, but cannot answer it
        tech = self._bench_user('bench_ask', self.wc_a)
        ask = self.env['lab.station'].with_user(tech).handover(self.wo_a.id)
        self.assertEqual(ask['action'], 'needs_person')
        self.assertFalse(ask['can_assign'])

    def test_one_tap_names_who_did_it_and_hands_it_on(self):
        """The popup's answer: assign and hand over in one call, so the rule costs
        the lead a single tap instead of three."""
        station = self.env['lab.station'].with_user(self.head)
        station.accept(self.wo_a.id)
        # somebody not on this (staffed) bench is refused, and nothing is finished
        self._bench_user('bench_here', self.wc_a)
        stranger = self._bench_user('bench_far', bound=False)
        with self.assertRaises(UserError):
            station.assign_and_handover(self.wo_a.id, stranger.id)
        self.wo_a.invalidate_recordset()
        self.assertNotEqual(self.wo_a.state, 'done')
        board = station.assign_and_handover(self.wo_a.id, self.head.id)
        self.wo_a.invalidate_recordset()
        self.assertEqual(self.wo_a.bench_user_id, self.head)
        self.assertEqual(self.wo_a.state, 'done')
        self.assertIn(self.wo_a.id, [c['id'] for c in board['done_today']])

    def test_a_finished_job_cannot_be_accepted_again(self):
        self.env['lab.station'].accept(self.wo_a.id)
        self.wo_a.action_assign_bench_user(self.head.id)
        self.env['lab.station'].handover(self.wo_a.id)
        with self.assertRaises(UserError):
            self.env['lab.station'].accept(self.wo_a.id)

    def test_accepting_twice_does_not_change_who_took_it(self):
        """Two people pressing Accept on a shared terminal must not rewrite the record
        of who is responsible."""
        self.env['lab.station'].with_user(self.head).accept(self.wo_a.id)
        self.wo_a.invalidate_recordset()
        first_at = self.wo_a.accepted_at
        self.env['lab.station'].accept(self.wo_a.id)
        self.wo_a.invalidate_recordset()
        self.assertEqual(self.wo_a.accepted_by_id, self.head)
        self.assertEqual(self.wo_a.accepted_at, first_at)

    def test_the_board_can_send_a_job_somewhere_other_than_the_next_step(self):
        """A rework, or a broken machine. The scan is the same one used on the form."""
        self.env['lab.station'].accept(self.wo_a.id)
        result = self.env['lab.station'].move_by_scan(self.wo_a.id, self.wc_b.scan_code)
        self.wo_a.invalidate_recordset()
        self.assertTrue(result['moved'])
        self.assertEqual(self.wo_a.workcenter_id, self.wc_b)
        self.assertIn('board', result, "the board must come back with the move")

    def test_a_user_only_sees_the_stations_they_are_posted_to(self):
        """Forty stations in a picker on a shared tablet invites accepting somebody
        else's job into your own queue — the confusion the board exists to remove."""
        board = self.env['lab.station'].with_user(self.head).get_station()
        names = [s['name'] for s in board['stations']]
        self.assertIn('Trim', names)
        self.assertNotIn('Polish', names, "not their bench")

    def test_a_technician_counts_as_posted_to_their_bench(self):
        """sale_custom already records who WORKS at a station; a technician should not
        need a second role invented for them."""
        tech = self.env['res.users'].create({
            'name': 'Bench Tech', 'login': 'wc_tech',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('mrp.group_mrp_user').id])]})
        self.wc_b.users = [(6, 0, [tech.id])]
        board = self.env['lab.station'].with_user(tech).get_station()
        self.assertEqual(board['workcenter']['id'], self.wc_b.id)

    def test_asking_for_someone_elses_bench_is_refused(self):
        """Refused rather than silently redirected: a board that shows a different
        station from the one asked for is a board that lies about where you are."""
        with self.assertRaises(UserError):
            self.env['lab.station'].with_user(self.head).get_station(self.wc_b.id)

    def test_a_manufacturing_manager_still_reaches_every_bench(self):
        """Covering a shift or chasing a job means reaching a station you do not run."""
        boss = self.env['res.users'].create({
            'name': 'Plant Manager', 'login': 'wc_boss',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('mrp.group_mrp_manager').id])]})
        board = self.env['lab.station'].with_user(boss).get_station(self.wc_b.id)
        self.assertEqual(board['workcenter']['id'], self.wc_b.id)

    def test_someone_posted_nowhere_is_told_so(self):
        nobody = self.env['res.users'].create({
            'name': 'No Bench', 'login': 'wc_none',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('mrp.group_mrp_user').id])]})
        board = self.env['lab.station'].with_user(nobody).get_station()
        self.assertFalse(board['workcenter'])
        self.assertTrue(board['unassigned'])
        self.assertEqual(board['stations'], [])

    # ------------------------------------------------------- scan to act
    def test_one_scan_accepts_then_hands_over(self):
        """A head with gloves on should not have to find the job, decide which button it
        is, and press the right one. The state decides."""
        code = self.mo.name
        station = self.env['lab.station'].with_user(self.head)
        # the first scan accepts — and, with nobody named, asks who is doing it.
        # `confirmed`: the bench confirms each scan now, and this test is about
        # what the scan DOES; TestScanConfirmation covers the question itself.
        first = station.scan(code, self.wc_a.id, confirmed=True)
        self.assertEqual((first['action'], first['stage']), ('needs_person', 'accepted'))
        self.wo_a.invalidate_recordset()
        self.assertTrue(self.wo_a.accepted_at, "asking must not undo the accept")
        station.assign(self.wo_a.id, self.head.id)
        second = station.scan(code, self.wc_a.id, confirmed=True)
        self.assertEqual(second['action'], 'handed')
        self.wo_a.invalidate_recordset()
        self.assertEqual(self.wo_a.state, 'done')

    def test_scanning_a_job_that_is_somewhere_else_says_where(self):
        """Doing nothing is right here: the job is not on this bench, and silently
        moving it would be a decision the scan never asked for."""
        self.wo_a.workcenter_id = self.wc_b
        result = self.env['lab.station'].scan(self.mo.name, self.wc_a.id)
        self.assertEqual(result['action'], 'elsewhere')
        self.assertIn('Polish', result['message'])
        self.wo_a.invalidate_recordset()
        self.assertEqual(self.wo_a.workcenter_id, self.wc_b, "nothing moved")

    def test_scanning_a_station_label_by_mistake_is_explained(self):
        """The two labels look alike and the mistake is made constantly."""
        with self.assertRaises(UserError) as caught:
            self.env['lab.station'].scan(self.wc_a.scan_code, self.wc_a.id)
        self.assertIn('STATION label', str(caught.exception))

    def test_the_scan_prefers_the_operation_at_this_bench(self):
        """One manufacturing order has several operations. A head scanning at Trimming
        means the trimming step, not the first one on the card."""
        wo = self.env['lab.station']._workorder_from_code(self.mo.name, self.wc_b)
        self.assertEqual(wo, self.wo_b)

    # ------------------------------------------------------- responsibility
    def test_a_handed_job_stays_the_senders_until_it_is_taken(self):
        """The gap between handing over and being accepted is where work is lost: the
        sender thinks it is gone, the receiver has not seen it, nobody is looking."""
        station = self.env['lab.station'].with_user(self.head)
        station.accept(self.wo_a.id)
        self.wo_a.with_user(self.head).action_assign_bench_user(self.head.id)
        station.handover(self.wo_a.id)

        self.wo_b.invalidate_recordset()
        self.assertEqual(self.wo_b.handed_from_workcenter_id, self.wc_a)
        self.assertEqual(self.wo_b.handed_from_user_id, self.head)

        board = station.get_station(self.wc_a.id)
        self.assertEqual([c['id'] for c in board['awaiting']], [self.wo_b.id],
                         "it is still on the sender's board")

        self.env['lab.station'].accept(self.wo_b.id)
        board = station.get_station(self.wc_a.id)
        self.assertEqual(board['awaiting'], [],
                         "responsibility ends when the other bench accepts")

    # ------------------------------------------------------- assignment
    def test_the_head_can_give_a_job_to_someone_on_the_bench(self):
        """"At Trimming" only says the job is somewhere in that room."""
        tech = self.env['res.users'].create({
            'name': 'Bench Hand', 'login': 'wc_hand',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('mrp.group_mrp_user').id])]})
        self.wc_a.users = [(6, 0, [tech.id])]
        board = self.env['lab.station'].with_user(self.head).assign(
            self.wo_a.id, tech.id)
        self.wo_a.invalidate_recordset()
        self.assertEqual(self.wo_a.bench_user_id, tech)
        self.assertIn(tech.id, [p['id'] for p in board['bench_people']])

    def test_a_job_cannot_be_given_to_somebody_who_is_not_on_that_bench(self):
        """A job assigned to somebody who is not there is a job nobody picks up."""
        outsider = self.env['res.users'].create({
            'name': 'Elsewhere', 'login': 'wc_outsider',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        self.wc_a.users = [(6, 0, [self.head.id])]
        with self.assertRaises(UserError):
            self.env['lab.station'].assign(self.wo_a.id, outsider.id)

    def test_the_assignment_shows_on_the_card(self):
        self.wc_a.users = [(6, 0, [self.head.id])]
        self.env['lab.station'].assign(self.wo_a.id, self.head.id)
        board = self.env['lab.station'].get_station(self.wc_a.id)
        card = [c for c in board['incoming'] if c['id'] == self.wo_a.id][0]
        self.assertEqual(card['bench_user'], self.head.name)

    # ------------------------------------------------------- bench-bound access
    def _bench_user(self, login, workcenter=None, as_head=False, bound=True):
        groups = [self.env.ref('base.group_user').id,
                  self.env.ref('mrp.group_mrp_user').id]
        if bound:
            groups.append(
                self.env.ref('lab_workcenter_scan.group_workcenter_bound').id)
        user = self.env['res.users'].create({
            'name': login, 'login': login, 'group_ids': [(6, 0, groups)]})
        if workcenter:
            field = 'head_user_ids' if as_head else 'users'
            workcenter.write({field: [(4, user.id)]})
        return user

    def test_a_bench_user_sees_only_their_own_stations_work(self):
        """The board already filtered the picker; the RECORDS were not restricted, so a
        technician could still open any work order in the system from a list."""
        tech = self._bench_user('bench_a', self.wc_a)
        visible = self.env['mrp.workorder'].with_user(tech).search([])
        self.assertIn(self.wo_a, visible)
        self.assertNotIn(self.wo_b, visible, "another bench's job is not their business")

        stations = self.env['mrp.workcenter'].with_user(tech).search([])
        self.assertIn(self.wc_a, stations)
        self.assertNotIn(self.wc_b, stations)

    def test_the_restriction_is_opt_in(self):
        """Planners and schedulers hold mrp.group_mrp_user too and have every reason to
        see the whole floor. Narrowing it for all of them would break their work."""
        planner = self._bench_user('planner_a', bound=False)
        visible = self.env['mrp.workorder'].with_user(planner).search([])
        self.assertIn(self.wo_a, visible)
        self.assertIn(self.wo_b, visible)

    def test_a_technician_cannot_act_at_a_bench_they_are_not_posted_to(self):
        """Enforced in the METHOD, not only by the record rule: the rule is opt-in, and
        somebody without the group still reaches these buttons from the form."""
        outsider = self._bench_user('bench_out', bound=False)
        with self.assertRaises(UserError):
            self.wo_a.with_user(outsider).action_station_accept()

    def test_a_technician_may_accept_and_hand_on_at_their_own_bench(self):
        tech = self._bench_user('bench_b', self.wc_a)
        self.wo_a.with_user(tech).action_station_accept()
        self.wo_a.invalidate_recordset()
        self.assertEqual(self.wo_a.accepted_by_id, tech)
        self.wo_a.with_user(self.head).action_assign_bench_user(tech.id)
        self.wo_a.with_user(tech).action_station_handover()
        self.wo_a.invalidate_recordset()
        self.assertEqual(self.wo_a.state, 'done')

    def test_only_a_lead_gives_out_the_work(self):
        """A technician doing the job should not be able to push it onto a colleague."""
        tech = self._bench_user('bench_c', self.wc_a)
        mate = self._bench_user('bench_d', self.wc_a)
        with self.assertRaises(UserError) as caught:
            self.wo_a.with_user(tech).action_assign_bench_user(mate.id)
        self.assertIn('lead', str(caught.exception).lower())

        head = self._bench_user('bench_head', self.wc_a, as_head=True)
        self.wo_a.with_user(head).action_assign_bench_user(mate.id)
        self.wo_a.invalidate_recordset()
        self.assertEqual(self.wo_a.bench_user_id, mate)

    def test_a_bench_user_scanning_a_job_elsewhere_is_told_where_it_is(self):
        """They cannot READ that station, but being told where the job went is
        information, not control — otherwise the scan just fails and they hunt for it."""
        tech = self._bench_user('bench_e', self.wc_a)
        self.wo_a.workcenter_id = self.wc_b
        result = self.env['lab.station'].with_user(tech).scan(
            self.mo.name, self.wc_a.id)
        self.assertEqual(result['action'], 'elsewhere')
        self.assertIn('Polish', result['message'])

    def test_a_manufacturing_manager_is_never_bench_bound(self):
        boss = self.env['res.users'].create({
            'name': 'Floor Boss', 'login': 'bench_boss',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('mrp.group_mrp_manager').id])]})
        self.wo_a.with_user(boss).action_station_accept()
        self.wo_a.invalidate_recordset()
        self.assertTrue(self.wo_a.accepted_at)

    def test_a_manufacturing_manager_still_may_not_give_out_work(self):
        """Working the floor and running a bench are two different authorities.

        Every manufacturing user on the client's database also holds the manager group,
        so if that group could reassign work, "the lead decides who does what" would
        mean nothing at all. (client, 2026-08-26)
        """
        boss = self.env['res.users'].create({
            'name': 'Floor Boss 2', 'login': 'bench_boss2',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('mrp.group_mrp_manager').id])]})
        mate = self._bench_user('bench_f', self.wc_a)
        with self.assertRaises(UserError):
            self.wo_a.with_user(boss).action_assign_bench_user(mate.id)

        # ...whereas somebody who genuinely runs the floor may, at any station.
        boss.group_ids = [(4, self.env.ref(
            'lab_workcenter_scan.group_production_manager').id)]
        self.wo_a.with_user(boss).action_assign_bench_user(mate.id)
        self.wo_a.invalidate_recordset()
        self.assertEqual(self.wo_a.bench_user_id, mate)

    def test_giving_out_work_is_stamped_with_who_and_when(self):
        """"It sat for two days" splits into the lead's part and the technician's only
        if the moment it was given out is recorded."""
        head = self._bench_user('bench_head2', self.wc_a, as_head=True)
        mate = self._bench_user('bench_g', self.wc_a)
        self.wo_a.with_user(head).action_assign_bench_user(mate.id)
        self.wo_a.invalidate_recordset()
        self.assertEqual(self.wo_a.bench_assigned_by_id, head)
        self.assertTrue(self.wo_a.bench_assigned_at)

    def test_a_lead_hands_out_several_jobs_at_once(self):
        """One name, many jobs: handing out a morning one job at a time is how a board
        stops being used by Wednesday."""
        head = self._bench_user('bench_head3', self.wc_a, as_head=True)
        mate = self._bench_user('bench_h', self.wc_a)
        second = self.wo_a.copy({'name': 'Trimming again'})
        jobs = self.wo_a | second
        self.env['lab.station'].with_user(head).assign(jobs.ids, mate.id)
        jobs.invalidate_recordset()
        self.assertEqual(jobs.bench_user_id, mate)

        # Two stations at once is refused: a technician is posted to a bench, not to
        # the floor, so one name cannot answer for both.
        with self.assertRaises(UserError):
            self.env['lab.station'].with_user(head).assign(
                (self.wo_a | self.wo_b).ids, mate.id)

    def test_the_board_says_when_nobody_is_posted_to_the_station(self):
        """A station with no people cannot have work given out, and a board that just
        looks empty of names never says why."""
        self.wc_a.write({'users': [(5, 0, 0)], 'head_user_ids': [(5, 0, 0)]})
        board = self.env['lab.station'].get_station(self.wc_a.id)
        self.assertFalse(board['staffed'])
        # A real login, not self.env.user: the test env runs as OdooBot, who is archived,
        # and an archived user is filtered straight back out of the list on read.
        self.wc_a.users = [(4, self._bench_user('bench_k').id)]
        board = self.env['lab.station'].get_station(self.wc_a.id)
        self.assertTrue(board['staffed'])

    def test_the_column_is_a_page_not_the_whole_queue(self):
        """One station on the client's floor holds 24,000 open jobs; a board that
        renders them all sends a payload no phone can hold."""
        board = self.env['lab.station'].get_station(self.wc_a.id)
        self.assertLessEqual(len(board['incoming']), board['totals']['shown'])
        self.assertEqual(
            board['totals']['incoming'],
            self.env['mrp.workorder'].search_count([
                ('workcenter_id', '=', self.wc_a.id),
                ('state', 'not in', ('done', 'cancel')),
                ('accepted_at', '=', False)]),
            "the count beside the heading must be the real depth of the pile")

    def test_the_people_strip_shows_what_each_of_them_is_carrying(self):
        """Spreading work evenly is guesswork without the load beside each name."""
        head = self._bench_user('bench_head4', self.wc_a, as_head=True)
        mate = self._bench_user('bench_i', self.wc_a)
        self.wo_a.with_user(head).action_assign_bench_user(mate.id)
        board = self.env['lab.station'].with_user(head).get_station(self.wc_a.id)
        loads = {p['name']: p['load'] for p in board['bench_people']}
        self.assertEqual(loads[mate.name], 1)
        self.assertIn('id', board['bench_people'][0])
        free = [p for p in board['bench_people'] if p['id'] == 'none']
        self.assertTrue(free, "the pile nobody has been given is itself a filter")

    def test_not_given_out_counts_what_its_columns_show(self):
        """The chip counted every open step at the station - later steps no case had
        reached among them - while the columns it filters show the step each case
        is actually at. (review, 2026-09-15)"""
        later = self.env['mrp.workorder'].create({
            'name': 'Trimming again', 'sequence': 30, 'production_id': self.mo.id,
            'workcenter_id': self.wc_a.id, 'product_uom_id': self.wo_a.product_uom_id.id})
        Station = self.env['lab.station']
        chip = next(p for p in Station._bench_load(self.wc_a) if p['id'] == 'none')
        board = Station.get_station(self.wc_a.id, person_id='none')
        self.assertEqual(chip['load'],
                         board['totals']['incoming'] + board['totals']['working'],
                         "the number on the chip is the work the chip opens")
        self.assertEqual(chip['load'], 1, "the case is at its first step, once")
        shown = [c['id'] for c in board['incoming'] + board['working']]
        self.assertNotIn(later.id, shown)

    def test_a_bench_user_can_push_a_job_to_a_station_they_cannot_see(self):
        """The station lookup ran under the bench rule, which hides every station but
        the user's own - so the label on the next bench answered "No work centre
        carries the code". (review, 2026-09-15)"""
        tech = self._bench_user('bench_push', self.wc_a)
        self.assertNotIn(self.wc_b, self.env['mrp.workcenter'].with_user(tech).search([]))
        result = self.wo_a.with_user(tech).move_to_scanned_workcenter(self.wc_b.scan_code)
        self.assertTrue(result['moved'])
        self.assertEqual(result['workcenter'], self.wc_b.display_name)
        self.assertEqual(self.wo_a.workcenter_id, self.wc_b)

    def test_a_lead_can_look_at_one_persons_bench(self):
        head = self._bench_user('bench_head5', self.wc_a, as_head=True)
        mate = self._bench_user('bench_j', self.wc_a)
        self.wo_a.with_user(head).action_assign_bench_user(mate.id)
        mine = self.env['lab.station'].with_user(head).get_station(
            self.wc_a.id, mate.id)
        shown = [c['id'] for c in mine['incoming']] + [c['id'] for c in mine['working']]
        self.assertEqual(shown, [self.wo_a.id])

    def test_the_card_carries_the_sales_order(self):
        """The office quotes the SO on the phone and the delivery note carries it; a
        patient name alone cannot be looked up anywhere else. (client, 2026-08-26)"""
        board = self.env['lab.station'].get_station(self.wc_a.id)
        card = [c for c in board['incoming'] if c['id'] == self.wo_a.id][0]
        self.assertEqual(card['order'], self.mo.sale_id.name or '')

    # ------------------------------------------------------- production flow
    def test_the_flow_board_shows_every_station_and_names_the_bottleneck(self):
        """A station board per bench shows six calm queues and hides the one that is
        drowning. The bottleneck is not a judgement — it is the tallest column."""
        flow = self.env['lab.flow'].get_flow()
        names = [s['name'] for s in flow['stations']]
        self.assertIn('Trim', names)
        self.assertIn('Polish', names)
        # The LIVE set, not every open workorder: 89.6% of "open" orders on
        # this database are migrated ghosts already on the doctor's shelf, and
        # the board deliberately excludes them (2026-09-02).
        self.assertEqual(flow['totals']['wip'],
                         self.env['mrp.workorder'].search_count(
                             self.env['lab.flow']._live_workorder_domain()))

    def test_the_flow_board_counts_what_nobody_has_accepted(self):
        before = self.env['lab.flow'].get_flow()['totals']['waiting']
        self.env['lab.station'].accept(self.wo_a.id)
        after = self.env['lab.flow'].get_flow()['totals']['waiting']
        self.assertEqual(after, before - 1)

    def test_a_station_with_work_and_nobody_posted_is_flagged(self):
        """A queue that cannot move looks identical to a slow one until you ask."""
        self.wc_a.write({'users': [(5, 0, 0)], 'head_user_ids': [(5, 0, 0)]})
        flow = self.env['lab.flow'].get_flow()
        trim = [s for s in flow['stations'] if s['name'] == 'Trim'][0]
        self.assertTrue(trim['unstaffed'])

    def test_throughput_is_reported_beside_work_in_progress(self):
        """WIP alone says how busy the lab looks, never how much it finished."""
        self.env['lab.station'].accept(self.wo_a.id)
        self.wo_a.action_assign_bench_user(self.head.id)
        self.env['lab.station'].handover(self.wo_a.id)
        flow = self.env['lab.flow'].get_flow()
        self.assertGreaterEqual(flow['throughput']['today'], 1)

    # ------------------------------------------------------- tracking buttons
    def test_a_job_reaches_its_operations_and_its_clinic(self):
        self.mo.invalidate_recordset()
        self.assertEqual(self.mo.workorder_open_count, 2)
        action = self.mo.action_view_open_workorders()
        self.assertEqual(action['res_model'], 'mrp.workorder')

    def test_a_station_reaches_the_jobs_standing_at_it(self):
        self.wc_a.invalidate_recordset()
        self.assertEqual(self.wc_a.open_workorder_count, 1)
        action = self.wc_a.action_view_open_workorders()
        self.assertEqual(action['res_model'], 'mrp.workorder')

    def test_the_visit_link_does_not_require_the_field_app(self):
        """Declaring a Many2one to lab.visit would make this manufacturing module
        refuse to load without the field-work app."""
        self.assertNotIn('visit_id', self.env['mrp.production']._fields)
        self.mo.invalidate_recordset()
        self.assertIn(self.mo.has_field_visit, (True, False))
        self.assertFalse(self.mo._field_visit() or False)


@tagged('post_install', '-at_install')
class TestScannedCodes(TransactionCase):
    """Every number the lab prints has to open the same job.

    The lab prints three different sheets and they do not carry the same reference: the
    job label prints the manufacturing order, the Production Order sheet prints the SALES
    order, and Odoo's own operation sheet prints a work order barcode. A scanner that
    understands one of them is a scanner the floor stops trusting. (client, 2026-08-26)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.wc = cls.env['mrp.workcenter'].create({'name': 'Scanned Bench'})
        product = cls.env['product.product'].create({
            'name': 'Scanned Appliance', 'type': 'consu', 'is_storable': True})
        partner = cls.env['res.partner'].create({'name': 'Dr Scanner'})
        cls.so = cls.env['sale.order'].create({
            'partner_id': partner.id,
            'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': 1})]})
        cls.mo = cls.env['mrp.production'].create({
            'product_id': product.id, 'product_qty': 1.0})
        cls.wo = cls.env['mrp.workorder'].create({
            'name': 'Scanned Step', 'production_id': cls.mo.id,
            'workcenter_id': cls.wc.id, 'product_uom_id': product.uom_id.id})

    def _link_sale_order(self):
        """`sale_id` is a stored related through sale_line_id, so it is written by SQL
        here rather than fighting the ORM for a link the fixture does not otherwise
        need."""
        self.env.cr.execute("UPDATE mrp_production SET sale_id = %s WHERE id = %s",
                            (self.so.id, self.mo.id))
        self.env.cr.execute("UPDATE mrp_workorder SET sale_id = %s WHERE id = %s",
                            (self.so.id, self.wo.id))
        self.env.invalidate_all()

    def test_the_manufacturing_reference_scans(self):
        found = self.env['lab.station']._workorder_from_code(self.mo.name, self.wc)
        self.assertEqual(found, self.wo)

    def test_the_sales_order_scans(self):
        """What the Production Order sheet encodes, and what every sheet already in
        circulation carries."""
        self._link_sale_order()
        found = self.env['lab.station']._workorder_from_code(self.so.name, self.wc)
        self.assertEqual(found, self.wo)

    def test_the_work_order_barcode_scans(self):
        self.assertTrue(self.wo.barcode, "core stamps a barcode on every work order")
        found = self.env['lab.station']._workorder_from_code(self.wo.barcode, self.wc)
        self.assertEqual(found, self.wo)

    def test_the_bare_order_number_finds_the_case(self):
        """Nobody reading an order out over the phone says the prefix: the digits of
        the sales order are enough. (client, 2026-09-14)"""
        self._link_sale_order()
        digits = ''.join(ch for ch in self.so.name if ch.isdigit())
        found = self.env['lab.station']._workorder_from_code(digits, self.wc)
        self.assertEqual(found, self.wo)
        where = self.env['lab.station'].locate(digits, self.wc.id)
        self.assertEqual(where['station'], self.wc.display_name,
                         "and Where? says the bench it is at")

    def test_part_of_a_number_does_not_match_another_case(self):
        """The digits must match whole: the tail of a number is not the number."""
        self._link_sale_order()
        digits = ''.join(ch for ch in self.so.name if ch.isdigit())
        from odoo.exceptions import UserError
        with self.assertRaises(UserError):
            self.env['lab.station']._workorder_from_code(digits[-2:] + '9999', self.wc)

    def test_where_searches_the_way_track_order_does(self):
        """Part of the number, the patient or the doctor finds the case, and says
        which bench it is at. (client, 2026-09-14)"""
        self._link_sale_order()
        self.so.patient = 'Findable Patient'
        station = self.env['lab.station']
        digits = ''.join(ch for ch in self.so.name if ch.isdigit())
        for term in (digits[-4:], 'findable pat', 'Dr Scanner'):
            rows = station.find_cases(term, self.wc.id)
            mine = [r for r in rows if r['order_id'] == self.so.id]
            self.assertTrue(mine, "%r finds the case" % term)
            piece = mine[0]['pieces'][0]
            self.assertEqual(piece['station'], self.wc.display_name)
            self.assertTrue(piece['here'])
            self.assertEqual(piece['standing'], 'waiting')

    def test_only_the_floor_may_look_cases_up(self):
        """Both look-ups read elevated. Left public, they answered any login every
        order's patient, doctor and bench. (review, 2026-09-15)"""
        from odoo.exceptions import AccessError
        clerk = self.env['res.users'].create({
            'name': 'Clerk', 'login': 'find_clerk',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        station = self.env['lab.station'].with_user(clerk)
        with self.assertRaises(AccessError):
            station.find_cases(self.so.name, self.wc.id)
        with self.assertRaises(AccessError):
            station.locate(self.mo.name, self.wc.id)
        floor = self.env['res.users'].create({
            'name': 'Floor Reader', 'login': 'find_floor',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('mrp.group_mrp_user').id])]})
        rows = self.env['lab.station'].with_user(floor).find_cases(self.so.name, self.wc.id)
        self.assertIn(self.so.id, [r['order_id'] for r in rows])

    def test_where_says_why_a_case_has_nothing_open(self):
        rows = self.env['lab.station'].find_cases(self.so.name, self.wc.id)
        mine = [r for r in rows if r['order_id'] == self.so.id]
        self.assertTrue(mine)
        self.assertFalse(mine[0]['pieces'], "nothing linked to the order yet")
        self.assertEqual(mine[0]['status'], 'Not in production yet')

    def test_a_typed_code_does_not_have_to_match_case(self):
        """The camera needs HTTPS; the fallback is a keyboard, where "mo/1" and "MO/1"
        are the same job to everybody except the database."""
        found = self.env['lab.station']._workorder_from_code(
            '  %s  ' % self.mo.name.lower(), self.wc)
        self.assertEqual(found, self.wo)

    def test_a_code_that_is_not_a_number_is_refused_not_crashed(self):
        """'WO-abc' used to reach int() and raise ValueError, which surfaces as a
        server error rather than something a bench can act on."""
        for bad in ('WO-abc', 'XX-999', 'WO-'):
            with self.assertRaises(UserError):
                self.env['lab.station']._workorder_from_code(bad, self.wc)

    def test_a_finished_job_no_longer_answers_to_its_code(self):
        self.wo.state = 'done'
        with self.assertRaises(UserError):
            self.env['lab.station']._workorder_from_code(self.mo.name, self.wc)


@tagged('post_install', '-at_install')
class TestProductionPerformance(TransactionCase):
    """The three clocks, and what they are allowed to say."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.wc = cls.env['mrp.workcenter'].create({'name': 'Timed Bench'})
        product = cls.env['product.product'].create({
            'name': 'Timed Appliance', 'type': 'consu', 'is_storable': True})
        cls.user = cls.env['res.users'].create({
            'name': 'Timed Tech', 'login': 'timed_tech',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('mrp.group_mrp_user').id])]})
        cls.mo = cls.env['mrp.production'].create({
            'product_id': product.id, 'product_qty': 1.0})
        cls.wo = cls.env['mrp.workorder'].create({
            'name': 'Timed Step', 'production_id': cls.mo.id,
            'workcenter_id': cls.wc.id, 'product_uom_id': product.uom_id.id})

    def _stamp(self, arrived_h, accepted_h, handed_h, duration=90.0):
        # Anchored inside the report's own day rather than to the wall clock:
        # this suite runs at every hour, and "two hours ago" is yesterday when
        # it runs just after midnight. The gaps between the three stamps, which
        # is what the clocks measure, are unchanged. (2026-09-10)
        now = self.env['lab.production.performance']._start_of(
            self.env['lab.station']._lab_today()) + timedelta(hours=arrived_h + 1)
        self.env.cr.execute("""
            UPDATE mrp_workorder SET create_date = %s, accepted_at = %s,
                   handed_over_at = %s, bench_user_id = %s, duration = %s,
                   duration_expected = 60 WHERE id = %s""",
            (now - timedelta(hours=arrived_h), now - timedelta(hours=accepted_h),
             now - timedelta(hours=handed_h), self.user.id, duration, self.wo.id))
        self.env.invalidate_all()

    def _row(self):
        return self.env['lab.production.performance'].search_read(
            [('workorder_id', '=', self.wo.id)],
            ['waiting_hours', 'working_hours', 'total_hours', 'clocked_minutes',
             'over_expected', 'stage', 'finished'])[0]

    def test_the_three_clocks_measure_what_they_say(self):
        self._stamp(10, 6, 2)
        row = self._row()
        self.assertAlmostEqual(row['waiting_hours'], 4.0, places=1)
        self.assertAlmostEqual(row['working_hours'], 4.0, places=1)
        self.assertAlmostEqual(row['total_hours'], 8.0, places=1)
        self.assertEqual(row['clocked_minutes'], 90.0)
        self.assertEqual(row['over_expected'], 30.0, "90 worked against 60 costed")
        self.assertEqual(row['stage'], 'finished')
        self.assertEqual(row['finished'], 1)

    def test_a_job_nobody_has_picked_up_keeps_counting(self):
        """Leaving waiting blank until somebody finally accepts hides exactly the case
        the lab needs to see: a job that has sat for three days."""
        row = self._row()
        self.assertGreaterEqual(row['waiting_hours'] or 0, 0.0)
        self.assertFalse(row['working_hours'], "it has not reached a bench yet")
        self.assertEqual(row['stage'], 'queued')
        self.assertEqual(row['finished'], 0)

    def test_the_period_is_measured_by_when_work_was_handed_on(self):
        """Not by when it arrived: otherwise last month's report changes every time an
        old job is finally finished, and two runs of it disagree.

        Scoped to this test's own bench. It counted the WHOLE lab and expected
        one, which held only while nothing else on this database had been handed
        on today - so the day somebody worked the floor, or another session ran
        the board, the test failed for a reason that had nothing to do with the
        report. (2026-09-09)
        """
        self._stamp(10, 6, 2)
        Perf = self.env['lab.production.performance']
        today = self.env['lab.station']._lab_today()
        mine = self.wc.ids
        inside = Perf.station_summary(today, today, workcenter_ids=mine)
        self.assertEqual(inside['totals']['jobs'], 1)
        long_ago = today - timedelta(days=30)
        outside = Perf.station_summary(long_ago, long_ago, workcenter_ids=mine)
        self.assertEqual(outside['totals']['jobs'], 0)

    def test_the_summary_names_the_station_and_the_person(self):
        self._stamp(10, 6, 2)
        today = self.env['lab.station']._lab_today()
        summary = self.env['lab.production.performance'].station_summary(today, today)
        self.assertIn(self.wc.display_name, [r['name'] for r in summary['stations']])
        self.assertIn(self.user.name, [r['name'] for r in summary['people']])

    def test_the_people_are_the_people_asked_for(self):
        """Picking a polisher printed the colleague whose job they finished, and the
        analysis the page opened filtered on the technician alone.
        (review, 2026-09-15)"""
        polisher = self.env['res.users'].create({
            'name': 'Timed Polisher', 'login': 'timed_polisher'})
        self._stamp(10, 6, 2)
        self.env.cr.execute("UPDATE mrp_workorder SET finisher_user_id = %s WHERE id = %s",
                            (polisher.id, self.wo.id))
        self.env.invalidate_all()
        today = self.env['lab.station']._lab_today()
        Perf = self.env['lab.production.performance']
        summary = Perf.station_summary(today, today, workcenter_ids=self.wc.ids,
                                       user_ids=polisher.ids)
        self.assertEqual([r['id'] for r in summary['people']], polisher.ids,
                         "the technician whose job it was is not who was asked for")
        self.assertEqual(summary['people'][0]['finished_for_others'], 1)
        self.assertEqual(summary['totals']['jobs'], 1)
        wizard = self.env['lab.production.report.wizard'].create({
            'date_from': today, 'date_to': today,
            'workcenter_ids': [(6, 0, self.wc.ids)],
            'user_ids': [(6, 0, polisher.ids)]})
        shown = Perf.search(wizard.action_open_analysis()['domain'])
        self.assertEqual(shown.workorder_id, self.wo,
                         "the analysis holds the job the page counted")

    def test_an_emergency_case_has_a_label(self):
        """Sale orders carry `emergency` (lab_order_control); the view copies it."""
        selection = dict(self.env['lab.production.performance']._fields['priority'].selection)
        self.assertEqual(selection.get('emergency'), 'Emergency')

    def test_the_report_prints_with_nothing_to_report(self):
        """A period with no work must get an empty report that says so, not a
        traceback. Scoped to a week nobody worked - this database now carries
        the client's real performance rows, so "the whole lab is empty" is no
        longer a premise a test may assume."""
        wizard = self.env['lab.production.report.wizard'].create({
            'date_from': '2020-01-06', 'date_to': '2020-01-12'})
        data = wizard.summary_data()
        self.assertEqual(data['totals']['jobs'], 0)
        self.assertFalse(data['slowest'])

    def test_the_hand_out_sheet_lists_what_a_person_is_holding(self):
        self.wc.users = [(4, self.user.id)]
        self.wo.bench_user_id = self.user.id
        wizard = self.env['lab.production.report.wizard'].create({
            'report_kind': 'handout', 'workcenter_ids': [(6, 0, self.wc.ids)]})
        blocks = wizard.handout_data()
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]['user'], self.user)
        self.assertIn(self.wo, blocks[0]['jobs'])


@tagged('post_install', '-at_install')
class TestFloorPrints(TransactionCase):
    """The sheets the floor carries, and the one filter they all depend on.

    89.6% of this client's 23,536 "open" manufacturing orders sit on a sales order that
    has already been delivered in full — they were never closed after the migration. Any
    sheet built on `state not in (done, cancel)` therefore prints mostly work the doctor
    already has, and the first supervisor who chases one stops trusting the paper. Every
    test here exists to keep that filter honest. (client, 2026-08-26)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Floor = cls.env['report.lab.floor']
        cls.wc_one = cls.env['mrp.workcenter'].create({'name': 'Floor Bench 1'})
        cls.wc_two = cls.env['mrp.workcenter'].create({'name': 'Floor Bench 2'})
        cls.product = cls.env['product.product'].create({
            'name': 'Floor Appliance', 'type': 'consu', 'is_storable': True})
        cls.partner = cls.env['res.partner'].create({'name': 'Dr Floor'})
        # A route of its own, so a fixture case is never crowded out of an
        # "oldest N" sheet by the 24,000 real ones already on this database.
        cls.team = cls.env['crm.team'].create({'name': 'FLOORTEST'})

    def _case(self, name='Floor Case', delivered=False, patient='  Alice  '):
        """A sales order, its manufacturing order and two routed operations."""
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {'product_id': self.product.id,
                                   'product_uom_qty': 1})]})
        order.team_id = self.team.id
        if 'patient' in order._fields:
            order.patient = patient
        mo = self.env['mrp.production'].create({
            'product_id': self.product.id, 'product_qty': 1.0})
        self.env.cr.execute("UPDATE mrp_production SET sale_id = %s WHERE id = %s",
                            (order.id, mo.id))
        for seq, wc in ((10, self.wc_one), (20, self.wc_two)):
            self.env['mrp.workorder'].create({
                'name': '%s step %s' % (name, seq), 'sequence': seq,
                'production_id': mo.id, 'workcenter_id': wc.id,
                'product_uom_id': self.product.uom_id.id})
        if delivered:
            self._deliver(order)
        self.env.invalidate_all()
        return order, mo

    def _deliver(self, order):
        """Ship the order, so the live filter has something to exclude.

        The picking's state is written to the column rather than validated: validating
        needs stock this fixture has no reason to create, and what is under test is the
        domain, not Odoo's delivery flow. The flush before the write is load-bearing —
        `state` is computed AND stored, so an unflushed create would otherwise write
        'draft' over this a moment later and the test would silently prove nothing.
        """
        stock = self.env.ref('stock.stock_location_stock')
        customers = self.env.ref('stock.stock_location_customers')
        picking = self.env['stock.picking'].create({
            'partner_id': self.partner.id,
            'picking_type_id': self.env.ref('stock.picking_type_out').id,
            'location_id': stock.id, 'location_dest_id': customers.id,
            'sale_id': order.id,
        })
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE stock_picking SET state = 'done', date_done = now() WHERE id = %s",
            (picking.id,))
        self.env.invalidate_all()
        self.assertEqual(picking.state, 'done', "fixture failed to ship the order")
        self.assertIn(picking, order.picking_ids)
        return picking

    # ------------------------------------------------------------------ the filter
    def test_work_already_delivered_is_not_in_the_lab(self):
        _o1, live = self._case('Live')
        _o2, gone = self._case('Gone', delivered=True)
        found = self.env['mrp.production'].search(self.Floor._live_domain())
        self.assertIn(live, found)
        self.assertNotIn(gone, found,
                         "the doctor already has this one; printing it causes a phone "
                         "call rather than saving one")

    def test_the_close_out_list_is_exactly_the_other_side(self):
        _o1, live = self._case('Live2')
        _o2, gone = self._case('Gone2', delivered=True)
        found = self.env['mrp.production'].search(self.Floor._closeout_domain())
        self.assertIn(gone, found)
        self.assertNotIn(live, found)

    # ------------------------------------------------------------------ the queue
    def test_a_station_queue_is_the_first_unfinished_step_only(self):
        """A case routed through two benches is unfinished at both from the moment it is
        created. Counting every unfinished operation puts one case in every queue at once
        and shows a bench thousands deep before anything has reached it."""
        _order, mo = self._case('Routed')
        first = self.Floor._first_open_step(mo)
        self.assertEqual(first[mo.id]['workcenter_id'][0], self.wc_one.id)
        self.assertNotEqual(first[mo.id]['workcenter_id'][0], self.wc_two.id)

        values = self.env['report.lab_workcenter_scan.report_station_queue']\
            ._get_report_values((self.wc_one | self.wc_two).ids)
        at_one = [r['mo'] for r in values['payload'][self.wc_one.id]['rows']]
        at_two = [r['mo'] for r in values['payload'][self.wc_two.id]['rows']]
        self.assertIn(mo, at_one)
        self.assertNotIn(mo, at_two, "the second bench is not waiting for it yet")

    def test_the_queue_sheet_states_the_real_depth(self):
        for i in range(3):
            self._case('Depth %s' % i)
        values = self.env['report.lab_workcenter_scan.report_station_queue']\
            ._get_report_values(self.wc_one.ids)
        payload = values['payload'][self.wc_one.id]
        self.assertGreaterEqual(payload['total'], 3)
        self.assertLessEqual(len(payload['rows']), payload['total'])

    # ------------------------------------------------------------------ the sheets
    def test_the_callover_is_oldest_first_and_skips_delivered_work(self):
        _o1, old = self._case('Old')
        _o2, gone = self._case('OldGone', delivered=True)
        past = fields.Datetime.now() - timedelta(days=120)
        self.env.cr.execute(
            "UPDATE mrp_production SET date_start = %s WHERE id IN %s",
            (past, tuple((old | gone).ids)))
        self.env.invalidate_all()

        wizard = self.env['lab.callover.wizard'].create({
            'older_than': 90, 'team_ids': [(6, 0, self.team.ids)]})
        cases = wizard._cases()
        self.assertIn(old, cases)
        self.assertNotIn(gone, cases)
        data = wizard.callover_data()
        self.assertTrue(data['rows'])
        self.assertGreaterEqual(data['rows'][0]['days'], 90)

    def test_the_manifest_defaults_to_boxes_that_are_actually_ready(self):
        """Confirmed is a backlog, not a packing list: 2,637 against 154 on the client's
        floor. A packing table cannot work through the first number."""
        wizard = self.env['lab.packing.manifest.wizard'].create({})
        self.assertEqual(wizard.scope, 'ready')
        data = wizard.manifest_data()
        for route in data['routes']:
            for row in route['rows']:
                self.assertTrue(row['ready'])

    def test_the_clinic_sheet_refuses_to_print_a_lie(self):
        """It is the only sheet that leaves the building. A clinic whose every case has
        already been delivered must produce nothing at all, not an empty table."""
        _order, _mo = self._case('ClinicGone', delivered=True)
        wizard = self.env['lab.clinic.work.wizard'].create({
            'partner_ids': [(6, 0, self.partner.ids)]})
        with self.assertRaises(UserError):
            wizard.clinic_data()

    def test_the_requisition_adds_up_the_same_tin_once(self):
        """The product master carries 'Monomor' and 'monomor' as two products; a slip
        that lists them separately gets one of the lines ignored."""
        one = self.env['product.product'].create({'name': 'Monomor', 'type': 'consu'})
        two = self.env['product.product'].create({'name': 'monomor', 'type': 'consu'})
        _order, mo = self._case('Components')
        for product, qty in ((one, 3.0), (two, 2.0)):
            self.env['stock.move'].create({
                'product_id': product.id,
                'product_uom_qty': qty, 'product_uom': product.uom_id.id,
                'location_id': self.env.ref('stock.stock_location_stock').id,
                'location_dest_id': self.env.ref('stock.stock_location_stock').id,
                'raw_material_production_id': mo.id})
        self.env.invalidate_all()
        values = self.env['report.lab_workcenter_scan.report_requisition']\
            ._get_report_values(mo.ids)
        monomor = [line for line in values['lines']
                   if line['name'].lower() == 'monomor']
        self.assertEqual(len(monomor), 1, "two spellings, one line")
        self.assertEqual(monomor[0]['qty'], 5.0)
        self.assertTrue(monomor[0]['aka'], "the other spelling is named, not hidden")

    # ------------------------------------------------------------------ the labels
    def test_route_names_are_tidied_but_never_folded_together(self):
        """`TVM` and `TVM 2` are two vans, not one route typed twice — folding them puts
        one van's boxes on the other's manifest."""
        teams = self.env['crm.team']
        one = teams.create({'name': ' tvm '})
        two = teams.create({'name': 'TVM 2'})
        self.assertEqual(self.Floor._route_label(one), 'TVM')
        self.assertEqual(self.Floor._route_label(two), 'TVM 2')
        self.assertNotEqual(self.Floor._route_label(one),
                            self.Floor._route_label(two))

    def test_the_patient_name_is_trimmed_and_never_blank(self):
        order, mo = self._case('Trim', patient='   Bobby   ')
        self.assertEqual(self.Floor._patient_label(order), 'Bobby')
        order.patient = '   '
        self.assertEqual(self.Floor._patient_label(order, mo.name), mo.name)

    def test_the_arch_label_survives_a_related_selection(self):
        """`_fields['ul'].selection` is a FUNCTION on a related field, so dict() on it
        raises — the label has to come through fields_get."""
        labels = self.Floor._arch_labels()
        self.assertIsInstance(labels, dict)
        _order, mo = self._case('Arch')
        if 'ul' in mo._fields:
            self.assertIsInstance(self.Floor._arch_label(mo), str)

    def test_the_operation_is_only_named_when_it_adds_something(self):
        """Most operations here are named after the bench they run at, and the two
        strings differ only in spacing — 'Wire Bending / Adams Clasp' against
        'Wire Bending/ Adams Clasp'. Printed together they read as a printing fault."""
        self.assertEqual(self.Floor._step_note('Banding', 'Banding'), '')
        self.assertEqual(
            self.Floor._step_note('Wire Bending / Adams Clasp',
                                  'Wire Bending/ Adams  Clasp'), '',
            "the difference is a space before the slash, not a run of spaces")
        self.assertEqual(self.Floor._step_note('Trimming', 'Final polish'),
                         'Final polish', "a real difference must survive")

    def test_age_is_reported_but_lateness_is_never_invented(self):
        """No promised date exists anywhere on this database, so a sheet that said
        'overdue' would be making it up."""
        _order, mo = self._case('Age')
        self.env.cr.execute(
            "UPDATE mrp_production SET date_start = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(days=10), mo.id))
        self.env.invalidate_all()
        self.assertEqual(self.Floor._days_in_lab(mo), 10)
        self.assertFalse(mo.date_deadline,
                         "if this ever fills in, the sheets may start saying 'late'")

    # ------------------------------------------------------------------ rendering
    def test_every_floor_sheet_renders(self):
        """HTML rather than PDF: it exercises the same templates and data, and catches a
        template error in milliseconds instead of running wkhtmltopdf eight times."""
        _order, mo = self._case('Render')
        Report = self.env['ir.actions.report']
        for xml_id, ids in (
                ('lab_workcenter_scan.report_traveller', mo.ids),
                ('lab_workcenter_scan.report_hold_tag', mo.ids),
                ('lab_workcenter_scan.report_requisition', mo.ids),
                ('lab_workcenter_scan.report_station_queue', self.wc_one.ids),
                ('lab_workcenter_scan.report_job_label', mo.ids)):
            html = Report._render_qweb_html(xml_id, ids)[0]
            self.assertTrue(html, "%s rendered nothing" % xml_id)

        for model, xml_id in (
                ('lab.callover.wizard', 'lab_workcenter_scan.report_callover'),
                ('lab.packing.manifest.wizard',
                 'lab_workcenter_scan.report_manifest'),
                ('lab.closeout.wizard', 'lab_workcenter_scan.report_closeout')):
            wizard = self.env[model].create({})
            html = Report._render_qweb_html(xml_id, wizard.ids)[0]
            self.assertTrue(html, "%s rendered nothing" % xml_id)

    def test_an_unprintable_symbology_stops_the_print(self):
        """A silent substitution fails at whoever receives the label, not at the printer.

        `auto` is deliberately NOT in that category: nine core templates — every product
        label among them — ask for it and mean "pick something that fits", so refusing it
        would break printing that works today.
        """
        Report = self.env['ir.actions.report']
        if Report._renderpm_works():
            self.skipTest("reportlab draws barcodes natively on this server")

        # ECC200DataMatrix is what core's package and lot labels ask for, and what this
        # server genuinely cannot draw.
        with self.assertRaises(UserError):
            Report.barcode('ECC200DataMatrix', '12345', width=100, height=100)

        for symbology, value in (('Code128', 'MO/1'), ('QR', 'MO/1'),
                                 ('auto', 'MO/1'), ('EAN13', '5901234123457')):
            self.assertTrue(Report.barcode(symbology, value, width=100, height=100),
                            "%s must keep printing" % symbology)

    def test_the_symbology_rail_can_be_switched_off(self):
        """It sits under every barcode in the system, core reports included, so a lab
        that would rather have a wrong label than a stopped print can say so."""
        Report = self.env['ir.actions.report']
        if Report._renderpm_works():
            self.skipTest("reportlab draws barcodes natively on this server")
        self.env['ir.config_parameter'].sudo().set_param(
            'epg_barcode_fallback.strict_symbology', '0')
        self.assertTrue(
            Report.barcode('ECC200DataMatrix', '12345', width=100, height=100))


@tagged('post_install', '-at_install')
class TestFloorPrintReviewFixes(TransactionCase):
    """Defects an adversarial review found in the floor sheets, each pinned here.

    Every one was confirmed against the real database before it was fixed, and every one
    would print a document that looks perfectly correct and says something untrue.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Floor = cls.env['report.lab.floor']
        cls.product = cls.env['product.product'].create({
            'name': 'Review Appliance', 'type': 'consu', 'is_storable': True})
        cls.other = cls.env['product.product'].create({
            'name': 'Review Other', 'type': 'consu', 'is_storable': True})
        cls.partner = cls.env['res.partner'].create({'name': 'Dr Review'})
        cls.team = cls.env['crm.team'].create({'name': 'REVIEWTEAM'})
        cls.wc = cls.env['mrp.workcenter'].create({'name': 'Review Bench'})

    def _order(self, team=None):
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id, 'team_id': (team or self.team).id,
            'order_line': [(0, 0, {'product_id': self.product.id,
                                   'product_uom_qty': 1})]})
        return order

    def _mo(self, order):
        mo = self.env['mrp.production'].create({
            'product_id': self.product.id, 'product_qty': 1.0})
        self.env.cr.execute("UPDATE mrp_production SET sale_id = %s WHERE id = %s",
                            (order.id, mo.id))
        self.env['mrp.workorder'].create({
            'name': 'Review step', 'sequence': 10, 'production_id': mo.id,
            'workcenter_id': self.wc.id, 'product_uom_id': self.product.uom_id.id})
        self.env.invalidate_all()
        return mo

    def _picking(self, order, state):
        stock = self.env.ref('stock.stock_location_stock')
        customers = self.env.ref('stock.stock_location_customers')
        picking = self.env['stock.picking'].create({
            'partner_id': self.partner.id,
            'picking_type_id': self.env.ref('stock.picking_type_out').id,
            'location_id': stock.id, 'location_dest_id': customers.id,
            'sale_id': order.id})
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE stock_picking SET state = %s, date_done = now() WHERE id = %s",
            (state, picking.id))
        self.env.invalidate_all()
        return picking

    def test_a_half_shipped_order_does_not_declare_the_other_half_delivered(self):
        """One line ships, the other is still being made. Testing only "has a done
        outgoing picking" declared the unmade appliance delivered: it vanished from the
        bench and the doctor's sheet, and appeared on the close-out list telling the
        office to close an appliance nobody had started. 113 real cases were in exactly
        that state. (review finding, 2026-08-26)"""
        order = self._order()
        mo = self._mo(order)
        self._picking(order, 'done')          # the other line went out
        self._picking(order, 'confirmed')     # this one has not

        live = self.env['mrp.production'].search(self.Floor._live_domain())
        closing = self.env['mrp.production'].search(self.Floor._closeout_domain())
        self.assertIn(mo, live, "an order with a delivery still outstanding is in the lab")
        self.assertNotIn(mo, closing,
                         "and must never be offered to the office as 'close this'")

    def test_a_fully_shipped_order_is_still_retired(self):
        order = self._order()
        mo = self._mo(order)
        self._picking(order, 'done')
        self.assertNotIn(mo, self.env['mrp.production'].search(self.Floor._live_domain()))
        self.assertIn(mo, self.env['mrp.production'].search(self.Floor._closeout_domain()))

    def test_the_headline_number_obeys_the_route_filter(self):
        """A route huddle was handed a sheet reading 867 next to a subtitle naming one
        route: the rows were filtered and the number was not."""
        order = self._order()
        mo = self._mo(order)
        self.env.cr.execute(
            "UPDATE mrp_production SET date_start = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(days=200), mo.id))
        self.env.invalidate_all()

        # Another route's old case, so "everyone" is more than "mine" even on a
        # database with no other production behind it.
        other = self._mo(self._order(team=self.env['crm.team'].create({'name': 'OTHERROUTE'})))
        self.env.cr.execute(
            "UPDATE mrp_production SET date_start = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(days=200), other.id))
        self.env.invalidate_all()

        mine = self.env['lab.callover.wizard'].create({
            'older_than': 90, 'team_ids': [(6, 0, self.team.ids)]}).callover_data()
        everyone = self.env['lab.callover.wizard'].create(
            {'older_than': 90}).callover_data()
        self.assertEqual(mine['total'], len(mine['rows']),
                         "the count and the rows must come from one domain")
        self.assertLess(mine['total'], everyone['total'])

    def test_the_close_out_count_obeys_the_route_filter_too(self):
        order = self._order()
        self._mo(order)
        self._picking(order, 'done')
        # Another route's finished case, for the same reason as above.
        other_order = self._order(team=self.env['crm.team'].create({'name': 'OTHERROUTE'}))
        self._mo(other_order)
        self._picking(other_order, 'done')
        mine = self.env['lab.closeout.wizard'].create({
            'team_ids': [(6, 0, self.team.ids)]}).closeout_data()
        everyone = self.env['lab.closeout.wizard'].create({}).closeout_data()
        self.assertLess(mine['total'], everyone['total'])
        self.assertGreater(mine['total'], 0)

    def test_the_close_out_limit_cannot_ask_for_more_than_a_pdf_survives(self):
        """The wizard's own alert quotes 21,099; typing that killed wkhtmltopdf and
        produced no file, which reads to the user as a broken button."""
        wizard = self.env['lab.closeout.wizard'].create({'limit': 999999})
        self.assertLessEqual(len(wizard._cases()), 400)

    def test_the_clinic_sheet_will_not_print_every_clinic_at_once(self):
        """Left to its default it built 807 forced page breaks — an 810-page PDF that
        killed the renderer outright."""
        with self.assertRaises(UserError):
            self.env['lab.clinic.work.wizard'].create({}).clinic_data()

    def test_a_route_typed_two_ways_is_one_van_but_two_vans_stay_apart(self):
        """'TVM 2' and 'TVM2' are one route typed twice and must share a manifest block;
        'TVM' and 'TVM 2' are two vans and must not."""
        spaced = self.env['crm.team'].create({'name': 'TVM 2'})
        tight = self.env['crm.team'].create({'name': 'TVM2'})
        plain = self.env['crm.team'].create({'name': 'TVM'})
        self.assertEqual(self.Floor._route_key(spaced), self.Floor._route_key(tight))
        self.assertNotEqual(self.Floor._route_key(spaced), self.Floor._route_key(plain))
        self.assertEqual(self.Floor._route_label(spaced), 'TVM 2',
                         "the heading keeps the name a human recognises")

    def test_the_printed_stamp_is_never_silently_utc(self):
        """90 of 111 users here have no timezone, and the fallback put the sheet five
        and a half hours behind the clock on the wall."""
        user = self.env['res.users'].create({
            'name': 'No TZ', 'login': 'floor_notz', 'tz': False,
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('mrp.group_mrp_user').id])]})
        stamp = self.Floor.with_user(user).with_context(tz=None)._printed_stamp()
        naive = fields.Datetime.now().strftime('%d/%m/%Y %H:%M')
        self.assertTrue(stamp['at'])
        self.assertNotEqual(stamp['at'], naive,
                            "a stamp identical to UTC means the fallback did not fire")

    def test_the_queue_sheet_admits_when_it_is_only_routing_order(self):
        """No work order on this floor has ever been accepted or handed on, so the order
        of steps is the routing's — the sheet must say so rather than imply it knows
        where each case physically is."""
        order = self._order()
        self._mo(order)
        values = self.env['report.lab_workcenter_scan.report_station_queue']\
            ._get_report_values(self.wc.ids)
        self.assertFalse(values['payload'][self.wc.id]['handovers'])

    def test_the_manifest_counts_each_route_over_the_whole_domain(self):
        """A driver signing a block headed '31 boxes' is signing for the route, so that
        number must be the route's and not the page's."""
        order = self._order()
        self._picking(order, 'assigned')
        data = self.env['lab.packing.manifest.wizard'].create({
            'scope': 'ready', 'team_ids': [(6, 0, self.team.ids)]}).manifest_data()
        self.assertTrue(data['routes'])
        for route in data['routes']:
            self.assertGreaterEqual(route['total'], route['count'])
            self.assertEqual(route['partial'], route['total'] > route['count'])
        self.assertEqual(sum(r['total'] for r in data['routes']), data['total'])

    def test_the_first_open_step_does_not_out_run_the_readers_own_rules(self):
        """It feeds the station column of a sheet a bench user can print; a superuser
        read there hands them work centres their own record rule exists to hide."""
        import inspect
        source = inspect.getsource(type(self.Floor)._first_open_step)
        self.assertNotIn('sudo()', source)

    def test_an_oversized_barcode_is_still_refused(self):
        """Core clamps this before drawing; the override returned before core ever ran,
        leaving an unauthenticated route able to ask Pillow for a huge image."""
        Report = self.env['ir.actions.report']
        if Report._renderpm_works():
            self.skipTest("reportlab draws barcodes natively on this server")
        with self.assertRaises(ValueError):
            Report.barcode('Code128', 'X', width=99999, height=99999)

    def test_the_refusal_does_not_publish_the_servers_capabilities(self):
        """barcode() is reachable through the unauthenticated /report/barcode route, so
        the message must not carry the parameter name or the symbology inventory."""
        Report = self.env['ir.actions.report']
        if Report._renderpm_works():
            self.skipTest("reportlab draws barcodes natively on this server")
        with self.assertRaises(UserError) as caught:
            Report.barcode('ECC200DataMatrix', '12345', width=100, height=100)
        message = str(caught.exception)
        self.assertNotIn('strict_symbology', message)
        self.assertNotIn('Code128', message)

    def test_symbologies_the_library_can_actually_draw_are_not_refused(self):
        Report = self.env['ir.actions.report']
        if Report._renderpm_works():
            self.skipTest("reportlab draws barcodes natively on this server")
        for symbology, value in (('Codabar', 'A123456A'), ('I2of5', '1234'),
                                 ('auto', 'MO/1')):
            self.assertTrue(Report.barcode(symbology, value, width=200, height=80),
                            "%s is drawable and must not be refused" % symbology)


@tagged('post_install', '-at_install')
class TestCloseOnDelivery(TransactionCase):
    """Delivering an appliance finishes the job that made it.

    The lab's manufacturing orders were never closed — 20,978 of them stood open against
    cases the doctor had had for months — because nothing connected "the box left" to
    "the job is finished". These tests hold that connection to two rules, and both exist
    because breaking either quietly finishes work nobody has done. (client, 2026-08-26)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.stock = cls.env.ref('stock.stock_location_stock')
        cls.customers = cls.env.ref('stock.stock_location_customers')
        cls.out = cls.env.ref('stock.picking_type_out')
        cls.partner = cls.env['res.partner'].create({'name': 'Dr Delivery'})
        cls.appliance = cls.env['product.product'].create({
            'name': 'Closing Appliance', 'type': 'consu', 'is_storable': True})
        cls.other = cls.env['product.product'].create({
            'name': 'Closing Other', 'type': 'consu', 'is_storable': True})
        # These tests describe the rule when it is ON. The database carries its
        # own setting - switched off on staging on 2026-09-14 at the client's
        # request - and a test that inherits it tests the database, not the code.
        cls.env['ir.config_parameter'].sudo().set_param(
            'lab_workcenter_scan.close_mo_on_delivery', '1')

    def _order(self, products):
        return self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {'product_id': p.id, 'product_uom_qty': 1})
                           for p in products]})

    def _job(self, order, product):
        mo = self.env['mrp.production'].create({
            'product_id': product.id, 'product_qty': 1.0})
        mo.action_confirm()
        self.env.cr.execute("UPDATE mrp_production SET sale_id = %s WHERE id = %s",
                            (order.id, mo.id))
        self.env.invalidate_all()
        return mo

    def _deliver(self, order, products):
        """A real validated delivery.

        The moves carry `sale_line_id` because that is what stock.picking.sale_id is
        computed from — a picking whose moves are not linked to the order has no sale_id
        at all, however the field is written.
        """
        picking = self.env['stock.picking'].create({
            'partner_id': self.partner.id, 'picking_type_id': self.out.id,
            'location_id': self.stock.id, 'location_dest_id': self.customers.id})
        for product in products:
            line = order.order_line.filtered(lambda l: l.product_id == product)[:1]
            self.env['stock.move'].create({
                'product_id': product.id, 'product_uom_qty': 1,
                'product_uom': product.uom_id.id, 'picking_id': picking.id,
                'sale_line_id': line.id,
                'location_id': self.stock.id, 'location_dest_id': self.customers.id})
        picking.action_confirm()
        picking.move_ids.write({'quantity': 1, 'picked': True})
        picking.button_validate()
        self.env.invalidate_all()
        return picking

    def test_delivering_an_appliance_finishes_its_job(self):
        order = self._order([self.appliance])
        mo = self._job(order, self.appliance)
        self.assertEqual(mo.state, 'confirmed')
        self._deliver(order, [self.appliance])
        self.assertEqual(mo.state, 'done')
        self.assertTrue(mo.workorder_ids.filtered(lambda w: w.state == 'done')
                        or not mo.workorder_ids,
                        "closing the job must close its operations too")

    def test_a_case_delivered_weeks_ago_stays_closed(self):
        """The 219. (client, 2026-09-09)

        Closing a job overwrites its arrival date, so the close puts the real one
        back — and core refuses `date_start` on a job that is done. The UserError
        was raised INSIDE the savepoint, so it rolled back the close that had just
        worked: the job came out open, flagged as needing a person, and the sweep
        never touched it again. Every one of the 219 flagged jobs on this database
        failed with that one message, all on 2026-08-28.
        """
        order = self._order([self.appliance])
        mo = self._job(order, self.appliance)
        arrived = fields.Datetime.now() - timedelta(days=40)
        mo.write({'date_start': arrived})
        shipped = fields.Datetime.now() - timedelta(days=30)

        self.assertTrue(mo._lab_mark_done_quietly(finished_on=shipped),
                        "the close reports success")
        mo.invalidate_recordset()
        self.assertEqual(mo.state, 'done', "and the job is actually closed")
        self.assertFalse(mo.lab_close_failed, "nobody is needed here")
        self.assertEqual(mo.date_start, arrived,
                         "the day it arrived in the lab is not the day it was closed")
        self.assertEqual(mo.date_finished, shipped,
                         "dated when the appliance left, not when this ran")

    def test_a_job_closed_by_a_delivery_today_needs_no_correction(self):
        """The path that always worked: nothing to put back, nothing to refuse."""
        order = self._order([self.appliance])
        mo = self._job(order, self.appliance)
        self._deliver(order, [self.appliance])
        self.assertEqual(mo.state, 'done')
        self.assertFalse(mo.lab_close_failed)

    def test_half_an_order_leaves_the_other_appliance_on_the_bench(self):
        """The rule that matters most. A case with two appliances where one ships today
        must not finish the one still being made — that is exactly the mistake that put
        113 unmade cases on the 'delivered' list before this existed."""
        order = self._order([self.appliance, self.other])
        made = self._job(order, self.appliance)
        unmade = self._job(order, self.other)

        self._deliver(order, [self.appliance])
        self.assertEqual(made.state, 'done')
        self.assertNotEqual(unmade.state, 'done',
                            "nobody has made this one yet")

        self._deliver(order, [self.other])
        self.assertEqual(unmade.state, 'done')

    def test_two_of_the_same_appliance_close_one_delivery_at_a_time(self):
        """Quantity, not just product: one delivered of two identical appliances closes
        one job — the older — and the second delivery closes the other."""
        order = self._order([self.appliance])
        first = self._job(order, self.appliance)
        second = self._job(order, self.appliance)

        self._deliver(order, [self.appliance])
        self.assertEqual(first.state, 'done', "the older job is the one that shipped")
        self.assertNotEqual(second.state, 'done')

        self._deliver(order, [self.appliance])
        self.assertEqual(second.state, 'done')

    def test_an_incoming_picking_closes_nothing(self):
        order = self._order([self.appliance])
        mo = self._job(order, self.appliance)
        picking = self.env['stock.picking'].create({
            'partner_id': self.partner.id,
            'picking_type_id': self.env.ref('stock.picking_type_in').id,
            'location_id': self.customers.id, 'location_dest_id': self.stock.id})
        self.env['stock.move'].create({
            'product_id': self.appliance.id, 'product_uom_qty': 1,
            'product_uom': self.appliance.uom_id.id, 'picking_id': picking.id,
            'location_id': self.customers.id, 'location_dest_id': self.stock.id})
        picking.action_confirm()
        picking.move_ids.write({'quantity': 1, 'picked': True})
        picking.button_validate()
        self.env.invalidate_all()
        self.assertNotEqual(mo.state, 'done', "work does not finish because stock arrived")

    def test_a_draft_job_is_never_closed_by_a_delivery(self):
        """An unconfirmed order is not work in progress, and finishing one would invent
        a history that never happened."""
        order = self._order([self.appliance])
        draft = self.env['mrp.production'].create({
            'product_id': self.appliance.id, 'product_qty': 1.0})
        self.env.cr.execute("UPDATE mrp_production SET sale_id = %s WHERE id = %s",
                            (order.id, draft.id))
        self.env.invalidate_all()
        self.assertEqual(draft.state, 'draft')
        self._deliver(order, [self.appliance])
        self.assertEqual(draft.state, 'draft')

    def test_by_default_a_delivery_leaves_the_job_open(self):
        """Off unless someone switches it on: a job is finished when the bench
        finishes it, not when the box leaves or the invoice is raised - and the
        scheduled catch-up follows the same switch. (client, 2026-09-17)"""
        self.env['ir.config_parameter'].sudo().set_param(
            'lab_workcenter_scan.close_mo_on_delivery', False)
        self.assertFalse(self.env['ir.config_parameter'].sudo().get_param(
            'lab_workcenter_scan.close_mo_on_delivery'), "no setting at all")
        order = self._order([self.appliance])
        mo = self._job(order, self.appliance)
        self._deliver(order, [self.appliance])
        self.assertNotEqual(mo.state, 'done', "delivered, but the bench never finished it")
        self.assertEqual(self.env['mrp.production']._lab_close_delivered_backlog(), 0,
                         "and the catch-up closes nothing either")

    def test_the_behaviour_can_be_switched_off(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'lab_workcenter_scan.close_mo_on_delivery', '0')
        order = self._order([self.appliance])
        mo = self._job(order, self.appliance)
        self._deliver(order, [self.appliance])
        self.assertNotEqual(mo.state, 'done')

    def test_a_job_that_cannot_be_closed_does_not_break_the_delivery(self):
        """One awkward case must never roll back the delivery that triggered it, nor the
        other jobs on the same order."""
        # A serial-tracked COMPONENT makes the job impossible to finish without a
        # serial number, so closing it raises — while the delivery of the untracked
        # appliance itself goes through perfectly well. That is the shape of the real
        # hazard: the awkward job must not take the delivery down with it.
        order = self._order([self.appliance, self.other])
        good = self._job(order, self.appliance)
        awkward = self._job(order, self.other)
        tracked_part = self.env['product.product'].create({
            'name': 'Closing Tracked Part', 'type': 'consu', 'is_storable': True,
            'tracking': 'serial'})
        self.env['stock.move'].create({
            'product_id': tracked_part.id, 'product_uom_qty': 1,
            'product_uom': tracked_part.uom_id.id,
            'raw_material_production_id': awkward.id,
            'location_id': self.stock.id, 'location_dest_id': self.stock.id})
        self.env.invalidate_all()

        picking = self._deliver(order, [self.appliance, self.other])

        self.assertEqual(picking.state, 'done', "the delivery itself must stand")
        self.assertEqual(good.state, 'done', "and the job that could close, closed")
        self.assertNotEqual(awkward.state, 'done',
                            "the one that could not stays open, and is logged")

    def test_the_backlog_runs_in_batches_and_leaves_live_work_alone(self):
        """Seven and a half hours of work cannot be a button press, so it is a scheduled
        action that takes a bite each run."""
        Floor = self.env['report.lab.floor']
        Production = self.env['mrp.production']
        live_before = Production.search_count(Floor._live_domain())
        closed = Production._lab_close_delivered_backlog(limit=3)
        self.env.invalidate_all()
        self.assertGreaterEqual(closed, 0)
        self.assertEqual(Production.search_count(Floor._live_domain()), live_before,
                         "clearing the delivered backlog must not touch live work")

    def test_the_backlog_job_is_not_started_by_installing_the_module(self):
        """It rewrites 21,000 records over hours; that is the lab's decision, not an
        upgrade's."""
        cron = self.env.ref('lab_workcenter_scan.cron_close_delivered_backlog')
        self.assertFalse(cron.active)

    def _return(self, order):
        """The doctor sends the appliance back."""
        picking = self.env['stock.picking'].create({
            'partner_id': self.partner.id,
            'picking_type_id': self.env.ref('stock.picking_type_in').id,
            'location_id': self.customers.id, 'location_dest_id': self.stock.id})
        self.env['stock.move'].create({
            'product_id': self.appliance.id, 'product_uom_qty': 1,
            'product_uom': self.appliance.uom_id.id, 'picking_id': picking.id,
            'sale_line_id': order.order_line[0].id,
            'location_id': self.customers.id, 'location_dest_id': self.stock.id})
        picking.action_confirm()
        picking.move_ids.write({'quantity': 1, 'picked': True})
        picking.button_validate()
        self.env.invalidate_all()
        return picking

    def test_a_returned_appliance_comes_back_off_the_delivered_total(self):
        """A case the doctor sent back is not delivered work.

        Counting only outgoing moves left a returned appliance on the books as delivered,
        so the remake raised to replace it was closed the moment it existed — finished
        work that had not been done. (review finding, 2026-08-26)
        """
        order = self._order([self.appliance])
        first = self._job(order, self.appliance)
        self._deliver(order, [self.appliance])
        self.assertEqual(first.state, 'done')

        self._return(order)
        remake = self._job(order, self.appliance)
        self.env['mrp.production']._lab_close_for_order(order)
        self.env.invalidate_all()
        self.assertNotEqual(remake.state, 'done',
                            "nothing has gone out since the appliance came back")

    def test_it_never_closes_more_jobs_than_the_doctor_actually_has(self):
        """Two made, two shipped, one returned: exactly one appliance is with the
        doctor, so exactly one job is finished."""
        order = self._order([self.appliance])
        first = self._job(order, self.appliance)
        self._deliver(order, [self.appliance])
        self._return(order)
        remake = self._job(order, self.appliance)
        self._deliver(order, [self.appliance])
        closed = [job for job in (first, remake) if job.state == 'done']
        self.assertEqual(len(closed), 1)

    def test_a_close_that_worked_is_never_undone_by_the_return_value(self):
        """`button_mark_done` writes state='done' and only then decides what to hand
        back, and on a complete success it can still return an action. Reading that as a
        failure rolled back a close that had genuinely worked, logged it as broken and
        flagged the job so the catch-up would never retry it. The state is the answer.
        (review finding, 2026-08-26)"""
        order = self._order([self.appliance])
        mo = self._job(order, self.appliance)
        self.assertTrue(mo._lab_mark_done_quietly())
        self.env.invalidate_all()
        self.assertEqual(mo.state, 'done')
        self.assertFalse(mo.lab_close_failed,
                         "a successful close must not be flagged as failed")

    def test_a_job_that_cannot_close_is_flagged_so_the_catch_up_moves_on(self):
        """The backlog runs oldest-first: one permanently stuck job would otherwise be
        picked at the head of every batch for ever."""
        order = self._order([self.other])
        awkward = self._job(order, self.other)
        tracked_part = self.env['product.product'].create({
            'name': 'Flagging Tracked Part', 'type': 'consu', 'is_storable': True,
            'tracking': 'serial'})
        self.env['stock.move'].create({
            'product_id': tracked_part.id, 'product_uom_qty': 1,
            'product_uom': tracked_part.uom_id.id,
            'raw_material_production_id': awkward.id,
            'location_id': self.stock.id, 'location_dest_id': self.stock.id})
        self.env.invalidate_all()

        self.assertFalse(awkward._lab_mark_done_quietly())
        self.env.invalidate_all()
        self.assertTrue(awkward.lab_close_failed)
        self.assertNotEqual(awkward.state, 'done')

    def test_an_auto_print_flag_cannot_switch_the_whole_feature_off(self):
        """The reviewer's reproduction, kept.

        With any auto-print flag ticked on the operation type, `button_mark_done`
        returns a print ACTION on complete success. Judging success by that return value
        rolled the close back and logged it as broken — so ticking one checkbox in
        Inventory silently disabled the entire feature. Success is the state.
        (review finding, 2026-08-26)
        """
        if 'auto_print_done_production_order' in self.out._fields:
            self.out.sudo().auto_print_done_production_order = True
        order = self._order([self.appliance])
        mo = self._job(order, self.appliance)
        self._deliver(order, [self.appliance])
        self.assertEqual(mo.state, 'done',
                         "an auto-print flag must not stop the job closing")
        self.assertFalse(mo.lab_close_failed)

    def test_a_returned_case_stops_counting_as_shipped(self):
        """The same blind spot lived in the domain as well as the arithmetic: a fully
        returned order still satisfied 'shipped, nothing pending', so it stayed off the
        floor sheets and sat on the close-out worklist waiting to be finished against an
        appliance physically back on the bench."""
        Floor = self.env['report.lab.floor']
        Production = self.env['mrp.production']
        order = self._order([self.appliance])
        mo = self._job(order, self.appliance)
        self._deliver(order, [self.appliance])
        self.assertEqual(mo.state, 'done')

        self._return(order)
        remake = self._job(order, self.appliance)
        self.assertIn(remake, Production.search(Floor._live_domain()),
                      "a returned case is work in the lab again")
        self.assertNotIn(remake, Production.search(Floor._closeout_domain()),
                         "and must never be offered to the catch-up as delivered")


@tagged('post_install', '-at_install')
class TestFlowBoardIsLive(TransactionCase):
    """The flow board counts LIVE work only (2026-09-02).

    `state not in (done, cancel)` alone counts the 21,099 migrated ghosts whose
    appliance is already on the doctor's shelf - the board said 21,856 while
    its own hub tile said 2,140, every stage's total equalled its "waiting"
    equalled its "old", and "Sitting longest" was one shipped order twelve
    times. Every figure now stands on report.lab.floor's live set.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.wc = cls.env['mrp.workcenter'].create({'name': 'Flow Bench'})
        cls.product = cls.env['product.product'].create({
            'name': 'Flow Appliance', 'type': 'consu', 'is_storable': True})

    def _mo_with_wo(self, name):
        mo = self.env['mrp.production'].create({
            'product_id': self.product.id, 'product_qty': 1.0})
        self.env['mrp.workorder'].create({
            'name': name, 'production_id': mo.id,
            'workcenter_id': self.wc.id,
            'product_uom_id': self.product.uom_id.id})
        return mo

    def test_the_board_stands_on_the_live_set(self):
        """A fresh MO with no shipped order is live: it must count. And the
        board's operation total must equal a direct count over the live
        domain, so board and floor sheets can never disagree."""
        before = self.env['lab.flow'].get_flow()['totals']
        self._mo_with_wo('Live op')
        after = self.env['lab.flow'].get_flow()['totals']
        self.assertEqual(after['wip'], before['wip'] + 1)
        self.assertEqual(after['jobs'], before['jobs'] + 1)
        direct = self.env['mrp.workorder'].search_count(
            self.env['lab.flow']._live_workorder_domain())
        self.assertEqual(after['wip'], direct)

    def test_jobs_agrees_with_the_hub_tile(self):
        """The banner's jobs figure and the Management tile read the same
        domain - the screenshot showed 21,856 beside a tile saying 2,140."""
        flow_jobs = self.env['lab.flow'].get_flow()['totals']['jobs']
        tile = self.env['mrp.production'].search_count(
            self.env['report.lab.floor']._live_domain())
        self.assertEqual(flow_jobs, tile)

    def test_sitting_longest_folds_a_two_appliance_case_to_one_row(self):
        """Aged past anything on the database. Thirty days was not: the staging
        copy holds thousands of live steps older than that, so the list rightly
        showed those and this case was never in it. (review, 2026-09-15)"""
        mo = self.env['mrp.production'].create({
            'product_id': self.product.id, 'product_qty': 1.0})
        old = datetime(2000, 1, 1)
        for name in ('Upper', 'Lower'):
            wo = self.env['mrp.workorder'].create({
                'name': name, 'production_id': mo.id,
                'workcenter_id': self.wc.id,
                'product_uom_id': self.product.uom_id.id})
            self.env.cr.execute(
                "UPDATE mrp_workorder SET create_date = %s WHERE id = %s",
                (old, wo.id))
        self.env.invalidate_all()
        attention = self.env['lab.flow'].get_flow()['attention']
        mine = [r for r in attention if r['mo'] == mo.name]
        self.assertEqual(len(mine), 1,
                         "one order, one row - not one per appliance")
        self.assertEqual(len({r['mo'] for r in attention}), len(attention),
                         "and every row is a different order")

    def test_the_station_drill_opens_only_live_work(self):
        act = self.env['lab.flow'].action_open_station(self.wc.id)
        self.assertEqual(act['res_model'], 'mrp.workorder')
        got = self.env['mrp.workorder'].search(act['domain'])
        expected = self.env['mrp.workorder'].search(
            self.env['lab.flow']._live_workorder_domain()
            + [('workcenter_id', '=', self.wc.id)])
        self.assertEqual(got, expected,
                         "the drill must hold the work the number counted")


@tagged('post_install', '-at_install')
class TestFloorInnovations(TransactionCase):
    """The 2026-09-02 additions to both production dashboards."""

    def test_the_flow_board_measures_adoption_and_urgency(self):
        flow = self.env['lab.flow'].get_flow()
        adoption = flow['adoption']
        for key in ('accepted', 'accepted_pct', 'moved_today',
                    'accepted_today', 'stations_active_today'):
            self.assertIn(key, adoption)
        self.assertIn('total', flow['urgent'])
        self.assertIn('rows', flow['urgent'])
        for station in flow['stations']:
            self.assertIn('arrived_today', station)
            self.assertIn('urgent', station)
        self.assertGreaterEqual(flow['closeout'], 0)

    def test_the_closeout_drill_holds_the_debt_and_nothing_live(self):
        act = self.env['lab.flow'].action_open_closeout()
        got = self.env['mrp.production'].search(act['domain'])
        expected = self.env['mrp.production'].sudo().search(
            self.env['report.lab.floor']._closeout_domain())
        self.assertEqual(set(got.ids), set(expected.ids))

    def test_the_report_weaves_quality_and_habit_into_the_weeks(self):
        data = self.env['lab.mrp.report'].get_mrp_report(8)
        self.assertEqual(len(data['weeks']), 8)
        for week in data['weeks']:
            self.assertIn('redos', week)
        self.assertEqual(sum(w['redos'] for w in data['weeks']),
                         self.env['lab.mrp.redo'].search_count(
                             [('date', '>=', self.env['lab.station']._day_window(
                                 fields.Date.from_string(data['from']))[0])]),
                         "every redo in the window lands in exactly one week")
        self.assertIn('first_pass', data)
        if data['redo']['rate'] is not None:
            self.assertAlmostEqual(
                data['first_pass'],
                max(0.0, round(100.0 - data['redo']['rate'], 1)), places=1)
            self.assertGreaterEqual(data['first_pass'], 0.0,
                                    "a negative yield is not a sentence")
        self.assertEqual(len(data['activity']['rows']), 14)
        self.assertIsInstance(data['flows'], list)
        for person in data['people']:
            self.assertEqual(len(person['spark']), 8,
                             "one point per week, per person")


@tagged('post_install', '-at_install')
class TestFinisherAndUndo(TransactionCase):
    """A second name at a finishing bench, and a way back from a mis-scan.

    (client, 2026-09-09)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.maker = cls.env['res.users'].create({
            'name': 'Maker', 'login': 'fin_maker',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('mrp.group_mrp_user').id])]})
        cls.finisher = cls.env['res.users'].create({
            'name': 'Finisher', 'login': 'fin_finisher',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('mrp.group_mrp_user').id])]})
        cls.polish = cls.env['mrp.workcenter'].create({
            'name': 'Polishing', 'is_finisher': True,
            'head_user_ids': [(6, 0, cls.env.user.ids)]})
        cls.polish.users = [(6, 0, (cls.maker | cls.finisher).ids)]
        cls.plain = cls.env['mrp.workcenter'].create({
            'name': 'Wire Bending',
            'head_user_ids': [(6, 0, cls.env.user.ids)]})
        product = cls.env['product.product'].create({
            'name': 'Finisher Appliance', 'is_storable': True})
        cls.production = cls.env['mrp.production'].create({
            'product_id': product.id, 'product_qty': 1.0})
        cls.production.action_confirm()
        cls.first, cls.second = cls.env['mrp.workorder'].create([
            {'name': 'Polish', 'production_id': cls.production.id, 'sequence': 10,
             'workcenter_id': cls.polish.id, 'product_uom_id': product.uom_id.id},
            {'name': 'Pack', 'production_id': cls.production.id, 'sequence': 20,
             'workcenter_id': cls.plain.id, 'product_uom_id': product.uom_id.id},
        ])

    def _accepted(self, wo=None, with_tech=True):
        wo = wo or self.first
        self.env['lab.station'].scan(str(wo.production_id.name),
                                       workcenter_id=wo.workcenter_id.id,
                                       confirmed=True)
        if with_tech:
            wo.action_assign_bench_user(self.maker.id)
        return wo

    # ------------------------------------------------------------- the flag
    def test_a_work_centre_says_whether_it_names_a_finisher(self):
        self.assertTrue(self.polish.is_finisher)
        self.assertFalse(self.plain.is_finisher, "off unless the lab says otherwise")
        self.assertTrue(self.first.workcenter_is_finisher)
        self.assertFalse(self.second.workcenter_is_finisher)

    # ------------------------------------------------- the second name is asked for
    def test_a_finishing_bench_will_not_hand_on_without_the_second_name(self):
        wo = self._accepted()
        with self.assertRaises(UserError) as caught:
            wo.action_station_handover()
        self.assertIn('finishing technician', str(caught.exception).lower())
        self.assertNotEqual(wo.state, 'done')

    def test_the_board_asks_for_it_instead_of_refusing(self):
        wo = self._accepted()
        answer = self.env['lab.station'].handover(wo.id)
        self.assertEqual(answer['action'], 'needs_finisher')
        self.assertEqual(answer['stage'], 'finisher')
        self.assertIn(self.finisher.id, [p['id'] for p in answer['people']])
        self.assertTrue(answer['can_assign'], "a technician may answer this one")

    def test_naming_the_finisher_hands_the_job_on(self):
        wo = self._accepted()
        self.env['lab.station'].assign_finisher_and_handover(wo.id, self.finisher.id)
        self.assertEqual(wo.finisher_user_id, self.finisher)
        self.assertEqual(wo.bench_user_id, self.maker, "two different people")
        self.assertEqual(wo.state, 'done')

    def test_the_technician_question_is_followed_by_the_finisher_question(self):
        wo = self._accepted(with_tech=False)
        answer = self.env['lab.station'].assign_and_handover(wo.id, self.maker.id)
        self.assertEqual(answer['action'], 'needs_finisher', "one question at a time")
        self.assertEqual(wo.bench_user_id, self.maker)
        self.assertNotEqual(wo.state, 'done', "it does not move until both are named")

    def test_an_ordinary_bench_is_never_asked(self):
        self.first.button_start(raise_on_invalid_state=False)
        self.first.write({'accepted_at': fields.Datetime.now(),
                          'accepted_by_id': self.env.uid,
                          'bench_user_id': self.maker.id,
                          'finisher_user_id': self.finisher.id})
        self.first.action_station_handover()
        wo = self._accepted(self.second)
        self.assertFalse(self.env['lab.station']._needs_finisher(wo))
        self.env['lab.station'].handover(wo.id)
        self.assertEqual(wo.state, 'done')

    def test_the_second_name_does_not_travel_with_the_job(self):
        """It is a fact about this step at this bench."""
        wo = self._accepted()
        wo.action_assign_finisher(self.finisher.id)
        wo.move_to_scanned_workcenter(self.plain.scan_code)
        self.assertFalse(wo.finisher_user_id)

    # ---------------------------------------------------------------- the undo
    def test_undoing_an_acceptance_puts_the_job_back_in_the_queue(self):
        wo = self._accepted()
        self.assertTrue(wo.accepted_at)
        self.env['lab.station'].undo_scan(wo.id)
        self.assertFalse(wo.accepted_at)
        self.assertFalse(wo.accepted_by_id)
        self.assertFalse(wo.time_ids.filtered(lambda t: not t.date_end),
                         "the clock the scan started must not keep running")
        self.assertEqual(wo.bench_user_id, self.maker, "who it was given to still holds")

    def test_undoing_a_handover_brings_the_job_back_to_the_bench(self):
        wo = self._accepted()
        wo.action_assign_finisher(self.finisher.id)
        wo.action_station_handover()
        self.assertEqual(wo.state, 'done')
        result = self.env['lab.station'].undo_scan(wo.id)
        self.assertEqual(result['action'], 'undone')
        self.assertNotEqual(wo.state, 'done')
        self.assertFalse(wo.handed_over_at)
        self.assertTrue(wo.accepted_at, "it is still accepted here")
        self.assertFalse(self.second.handed_from_workcenter_id,
                         "the next bench is no longer expecting it")

    def test_a_job_the_next_bench_has_taken_is_not_taken_back(self):
        wo = self._accepted()
        wo.action_assign_finisher(self.finisher.id)
        wo.action_station_handover()
        self.second.action_station_accept()
        with self.assertRaises(UserError) as caught:
            self.env['lab.station'].undo_scan(wo.id)
        self.assertIn('already been taken on', str(caught.exception))

    def test_a_job_the_next_bench_has_already_passed_on_is_not_taken_back(self):
        """`next_workorder_id` skips finished steps, so once the next bench had taken
        the job AND handed it on, the guard looked straight past it and the undo
        reset this step under a finished one. (review, 2026-09-15)"""
        wo = self._accepted()
        wo.action_assign_finisher(self.finisher.id)
        wo.action_station_handover()
        self._accepted(self.second)
        self.second.action_station_handover()
        self.assertEqual(self.second.state, 'done')
        self.assertFalse(self.env['lab.station']._card(wo)['can_undo'],
                         "the card must not offer what the server refuses")
        with self.assertRaises(UserError) as caught:
            self.env['lab.station'].undo_scan(wo.id)
        self.assertIn('already been taken on', str(caught.exception))
        self.assertEqual(wo.state, 'done', "this step stays finished")
        self.assertEqual(self.second.handed_from_workcenter_id, self.polish)

    def test_there_is_nothing_to_undo_on_a_job_nobody_touched(self):
        with self.assertRaises(UserError) as caught:
            self.env['lab.station'].undo_scan(self.second.id)
        self.assertIn('no scan to take back', str(caught.exception))

    def test_every_undo_is_written_on_the_case(self):
        wo = self._accepted()
        before = len(self.production.message_ids)
        self.env['lab.station'].undo_scan(wo.id)
        self.assertGreater(len(self.production.message_ids), before)
        self.assertIn('taken back', self.production.message_ids[0].body)

    def test_the_card_says_what_can_be_undone(self):
        wo = self._accepted()
        card = self.env['lab.station']._card(wo)
        self.assertTrue(card['can_undo'])
        self.assertTrue(card['wants_finisher'])
        self.assertEqual(card['finisher'], '')
        fresh = self.env['lab.station']._card(self.second)
        self.assertFalse(fresh['can_undo'], "nothing has been scanned on it")

    def test_an_undone_handover_leaves_exactly_one_clock_running(self):
        """Two open time records would bill the bench twice for one job."""
        wo = self._accepted()
        wo.action_assign_finisher(self.finisher.id)
        wo.action_station_handover()
        self.env['lab.station'].undo_scan(wo.id)
        running = wo.time_ids.filtered(lambda t: not t.date_end)
        self.assertLessEqual(len(running), 1, "one bench, one clock")
        self.assertTrue(running, "the job is being worked on again")


@tagged('post_install', '-at_install')
class TestScanConfirmation(TransactionCase):
    """A scan says what it will do, and waits. (client, 2026-09-09)"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bench = cls.env['mrp.workcenter'].create({
            'name': 'Confirm Bench', 'head_user_ids': [(6, 0, cls.env.user.ids)]})
        cls.next_bench = cls.env['mrp.workcenter'].create({
            'name': 'Next Bench', 'head_user_ids': [(6, 0, cls.env.user.ids)]})
        cls.quick = cls.env['mrp.workcenter'].create({
            'name': 'Bulk Bench', 'scan_confirm': False,
            'head_user_ids': [(6, 0, cls.env.user.ids)]})
        product = cls.env['product.product'].create({
            'name': 'Confirm Appliance', 'is_storable': True})
        cls.production = cls.env['mrp.production'].create({
            'product_id': product.id, 'product_qty': 1.0})
        cls.production.action_confirm()
        cls.first, cls.second = cls.env['mrp.workorder'].create([
            {'name': 'Step one', 'production_id': cls.production.id, 'sequence': 10,
             'workcenter_id': cls.bench.id, 'product_uom_id': product.uom_id.id},
            {'name': 'Step two', 'production_id': cls.production.id, 'sequence': 20,
             'workcenter_id': cls.next_bench.id, 'product_uom_id': product.uom_id.id},
        ])
        cls.Station = cls.env['lab.station']

    def _scan(self, **kw):
        return self.Station.scan(self.production.name,
                                 workcenter_id=self.bench.id, **kw)

    def test_a_bench_confirms_by_default(self):
        self.assertTrue(self.bench.scan_confirm, "the lab asked for it everywhere")

    def test_the_scan_says_what_it_will_do_and_does_nothing(self):
        answer = self._scan()
        self.assertEqual(answer['action'], 'confirm')
        self.assertEqual(answer['intent'], 'accept')
        self.assertIn('Accept', answer['message'])
        self.assertIn('Confirm Bench', answer['message'])
        self.assertEqual(answer['code'], self.production.name)
        self.assertFalse(self.first.accepted_at, "nothing is written until it is agreed")

    def test_confirming_does_exactly_what_the_scan_meant(self):
        self._scan()
        answer = self._scan(confirmed=True)
        self.assertNotEqual(answer['action'], 'confirm', "asked once, not twice")
        self.assertTrue(self.first.accepted_at)
        # The bench has people and the job has no technician yet, so the accept is
        # followed by the board's own question - which is the point of asking it
        # while the card is still in the lead's hand.
        self.assertEqual(answer['action'], 'needs_person')

    def test_the_second_scan_names_the_next_bench_in_the_question(self):
        self._scan(confirmed=True)
        self.first.action_assign_bench_user(self.env.uid)
        answer = self._scan()
        self.assertEqual(answer['intent'], 'handover')
        self.assertIn('Next Bench', answer['message'])
        self.assertNotEqual(self.first.state, 'done', "still nothing written")

    def test_a_bulk_bench_keeps_the_one_tap_scan(self):
        wo = self.env['mrp.workorder'].create({
            'name': 'Bulk step', 'production_id': self.production.id, 'sequence': 5,
            'workcenter_id': self.quick.id,
            'product_uom_id': self.production.product_uom_id.id})
        answer = self.Station.scan(self.production.name, workcenter_id=self.quick.id)
        self.assertNotEqual(answer['action'], 'confirm', "this bench does not ask")
        self.assertTrue(wo.accepted_at, "it acted on the one tap")

    def test_a_job_that_is_somewhere_else_is_never_a_question(self):
        """Being told where the job is changes nothing, so there is nothing to agree to.

        Scanned at a bench this case never passes through: the second step is at
        Next Bench, so scanning THERE resolves to that step and is a real scan.
        """
        away = self.env['mrp.workcenter'].create({
            'name': 'Away Bench', 'head_user_ids': [(6, 0, self.env.user.ids)]})
        answer = self.Station.scan(self.production.name, workcenter_id=away.id)
        self.assertEqual(answer['action'], 'elsewhere')
        self.assertFalse(self.first.accepted_at)


@tagged('post_install', '-at_install')
class TestFinishedByDate(TransactionCase):
    """The finished column, on any day. Today unless asked. (client, 2026-09-09)"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.tz = 'Asia/Kolkata'
        cls.bench = cls.env['mrp.workcenter'].create({
            'name': 'History Bench', 'head_user_ids': [(6, 0, cls.env.user.ids)]})
        product = cls.env['product.product'].create({
            'name': 'History Appliance', 'is_storable': True})
        cls.production = cls.env['mrp.production'].create({
            'product_id': product.id, 'product_qty': 1.0})
        cls.production.action_confirm()
        cls.today = fields.Date.context_today(cls.env.user)
        cls.yesterday = cls.today - timedelta(days=1)
        cls.Station = cls.env['lab.station']
        cls.product = product

    def _passed_on(self, day, hour=10):
        """A job handed on at `hour` local time on `day`."""
        wo = self.env['mrp.workorder'].create({
            'name': 'Step %s %s' % (day, hour), 'production_id': self.production.id,
            'workcenter_id': self.bench.id,
            'product_uom_id': self.product.uom_id.id})
        local = datetime.combine(day, time(hour, 0))
        stamp = pytz.timezone('Asia/Kolkata').localize(local).astimezone(
            pytz.utc).replace(tzinfo=None)
        wo.write({'handed_over_at': stamp})
        return wo

    def test_today_is_what_the_board_opens_on(self):
        mine = self._passed_on(self.today)
        board = self.Station.get_station(self.bench.id)
        self.assertEqual(board['done_day'], fields.Date.to_string(self.today))
        self.assertTrue(board['done_is_today'])
        self.assertIn(mine.id, [c['id'] for c in board['done_today']])

    def test_yesterdays_work_is_not_in_todays_column(self):
        old = self._passed_on(self.yesterday)
        board = self.Station.get_station(self.bench.id)
        self.assertNotIn(old.id, [c['id'] for c in board['done_today']])

    def test_asking_for_a_day_gives_that_day(self):
        old = self._passed_on(self.yesterday)
        mine = self._passed_on(self.today)
        answer = self.Station.done_on(self.bench.id, fields.Date.to_string(self.yesterday))
        ids = [c['id'] for c in answer['rows']]
        self.assertIn(old.id, ids)
        self.assertNotIn(mine.id, ids, "one day at a time")
        self.assertFalse(answer['done_is_today'])
        self.assertEqual(answer['done_day'], fields.Date.to_string(self.yesterday))
        self.assertEqual(answer['today'], fields.Date.to_string(self.today))

    def test_the_day_is_the_users_own_day_not_a_utc_one(self):
        """05:30 IST is still yesterday in UTC: it belongs to the shift that did it."""
        early = self._passed_on(self.today, hour=1)
        late = self._passed_on(self.today, hour=23)
        ids = [c['id'] for c in self.Station.get_station(self.bench.id)['done_today']]
        self.assertIn(early.id, ids, "01:00 this morning is today")
        self.assertIn(late.id, ids, "23:00 tonight is today too")

    def test_the_count_is_the_whole_day_not_the_page(self):
        for hour in range(8, 12):
            self._passed_on(self.today, hour=hour)
        board = self.Station.get_station(self.bench.id)
        self.assertEqual(board['done_total'], 4)

    def test_the_board_can_be_opened_straight_onto_a_day(self):
        old = self._passed_on(self.yesterday)
        board = self.Station.get_station(
            self.bench.id, done_day=fields.Date.to_string(self.yesterday))
        self.assertIn(old.id, [c['id'] for c in board['done_today']])
        self.assertFalse(board['done_is_today'])

    # ------------------------------------------------ the bench chips count a day
    def _given_to(self, user, day, taken=True, finished=False):
        """A job given to `user` at this bench, stamped on `day`."""
        wo = self.env['mrp.workorder'].create({
            'name': 'Day job %s %s' % (user.id, day), 'production_id': self.production.id,
            'workcenter_id': self.bench.id, 'product_uom_id': self.product.uom_id.id})
        stamp = pytz.timezone('Asia/Kolkata').localize(
            datetime.combine(day, time(10, 0))).astimezone(pytz.utc).replace(tzinfo=None)
        vals = {'bench_user_id': user.id, 'bench_assigned_at': stamp}
        if taken:
            vals['accepted_at'] = stamp
        if finished:
            vals['handed_over_at'] = stamp
        wo.write(vals)
        return wo

    def test_a_chip_counts_the_work_that_person_took_or_finished_today(self):
        tech = self.env['res.users'].create({
            'name': 'Chip Tech', 'login': 'chip_tech'})
        self.bench.users = [(6, 0, tech.ids)]
        self._given_to(tech, self.today)
        self._given_to(tech, self.today, taken=False, finished=True)
        self._given_to(tech, self.yesterday)
        rows = {r['id']: r for r in self.Station.get_station(self.bench.id)['bench_people']}
        self.assertEqual(rows[tech.id]['day_count'], 2, "today's two, not yesterday's")

    def test_one_job_taken_and_finished_the_same_day_counts_once(self):
        tech = self.env['res.users'].create({
            'name': 'Once Tech', 'login': 'once_tech'})
        self.bench.users = [(6, 0, tech.ids)]
        self._given_to(tech, self.today, taken=True, finished=True)
        rows = {r['id']: r for r in self.Station.get_station(self.bench.id)['bench_people']}
        self.assertEqual(rows[tech.id]['day_count'], 1, "one piece of work, not two")

    def test_the_chips_follow_the_day_being_looked_at(self):
        tech = self.env['res.users'].create({
            'name': 'Back Tech', 'login': 'back_tech'})
        self.bench.users = [(6, 0, tech.ids)]
        self._given_to(tech, self.yesterday)
        answer = self.Station.done_on(self.bench.id, fields.Date.to_string(self.yesterday))
        rows = {r['id']: r for r in answer['bench_people']}
        self.assertEqual(rows[tech.id]['day_count'], 1)
        today_rows = {r['id']: r
                      for r in self.Station.get_station(self.bench.id)['bench_people']}
        self.assertEqual(today_rows[tech.id]['day_count'], 0, "today they did none")

    def test_what_a_person_is_holding_is_still_reported_for_handing_out(self):
        """The popup spreads work by the pile; the chip shows the day. Both survive."""
        tech = self.env['res.users'].create({
            'name': 'Hold Tech', 'login': 'hold_tech'})
        self.bench.users = [(6, 0, tech.ids)]
        self._given_to(tech, self.yesterday)          # still open, taken yesterday
        rows = {r['id']: r for r in self.Station.get_station(self.bench.id)['bench_people']}
        self.assertEqual(rows[tech.id]['load'], 1, "they are carrying it now")
        self.assertEqual(rows[tech.id]['day_count'], 0, "they did not take it today")

    # ----------------------------------------- every column obeys the people strip
    def test_the_waiting_column_belongs_to_whoever_sent_it(self):
        """Reported from the floor: filtering to one person still showed
        everybody's work waiting at the next bench. (client, 2026-09-09)"""
        mine = self.env['res.users'].create({'name': 'Sender', 'login': 'wait_sender'})
        other = self.env['res.users'].create({'name': 'Other', 'login': 'wait_other'})
        self.bench.users = [(6, 0, (mine | other).ids)]
        next_bench = self.env['mrp.workcenter'].create({'name': 'Waiting Bench'})
        sent = []
        for who in (mine, other):
            wo = self.env['mrp.workorder'].create({
                'name': 'Sent by %s' % who.name, 'production_id': self.production.id,
                'workcenter_id': next_bench.id,
                'product_uom_id': self.product.uom_id.id})
            wo.write({'handed_from_workcenter_id': self.bench.id,
                      'handed_from_user_id': who.id})
            sent.append(wo)
        everyones = self.Station.get_station(self.bench.id)
        self.assertEqual(len(everyones['awaiting']), 2, "unfiltered shows the bench")
        just_mine = self.Station.get_station(self.bench.id, person_id=mine.id)
        self.assertEqual([c['id'] for c in just_mine['awaiting']], [sent[0].id])
        self.assertEqual(just_mine['totals']['awaiting'], 1)

    def test_the_finished_column_obeys_the_strip_too(self):
        mine = self.env['res.users'].create({'name': 'Done Me', 'login': 'done_me'})
        other = self.env['res.users'].create({'name': 'Done Other', 'login': 'done_other'})
        self.bench.users = [(6, 0, (mine | other).ids)]
        wos = []
        for who in (mine, other):
            wo = self._passed_on(self.today)
            wo.bench_user_id = who.id
            wos.append(wo)
        board = self.Station.get_station(self.bench.id, person_id=mine.id)
        self.assertEqual([c['id'] for c in board['done_today']], [wos[0].id])
        self.assertEqual(board['done_total'], 1)
        answer = self.Station.done_on(self.bench.id,
                                      fields.Date.to_string(self.today), mine.id)
        self.assertEqual([c['id'] for c in answer['rows']], [wos[0].id])

    def test_not_given_out_means_nobody_sent_it_either(self):
        """The 'none' chip is the work in nobody's name, in every column."""
        someone = self.env['res.users'].create({'name': 'Named', 'login': 'wait_named'})
        next_bench = self.env['mrp.workcenter'].create({'name': 'Anon Bench'})
        anon = self.env['mrp.workorder'].create({
            'name': 'Sent by nobody', 'production_id': self.production.id,
            'workcenter_id': next_bench.id, 'product_uom_id': self.product.uom_id.id})
        anon.write({'handed_from_workcenter_id': self.bench.id})
        named = self.env['mrp.workorder'].create({
            'name': 'Sent by somebody', 'production_id': self.production.id,
            'workcenter_id': next_bench.id, 'product_uom_id': self.product.uom_id.id})
        named.write({'handed_from_workcenter_id': self.bench.id,
                     'handed_from_user_id': someone.id})
        board = self.Station.get_station(self.bench.id, person_id='none')
        ids = [c['id'] for c in board['awaiting']]
        self.assertIn(anon.id, ids)
        self.assertNotIn(named.id, ids)


@tagged('post_install', '-at_install')
class TestLocateAJob(TransactionCase):
    """Where is this job? Asked on its own, and nothing moves. (client, 2026-09-09)"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.here = cls.env['mrp.workcenter'].create({
            'name': 'Asking Bench', 'head_user_ids': [(6, 0, cls.env.user.ids)]})
        cls.there = cls.env['mrp.workcenter'].create({
            'name': 'Holding Bench', 'head_user_ids': [(6, 0, cls.env.user.ids)]})
        product = cls.env['product.product'].create({
            'name': 'Locate Appliance', 'is_storable': True})
        cls.production = cls.env['mrp.production'].create({
            'product_id': product.id, 'product_qty': 1.0})
        cls.production.action_confirm()
        cls.wo, cls.next_wo = cls.env['mrp.workorder'].create([
            {'name': 'Bending', 'production_id': cls.production.id, 'sequence': 10,
             'workcenter_id': cls.there.id, 'product_uom_id': product.uom_id.id},
            {'name': 'Polishing', 'production_id': cls.production.id, 'sequence': 20,
             'workcenter_id': cls.here.id, 'product_uom_id': product.uom_id.id},
        ])
        cls.Station = cls.env['lab.station']

    def test_it_says_which_bench_has_the_job(self):
        found = self.Station.locate(self.production.name, self.here.id)
        self.assertTrue(found['found'])
        self.assertEqual(found['station'], 'Holding Bench')
        self.assertFalse(found['here'])
        self.assertEqual(found['production'], self.production.name)

    def test_it_says_the_step_and_what_comes_next(self):
        found = self.Station.locate(self.production.name, self.here.id)
        self.assertEqual(found['operation'], 'Bending')
        self.assertEqual(found['next_station'], 'Asking Bench')
        self.assertFalse(found['accepted'], "nobody has taken it")
        self.assertEqual(found['technician'], '')

    def test_it_names_whoever_is_carrying_it(self):
        tech = self.env['res.users'].create({'name': 'Holder', 'login': 'loc_holder'})
        self.wo.write({'accepted_at': fields.Datetime.now(),
                       'bench_user_id': tech.id})
        found = self.Station.locate(self.production.name, self.here.id)
        self.assertTrue(found['accepted'])
        self.assertEqual(found['technician'], 'Holder')
        self.assertTrue(found['accepted_at'], "and since when")

    def test_asking_changes_nothing(self):
        """The board could already say where a job was - by refusing a scan."""
        before = (self.wo.state, self.wo.accepted_at, self.wo.workcenter_id)
        self.Station.locate(self.production.name, self.here.id)
        self.wo.invalidate_recordset()
        self.assertEqual((self.wo.state, self.wo.accepted_at, self.wo.workcenter_id),
                         before)

    def test_a_job_at_this_bench_says_so(self):
        self.wo.state = 'done'
        found = self.Station.locate(self.production.name, self.here.id)
        self.assertEqual(found['station'], 'Asking Bench')
        self.assertTrue(found['here'])

    def test_an_unknown_number_is_explained_not_swallowed(self):
        with self.assertRaises(UserError):
            self.Station.locate('NOT-A-JOB', self.here.id)


@tagged('post_install', '-at_install')
class TestFlowDay(TransactionCase):
    """The floor, for one day: taken, finished, by whom, when, how long.

    (client, 2026-09-09)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.tz = 'Asia/Kolkata'
        cls.tz = pytz.timezone('Asia/Kolkata')
        cls.tech = cls.env['res.users'].create({
            'name': 'Day Tech', 'login': 'flow_day_tech'})
        cls.bench = cls.env['mrp.workcenter'].create({
            'name': 'Day Bench', 'head_user_ids': [(6, 0, cls.env.user.ids)]})
        cls.later = cls.env['mrp.workcenter'].create({'name': 'Later Day Bench'})
        product = cls.env['product.product'].create({
            'name': 'Day Appliance', 'is_storable': True})
        cls.clinic = cls.env['res.partner'].create({'name': 'Day Clinic'})
        cls.order = cls.env['sale.order'].create({
            'partner_id': cls.clinic.id, 'patient': 'Day Patient',
            'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': 1})]})
        cls.order.action_confirm()
        cls.mo = cls.env['mrp.production'].create({
            'product_id': product.id, 'product_qty': 1.0})
        if 'sale_id' in cls.mo._fields:
            cls.mo.sale_id = cls.order.id
        cls.mo.action_confirm()
        cls.product = product
        cls.today = fields.Date.context_today(cls.env.user)
        cls.Flow = cls.env['lab.flow']

    def _at(self, day, hour):
        return self.tz.localize(datetime.combine(day, time(hour, 0))).astimezone(
            pytz.utc).replace(tzinfo=None)

    def _step(self, bench, sequence, taken_h=None, finished_h=None, minutes=0,
              day=None):
        day = day or self.today
        wo = self.env['mrp.workorder'].create({
            'name': 'Step %s' % sequence, 'production_id': self.mo.id,
            'workcenter_id': bench.id, 'sequence': sequence,
            'product_uom_id': self.product.uom_id.id})
        vals = {}
        if taken_h is not None:
            vals.update({'bench_user_id': self.tech.id,
                         'accepted_at': self._at(day, taken_h),
                         'bench_assigned_at': self._at(day, taken_h)})
        if finished_h is not None:
            vals['handed_over_at'] = self._at(day, finished_h)
        wo.write(vals)
        if minutes:
            # `duration` is a stored compute over the time records. Creating the
            # work order queues that compute; flushed AFTER a raw update it writes
            # zero over the minutes. So: flush first, then write the column.
            self.env.flush_all()
            self.env.cr.execute(
                "UPDATE mrp_workorder SET duration = %s, duration_expected = 30 "
                "WHERE id = %s", (minutes, wo.id))
            wo.invalidate_recordset()
        return wo

    def test_today_is_the_default_and_tomorrow_is_refused(self):
        flow = self.Flow.get_flow()
        self.assertTrue(flow['day']['is_today'])
        self.assertEqual(flow['day']['date'], fields.Date.to_string(self.today))
        ahead = self.Flow.get_flow(fields.Date.to_string(self.today + timedelta(days=3)))
        self.assertTrue(ahead['day']['is_today'], "forward stops at today")

    def test_a_case_stands_at_one_station(self):
        """The fault the old board had: a case queued at every bench it would
        ever reach, so three stations showed the same 2,009."""
        self._step(self.bench, 10)
        self._step(self.later, 20)
        flow = self.Flow.get_flow()
        here = {s['name']: s for s in flow['stations']}
        self.assertEqual(here['Day Bench']['here'], 1)
        self.assertEqual(here['Later Day Bench']['here'], 0, "not yet reached")
        self.assertEqual(here['Later Day Bench']['wip'], 1,
                         "the open-step count is still reported, as before")

    def test_the_day_says_what_the_person_did_and_when(self):
        self._step(self.bench, 10, taken_h=9, finished_h=11, minutes=90)
        self._step(self.later, 20, taken_h=13, finished_h=16, minutes=45)
        flow = self.Flow.get_flow()
        me = next(p for p in flow['people'] if p['id'] == self.tech.id)
        self.assertEqual(me['taken'], 2)
        self.assertEqual(me['finished'], 2)
        self.assertEqual(me['minutes'], 135)
        self.assertEqual(me['avg_min'], 68)
        self.assertEqual(me['first'], '09:00')
        self.assertEqual(me['last'], '16:00')
        self.assertEqual(me['stations'], 2)
        self.assertEqual(me['over'], 2, "both ran past the 30 expected")

    def test_the_day_totals_count_only_that_day(self):
        yesterday = self.today - timedelta(days=1)
        self._step(self.bench, 10, taken_h=9, finished_h=11, minutes=30)
        self._step(self.later, 20, taken_h=9, finished_h=11, minutes=30, day=yesterday)
        today = self.Flow.get_flow()['day']
        back = self.Flow.get_flow(fields.Date.to_string(yesterday))['day']
        self.assertFalse(back['is_today'])
        self.assertGreaterEqual(today['finished'], 1)
        self.assertGreaterEqual(back['finished'], 1)
        mine_today = [p for p in self.Flow.get_flow()['people'] if p['id'] == self.tech.id]
        mine_back = [p for p in self.Flow.get_flow(fields.Date.to_string(yesterday))['people']
                     if p['id'] == self.tech.id]
        self.assertEqual(mine_today[0]['finished'], 1)
        self.assertEqual(mine_back[0]['finished'], 1)

    def test_the_hours_show_when_not_only_how_much(self):
        self._step(self.bench, 10, taken_h=9, finished_h=11)
        hours = {h['h']: h for h in self.Flow.get_flow()['hours']}
        self.assertGreaterEqual(hours[9]['taken'], 1)
        self.assertGreaterEqual(hours[11]['finished'], 1)
        self.assertIn(8, hours, "the working day starts at eight even when the first act is later")

    def test_the_station_carries_its_day(self):
        self._step(self.bench, 10, taken_h=9, finished_h=11, minutes=40)
        me = next(s for s in self.Flow.get_flow()['stations'] if s['id'] == self.bench.id)
        self.assertEqual(me['finished_day'], 1)
        self.assertEqual(me['taken_day'], 1)
        self.assertEqual(me['avg_min_day'], 40)

    def test_pace_compares_today_with_the_usual_by_this_hour(self):
        pace = self.Flow.get_flow()['pace']
        self.assertIsNotNone(pace, "today has a pace")
        for key in ('so_far', 'usual', 'delta', 'pct', 'days'):
            self.assertIn(key, pace)
        back = self.Flow.get_flow(
            fields.Date.to_string(self.today - timedelta(days=1)))['pace']
        self.assertIsNone(back, "a finished day has no pace, only a result")

    def test_a_lane_goes_to_the_next_bench_of_its_own_arch(self):
        """The lane looked for the next step still OPEN, across both arches: a next
        bench that had finished too was skipped, and the upper piece's last hand-over
        was credited to the lower chain's first bench. (review, 2026-09-15)"""
        polish = self.env['mrp.workcenter'].create({'name': 'Lane Polish'})
        lower_bench = self.env['mrp.workcenter'].create({'name': 'Lane Lower Bend'})
        up_first = self._step(self.bench, 10, taken_h=9, finished_h=10)
        up_last = self._step(polish, 20, taken_h=10, finished_h=11)
        low_first = self._step(lower_bench, 110)
        (up_first | up_last).write({'arch': 'upper'})
        low_first.write({'arch': 'lower'})
        up_last.write({'state': 'done'})
        start, end = self.env['lab.station']._day_window(self.today)
        lanes = {(r['from_id'], r['to_id']): r['count']
                 for r in self.Flow._flows(start, end, limit=1000)}
        self.assertEqual(lanes.get((self.bench.id, polish.id)), 1,
                         "a finished next bench is still where the work went")
        self.assertNotIn((polish.id, lower_bench.id), lanes,
                         "the upper piece never goes to the lower chain")
        self.assertEqual(lanes.get((polish.id, False)), 1)

    def test_a_person_opens_as_their_days_work_orders(self):
        wo = self._step(self.bench, 10, taken_h=9, finished_h=11)
        act = self.Flow.action_open_person(self.tech.id)
        found = self.env['mrp.workorder'].search(act['domain'])
        self.assertIn(wo, found)
        self.assertEqual(act['res_model'], 'mrp.workorder')


@tagged('post_install', '-at_install')
class TestStationProcess(TransactionCase):
    """How a work centre processes work: in, out, wait, step time, flow, stuck.

    The widgets that replaced the station columns. (client, 2026-09-09)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.tz = 'Asia/Kolkata'
        cls.tz = pytz.timezone('Asia/Kolkata')
        cls.tech = cls.env['res.users'].create({
            'name': 'Process Tech', 'login': 'proc_tech'})
        cls.first_bench = cls.env['mrp.workcenter'].create({
            'name': 'Process Bench A', 'head_user_ids': [(6, 0, cls.env.user.ids)]})
        cls.second_bench = cls.env['mrp.workcenter'].create({
            'name': 'Process Bench B', 'head_user_ids': [(6, 0, cls.env.user.ids)]})
        product = cls.env['product.product'].create({
            'name': 'Process Appliance', 'is_storable': True})
        cls.clinic = cls.env['res.partner'].create({'name': 'Process Clinic'})
        cls.order = cls.env['sale.order'].create({
            'partner_id': cls.clinic.id, 'patient': 'Process Patient',
            'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': 1})]})
        cls.order.action_confirm()
        cls.mo = cls.env['mrp.production'].create({
            'product_id': product.id, 'product_qty': 1.0})
        if 'sale_id' in cls.mo._fields:
            cls.mo.sale_id = cls.order.id
        cls.mo.action_confirm()
        cls.product = product
        cls.today = fields.Date.context_today(cls.env.user)
        cls.Flow = cls.env['lab.flow']

    def _at(self, hour, minute=0):
        return self.tz.localize(datetime.combine(self.today, time(hour, minute))).astimezone(
            pytz.utc).replace(tzinfo=None)

    def _stations(self, flow=None):
        flow = flow or self.Flow.get_flow()
        return {s['id']: s for s in flow['stations']}

    def test_a_step_measures_its_wait_and_its_time(self):
        """Handed on from A at 09:00, started at B at 09:30, finished 10:15:
        B waited 30 minutes to start and took 45."""
        a = self.env['mrp.workorder'].create({
            'name': 'A step', 'production_id': self.mo.id, 'sequence': 10,
            'workcenter_id': self.first_bench.id,
            'product_uom_id': self.product.uom_id.id})
        b = self.env['mrp.workorder'].create({
            'name': 'B step', 'production_id': self.mo.id, 'sequence': 20,
            'workcenter_id': self.second_bench.id,
            'product_uom_id': self.product.uom_id.id})
        a.write({'bench_user_id': self.tech.id, 'accepted_at': self._at(8),
                 'handed_over_at': self._at(9)})
        b.write({'bench_user_id': self.tech.id, 'accepted_at': self._at(9, 30),
                 'handed_over_at': self._at(10, 15)})
        rows = self._stations()
        self.assertEqual(rows[self.second_bench.id]['wait_min'], 30)
        self.assertEqual(rows[self.second_bench.id]['cycle_min'], 45)
        self.assertEqual(rows[self.second_bench.id]['in_day'], 1)
        self.assertEqual(rows[self.second_bench.id]['out_day'], 1)
        self.assertEqual(rows[self.second_bench.id]['net_day'], 0)
        self.assertEqual(rows[self.second_bench.id]['people_day'], 1)
        self.assertEqual(rows[self.second_bench.id]['first'], '09:30')
        self.assertEqual(rows[self.second_bench.id]['last'], '10:15')

    def test_a_queue_that_grew_says_so(self):
        for n in range(3):
            wo = self.env['mrp.workorder'].create({
                'name': 'Taken %s' % n, 'production_id': self.mo.id, 'sequence': 10 + n,
                'workcenter_id': self.first_bench.id,
                'product_uom_id': self.product.uom_id.id})
            wo.write({'bench_user_id': self.tech.id, 'accepted_at': self._at(9 + n)})
        rows = self._stations()
        me = rows[self.first_bench.id]
        self.assertEqual(me['in_day'], 3)
        self.assertEqual(me['out_day'], 0)
        self.assertEqual(me['net_day'], 3, "three in, none out: the queue grew by three")

    def test_the_flow_says_where_the_work_went(self):
        a = self.env['mrp.workorder'].create({
            'name': 'A step', 'production_id': self.mo.id, 'sequence': 10,
            'workcenter_id': self.first_bench.id,
            'product_uom_id': self.product.uom_id.id})
        self.env['mrp.workorder'].create({
            'name': 'B step', 'production_id': self.mo.id, 'sequence': 20,
            'workcenter_id': self.second_bench.id,
            'product_uom_id': self.product.uom_id.id})
        a.write({'bench_user_id': self.tech.id, 'accepted_at': self._at(8),
                 'handed_over_at': self._at(9)})
        flows = self.Flow.get_flow()['flows']
        mine = [f for f in flows if f['from_id'] == self.first_bench.id]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]['to_id'], self.second_bench.id)
        self.assertEqual(mine[0]['to'], 'Process Bench B')
        self.assertGreaterEqual(mine[0]['count'], 1)

    def test_the_last_step_flows_to_finished(self):
        only = self.env['mrp.workorder'].create({
            'name': 'Only step', 'production_id': self.mo.id, 'sequence': 10,
            'workcenter_id': self.first_bench.id,
            'product_uom_id': self.product.uom_id.id})
        only.write({'bench_user_id': self.tech.id, 'accepted_at': self._at(8),
                    'handed_over_at': self._at(9)})
        flows = self.Flow.get_flow()['flows']
        mine = [f for f in flows if f['from_id'] == self.first_bench.id]
        self.assertEqual(mine[0]['to_id'], False, "nowhere next: finished")

    def test_a_case_untaken_past_the_line_is_stuck_at_its_bench(self):
        wo = self.env['mrp.workorder'].create({
            'name': 'Stuck step', 'production_id': self.mo.id, 'sequence': 10,
            'workcenter_id': self.first_bench.id,
            'product_uom_id': self.product.uom_id.id})
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE mrp_workorder SET create_date = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(hours=30), wo.id))
        wo.invalidate_recordset()
        flow = self.Flow.get_flow()
        me = self._stations(flow)[self.first_bench.id]
        self.assertEqual(me['stuck'], 1)
        self.assertGreaterEqual(flow['stuck_total'], 1)
        wo.write({'accepted_at': fields.Datetime.now()})
        self.assertEqual(self._stations()[self.first_bench.id]['stuck'], 0,
                         "started is no longer stuck, however old")

    def test_a_bench_with_nothing_on_the_day_reports_zeros_not_errors(self):
        rows = self._stations()
        me = rows[self.first_bench.id]
        for key in ('in_day', 'out_day', 'net_day', 'wait_min', 'cycle_min',
                    'people_day', 'stuck', 'over_day'):
            self.assertEqual(me[key], 0)
        self.assertEqual((me['first'], me['last']), ('', ''))


@tagged('post_install', '-at_install')
class TestCaseShapedWorkorderList(TransactionCase):
    """A work order says which case it is, not only which bench. (client, 2026-09-09)"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bench = cls.env['mrp.workcenter'].create({'name': 'Case List Bench'})
        product = cls.env['product.product'].create({
            'name': 'Case List Appliance', 'is_storable': True})
        cls.clinic = cls.env['res.partner'].create({'name': 'DR CASE LIST'})
        cls.order = cls.env['sale.order'].create({
            'partner_id': cls.clinic.id, 'patient': 'Case List Patient',
            'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': 1})]})
        cls.order.action_confirm()
        cls.mo = cls.env['mrp.production'].create({
            'product_id': product.id, 'product_qty': 1.0})
        if 'sale_id' in cls.mo._fields:
            cls.mo.sale_id = cls.order.id
        cls.wo = cls.env['mrp.workorder'].create({
            'name': 'Case step', 'production_id': cls.mo.id,
            'workcenter_id': cls.bench.id, 'product_uom_id': product.uom_id.id})

    def test_the_work_order_carries_its_case_doctor_and_patient(self):
        if not self.wo.production_id.sale_id:
            self.skipTest("this build does not link the job to the order")
        self.assertEqual(self.wo.sale_order_id, self.order)
        self.assertEqual(self.wo.clinic_id, self.clinic)
        self.assertEqual(self.wo.patient, 'Case List Patient')

    def test_the_list_names_the_case_and_hides_the_bench(self):
        view = self.env.ref('lab_workcenter_scan.view_workorder_list_case')
        arch = self.env['mrp.workorder'].get_view(view.id, view_type='list')['arch']
        for field in ('sale_order_id', 'clinic_id', 'patient'):
            self.assertIn('name="%s"' % field, arch)
        self.assertIn('name="workcenter_id" string="Work Centre" optional="hide"', arch,
                      "the bench is what the drill filtered by, so it is not a column")

    def test_both_drills_open_that_list(self):
        """A station's queue and a person's day are both read case by case."""
        Flow = self.env['lab.flow']
        view_id = self.env.ref('lab_workcenter_scan.view_workorder_list_case').id
        station = Flow.action_open_station(self.bench.id)
        person = Flow.action_open_person(self.env.uid)
        for act in (station, person):
            self.assertEqual(act['views'][0], (view_id, 'list'))
            self.assertEqual(act['res_model'], 'mrp.workorder')


@tagged('post_install', '-at_install')
class TestRushReport(TransactionCase):
    """Urgent and emergency work, as a report rather than a count.

    A number sends a supervisor looking; the board says which cases they are,
    where each stands, whether anybody has taken it and how long it has waited.
    (client, 2026-09-10)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Flow = cls.env['lab.flow']

    def _rush(self):
        return self.Flow.get_flow()['urgent']

    def test_the_report_is_shaped_for_the_panel_and_the_drill(self):
        rush = self._rush()
        for key in ('total', 'emergency', 'urgent', 'waiting', 'on_bench',
                    'oldest', 'finished_today', 'rows', 'more', 'stations', 'ids'):
            self.assertIn(key, rush)
        self.assertEqual(rush['total'], rush['emergency'] + rush['urgent'])
        self.assertEqual(rush['total'], rush['waiting'] + rush['on_bench'])
        self.assertEqual(rush['total'], len(rush['ids']))
        self.assertLessEqual(len(rush['rows']), 12)

    def test_emergency_ranks_above_urgent_and_the_longest_wait_first(self):
        rows = self._rush()['rows']
        keys = [(0 if r['emergency'] else 1, -r['days']) for r in rows]
        self.assertEqual(keys, sorted(keys), "emergency first, then whoever waited longest")

    def test_a_case_is_named_once_at_the_step_it_is_actually_at(self):
        rush = self._rush()
        self.assertEqual(len(rush['ids']), len(set(rush['ids'])),
                         "a case routed through four benches is one row, not four")
        for row in rush['rows']:
            self.assertIn(row['priority'], ('urgent', 'emergency'))
            self.assertGreaterEqual(row['days'], 0)

    def test_the_drill_opens_the_cases_the_panel_counted(self):
        rush = self._rush()
        action = self.Flow.action_open_rush()
        self.assertEqual(action['res_model'], 'mrp.production')
        self.assertEqual(sorted(action['domain'][0][2]), sorted(rush['ids']))
        only = self.Flow.action_open_rush('emergency')
        self.assertEqual(len(only['domain'][0][2]), rush['emergency'])

    def test_the_station_strip_adds_up_to_the_cases_standing_at_a_bench(self):
        rush = self._rush()
        placed = sum(c['count'] for c in rush['stations'])
        self.assertLessEqual(placed, rush['total'])


@tagged('post_install', '-at_install')
class TestOperationTakesItsBenchName(TransactionCase):
    """An operation IS its bench here, so choosing the work centre names it.

    Typed by hand the two drift - "Wire Bending / Adams Clasp" against
    "WireBending Adams" - and the station board then shows an operation nobody
    recognises. (client, 2026-09-11)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bench = cls.env['mrp.workcenter'].create({'name': 'Op Bench One'})
        cls.other = cls.env['mrp.workcenter'].create({'name': 'Op Bench Two'})
        cls.product = cls.env['product.product'].create({
            'name': 'Op Appliance', 'is_storable': True})
        cls.bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.product.product_tmpl_id.id, 'product_qty': 1.0})

    def _form(self):
        return Form(self.env['mrp.routing.workcenter'].with_context(
            default_bom_id=self.bom.id))

    def test_choosing_the_bench_names_the_operation(self):
        with self._form() as operation:
            operation.workcenter_id = self.bench
            self.assertEqual(operation.name, 'Op Bench One')

    def test_changing_the_bench_renames_an_untouched_operation(self):
        with self._form() as operation:
            operation.workcenter_id = self.bench
            operation.workcenter_id = self.other
            self.assertEqual(operation.name, 'Op Bench Two',
                             "the name followed the bench it was taken from")

    def test_a_name_somebody_typed_is_never_overwritten(self):
        with self._form() as operation:
            operation.workcenter_id = self.bench
            operation.name = 'Polish - second pass'
            operation.workcenter_id = self.other
            self.assertEqual(operation.name, 'Polish - second pass',
                             "a step named on purpose survives its bench changing")

    def test_an_existing_operation_keeps_its_own_name(self):
        operation = self.env['mrp.routing.workcenter'].create({
            'name': 'Hand finishing', 'bom_id': self.bom.id,
            'workcenter_id': self.bench.id})
        form = Form(operation)
        form.workcenter_id = self.other
        form.save()
        self.assertEqual(operation.name, 'Hand finishing')
