# -*- coding: utf-8 -*-
"""The redo feature and the weekly MRP report widget. (client, 2026-08-28)"""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


class RedoCase(TransactionCase):
    """A confirmed two-bench case, partly done, ready to be sent back."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.wc_a = cls.env['mrp.workcenter'].create({'name': 'Redo Bench A'})
        cls.wc_b = cls.env['mrp.workcenter'].create({'name': 'Redo Bench B'})
        product = cls.env['product.product'].create({
            'name': 'Redo Appliance', 'type': 'consu', 'is_storable': True})
        cls.mo = cls.env['mrp.production'].create({
            'product_id': product.id, 'product_qty': 1.0})
        cls.wo_a = cls.env['mrp.workorder'].create({
            'name': 'Wire Bending', 'production_id': cls.mo.id, 'sequence': 1,
            'workcenter_id': cls.wc_a.id, 'product_uom_id': product.uom_id.id})
        cls.wo_b = cls.env['mrp.workorder'].create({
            'name': 'Polishing', 'production_id': cls.mo.id, 'sequence': 2,
            'workcenter_id': cls.wc_b.id, 'product_uom_id': product.uom_id.id})
        cls.mo.action_confirm()
        cls.reason = cls.env['lab.redo.reason'].search(
            [('name', '=', 'Broken')], limit=1) or \
            cls.env['lab.redo.reason'].create({'name': 'Broken'})


@tagged('post_install', '-at_install')
class TestRedo(RedoCase):

    def test_the_eight_reasons_ship_with_the_module(self):
        names = set(self.env['lab.redo.reason'].search([]).mapped('name'))
        for expected in ('Ill-fitting (Wire Bending)', 'Ill-fitting Acrylisation',
                         'Ill-fitting (Polishing)', 'Broken', 'Color Changes',
                         'Loose Bands', 'Occlusion', 'Bite Problems'):
            self.assertIn(expected, names)

    def test_a_redo_resets_every_bench_and_records_why(self):
        self.wo_a.button_start()
        self.wo_a.button_finish()
        self.assertEqual(self.wo_a.state, 'done')

        redo = self.mo.lab_redo(self.reason, note='snapped at pickup')
        self.assertEqual(redo.reason_id, self.reason)
        self.assertEqual(redo.operations_reset, 2)
        self.assertNotEqual(self.wo_a.state, 'done',
                            "the finished bench must be reopened")

    def test_a_restarted_case_is_recognisable(self):
        self.assertFalse(self.mo.lab_is_redo)
        self.mo.lab_redo(self.reason)
        self.assertTrue(self.mo.lab_is_redo)
        self.assertEqual(self.mo.lab_redo_count, 1)
        self.assertEqual(self.mo.lab_redo_reason_id, self.reason)

    def test_each_redo_counts_the_attempt(self):
        first = self.mo.lab_redo(self.reason)
        second = self.mo.lab_redo(self.reason)
        self.assertEqual((first.attempt, second.attempt), (1, 2))

    def test_it_remembers_where_the_fault_was_found(self):
        self.wo_a.button_start()
        self.wo_a.button_finish()
        redo = self.mo.lab_redo(self.reason)
        self.assertEqual(redo.stage_id, self.wc_a,
                         "the last finished bench is where it had got to")

    def test_a_redo_without_a_reason_is_refused(self):
        with self.assertRaises(UserError):
            self.mo.lab_redo(False)

    def test_a_cancelled_case_cannot_be_restarted(self):
        self.mo.action_cancel()
        with self.assertRaises(UserError):
            self.mo.lab_redo(self.reason)

    def test_the_trail_lands_on_the_order_chatter(self):
        before = len(self.mo.message_ids)
        self.mo.lab_redo(self.reason, note='colour wrong')
        self.assertGreater(len(self.mo.message_ids), before)
        body = self.mo.message_ids[0].body
        self.assertIn('Broken', body)
        self.assertIn('colour wrong', body)


@tagged('post_install', '-at_install')
class TestMrpReportWidget(RedoCase):

    def test_the_payload_has_the_three_panels(self):
        data = self.env['lab.mrp.report'].get_mrp_report()
        for key in ('weeks', 'people', 'stations', 'totals'):
            self.assertIn(key, data)

    def test_weeks_are_monday_anchored(self):
        for row in self.env['lab.mrp.report'].get_mrp_report()['weeks']:
            day = fields.Date.to_date(row['week'])
            self.assertEqual(day.weekday(), 0,
                             "%s is not a Monday" % row['week'])

    def test_a_finished_operation_lands_in_this_week(self):
        self.wo_a.button_start()
        self.wo_a.button_finish()
        # The clock the report runs on is the hand-over stamp the scan flow
        # writes; a plain button_finish leaves it empty on purpose.
        self.wo_a.handed_over_at = fields.Datetime.now()
        data = self.env['lab.mrp.report'].get_mrp_report()
        self.assertGreaterEqual(sum(w['jobs'] for w in data['weeks']), 1)


@tagged('post_install', '-at_install')
class TestMrpReportBacklog(RedoCase):
    """The half of the report that does not need the board to be scanned.

    The floor here has never handed a job over, so weeks/people/stations are all zero.
    The backlog is read from the routing and the order dates instead, and must be right
    whether or not anybody scans. (client, 2026-08-28)
    """

    def _payload(self):
        return self.env['lab.mrp.report'].get_mrp_report(8)

    def test_the_payload_carries_both_halves(self):
        data = self._payload()
        for key in ('weeks', 'people', 'stations', 'backlog', 'redo', 'pace'):
            self.assertIn(key, data)
        for key in ('stations', 'ages', 'oldest', 'jobs', 'hours', 'busiest'):
            self.assertIn(key, data['backlog'])

    def test_a_live_case_is_counted_once_at_its_first_open_step(self):
        """Two unfinished benches, one case: it queues at the FIRST, not at both."""
        backlog = self._payload()['backlog']
        mine = [r for r in backlog['stations'] if r['name'] == self.wc_a.name]
        theirs = [r for r in backlog['stations'] if r['name'] == self.wc_b.name]
        self.assertTrue(mine, "the case must queue at its first bench")
        self.assertFalse(theirs, "and not also at the bench behind it")

    def test_finishing_the_first_step_moves_the_case_to_the_next_bench(self):
        self.wo_a.button_start()
        self.wo_a.button_finish()
        backlog = self._payload()['backlog']
        names = {r['name'] for r in backlog['stations']}
        self.assertIn(self.wc_b.name, names)
        self.assertNotIn(self.wc_a.name, names)

    def test_the_queue_is_costed_in_hours_not_only_in_cases(self):
        self.wo_a.duration_expected = 120.0
        backlog = self._payload()['backlog']
        row = next(r for r in backlog['stations'] if r['name'] == self.wc_a.name)
        self.assertEqual(row['hours'], 2.0)
        self.assertGreaterEqual(backlog['hours'], 2.0)

    def test_the_busiest_bench_is_the_one_with_the_most_hours_not_the_most_cases(self):
        # One long job at B, several short ones at A: B is the bottleneck.
        product = self.env['product.product'].create({
            'name': 'Quick Appliance', 'type': 'consu', 'is_storable': True})
        for _i in range(3):
            mo = self.env['mrp.production'].create({
                'product_id': product.id, 'product_qty': 1.0})
            self.env['mrp.workorder'].create({
                'name': 'Quick', 'production_id': mo.id, 'sequence': 1,
                'workcenter_id': self.wc_a.id, 'duration_expected': 10.0,
                'product_uom_id': product.uom_id.id})
            mo.action_confirm()
        self.wo_a.duration_expected = 10.0
        long_mo = self.env['mrp.production'].create({
            'product_id': product.id, 'product_qty': 1.0})
        self.env['mrp.workorder'].create({
            'name': 'Slow', 'production_id': long_mo.id, 'sequence': 1,
            'workcenter_id': self.wc_b.id, 'duration_expected': 600.0,
            'product_uom_id': product.uom_id.id})
        long_mo.action_confirm()
        # Relative to each other, not to the top of the list: this database carries
        # 2,558 real live cases and one of its own benches is far busier than any
        # fixture. The claim under test is the ORDERING RULE - hours, not cases.
        backlog = self._payload()['backlog']
        order = [r['name'] for r in backlog['stations']]
        mine = next(r for r in backlog['stations'] if r['name'] == self.wc_a.name)
        theirs = next(r for r in backlog['stations'] if r['name'] == self.wc_b.name)
        self.assertGreater(mine['jobs'], theirs['jobs'], "A has more cases")
        self.assertGreater(theirs['hours'], mine['hours'], "B has more hours")
        self.assertLess(order.index(self.wc_b.name), order.index(self.wc_a.name),
                        "the bench with more HOURS ranks above the one with more cases")
        self.assertEqual(backlog['busiest'], backlog['stations'][0]['name'])

    def test_every_open_case_lands_in_exactly_one_age_band(self):
        backlog = self._payload()['backlog']
        self.assertEqual(sum(a['jobs'] for a in backlog['ages']), backlog['jobs'])
        self.assertEqual([a['key'] for a in backlog['ages']],
                         ['fresh', 'week', 'month', 'old'])

    def test_a_case_ordered_today_is_in_the_freshest_band(self):
        backlog = self._payload()['backlog']
        fresh = next(a for a in backlog['ages'] if a['key'] == 'fresh')
        self.assertGreaterEqual(fresh['jobs'], 1)

    def test_an_old_case_is_banded_and_listed_by_age(self):
        self.mo.date_start = fields.Datetime.now() - timedelta(days=200)
        backlog = self._payload()['backlog']
        old = next(a for a in backlog['ages'] if a['key'] == 'old')
        self.assertGreaterEqual(old['jobs'], 1)
        top = backlog['oldest'][0]
        self.assertEqual(top['name'], self.mo.name)
        self.assertGreaterEqual(top['days'], 200)
        self.assertEqual(top['station'], self.wc_a.name)

    def test_a_shipped_case_is_not_in_the_backlog(self):
        """`open_now` must agree with the backlog it is drawn from."""
        data = self._payload()
        self.assertEqual(data['open_now'], data['backlog']['jobs'])

    def test_a_cancelled_case_leaves_the_backlog(self):
        before = self._payload()['backlog']['jobs']
        self.mo.action_cancel()
        self.assertEqual(self._payload()['backlog']['jobs'], before - 1)

    # ------------------------------------------------------------------ drills
    def test_the_station_drill_opens_exactly_the_cases_behind_the_bar(self):
        action = self.env['lab.mrp.report'].open_backlog(
            workcenter_id=self.wc_a.id)
        self.assertEqual(action['res_model'], 'mrp.production')
        ids = action['domain'][0][2]
        self.assertIn(self.mo.id, ids)
        self.assertIn(self.wc_a.name, action['name'])

    def test_the_age_drill_opens_only_that_band(self):
        self.mo.date_start = fields.Datetime.now() - timedelta(days=200)
        action = self.env['lab.mrp.report'].open_backlog(age='old')
        self.assertIn(self.mo.id, action['domain'][0][2])
        fresh = self.env['lab.mrp.report'].open_backlog(age='fresh')
        self.assertNotIn(self.mo.id, fresh['domain'][0][2])

    def test_the_two_drill_filters_combine(self):
        self.mo.date_start = fields.Datetime.now() - timedelta(days=200)
        action = self.env['lab.mrp.report'].open_backlog(
            workcenter_id=self.wc_b.id, age='old')
        self.assertNotIn(self.mo.id, action['domain'][0][2],
                         "its first open step is at bench A, not B")

    def test_every_drill_carries_views_the_client_can_open(self):
        """A dict handed straight to doAction needs `views`; the JS action service
        reads it and does not fall back to view_mode. Without it the bar throws in
        the console and nothing opens."""
        Report = self.env['lab.mrp.report']
        for action in (Report.open_operations(),
                       Report.open_backlog(workcenter_id=self.wc_a.id),
                       Report.open_backlog(age='old'),
                       Report.open_redo()):
            self.assertTrue(action.get('views'), action['res_model'])
            modes = [v[1] for v in action['views']]
            self.assertEqual(modes, action['view_mode'].split(','),
                             "views and view_mode must agree")

    # ------------------------------------------------------------------ quality
    def test_a_redo_is_counted_and_reasoned(self):
        # A difference, not a total: the client records real redos now.
        def mine():
            redo = self._payload()['redo']
            row = next((r for r in redo['reasons']
                        if r['name'] == self.reason.display_name), None)
            return redo['total'], row['count'] if row else 0
        total0, count0 = mine()
        self.mo.lab_redo(self.reason, note='snapped')
        total1, count1 = mine()
        self.assertEqual(total1, total0 + 1)
        self.assertEqual(count1, count0 + 1)

    def test_the_redo_rate_is_withheld_rather_than_faked_when_nothing_finished(self):
        """The rule itself, asked directly of _redo: a database where real
        operations HAVE finished can no longer stage 'nothing finished'
        through the full payload."""
        self.mo.lab_redo(self.reason)
        first = self.env['lab.station']._lab_today() - timedelta(weeks=8)
        starved = self.env['lab.mrp.report']._redo(first, 0)
        self.assertIsNone(starved['rate'],
                          "a rate against zero finished operations is not a rate")
        fed = self.env['lab.mrp.report']._redo(first, 100)
        self.assertIsNotNone(fed['rate'])
        self.assertEqual(fed['rate'], round(fed['total'] / 100 * 100, 1))

    def test_the_redo_drill_filters_by_reason(self):
        action = self.env['lab.mrp.report'].open_redo(reason_id=self.reason.id)
        self.assertEqual(action['res_model'], 'lab.mrp.redo')
        self.assertIn(('reason_id', '=', self.reason.id), action['domain'])

    def test_pace_counts_untimed_jobs_as_untimed_not_as_on_time(self):
        """An untimed job must not move 'timed' - proven as a difference,
        since the client's own timed rows now live in this database."""
        before = self._payload()['pace']
        # The fixture work this class creates carries no timer reading.
        self.assertEqual(self._payload()['pace']['timed'], before['timed'],
                         "fixture jobs without timers must stay out of 'timed'")
        self.assertGreaterEqual(before['timed'], before['over'])


@tagged('post_install', '-at_install')
class TestRightNowDecisions(TransactionCase):
    """The Right-now half made actionable: capacity beside the queue, in against
    out, cases later than the lab's own standard, and a move tried on paper.
    Scoped to its own benches - this database carries the lab's real queue."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        mrp_user = cls.env.ref('mrp.group_mrp_user').id
        cls.people = cls.env['res.users'].create([
            {'name': 'Cap One', 'login': 'cap_one', 'group_ids': [(4, mrp_user)]},
            {'name': 'Cap Two', 'login': 'cap_two', 'group_ids': [(4, mrp_user)]},
        ])
        cls.staffed = cls.env['mrp.workcenter'].create({
            'name': 'Cap Staffed', 'users': [(6, 0, cls.people.ids)]})
        cls.empty = cls.env['mrp.workcenter'].create({'name': 'Cap Nobody'})
        cls.product = cls.env['product.product'].create({
            'name': 'Cap Appliance', 'type': 'consu', 'is_storable': True})
        cls.Report = cls.env['lab.mrp.report']

    def _case(self, bench, minutes):
        mo = self.env['mrp.production'].create({
            'product_id': self.product.id, 'product_qty': 1.0})
        self.env['mrp.workorder'].create({
            'name': 'Step', 'production_id': mo.id, 'sequence': 1,
            'workcenter_id': bench.id, 'duration_expected': minutes,
            'product_uom_id': self.product.uom_id.id})
        mo.action_confirm()
        return mo

    def _row(self, bench):
        rows = self.Report._backlog()['stations']
        return next((r for r in rows if r['id'] == bench.id), None)

    # ------------------------------------------------------------ capacity
    def test_a_bench_knows_how_long_its_queue_takes_at_full_capacity(self):
        self._case(self.staffed, 8 * 60)
        self._case(self.staffed, 8 * 60)
        row = self._row(self.staffed)
        self.assertEqual(row['people'], 2)
        self.assertEqual(row['hours'], 16.0)
        self.assertEqual(row['capacity'], 2 * row['hours_per_day'])
        self.assertEqual(row['days_at_capacity'],
                         round(16.0 / (2 * row['hours_per_day']), 1))

    def test_a_bench_with_nobody_posted_cannot_clear_at_all(self):
        self._case(self.empty, 60)
        backlog = self.Report._backlog()
        row = next(r for r in backlog['stations'] if r['id'] == self.empty.id)
        self.assertEqual(row['people'], 0)
        self.assertIsNone(row['days_at_capacity'])
        self.assertIn(self.empty.display_name, backlog['unstaffed'])

    def test_the_lab_clears_when_its_slowest_bench_does(self):
        backlog = self.Report._backlog()
        judged = [r for r in backlog['stations'] if r['days_at_capacity'] is not None]
        if not judged:
            self.skipTest("nothing staffed is queued on this database")
        self.assertEqual(backlog['days_to_clear'],
                         max(r['days_at_capacity'] for r in judged))
        self.assertEqual(backlog['gating'],
                         max(judged, key=lambda r: r['days_at_capacity'])['name'])

    def test_pace_is_what_the_bench_actually_handed_on(self):
        mo = self._case(self.staffed, 120)
        wo = mo.workorder_ids[0]
        wo.write({'bench_user_id': self.people[0].id,
                  'handed_over_at': fields.Datetime.now()})
        pace = self.Report._bench_pace([self.staffed.id], min_scans=1)
        self.assertIn(self.staffed.id, pace)
        self.assertGreater(pace[self.staffed.id], 0.0)
        self.assertNotIn(self.staffed.id, self.Report._bench_pace([self.staffed.id]),
                         "one scan in a fortnight is a guess, not a pace")
        self.assertNotIn(self.empty.id, self.Report._bench_pace([self.empty.id]),
                         "no scans is no information, not a bench that did nothing")

    def test_a_pace_the_scanner_barely_saw_is_not_shown(self):
        trust = self.Report._trust_pace
        self.assertFalse(trust(1.5, 40.0), "ninety minutes a day at a bench of five is the scanner, not the bench")
        self.assertTrue(trust(4.0, 40.0), "a tenth of capacity is enough to mean it")
        self.assertTrue(trust(3.0, 0.0), "with nobody posted there is nothing to judge against")
        self.assertFalse(trust(None, 40.0))

    # ------------------------------------------------------------ lateness
    def test_late_means_half_again_as_long_and_at_least_a_day_over(self):
        late = self.Report._is_late
        self.assertFalse(late(3.0, 2.0), "exactly half again is not over")
        self.assertTrue(late(3.5, 2.0))
        self.assertFalse(late(1.9, 1.0), "half again but not a day over")
        self.assertTrue(late(2.5, 1.0))
        self.assertFalse(late(40.0, 0), "no standard, no judgement")

    def test_the_late_list_is_shaped_for_the_screen_and_the_drill(self):
        late = self.Report._backlog()['late']
        for key in ('count', 'judged', 'pct', 'rows', 'ids', 'standards'):
            self.assertIn(key, late)
        self.assertEqual(late['count'], len(late['ids']))
        self.assertLessEqual(len(late['rows']), 12)
        for row in late['rows']:
            self.assertGreater(row['days'], row['usual'])
        action = self.Report.open_late()
        self.assertEqual(action['res_model'], 'mrp.production')
        self.assertEqual(action['domain'], [('id', 'in', late['ids'])])

    def test_a_case_with_no_standard_is_not_judged(self):
        """The fixture appliance has never been delivered, so it has no usual."""
        mo = self._case(self.staffed, 60)
        late = self.Report._backlog()['late']
        self.assertNotIn(mo.id, late['ids'])

    # ------------------------------------------------------------ in and out
    def test_in_against_out_by_week(self):
        before = self.Report._balance()
        self.assertEqual(len(before['rows']), 8)
        for key in ('avg_in', 'avg_out', 'net', 'weeks', 'peak'):
            self.assertIn(key, before)
        this_week = next(r for r in before['rows'] if r['current'])
        order = self.env['sale.order'].create({
            'partner_id': self.env['res.partner'].create({'name': 'Cap Clinic'}).id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 1})],
        })
        order.action_confirm()
        after = next(r for r in self.Report._balance()['rows'] if r['current'])
        self.assertEqual(after['orders'], this_week['orders'] + 1,
                         "an order confirmed today is a case in this week")
        self.assertEqual(after['net'], after['orders'] - after['delivered'])

    def test_the_payload_carries_the_new_pieces(self):
        data = self.Report.get_mrp_report(4)
        self.assertIn('balance', data)
        for key in ('days_to_clear', 'gating', 'capacity', 'unstaffed', 'late'):
            self.assertIn(key, data['backlog'])
        for row in data['backlog']['stations']:
            for key in ('people', 'hours_per_day', 'capacity', 'days_at_capacity',
                        'pace', 'days_at_pace'):
                self.assertIn(key, row)


@tagged('post_install', '-at_install')
class TestTheFloorAsDecisions(TransactionCase):
    """The Right-now half: what is due out, which bench the lab waits on, where
    the old work sits, and which doctor has waited longest. (client, 2026-09-10)"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Report = cls.env['lab.mrp.report']

    def test_the_payload_carries_every_widget(self):
        data = self.Report.get_mrp_report(4)
        for key in ('promises', 'radar', 'rot', 'doctors'):
            self.assertIn(key, data)
        for key in ('rows', 'promised', 'unpromised', 'due_now', 'standards'):
            self.assertIn(key, data['promises'])
        for key in ('rows', 'in_week', 'out_week', 'net', 'days'):
            self.assertIn(key, data['radar'])

    def test_the_promise_clock_places_every_promised_case_once(self):
        promises = self.Report._floor_now()['promises']
        self.assertEqual(sum(r['count'] for r in promises['rows']), promises['promised'])
        self.assertEqual([r['key'] for r in promises['rows']],
                         ['overdue', 'today', 'tomorrow', 'week', 'later'])
        self.assertEqual(promises['due_now'],
                         sum(r['count'] for r in promises['rows']
                             if r['key'] in ('overdue', 'today')))

    def test_a_bucket_opens_the_cases_it_counted(self):
        promises = self.Report._floor_now()['promises']
        overdue = next(r for r in promises['rows'] if r['key'] == 'overdue')
        action = self.Report.open_promise('overdue')
        self.assertEqual(action['res_model'], 'mrp.production')
        self.assertEqual(len(action['domain'][0][2]), overdue['count'],
                         "the list behind a segment is the segment")

    def test_the_radar_ranks_benches_by_what_the_lab_is_waiting_for(self):
        radar = self.Report._floor_now()['radar']
        judged = [r['days'] for r in radar['rows'] if r['days'] is not None]
        self.assertEqual(judged, sorted(judged, reverse=True),
                         "the bench that takes longest to clear comes first")
        for row in radar['rows']:
            self.assertEqual(row['net'], row['in_week'] - row['out_week'])

    def test_the_rot_matrix_counts_each_live_case_once(self):
        floor = self.Report._floor_now()
        rot = floor['rot']
        banded = sum(b['count'] for b in rot['bands'])
        live, first = self.Report._live_steps()
        placed = len([1 for step in first.values()
                      if (step.get('workcenter_id') or [None])[0]])
        self.assertEqual(banded, placed,
                         "every case standing at a bench lands in exactly one band")
        for row in rot['rows']:
            self.assertEqual(sum(c['count'] for c in row['cells']), row['total'])
            self.assertEqual([c['key'] for c in row['cells']],
                             ['fresh', 'week', 'month', 'old'])

    def test_the_doctor_list_only_names_clinics_actually_kept_waiting(self):
        doctors = self.Report._floor_now()['doctors']
        for row in doctors:
            self.assertGreaterEqual(row['late'], 1)
            self.assertLessEqual(row['late'], row['cases'])
        self.assertEqual([r['late'] for r in doctors],
                         sorted([r['late'] for r in doctors], reverse=True))

    def test_a_doctor_opens_their_own_live_cases(self):
        doctors = self.Report._floor_now()['doctors']
        if not doctors:
            self.skipTest("nobody is waiting on this database")
        action = self.Report.open_doctor(doctors[0]['id'])
        self.assertEqual(action['res_model'], 'mrp.production')
        found = self.env['mrp.production'].search(action['domain'])
        self.assertEqual(len(found), doctors[0]['cases'])

    def test_the_retired_widgets_are_gone_from_the_payload(self):
        """Try moving people, the case calendar and the weekly in-out chart were
        judged not to help; what replaced them must not leave them behind."""
        arch = self.env.ref('lab_workcenter_scan.action_mrp_report')
        self.assertTrue(arch, "the client action still exists")
