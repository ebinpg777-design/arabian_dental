# -*- coding: utf-8 -*-
"""Track a Work (R18) — one box, one screen.

The promise of this screen is narrow and testable: whatever reference somebody is
holding leads to the same case, the answer arrives complete in one call, and a manager
gets it without being handed Manufacturing rights. Those three are what is pinned here.
"""
from datetime import datetime, timedelta
from unittest.mock import patch

from freezegun import freeze_time

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged


def link_job(order, mo):
    """Make `mo` one of `order`'s manufacturing orders, the way core finds them.

    `sale.order.mrp_production_ids` is computed from
    `stock_reference_ids.production_ids`, NOT from the lab's own `sale_id`
    related - so a fixture that only set `mo.sale_id` produced an order with no
    jobs, every workorder assertion in this file fell through its "does not
    link" guard, and five tests reported green while testing nothing.
    (client, 2026-09-12)
    """
    ref = order.env['stock.reference'].create({'name': order.name})
    ref.production_ids = [(4, mo.id)]
    order.stock_reference_ids = [(4, ref.id)]
    order.invalidate_recordset()
    return mo


@tagged('post_install', '-at_install')
class TestTrackWork(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Track = cls.env['lab.track']
        cls.clinic = cls.env['res.partner'].create({'name': 'Track Test Clinic'})
        cls.other_clinic = cls.env['res.partner'].create({'name': 'Track Other Clinic'})
        cls.product = cls.env['product.product'].create({
            'name': 'Track Test Appliance', 'type': 'consu', 'list_price': 1500.0,
            'invoice_policy': 'order'})
        cls.order = cls._make_order('Meera Nair')

    @classmethod
    def _make_order(cls, patient, partner=None):
        order = cls.env['sale.order'].create({
            'partner_id': (partner or cls.clinic).id,
            'patient': patient,
            'order_line': [(0, 0, {'product_id': cls.product.id,
                                   'product_uom_qty': 1})]})
        order.action_confirm()
        return order

    # ------------------------------------------------------- every reference leads home
    def test_the_order_number_resolves(self):
        result = self.Track.search_work(self.order.name)
        self.assertTrue(result['found'])
        self.assertEqual(result['order']['id'], self.order.id)
        self.assertEqual(result['order']['patient'], 'Meera Nair')

    def test_a_delivery_note_number_resolves_to_its_case(self):
        delivery = self.env['lab.delivery'].create({
            'sale_order_id': self.order.id})
        result = self.Track.search_work(delivery.name)
        self.assertTrue(result['found'])
        self.assertEqual(result['order']['id'], self.order.id)

    def test_an_invoice_number_resolves_to_its_case(self):
        invoice = self.order._create_invoices()
        invoice.action_post()
        result = self.Track.search_work(invoice.name)
        self.assertTrue(result['found'])
        self.assertEqual(result['order']['id'], self.order.id)

    def test_a_rework_number_resolves_to_its_case(self):
        rework = self.env['sale.order'].create({
            'partner_id': self.order.partner_id.id,
            'is_rework': True,
            'rework_origin_id': self.order.id,
            'rework_reason_id': self.env.ref('lab_rework.reason_fit_issue').id,
            'rework_note': 'Loose at recall'})
        result = self.Track.search_work(rework.name)
        self.assertTrue(result['found'])
        self.assertEqual(result['order']['id'], rework.id)
        # and the original case lists the remake
        origin = self.Track.get_work(self.order.id)
        self.assertIn(rework.name, [r['name'] for r in origin['reworks']])

    def test_whitespace_around_a_pasted_reference_is_forgiven(self):
        result = self.Track.search_work('  %s  ' % self.order.name)
        self.assertTrue(result['found'])

    # ---------------------------------------------------------------- ambiguity
    def test_a_shared_patient_name_offers_candidates_instead_of_guessing(self):
        """Picking the wrong case for someone reading a name down the phone is worse
        than one more tap."""
        self._make_order('Meera Nair', partner=self.other_clinic)
        result = self.Track.search_work('Meera Nair')
        self.assertFalse(result['found'])
        self.assertEqual(len(result['matches']), 2)
        self.assertTrue(all(m['doctor'] for m in result['matches']))
        # Doctor, patient and product are what the card is read by on the phone -
        # the SO number is only what got typed to find it.
        self.assertTrue(all(m['product'] == 'Track Test Appliance'
                            for m in result['matches']))

    def test_a_single_partial_match_goes_straight_through(self):
        result = self.Track.search_work('Meera Na')
        self.assertTrue(result['found'])
        self.assertEqual(result['order']['id'], self.order.id)

    def test_an_unknown_reference_says_so_rather_than_erroring(self):
        result = self.Track.search_work('NOPE-12345')
        self.assertFalse(result['found'])
        self.assertNotIn('matches', result)

    def test_an_empty_box_is_not_a_search(self):
        self.assertTrue(self.Track.search_work('   ')['empty'])

    # ---------------------------------------------------------------- completeness
    def test_one_call_returns_every_section_the_screen_draws(self):
        """The screen makes no follow-up requests, so anything missing here is a hole
        in the UI rather than a lazy load."""
        result = self.Track.get_work(self.order.id)
        for section in ('order', 'timeline', 'productions', 'deliveries',
                        'invoices', 'reworks', 'calls'):
            self.assertIn(section, result, "%s missing from the payload" % section)

    def test_the_journey_starts_at_registration_and_records_confirmation(self):
        labels = [e['label'] for e in self.Track.get_work(self.order.id)['timeline']]
        self.assertEqual(labels[0], 'Registered')
        self.assertIn('Confirmed', labels)

    def test_delivery_outcome_and_lateness_reach_the_screen(self):
        delivery = self.env['lab.delivery'].create({
            'sale_order_id': self.order.id})
        row = self.Track.get_work(self.order.id)['deliveries']
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0]['id'], delivery.id)
        self.assertIn('near_place', row[0])
        self.assertIn('delayed', row[0])

    def test_money_owed_is_shown_not_just_the_total(self):
        invoice = self.order._create_invoices()
        invoice.action_post()
        row = self.Track.get_work(self.order.id)['invoices'][0]
        self.assertEqual(row['amount'], invoice.amount_total)
        self.assertEqual(row['residual'], invoice.amount_residual)

    def test_a_dead_id_returns_not_found_rather_than_raising(self):
        self.assertFalse(self.Track.get_work(0)['found'])

    # ---------------------------------------------------------------- cold start
    def test_the_empty_screen_still_shows_what_the_lab_is_working_on(self):
        recent = self.Track.recent_works()
        self.assertTrue(recent)
        self.assertIn(self.order.id, [r['id'] for r in recent])
        mine = next(r for r in recent if r['id'] == self.order.id)
        self.assertEqual(mine['product'], 'Track Test Appliance')

    # ---------------------------------------------------------------- access
    def test_sales_staff_see_the_delivery_without_field_work_rights(self):
        """Give people the answer, not a pile of new privileges.

        lab.delivery is scoped to the field-work and order-control groups, so office
        sales staff cannot open one — yet the delivery is half of "where is my case".
        (A CEO is expected to hold Sales / Administrator, which reads everything; this
        covers the tier below, where the elevation is what makes the screen complete.)
        """
        staff = self.env['res.users'].create({
            'name': 'Track Sales Staff', 'login': 'track_staff',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('sales_team.group_sale_salesman').id])]})
        self.order.user_id = staff
        delivery = self.env['lab.delivery'].create({'sale_order_id': self.order.id})
        with self.assertRaises(AccessError):
            self.env['lab.delivery'].with_user(staff).check_access('read')

        result = self.Track.with_user(staff).search_work(self.order.name)
        self.assertTrue(result['found'])
        self.assertEqual([d['id'] for d in result['deliveries']], [delivery.id])

    def test_an_id_from_the_browser_cannot_bypass_the_order_s_own_rules(self):
        """get_work takes a bare id, so it must not become a way to walk the id range."""
        outsider = self.env['res.users'].create({
            'name': 'Track Outsider', 'login': 'track_outsider',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(AccessError):
            self.Track.with_user(outsider).get_work(self.order.id)


@tagged('post_install', '-at_install')
class TestTrackNamesAndRefresh(TransactionCase):
    """Both names on every step, and a reading you can take again.

    (client, 2026-09-09)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Track = cls.env['lab.track']
        cls.clinic = cls.env['res.partner'].create({'name': 'Names Clinic'})
        cls.product = cls.env['product.product'].create({
            'name': 'Names Appliance', 'type': 'consu', 'is_storable': True,
            'list_price': 900.0})
        cls.order = cls.env['sale.order'].create({
            'partner_id': cls.clinic.id, 'patient': 'Named Patient',
            'order_line': [(0, 0, {'product_id': cls.product.id,
                                   'product_uom_qty': 1})]})
        cls.order.action_confirm()
        cls.maker = cls.env['res.users'].create({
            'name': 'Track Maker', 'login': 'trk_maker'})
        cls.finisher = cls.env['res.users'].create({
            'name': 'Track Finisher', 'login': 'trk_finisher'})
        cls.mo = cls.env['mrp.production'].create({
            'product_id': cls.product.id, 'product_qty': 1.0})
        if 'sale_id' in cls.mo._fields:
            cls.mo.sale_id = cls.order.id
        link_job(cls.order, cls.mo)
        cls.bench = cls.env['mrp.workcenter'].create({
            'name': 'Names Bench', 'is_finisher': True})
        cls.wo = cls.env['mrp.workorder'].create({
            'name': 'Polish', 'production_id': cls.mo.id,
            'workcenter_id': cls.bench.id, 'product_uom_id': cls.product.uom_id.id})
        cls.wo.write({'bench_user_id': cls.maker.id,
                      'finisher_user_id': cls.finisher.id})

    def _steps(self):
        data = self.Track.get_work(self.order.id)
        self.assertTrue(data['found'])
        jobs = [m for m in data['productions'] if m['id'] == self.mo.id]
        self.assertTrue(jobs, "the job must reach the payload, or this tests nothing")
        return data, jobs[0]['workorders']

    def test_each_step_carries_both_names(self):
        data, steps = self._steps()
        self.assertEqual(steps[0]['person'], 'Track Maker')
        self.assertEqual(steps[0]['finisher'], 'Track Finisher')

    def test_a_step_nobody_finished_says_nothing_rather_than_repeating_the_maker(self):
        self.wo.finisher_user_id = False
        data, steps = self._steps()
        self.assertEqual(steps[0]['person'], 'Track Maker')
        self.assertEqual(steps[0]['finisher'], '')

    def test_the_reading_says_when_it_was_taken(self):
        data = self.Track.get_work(self.order.id)
        self.assertRegex(data['read_at'], r'^\d\d:\d\d$')

    def test_refreshing_is_the_same_call_and_sees_the_change(self):
        """What the Refresh button does: ask for this one case again."""
        before = self.Track.get_work(self.order.id)
        self.assertTrue(before['found'])
        self.wo.bench_user_id = self.finisher.id
        after = self.Track.get_work(self.order.id)
        jobs = [m for m in after['productions'] if m['id'] == self.mo.id]
        self.assertTrue(jobs, "the job must reach the payload, or this tests nothing")
        self.assertEqual(jobs[0]['workorders'][0]['person'], 'Track Finisher')


@tagged('post_install', '-at_install')
class TestFloorNow(TransactionCase):
    """The landing page: where the work actually is. (client, 2026-09-09)"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Track = cls.env['lab.track']
        cls.clinic = cls.env['res.partner'].create({'name': 'Floor Clinic'})
        cls.product = cls.env['product.product'].create({
            'name': 'Floor Appliance', 'type': 'consu', 'is_storable': True,
            'list_price': 600.0})
        cls.order = cls.env['sale.order'].create({
            'partner_id': cls.clinic.id, 'patient': 'Floor Patient',
            'order_line': [(0, 0, {'product_id': cls.product.id,
                                   'product_uom_qty': 1})]})
        cls.order.action_confirm()
        cls.bench = cls.env['mrp.workcenter'].create({'name': 'Floor Bench'})
        cls.mo = cls.env['mrp.production'].create({
            'product_id': cls.product.id, 'product_qty': 1.0})
        if 'sale_id' in cls.mo._fields:
            cls.mo.sale_id = cls.order.id
        link_job(cls.order, cls.mo)
        # Confirmed: a draft job is not on the floor, and the floor says so.
        cls.mo.action_confirm()
        cls.tech = cls.env['res.users'].create({
            'name': 'Floor Tech', 'login': 'floor_tech'})

    def _step(self, name, accepted=False, sequence=10):
        # An explicit sequence: the model's default is higher than the 20 the
        # 'second step' below uses, which made the second step the first.
        wo = self.env['mrp.workorder'].create({
            'name': name, 'production_id': self.mo.id,
            'workcenter_id': self.bench.id, 'sequence': sequence,
            'product_uom_id': self.product.uom_id.id})
        if accepted:
            wo.write({'accepted_at': fields.Datetime.now(),
                      'bench_user_id': self.tech.id})
        return wo

    def _mine(self, floor):
        return next((s for s in floor['stations'] if s['id'] == self.bench.id), None)

    def test_a_case_is_counted_at_one_station_not_at_every_one(self):
        """The fault this page was rebuilt around. (client, 2026-09-09)

        A case has a step at every bench it will ever pass through, all open
        until it gets there, so counting open work orders per station reported
        the entire backlog at every bench - the same number and the same names
        under Acrylisation, Trimming and Polishing alike.
        """
        later = self.env['mrp.workcenter'].create({'name': 'Later Bench'})
        self._step('First step')
        self.env['mrp.workorder'].create({
            'name': 'Second step', 'production_id': self.mo.id,
            'workcenter_id': later.id, 'sequence': 20,
            'product_uom_id': self.product.uom_id.id})
        # Per station, not from the page's top twelve: this runs on a live floor
        # where a bench with one test job is nowhere near the busiest.
        here = [r['id'] for r in self.Track.station_jobs(self.bench.id)]
        there = [r['id'] for r in self.Track.station_jobs(later.id)]
        self.assertEqual(len(here), 1, "the case stands at its first open step")
        self.assertEqual(there, [], "and nowhere else")

    def test_it_lists_the_stations_with_what_they_hold(self):
        self._step('Being made', accepted=True)
        floor = self.Track.floor_now()
        self.assertTrue(floor['has_floor'])
        first = floor['stations'][0]
        for key in ('name', 'working', 'waiting', 'total', 'oldest_h', 'share'):
            self.assertIn(key, first)
        self.assertEqual(first['total'], first['working'] + first['waiting'])
        rows = self.Track.station_jobs(self.bench.id)
        self.assertEqual(len(rows), 1, "the bench holds its one accepted job")
        self.assertEqual(rows[0]['technician'], 'Floor Tech')

    def test_a_job_in_production_says_who_has_it_and_since_when(self):
        self._step('Being made', accepted=True)
        row = self.Track.station_jobs(self.bench.id)[0]
        self.assertEqual(row['technician'], 'Floor Tech')
        self.assertEqual(row['patient'], 'Floor Patient')
        self.assertEqual(row['production'], self.mo.name)
        self.assertRegex(row['since'], r'^\d\d:\d\d$')
        self.assertEqual(row['order_id'], self.order.id, "and it opens the case")

    def test_a_waiting_job_says_how_long_it_has_waited(self):
        wo = self._step('Not started')
        self.env.cr.execute(
            "UPDATE mrp_workorder SET create_date = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(hours=5), wo.id))
        self.env.invalidate_all()
        row = self.Track.station_jobs(self.bench.id)[0]
        self.assertEqual(row['id'], wo.id)
        self.assertGreaterEqual(row['hours'], 4)
        self.assertGreaterEqual(row['hours'], 4)

    def test_the_oldest_list_says_where_the_case_is_standing(self):
        """The one list on the page that says what to do about it."""
        wo = self._step('Not started')
        # Older than anything a live floor holds, so it is the oldest by right.
        self.env.cr.execute(
            "UPDATE mrp_workorder SET create_date = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(days=3650), wo.id))
        self.env.invalidate_all()
        oldest = self.Track.floor_now()['oldest']
        self.assertEqual(oldest[0]['id'], wo.id, "the case standing longest leads")
        self.assertEqual(oldest[0]['station'], 'Floor Bench')
        self.assertGreaterEqual(oldest[0]['hours'], 24 * 3000)

    def test_the_jobs_of_a_station_are_fetched_on_demand(self):
        """Twelve stations of six rows is a payload nobody reads."""
        self._step('Being made', accepted=True)
        floor = self.Track.floor_now()
        self.assertIsInstance(floor['stations'][0]['working'], int,
                              "the page carries counts, not rows")
        self.assertTrue(self.Track.station_jobs(self.bench.id))

    def test_the_day_line_counts_what_started_and_what_is_waiting(self):
        self._step('Being made', accepted=True)
        self._step('Not started')
        floor = self.Track.floor_now()
        self.assertGreaterEqual(floor['started_today'], 1)
        self.assertGreaterEqual(floor['waiting_today'], 1)
        self.assertRegex(floor['as_of'], r'^\d\d:\d\d$')

    def _second_job(self, patient='Other Patient'):
        """A second live case standing at the same bench, on an order of its own."""
        other = self.env['sale.order'].create({
            'partner_id': self.env['res.partner'].create({'name': 'Floor Other'}).id,
            'patient': patient,
            'order_line': [(0, 0, {'product_id': self.product.id,
                                   'product_uom_qty': 1})]})
        other.action_confirm()
        mo = self.env['mrp.production'].create({
            'product_id': self.product.id, 'product_qty': 1.0})
        if 'sale_id' in mo._fields:
            mo.sale_id = other.id
        link_job(other, mo)
        mo.action_confirm()
        wo = self.env['mrp.workorder'].create({
            'name': 'Their step', 'production_id': mo.id,
            'workcenter_id': self.bench.id, 'sequence': 10,
            'product_uom_id': self.product.uom_id.id})
        return other, wo

    def test_a_reader_only_sees_the_jobs_of_orders_they_can_open(self):
        """The floor is found as superuser, but each row names a patient, a doctor
        and an order. A field executive whose rules stop at their own route was
        handed every live case in the lab here - the thing get_work already
        refuses. (2026-09-15)"""
        mine = self._step('My step')
        other, theirs = self._second_job('Hidden Patient')
        self.env.cr.execute(
            "UPDATE mrp_workorder SET create_date = %s WHERE id = ANY(%s)",
            (fields.Datetime.now() - timedelta(days=3650), [mine.id, theirs.id]))
        self.env.invalidate_all()
        reader = self.env['res.users'].create({
            'name': 'Floor Route Exec', 'login': 'floor_route_exec',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('sales_team.group_sale_salesman').id])]})
        (self.order | other).user_id = reader
        # A route rule of the kind the executives carry, pinned here so the test
        # does not depend on which of the database's own rules are switched on.
        self.env['ir.rule'].create({
            'name': 'Floor test: not that order',
            'model_id': self.env['ir.model']._get_id('sale.order'),
            'domain_force': "[('id', '!=', %d)]" % other.id,
            'perm_read': True})
        self.assertTrue(self.order.with_user(reader).has_access('read'))
        self.assertFalse(other.with_user(reader).has_access('read'))

        as_reader = self.Track.with_user(reader)
        seen = {r['id'] for r in as_reader.station_jobs(self.bench.id)}
        self.assertIn(mine.id, seen, "their own case is still on the bench")
        self.assertNotIn(theirs.id, seen, "a case they cannot open is not listed")
        oldest = {r['id'] for r in as_reader.floor_now()['oldest']}
        self.assertIn(mine.id, oldest)
        self.assertNotIn(theirs.id, oldest)
        self.assertNotIn('Hidden Patient', str(as_reader.floor_now()))

        everyone = {r['id'] for r in self.Track.station_jobs(self.bench.id)}
        self.assertTrue({mine.id, theirs.id} <= everyone,
                        "whoever may read both still sees both")

    def test_a_station_never_returns_more_rows_than_the_cap(self):
        self._step('My step')
        self._second_job()
        with patch.object(type(self.Track), 'FLOOR_ROWS_MAX', 1):
            self.assertEqual(len(self.Track.station_jobs(self.bench.id, limit=10 ** 6)), 1)
        self.assertEqual(len(self.Track.station_jobs(self.bench.id, limit='all')), 2,
                         "a limit that is not a number falls back to the default")

    def test_the_case_detail_says_when_each_step_happened(self):
        wo = self._step('Timed step', accepted=True)
        # `waiting_h` counts from date_start only for a step actually RUNNING;
        # anything else is counted from when the step was created, which in a
        # fixture is now. Without the state this asserted 2 hours against a
        # step that had been waiting none - and never ran to say so.
        # (client, 2026-09-12)
        wo.write({'date_start': fields.Datetime.now() - timedelta(hours=2),
                  'state': 'progress'})
        data = self.Track.get_work(self.order.id)
        jobs = [m for m in data['productions'] if m['id'] == self.mo.id]
        self.assertTrue(jobs, "the job must reach the payload, or this tests nothing")
        step = jobs[0]['workorders'][0]
        self.assertTrue(step['started'], "the step says when it began")
        self.assertIn('minutes', step)
        self.assertGreaterEqual(step['waiting_h'], 1, "and how long it has been there")


@tagged('post_install', '-at_install')
class TestTrackClock(TransactionCase):
    """Every stamp on this screen is the lab's own clock.

    Odoo stores datetimes in UTC. Formatted straight off the record they put
    this lab 5.5 hours behind itself: the floor reported a step finished at
    09:28 that a bench had actually finished at 14:58, which reads as the
    morning's work rather than the afternoon's. (client, 2026-09-12)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Track = cls.env['lab.track']
        # Pinned, not inherited: this must assert the same thing on a laptop in
        # any timezone, and an empty tz silently falls back to UTC - which is
        # the bug, so it would pass while broken.
        cls.env.user.tz = 'Asia/Kolkata'

    def test_a_datetime_is_shown_in_the_readers_own_clock(self):
        stamped = self.Track._stamp(
            fields.Datetime.to_datetime('2026-09-12 09:28:00'))
        self.assertEqual(stamped, '12 Sep 14:58',
                         "09:28 UTC is the lab's 14:58, not its 09:28")

    def test_a_date_is_left_exactly_as_it_is(self):
        # `datetime` subclasses `date`, so a careless isinstance test shifts
        # these too and an invoice dated the 1st reads as the 31st.
        stamped = self.Track._stamp(
            fields.Date.to_date('2026-09-01'), '%d %b %Y')
        self.assertEqual(stamped, '01 Sep 2026')

    def test_the_floor_s_today_is_the_lab_s_day_for_a_user_without_a_timezone(self):
        """`user.tz or 'UTC'` started the day at 05:30 here, so the small hours'
        work counted towards yesterday. (2026-09-15)"""
        self.env.user.tz = False
        self.env.company.partner_id.tz = False
        with freeze_time('2026-09-12 20:00:00'):      # 01:30 on the 13th in Kochi
            start, end = self.Track._today_window()
        self.assertEqual(start, datetime(2026, 9, 12, 18, 30))
        self.assertEqual(end, datetime(2026, 9, 13, 18, 30))

    def test_nothing_is_stamped_where_there_is_no_time(self):
        self.assertEqual(self.Track._stamp(False), '')

    def test_an_order_taken_late_is_not_dated_a_day_early(self):
        """20:30 UTC is 02:00 the NEXT morning here - the day the lab says it
        took the order, and the day the doctor will quote back."""
        clinic = self.env['res.partner'].create({'name': 'Clock Clinic'})
        product = self.env['product.product'].create(
            {'name': 'Clock Appliance', 'type': 'consu'})
        order = self.env['sale.order'].create({
            'partner_id': clinic.id, 'patient': 'Clock Patient',
            'order_line': [(0, 0, {'product_id': product.id,
                                   'product_uom_qty': 1})]})
        order.date_order = fields.Datetime.to_datetime('2026-09-11 20:30:00')
        self.assertEqual(self.Track._row(order)['date'], '12 Sep 2026')

    def test_a_finished_step_says_the_hour_the_bench_finished_it(self):
        """The screen the fault was reported from: the work centre card."""
        clinic = self.env['res.partner'].create({'name': 'Clock Floor Clinic'})
        product = self.env['product.product'].create(
            {'name': 'Clock Floor Appliance', 'type': 'consu',
             'is_storable': True})
        order = self.env['sale.order'].create({
            'partner_id': clinic.id, 'patient': 'Clock Floor Patient',
            'order_line': [(0, 0, {'product_id': product.id,
                                   'product_uom_qty': 1})]})
        order.action_confirm()
        bench = self.env['mrp.workcenter'].create({'name': 'Clock Bench'})
        mo = self.env['mrp.production'].create(
            {'product_id': product.id, 'product_qty': 1.0})
        if 'sale_id' in mo._fields:
            mo.sale_id = order.id
        link_job(order, mo)
        mo.action_confirm()
        wo = self.env['mrp.workorder'].create({
            'name': 'Clock step', 'production_id': mo.id,
            'workcenter_id': bench.id, 'sequence': 10,
            'product_uom_id': product.uom_id.id})
        # Written straight to the columns: MRP plans a confirmed job, and the
        # planner recomputed date_finished to the next working morning the
        # moment this test wrote it. The stamps are the subject here, so they
        # are pinned where the compute cannot reach them. (client, 2026-09-12)
        self.env.cr.execute(
            "UPDATE mrp_workorder SET date_start = %s, date_finished = %s"
            " WHERE id = %s",
            ('2026-09-12 09:28:00', '2026-09-12 09:39:00', wo.id))
        wo.invalidate_recordset()
        data = self.Track.get_work(order.id)
        jobs = [m for m in data['productions'] if m['id'] == mo.id]
        self.assertTrue(jobs, "the job must reach the payload, or this tests nothing")
        step = jobs[0]['workorders'][0]
        self.assertEqual(step['started'], '12 Sep 14:58')
        self.assertEqual(step['finished'], '12 Sep 15:09')

    def test_a_user_with_no_timezone_still_reads_the_labs_clock(self):
        """The majority case, and the one that makes this fix worth shipping.

        129 of the 156 active users on this database have no timezone set.
        `context_timestamp` falls back to UTC for them, so a fix that converts
        only through the user would leave most of the lab reading 09:28 for
        work finished at 14:58 - the original complaint, unchanged, for
        everybody except the few who happen to have set a timezone.
        """
        nobody = self.env['res.users'].create(
            {'name': 'No Timezone', 'login': 'clock_no_tz', 'tz': False})
        stamped = self.Track.with_user(nobody)._stamp(
            fields.Datetime.to_datetime('2026-09-12 09:28:00'))
        self.assertEqual(stamped, '12 Sep 14:58')

    def test_a_users_own_timezone_still_wins(self):
        """The fallback must not overrule somebody who HAS chosen a zone."""
        abroad = self.env['res.users'].create(
            {'name': 'Abroad', 'login': 'clock_abroad', 'tz': 'Europe/London'})
        stamped = self.Track.with_user(abroad)._stamp(
            fields.Datetime.to_datetime('2026-09-12 09:28:00'))
        self.assertEqual(stamped, '12 Sep 10:28', "BST, not the lab's zone")


@tagged('post_install', '-at_install')
class TestTrackArch(TransactionCase):
    """A case sold as Upper & Lower is made as two chains inside one job.

    The rail on this screen therefore runs through the same benches twice, and
    without a marker there is nothing to say which run is the upper and which
    the lower - the fault the floor reported. (client, 2026-09-12)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Track = cls.env['lab.track']
        cls.clinic = cls.env['res.partner'].create({'name': 'Arch Track Clinic'})
        cls.product = cls.env['product.product'].create(
            {'name': 'Arch Track Appliance', 'type': 'consu', 'is_storable': True})
        cls.bench = cls.env['mrp.workcenter'].create({'name': 'Arch Track Bench'})

    def _case(self, ul):
        order = self.env['sale.order'].create({
            'partner_id': self.clinic.id, 'patient': 'Arch Track Patient',
            'order_line': [(0, 0, {'product_id': self.product.id,
                                   'product_uom_qty': 1, 'ul': ul})]})
        order.action_confirm()
        mo = self.env['mrp.production'].create(
            {'product_id': self.product.id, 'product_qty': 1.0})
        # `ul` and `sale_id` are stored relateds through sale_line_id, so the
        # link is written by SQL rather than inventing a procurement chain.
        self.env.cr.execute(
            "UPDATE mrp_production SET sale_line_id = %s, sale_id = %s, ul = %s"
            " WHERE id = %s",
            (order.order_line[0].id, order.id, ul, mo.id))
        mo.invalidate_recordset()
        link_job(order, mo)
        return order, mo

    def _job(self, order, mo):
        data = self.Track.get_work(order.id)
        jobs = [m for m in data['productions'] if m['id'] == mo.id]
        self.assertTrue(jobs, "the job must reach the payload, or this tests nothing")
        return jobs[0]

    def test_the_job_says_what_was_ordered(self):
        order, mo = self._case('ul')
        self.assertEqual(self._job(order, mo)['ul'], 'UL')

    def test_a_single_arch_job_says_its_own_side(self):
        order, mo = self._case('upper')
        self.assertEqual(self._job(order, mo)['ul'], 'U')

    def test_each_step_says_which_piece_it_makes(self):
        order, mo = self._case('ul')
        for arch in ('upper', 'lower'):
            wo = self.env['mrp.workorder'].create({
                'name': 'Bend', 'production_id': mo.id,
                'workcenter_id': self.bench.id,
                'product_uom_id': self.product.uom_id.id})
            if 'arch' not in wo._fields:
                self.skipTest("this build has no arch on work orders")
            wo.arch = arch
        steps = self._job(order, mo)['workorders']
        self.assertEqual(sorted(s['arch'] for s in steps), ['L', 'U'],
                         "the same bench twice, told apart")

    def test_a_step_on_a_single_arch_case_is_not_marked(self):
        order, mo = self._case('upper')
        self.env['mrp.workorder'].create({
            'name': 'Bend', 'production_id': mo.id,
            'workcenter_id': self.bench.id,
            'product_uom_id': self.product.uom_id.id})
        steps = self._job(order, mo)['workorders']
        self.assertEqual([s['arch'] for s in steps], [''],
                         "nothing to tell apart, so nothing to say")
