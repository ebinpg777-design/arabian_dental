# -*- coding: utf-8 -*-
"""A technician's day, slot by slot, from the board's stamps.

One day, built by hand in the lab's own time: Asha picks a crown up at 09:10 and
hands it on at 10:40, starts a second at 11:00 that a timer says took 25 minutes,
and stands between 13:00 and 14:00 while a job waits at her station. The grid
must say exactly that - and must not count yesterday's forgotten case as work.
"""
from datetime import datetime, time, timedelta
from unittest.mock import patch

import pytz

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestTechDay(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        mrp_user = cls.env.ref('mrp.group_mrp_user').id
        cls.asha = cls.env['res.users'].create({'name': 'Asha Day', 'login': 'td_asha', 'group_ids': [(4, mrp_user)]})
        cls.ben = cls.env['res.users'].create({'name': 'Ben Day', 'login': 'td_ben', 'group_ids': [(4, mrp_user)]})
        # the rows are the technicians named on a work centre: Asha on Ceramics, Ben
        # on Casting - anybody else with a job is kept aside (client, 2026-10-14)
        cls.bench = cls.env['mrp.workcenter'].create({'name': 'TD Ceramics', 'users': [(6, 0, cls.asha.ids)]})
        cls.other = cls.env['mrp.workcenter'].create({'name': 'TD Casting', 'users': [(6, 0, cls.ben.ids)]})
        cls.product = cls.env['product.product'].create({'name': 'TD Crown', 'is_storable': True})
        cls.bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.product.product_tmpl_id.id, 'product_qty': 1.0,
            'operation_ids': [(0, 0, {'name': 'Build', 'workcenter_id': cls.bench.id, 'time_cycle_manual': 60.0})]})
        cls.Day = cls.env['lab.tech.day']
        cls.Station = cls.env['lab.station']
        cls.tz = cls.Station._lab_tz()
        cls.day = fields.Date.today() - timedelta(days=3)

    def at(self, hh, mm, day=None):
        """The lab's local clock time as the naive UTC Odoo stores."""
        local = self.tz.localize(datetime.combine(day or self.day, time(hh, mm)))
        return local.astimezone(pytz.utc).replace(tzinfo=None)

    def job(self, workcenter=None):
        mo = self.env['mrp.production'].create({'product_id': self.product.id, 'bom_id': self.bom.id, 'product_qty': 1.0})
        mo.action_confirm()
        wo = mo.workorder_ids[:1]
        if workcenter:
            wo.workcenter_id = workcenter
        return wo

    def stamp(self, wo, user, accepted, handed=None, created=None):
        vals = {'bench_user_id': user.id, 'accepted_by_id': user.id, 'accepted_at': accepted}
        if handed:
            vals.update({'handed_over_by_id': user.id, 'handed_over_at': handed})
        wo.write(vals)
        if created:
            self.env.cr.execute("UPDATE mrp_workorder SET create_date = %s WHERE id = %s", (created, wo.id))
        wo.invalidate_recordset()

    def row(self, data, user):
        return next(r for r in data['rows'] if r['id'] == user.id)

    def cell(self, row, hh, mm=0):
        m = hh * 60 + mm
        return next(c for c in row['cells'] if c['m'] <= m < c['m'] + 60)

    def test_a_bench_job_fills_the_slots_it_spans(self):
        wo = self.job()
        self.stamp(wo, self.asha, self.at(9, 10), self.at(10, 40))
        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60)
        r = self.row(data, self.asha)
        self.assertEqual(self.cell(r, 9)['busy'], 50)
        self.assertEqual(self.cell(r, 10)['busy'], 40)
        self.assertEqual(self.cell(r, 10)['handed'], 1)
        self.assertEqual(self.cell(r, 9)['accepted'], 1)
        self.assertEqual(r['busy'], 90)
        self.assertEqual(r['handed'], 1)
        self.assertEqual(r['earned'], 60, "the costed minutes of the job handed on")
        self.assertEqual(r['efficiency'], 67)
        self.assertEqual(r['first'], 9 * 60 + 10)
        self.assertGreaterEqual(data['kpis']['handed'], 1)
        self.assertEqual(data['lo'] <= 9 * 60 and data['hi'] >= 11 * 60, True)
        seg = next(s for s in r['segments'] if s['wo'] == wo.id)
        self.assertEqual((seg['a'], seg['b'], seg['kind']), (9 * 60 + 10, 10 * 60 + 40, 'bench'))

    def test_a_timer_beats_the_wall_clock(self):
        wo = self.job()
        self.stamp(wo, self.asha, self.at(11, 0), self.at(12, 30))
        loss = self.env['mrp.workcenter.productivity.loss'].search([('loss_type', '=', 'productive')], limit=1)
        self.env['mrp.workcenter.productivity'].create({
            'workorder_id': wo.id, 'workcenter_id': self.bench.id, 'user_id': self.asha.id, 'loss_id': loss.id,
            'date_start': self.at(11, 20), 'date_end': self.at(11, 45)})
        r = self.row(self.Day.get_day(day=fields.Date.to_string(self.day), slot=60), self.asha)
        self.assertEqual(r['busy'], 25, "hands-on is the timer, not the ninety minutes on the bench")
        self.assertEqual(self.cell(r, 11)['busy'], 25)
        self.assertEqual(self.cell(r, 12)['busy'], 0)
        self.assertEqual(self.cell(r, 12)['held'], 30)
        kinds = {s['kind'] for s in r['segments'] if s['wo'] == wo.id}
        self.assertEqual(kinds, {'bench', 'timer'})

    def test_odoobot_timers_are_not_a_person(self):
        wo = self.job()
        loss = self.env['mrp.workcenter.productivity.loss'].search([('loss_type', '=', 'productive')], limit=1)
        self.env['mrp.workcenter.productivity'].create({
            'workorder_id': wo.id, 'workcenter_id': self.bench.id, 'user_id': self.env.ref('base.user_root').id,
            'loss_id': loss.id, 'date_start': self.at(9, 0), 'date_end': self.at(10, 0)})
        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60)
        self.assertFalse([r for r in data['rows'] if r['id'] == self.env.ref('base.user_root').id])

    def test_idle_while_work_waited_is_measured(self):
        done = self.job()
        self.stamp(done, self.asha, self.at(9, 0), self.at(13, 0))
        late = self.job()
        self.stamp(late, self.asha, self.at(14, 0), self.at(15, 0), created=self.at(12, 30))
        r = self.row(self.Day.get_day(day=fields.Date.to_string(self.day), slot=60), self.asha)
        c13 = self.cell(r, 13)
        self.assertEqual(c13['busy'], 0)
        self.assertEqual(c13['idle_wait'], 60, "present (between two jobs) with the second one waiting at her station")
        self.assertEqual(c13['state'], 'idle_wait')
        self.assertEqual(r['idle_wait'], 60)
        self.assertEqual(r['longest_gap'], {'m': 13 * 60, 'len': 60})

    def test_a_case_carried_in_is_held_not_worked(self):
        wo = self.job()
        self.stamp(wo, self.ben, self.at(16, 0, self.day - timedelta(days=2)))
        r = self.row(self.Day.get_day(day=fields.Date.to_string(self.day), slot=60), self.ben)
        self.assertEqual(r['busy'], 0)
        self.assertEqual(r['present'], 0, "a case left on the bench does not make anybody present")
        self.assertEqual(r['carried'], 1)
        self.assertTrue(any(s['kind'] == 'carried' for s in r['segments']))

    def test_slots_can_be_narrower(self):
        wo = self.job()
        self.stamp(wo, self.asha, self.at(9, 10), self.at(9, 40))
        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=15)
        r = self.row(data, self.asha)
        busy = {c['m']: c['busy'] for c in r['cells'] if c['busy']}
        self.assertEqual(busy, {9 * 60: 5, 9 * 60 + 15: 15, 9 * 60 + 30: 10})
        self.assertEqual(data['slot'], 15)

    def test_the_station_filter_keeps_only_that_station(self):
        a = self.job()
        self.stamp(a, self.asha, self.at(9, 0), self.at(10, 0))
        b = self.job(self.other)
        self.stamp(b, self.ben, self.at(9, 0), self.at(10, 0))
        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60, station_ids=[self.other.id])
        self.assertEqual({r['id'] for r in data['rows']} & {self.asha.id, self.ben.id}, {self.ben.id})

    def test_the_usual_profile_comes_from_the_same_weekday(self):
        for weeks in (1, 2):
            wo = self.job()
            self.stamp(wo, self.asha, self.at(9, 0, self.day - timedelta(weeks=weeks)), self.at(10, 10, self.day - timedelta(weeks=weeks)))
        today = self.job()
        self.stamp(today, self.asha, self.at(9, 0), self.at(10, 10))
        r = self.row(self.Day.get_day(day=fields.Date.to_string(self.day), slot=60), self.asha)
        self.assertEqual(self.cell(r, 10)['usual'], 0.5, "two hand-overs over four weeks")
        self.assertEqual(r['usual_handed'], 0.5)

    def test_the_strip_and_the_roster(self):
        wo = self.job()
        self.stamp(wo, self.asha, self.at(9, 0), self.at(10, 0))
        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60, rostered=True)
        strip = {s['date']: s for s in data['strip']}
        self.assertGreaterEqual(strip[fields.Date.to_string(self.day)]['jobs'], 1)
        self.assertEqual(len(data['strip']), 14)
        self.assertIn(self.asha.id, [r['id'] for r in data['rows']])
        self.assertTrue(self.row(data, self.asha)['rostered'])

    def test_a_technician_without_the_production_group_is_refused(self):
        outsider = self.env['res.users'].create({'name': 'TD Outsider', 'login': 'td_out',
                                                 'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(AccessError):
            self.Day.with_user(outsider).get_day()

    # ------------------------------------------------------ whose job it is
    # The lead scans for the whole room, so Accept and Hand over carry the lead's
    # name. The day is the TECHNICIAN's and the FINISHER's, from the names on
    # the work order - the first version put every job on the lead. (client, 2026-09-30)
    def test_the_job_is_the_technicians_not_the_leads_who_scanned_it(self):
        lead = self.env['res.users'].create({'name': 'Lead Day', 'login': 'td_lead',
                                             'group_ids': [(4, self.env.ref('mrp.group_mrp_user').id)]})
        wo = self.job()
        wo.write({'bench_user_id': self.asha.id, 'accepted_by_id': lead.id, 'accepted_at': self.at(9, 0),
                  'handed_over_by_id': lead.id, 'handed_over_at': self.at(10, 0)})
        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60)
        r = self.row(data, self.asha)
        self.assertEqual((r['busy'], r['handed'], r['accepted']), (60, 1, 1))
        self.assertNotIn(lead.id, [row['id'] for row in data['rows']],
                         "the lead who pressed the buttons did none of the work")

    def test_a_finisher_shares_the_job_and_takes_the_hand_over(self):
        wo = self.job()
        wo.write({'bench_user_id': self.asha.id, 'finisher_user_id': self.ben.id,
                  'accepted_by_id': self.asha.id, 'accepted_at': self.at(9, 0),
                  'handed_over_by_id': self.asha.id, 'handed_over_at': self.at(10, 0)})
        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60)
        asha, ben = self.row(data, self.asha), self.row(data, self.ben)
        self.assertEqual(asha['busy'], 60, "the technician had it on the bench")
        self.assertEqual(ben['busy'], 60, "so did the finisher")
        self.assertEqual((asha['accepted'], asha['handed']), (1, 1), "the technician's job")
        self.assertEqual((ben['accepted'], ben['handed']), (0, 1), "and the finisher's too")
        self.assertEqual(data['kpis']['handed'], 1, "but on the floor a job two people share is one job")
        self.assertEqual(self.cell(self.row(data, self.asha), 10)['handed'], 1)
        self.assertEqual(next(f for f in data['floor'] if f['m'] == 10 * 60)['handed'], 1)

    def test_a_clock_left_running_from_an_earlier_day_is_carried_not_worked(self):
        wo = self.job()
        loss = self.env['mrp.workcenter.productivity.loss'].search([('loss_type', '=', 'productive')], limit=1)
        self.env['mrp.workcenter.productivity'].create({
            'workorder_id': wo.id, 'workcenter_id': self.bench.id, 'user_id': self.asha.id, 'loss_id': loss.id,
            'date_start': self.at(9, 0, self.day - timedelta(days=3)), 'date_end': False})
        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60)
        r = self.row(data, self.asha)
        self.assertEqual(r['busy'], 0, "a month-old timer is not a day on the job")
        self.assertEqual(r['carried'], 1)
        self.assertEqual({s['kind'] for s in r['segments']}, {'carried'})

    def test_a_job_with_nobody_named_is_on_nobodys_row(self):
        wo = self.job()
        wo.write({'accepted_by_id': self.asha.id, 'accepted_at': self.at(9, 0),
                  'handed_over_by_id': self.asha.id, 'handed_over_at': self.at(10, 0)})
        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60)
        self.assertNotIn(self.asha.id, [row['id'] for row in data['rows']],
                         "pressing the button is not doing the work")

    def test_the_usual_profile_follows_the_same_names(self):
        lead = self.env['res.users'].create({'name': 'Lead Usual', 'login': 'td_lead_usual',
                                             'group_ids': [(4, self.env.ref('mrp.group_mrp_user').id)]})
        for weeks in (1, 2):
            then = self.day - timedelta(weeks=weeks)
            wo = self.job()
            wo.write({'bench_user_id': self.asha.id, 'accepted_by_id': lead.id, 'accepted_at': self.at(9, 0, then),
                      'handed_over_by_id': lead.id, 'handed_over_at': self.at(10, 10, then)})
        today = self.job()
        self.stamp(today, self.asha, self.at(9, 0), self.at(10, 10))
        r = self.row(self.Day.get_day(day=fields.Date.to_string(self.day), slot=60), self.asha)
        self.assertEqual(r['usual_handed'], 0.5, "those weeks' jobs were Asha's, whoever scanned them")

    # ------------------------------------------------------ what a bar says
    # The chart and the slot list name the sale order and the patient, which is
    # how the counter knows a case; the MO alone is the lab's own number. And a
    # job that only Odoo's timer put on the chart is named all the same.
    # (client, 2026-09-30)
    def _order(self, patient):
        clinic = self.env['res.partner'].create({'name': 'TD Clinic %s' % patient})
        return self.env['sale.order'].create({
            'partner_id': clinic.id, 'patient': patient,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 1})]})

    def _sold(self, wo, patient):
        """Tie the job to a sale order the way the lab's orders are tied: through the
        line. `mrp.production.sale_id` is a stored related field on it, so writing
        the order directly writes nothing."""
        order = self._order(patient)
        wo.production_id.sale_line_id = order.order_line[:1]
        return order

    def test_the_bar_names_the_sale_order_and_the_patient(self):
        wo = self.job()
        order = self._sold(wo, 'TD Patient')
        self.stamp(wo, self.asha, self.at(9, 0), self.at(10, 0))
        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60)
        seg = next(s for s in self.row(data, self.asha)['segments'] if s['wo'] == wo.id)
        self.assertEqual((seg['so'], seg['so_id'], seg['mo'], seg['patient']),
                         (order.name, order.id, wo.production_id.name, 'TD Patient'))
        self.assertIn(order.partner_id.id, data['partners'], "the doctor's name is sent for the tooltip")

    def test_a_job_only_a_timer_put_on_the_chart_is_named_too(self):
        wo = self.job()
        order = self._sold(wo, 'Timer Patient')
        loss = self.env['mrp.workcenter.productivity.loss'].search([('loss_type', '=', 'productive')], limit=1)
        self.env['mrp.workcenter.productivity'].create({
            'workorder_id': wo.id, 'workcenter_id': self.bench.id, 'user_id': self.asha.id, 'loss_id': loss.id,
            'date_start': self.at(11, 0), 'date_end': self.at(11, 30)})
        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60)
        seg = next(s for s in self.row(data, self.asha)['segments'] if s['wo'] == wo.id)
        self.assertEqual(seg['kind'], 'timer')
        self.assertEqual((seg['so'], seg['mo'], seg['patient'], seg['partner_id']),
                         (order.name, wo.production_id.name, 'Timer Patient', order.partner_id.id))

    # ---------------------------------------------------- the clock nobody stopped
    def test_a_timer_left_open_does_not_read_as_a_whole_day_on_a_job(self):
        """Two technicians showed 14h 13m "on jobs" at 100% on a day nobody worked
        fourteen hours. An open timer has no end, so it is counted to `now`: a scan
        at half past midnight that was never closed becomes the whole day's work.
        `stale()` only catches a clock started on an EARLIER day, so this one walks
        straight past it. (client, 2026-09-30)

        Nobody is on a job while they are not at work, so it counts only inside
        attendance - and what falls outside is still HELD, because the job really is
        sitting at that bench.
        """
        self._attend(self._employee(self.asha), self.at(9, 0), self.at(17, 0))

        wo = self.job()
        self.stamp(wo, self.asha, self.at(0, 32))
        loss = self.env['mrp.workcenter.productivity.loss'].search(
            [('loss_type', '=', 'productive')], limit=1)
        # started at 00:32 and never stopped - no date_end at all
        self.env['mrp.workcenter.productivity'].create({
            'workorder_id': wo.id, 'workcenter_id': self.bench.id,
            'user_id': self.asha.id, 'loss_id': loss.id,
            'date_start': self.at(0, 32), 'date_end': False})

        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60)
        r = self.row(data, self.asha)

        self.assertEqual(self.cell(r, 3)['busy'], 0,
                         "nobody is on a job at three in the morning")
        self.assertGreater(self.cell(r, 3)['held'], 0,
                           "but the job is still sitting at the bench, and says so")
        self.assertLessEqual(r['busy'], 8 * 60,
                             "on a job for no longer than the hours actually worked")
        if r['utilisation'] is not None:
            self.assertLessEqual(r['utilisation'], 100,
                                 "utilisation cannot exceed the time somebody was there")

    # ------------------------------------------------------ who the rows are
    # "Only show the users who are assigned in any work centres": the rows are the
    # technicians on a station's list; the office staff with a check-in and no job
    # were forty empty lanes. (client, 2026-10-14)
    # hr and hr_attendance are not dependencies of this module (the lab installs them
    # beside it): without them there is no employee, no shift and no check-in to test
    def _employee(self, user, calendar=None, tz=None):
        """The person's employee, its hours read on the lab's clock: Odoo 20 keeps the
        timezone on the employee (its contract version), no longer on the calendar."""
        if 'hr.employee' not in self.env:
            self.skipTest("hr is not installed")
        vals = {'name': user.name, 'user_id': user.id, 'company_id': self.env.company.id, 'tz': tz or self.tz.zone}
        if calendar is not None:
            vals['resource_calendar_id'] = calendar.id
        return self.env['hr.employee'].sudo().create(vals)

    def _calendar(self, day=None, lunch=True, hours=((8, 12), (13, 17))):
        """Working hours for the weekday of `day` only: 08-12, lunch, 13-17 by default.
        Odoo 20 has no lunch line: the lunch is the gap between the two work periods,
        and a day without one is a single period from the first hour to the last."""
        dow = str((day or self.day).weekday())
        if lunch:
            spans = hours
        else:
            spans = ((hours[0][0], hours[1][1]),)
        lines = [(0, 0, {'dayofweek': dow, 'hour_from': a, 'hour_to': b}) for a, b in spans]
        return self.env['resource.calendar'].sudo().create({'name': 'TD hours %s' % dow, 'attendance_ids': lines})

    def _attend(self, employee, check_in, check_out=None):
        if 'hr.attendance' not in self.env:
            self.skipTest("hr_attendance is not installed")
        return self.env['hr.attendance'].sudo().create({'employee_id': employee.id, 'check_in': check_in, 'check_out': check_out})

    def _created(self, wo, at):
        self.env.cr.execute("UPDATE mrp_workorder SET create_date = %s WHERE id = %s", (at, wo.id))
        wo.invalidate_recordset()

    def test_rows_are_the_technicians_on_a_station_list(self):
        cara = self.env['res.users'].create({'name': 'Cara Day', 'login': 'td_cara',
                                             'group_ids': [(4, self.env.ref('mrp.group_mrp_user').id)]})
        wo = self.job()
        self.stamp(wo, cara, self.at(9, 0), self.at(10, 0))
        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60)
        self.assertEqual(data['people'], 'roster', "the technicians are the default")
        self.assertNotIn(cara.id, [r['id'] for r in data['rows']], "on no station's list: not a lane")
        self.assertIn(cara.id, [h['id'] for h in data['hidden']], "but kept aside and named")
        self.assertTrue(any(i.get('people') == 'everyone' and 'Cara Day' in i['text'] for i in data['insights']),
                        "an insight names them and switches the screen to Everyone")
        everyone = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60, people='everyone')
        self.assertEqual(self.row(everyone, cara)['busy'], 60)
        self.assertFalse(self.row(everyone, cara)['rostered'])
        self.assertEqual(everyone['hidden'], [])

    def test_a_rostered_technician_with_nothing_is_a_name_not_a_lane(self):
        wo = self.job()
        self.stamp(wo, self.asha, self.at(9, 0), self.at(10, 0))
        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60)
        self.assertNotIn(self.ben.id, [r['id'] for r in data['rows']])
        absent = next(a for a in data['absent'] if a['id'] == self.ben.id)
        self.assertEqual(absent['stations'], [self.other.id])
        self.assertGreaterEqual(data['kpis']['absent'], 1)

    # ------------------------------------------------------ idle, and whose it is
    def test_idle_is_counted_outside_lunch_and_kept_as_periods(self):
        """Asha is in 09:00-17:00 on an 08-12 / 13-17 roster: the lunch is not idle,
        a four-minute gap is not a period, the late start is measured."""
        emp = self._employee(self.asha, self._calendar())
        self._attend(emp, self.at(9, 0), self.at(17, 0))
        for (h1, m1), (h2, m2) in (((9, 0), (10, 0)), ((11, 0), (12, 0)), ((13, 30), (14, 0)), ((14, 4), (16, 0))):
            self.stamp(self.job(), self.asha, self.at(h1, m1), self.at(h2, m2))
        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60)
        r = self.row(data, self.asha)
        self.assertEqual([(p['a'], p['b']) for p in r['idle_periods']], [(600, 660), (780, 810), (960, 1020)],
                         "10-11, 13:00-13:30 (lunch 12-13 left out), 16-17; the four minutes at 14:00 are not a period")
        self.assertEqual((r['idle'], r['idle_wait'], r['idle_free'], r['break_min']), (154, 0, 154, 60))
        self.assertEqual((r['shift']['a'], r['shift']['b'], r['shift']['breaks']), (480, 1020, [[720, 780]]))
        self.assertEqual((r['late'], r['early']), (60, 0), "in at nine on an eight o'clock roster")
        self.assertEqual(r['longest_gap'], {'m': 600, 'len': 60})
        self.assertEqual((r['first_present'], r['last_present']), (540, 1020))
        self.assertGreaterEqual(data['kpis']['late'], 1)
        self.assertTrue(any('started' in i['text'] and 'Asha Day' in i['text'] for i in data['insights']))
        self.assertEqual(data['kpis']['idle_h'], round(sum(x['idle'] for x in data['rows']) / 60.0, 1))

    def test_no_roster_hours_means_no_shift_and_no_late(self):
        # Odoo 20: an employee always has a calendar; one that says only how LONG the
        # day is ("8 hours", the default week) says nothing about when it starts
        dow = str(self.day.weekday())
        hours_only = self.env['resource.calendar'].sudo().create({
            'name': 'TD eight hours', 'attendance_ids': [(0, 0, {'dayofweek': dow, 'duration_hours': 8, 'hour_from': 0, 'hour_to': 0})]})
        emp = self._employee(self.asha, hours_only)
        self._attend(emp, self.at(9, 0), self.at(17, 0))
        self.stamp(self.job(), self.asha, self.at(9, 0), self.at(10, 0))
        r = self.row(self.Day.get_day(day=fields.Date.to_string(self.day), slot=60), self.asha)
        self.assertIsNone(r['shift'])
        self.assertEqual((r['late'], r['early'], r['break_min']), (0, 0, 0))
        self.assertEqual(r['idle'], 7 * 60, "present eight hours, one on a job, no lunch to take out")

    def test_whose_idle_it_is_a_job_in_hand_or_a_queue_nobody_gave_out(self):
        """The second job arrives at 10:00, is given to Asha at 10:30 and picked up at
        11:00: the first half hour is the hand-out's idle, the second is hers."""
        emp = self._employee(self.asha, self._calendar(lunch=False))
        self._attend(emp, self.at(9, 0), self.at(12, 0))
        self.stamp(self.job(), self.asha, self.at(9, 0), self.at(10, 0))
        second = self.job()
        second.write({'bench_user_id': self.asha.id, 'bench_assigned_at': self.at(10, 30),
                      'accepted_by_id': self.asha.id, 'accepted_at': self.at(11, 0),
                      'handed_over_by_id': self.asha.id, 'handed_over_at': self.at(12, 0)})
        self._created(second, self.at(10, 0))
        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60)
        r = self.row(data, self.asha)
        self.assertEqual((r['idle'], r['idle_wait'], r['idle_own'], r['idle_nobody']), (60, 60, 30, 30))
        q = data['queue'][self.bench.id]
        self.assertEqual([(j['wo'], j['a'], j['b'], j['given_to'], j['given_at']) for j in q],
                         [(second.id, 600, 660, self.asha.id, 630)])
        self.assertEqual((data['handout']['n'], data['handout']['median']), (1, 30))
        self.assertEqual([(l['wo'], l['user_id'], l['m']) for l in data['handout']['late']], [(second.id, self.asha.id, 630)],
                         "given out only after she had stood idle a quarter of an hour")
        texts = ' | '.join(i['text'] for i in data['insights'])
        self.assertIn('with a job already given to them', texts)
        self.assertIn('handed out only after', texts)

    def test_a_normal_pick_up_while_an_urgent_case_waited_is_passed_over(self):
        rush, plain = self.job(), self.job()
        self._sold(rush, 'Rush Patient').priority = 'urgent'
        self._sold(plain, 'Plain Patient')
        for wo in (rush, plain):
            self._created(wo, self.at(9, 0))
        self.stamp(plain, self.asha, self.at(9, 30), self.at(10, 0))
        self.stamp(rush, self.asha, self.at(10, 0), self.at(10, 30))
        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60)
        self.assertEqual(data['kpis']['passed_over'], 1)
        p = data['passed_over'][0]
        self.assertEqual((p['wo'], p['user_id'], p['station_id'], p['m']), (plain.id, self.asha.id, self.bench.id, 570))
        self.assertEqual([(sk['wo'], sk['priority'], sk['waited']) for sk in p['skipped']], [(rush.id, 'urgent', 30)])
        self.assertTrue(any('passed over' in i['text'] for i in data['insights']))

    def test_a_rush_case_given_to_somebody_else_is_not_passed_over(self):
        rush, plain = self.job(), self.job()
        order = self._sold(rush, 'Rush Patient')
        # 'emergency' is lab_order_control's level above urgent; without it, urgent
        rush_levels = dict(order._fields['priority']._description_selection(self.env))
        order.priority = 'emergency' if 'emergency' in rush_levels else 'urgent'
        for wo in (rush, plain):
            self._created(wo, self.at(9, 0))
        rush.write({'bench_user_id': self.ben.id, 'bench_assigned_at': self.at(9, 5)})
        self.stamp(plain, self.asha, self.at(9, 30), self.at(10, 0))
        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60)
        self.assertEqual(data['passed_over'], [], "Ben's case was not Asha's to take")

    def test_the_station_day_balance(self):
        a, b = self.job(), self.job()
        for wo in (a, b):
            self._created(wo, self.at(9, 0))
        self.stamp(a, self.asha, self.at(9, 0), self.at(10, 0))
        self.stamp(b, self.asha, self.at(10, 0), self.at(11, 0))
        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60)
        sd = next(x for x in data['station_day'] if x['station_id'] == self.bench.id)
        self.assertEqual((sd['arrived'], sd['picked'], sd['handed']), (2, 2, 2))
        self.assertEqual((sd['queue_start'], sd['queue_end']), (0, 0))
        self.assertEqual(sd['longest_wait'], 60, "the second job waited an hour for the first to finish")
        self.assertEqual((sd['rostered'], sd['present']), (1, 1))
        self.assertEqual(sd['bench_min'], 120)
        self.assertEqual(sd['uncovered'], 60, "it waited with the only technician on the list busy")

    def test_hand_overs_scanned_in_a_batch_are_flagged_not_scored(self):
        for i in range(4):
            self.stamp(self.job(), self.asha, self.at(9, i), self.at(17, 55 + min(i, 2)))
        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60)
        r = self.row(data, self.asha)
        self.assertEqual([(f['kind'], f['n'], f['m']) for f in r['flags']], [('burst', 4, 17 * 60 + 55)])
        self.assertEqual(data['kpis']['bursts'], 1)
        self.assertTrue(any('batch' in i['text'] for i in data['insights']))
        self.bench.scan_confirm = False      # a bench that scans a tray in bulk is exempt
        r = self.row(self.Day.get_day(day=fields.Date.to_string(self.day), slot=60), self.asha)
        self.assertEqual(r['flags'], [])

    def _clock(self, hh, mm, day=None):
        """The lab's clock stopped at hh:mm on `day` (the test's day by default): the
        live panel is tested at any hour, never skipped near midnight."""
        return patch.object(fields.Datetime, 'now', return_value=self.at(hh, mm, day))

    def _day_and_panel(self, day=None):
        """get_day, and the right-now figures behind its insight - no longer sent to
        the screen, so read on the way past."""
        Model = type(self.Day)
        orig, seen = Model._now_panel, {}

        def spy(model, *args, **kwargs):
            seen['panel'] = orig(model, *args, **kwargs)
            return seen['panel']
        with patch.object(Model, '_now_panel', spy):
            data = self.Day.get_day(day=fields.Date.to_string(day or self.day), slot=60)
        return data, seen.get('panel')

    def test_today_has_a_right_now_panel_with_free_hands_and_the_queue(self):
        """15:00 on the lab's clock: Asha, checked in, handed her job on at 13:00 and
        stands free; a job has waited at her station since 14:10."""
        emp = self._employee(self.asha, self._calendar(lunch=False, hours=((8, 12), (12, 18))))
        self._attend(emp, self.at(12, 0))          # still checked in
        self.stamp(self.job(), self.asha, self.at(12, 0), self.at(13, 0))
        waiting = self.job()
        self._created(waiting, self.at(14, 10))
        with self._clock(15, 0):
            data, panel = self._day_and_panel()
        self.assertTrue(data['is_today'])
        self.assertEqual(data['now'], 15 * 60)
        self.assertNotIn('now_panel', data, "the panel is off the screen (client, 2026-10-14)")
        r = self.row(data, self.asha)
        self.assertEqual((r['now_state'], r['idle_since']), ('idle', 13 * 60))
        free = next(f for f in panel['free'] if f['id'] == self.asha.id)
        self.assertEqual((free['since'], free['stations']), (13 * 60, [self.bench.id]), "work waits at her station")
        q = next(q for q in panel['queues'] if q['station_id'] == self.bench.id)
        self.assertEqual([j['wo'] for j in q['jobs']], [waiting.id])
        self.assertEqual((q['free'], q['named'], q['in'], q['on_job']), ([self.asha.id], 1, 1, 0))
        self.assertEqual((q['oldest_label'], q['oldest_wait'], q['jobs'][0]['wait_min']), ('14:10', 50, 50))
        self.assertTrue(any(i['text'].startswith('Right now') and '14:10' in i['text'] for i in data['insights']))
        with self._clock(15, 0):
            _earlier, earlier_panel = self._day_and_panel(self.day - timedelta(days=1))
        self.assertIsNone(earlier_panel, "only today has a now")

    # ------------------------------------------------------ the review of 2026-10-14
    def test_without_a_check_in_a_technician_is_free_from_their_last_job_until_now(self):
        """0 of 49 technicians check in on this floor. Their presence is their own scans -
        and today on to now while the shift runs, or nobody could ever show as free."""
        self._employee(self.asha, self._calendar(lunch=False, hours=((8, 12), (12, 18))))
        self.stamp(self.job(), self.asha, self.at(12, 0), self.at(13, 0))
        waiting = self.job()
        self._created(waiting, self.at(14, 10))
        with self._clock(15, 0):
            data, panel = self._day_and_panel()
        r = self.row(data, self.asha)
        self.assertFalse(r['has_attendance'])
        self.assertEqual((r['now_state'], r['idle_since'], r['last_present']), ('idle', 13 * 60, 15 * 60))
        self.assertEqual(r['late'], 0, "a first scan at noon is not a late start: nobody knows when she came in")
        q = next(q for q in panel['queues'] if q['station_id'] == self.bench.id)
        self.assertEqual(q['free'], [self.asha.id])
        with self._clock(19, 0):            # the shift is over: first scan to last again
            after = self.row(self.Day.get_day(day=fields.Date.to_string(self.day), slot=60), self.asha)
        self.assertEqual((after['last_present'], after['now_state']), (13 * 60 + 1, 'off'))

    def test_a_job_given_to_somebody_who_never_came_in_does_not_make_them_present(self):
        wo = self.job(self.other)
        wo.write({'bench_user_id': self.ben.id, 'bench_assigned_at': self.at(10, 0)})
        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60)
        self.assertNotIn(self.ben.id, [r['id'] for r in data['rows']], "the hand-out is the lead's stamp")
        self.assertIn(self.ben.id, [a['id'] for a in data['absent']])

    def test_check_in_only_office_staff_are_neither_lanes_nor_named_as_workers(self):
        clerk = self.env['res.users'].create({'name': 'Office Day', 'login': 'td_office',
                                              'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        self._attend(self._employee(clerk), self.at(9, 0), self.at(17, 0))
        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60)
        self.assertNotIn(clerk.id, [r['id'] for r in data['rows']])
        self.assertNotIn(clerk.id, [h['id'] for h in data['hidden']], "a check-in is not floor work")
        self.assertFalse([i for i in data['insights'] if i.get('people') and 'Office Day' in i['text']])
        everyone = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60, people='everyone')
        self.assertEqual(self.row(everyone, clerk)['present'], 8 * 60, "Everyone still shows the whole house")

    def test_a_station_filter_does_not_call_somebody_who_worked_elsewhere_not_in(self):
        self.stamp(self.job(self.other), self.asha, self.at(9, 0), self.at(10, 0))   # Ceramics' Asha, at Casting
        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60, station_ids=[self.bench.id])
        self.assertNotIn(self.asha.id, [r['id'] for r in data['rows']])
        self.assertNotIn(self.asha.id, [a['id'] for a in data['absent']], "she was in - at another station")

    def test_a_station_filter_keeps_a_checked_in_technician_of_that_station_with_nothing_yet(self):
        self._attend(self._employee(self.asha), self.at(9, 0), self.at(11, 0))
        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60, station_ids=[self.bench.id])
        r = self.row(data, self.asha)
        self.assertEqual((r['present'], r['busy'], r['idle']), (120, 0, 120))
        self.assertNotIn(self.asha.id, [a['id'] for a in data['absent']])

    def test_a_check_in_left_open_from_an_earlier_day_is_not_a_day_at_work(self):
        self._attend(self._employee(self.asha), self.at(8, 0, self.day - timedelta(days=1)))   # never checked out
        self.stamp(self.job(), self.asha, self.at(10, 0), self.at(11, 0))
        r = self.row(self.Day.get_day(day=fields.Date.to_string(self.day), slot=60), self.asha)
        self.assertFalse(r['has_attendance'])
        self.assertEqual(r['first_present'], 10 * 60, "present from her first scan, not since midnight")

    def test_the_lower_arch_does_not_wait_for_the_upper_arch(self):
        """An Upper & Lower case is two chains in one order, the lower at sequence + 100:
        the lower's first step arrives with the order, not when the upper is done."""
        self.stamp(self.job(), self.asha, self.at(8, 30), self.at(8, 40))     # the bench uses the board
        bom = self.env['mrp.bom'].create({
            'product_tmpl_id': self.product.product_tmpl_id.id, 'product_qty': 1.0,
            'operation_ids': [(0, 0, {'name': 'Upper build', 'workcenter_id': self.bench.id, 'time_cycle_manual': 30.0}),
                              (0, 0, {'name': 'Lower build', 'workcenter_id': self.bench.id, 'time_cycle_manual': 30.0})]})
        mo = self.env['mrp.production'].create({'product_id': self.product.id, 'bom_id': bom.id, 'product_qty': 1.0})
        mo.action_confirm()
        upper, lower = mo.workorder_ids.sorted('id')
        upper.write({'arch': 'upper', 'sequence': 1})
        lower.write({'arch': 'lower', 'sequence': 101})
        for wo in (upper, lower):
            self._created(wo, self.at(9, 0))
        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60)
        q = {j['wo']: j for j in data['queue'][self.bench.id]}
        self.assertEqual((q[upper.id]['a'], q[lower.id]['a']), (9 * 60, 9 * 60), "both arrived with the order")

    def test_the_station_day_counts_every_job_once_whoever_did_it(self):
        cara = self.env['res.users'].create({'name': 'Cara Day', 'login': 'td_cara',
                                             'group_ids': [(4, self.env.ref('mrp.group_mrp_user').id)]})
        self.stamp(self.job(), cara, self.at(9, 0), self.at(10, 0))           # on no station's list
        timed = self.job()
        self.stamp(timed, self.asha, self.at(11, 0), self.at(12, 30))
        loss = self.env['mrp.workcenter.productivity.loss'].search([('loss_type', '=', 'productive')], limit=1)
        self.env['mrp.workcenter.productivity'].create({
            'workorder_id': timed.id, 'workcenter_id': self.bench.id, 'user_id': self.asha.id, 'loss_id': loss.id,
            'date_start': self.at(11, 20), 'date_end': self.at(11, 45)})
        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60)    # roster mode: Cara is no lane
        self.assertNotIn(cara.id, [r['id'] for r in data['rows']])
        sd = next(x for x in data['station_day'] if x['station_id'] == self.bench.id)
        self.assertEqual((sd['picked'], sd['handed']), (2, 2), "Cara's job is the station's all the same")
        self.assertEqual(sd['bench_min'], 60 + 90, "a timer inside a bench span is not counted twice")

    def test_a_rush_case_given_out_after_the_pick_up_was_still_passed_over(self):
        rush, plain = self.job(), self.job()
        self._sold(rush, 'Rush Patient').priority = 'urgent'
        for wo in (rush, plain):
            self._created(wo, self.at(9, 0))
        self.stamp(plain, self.asha, self.at(9, 30), self.at(10, 0))
        rush.write({'bench_user_id': self.ben.id, 'bench_assigned_at': self.at(9, 45)})
        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60)
        self.assertEqual(data['kpis']['passed_over'], 1, "at 09:30 it was nobody's yet")

    def test_work_waiting_at_a_station_with_nobody_on_its_list_says_so(self):
        polish = self.env['mrp.workcenter'].create({'name': 'TD Polish'})
        self.stamp(self.job(polish), self.asha, self.at(8, 0), self.at(8, 10))      # the station uses the board
        late = self.job(polish)
        self._created(late, self.at(9, 0))
        self.stamp(late, self.asha, self.at(11, 0), self.at(11, 30))
        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60)
        text = next(i['text'] for i in data['insights'] if 'TD Polish' in i['text'])
        self.assertIn('nobody on its technician list', text)
        self.assertNotIn('everyone on its list busy', text)

    def test_the_shift_is_read_in_the_employees_timezone(self):
        # Odoo 20: the calendar has no timezone; its hours are the employee's wall clock
        self._employee(self.asha, self._calendar(lunch=False), tz='UTC')
        self.stamp(self.job(), self.asha, self.at(9, 0), self.at(10, 0))
        r = self.row(self.Day.get_day(day=fields.Date.to_string(self.day), slot=60), self.asha)
        offset = int(self.tz.utcoffset(datetime.combine(self.day, time(12))).total_seconds() // 60)
        self.assertEqual(r['shift']['a'], 8 * 60 + offset, "08:00 UTC on the lab's clock")

    # ------------------------------------------------------------------ the work order's own times
    # (client, 2026-10-14: "the gantt chart should be based on the time in work order")
    def ran(self, wo, user, started, finished=None, state=None, due=None):
        """The work order as Odoo leaves it: started (in progress) and finished (done),
        its Start Date and End Date set - written straight to the table, so no
        replanning runs underneath."""
        state = state or ('done' if finished else 'progress')
        wo.write({'bench_user_id': user.id})
        self.env.flush_all()
        self.env.cr.execute("UPDATE mrp_workorder SET state = %s, date_start = %s, date_finished = %s WHERE id = %s",
                            (state, started, finished or due, wo.id))
        wo.invalidate_recordset()

    def clock(self, wo, user, a, b):
        loss = self.env['mrp.workcenter.productivity.loss'].search([('loss_type', '=', 'productive')], limit=1)
        return self.env['mrp.workcenter.productivity'].create({
            'workorder_id': wo.id, 'workcenter_id': wo.workcenter_id.id, 'user_id': user.id, 'loss_id': loss.id,
            'date_start': a, 'date_end': b})

    def test_the_bar_is_the_work_orders_start_and_end_not_the_scans(self):
        wo = self.job()
        # scanned 09:30 -> 10:20, but the work order says 09:10 -> 10:40 (corrected on its form)
        self.stamp(wo, self.asha, self.at(9, 30), self.at(10, 20))
        self.ran(wo, self.asha, self.at(9, 10), self.at(10, 40))
        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60)
        r = self.row(data, self.asha)
        seg = next(s for s in r['segments'] if s['wo'] == wo.id)
        self.assertEqual((seg['a'], seg['b'], seg['kind']), (9 * 60 + 10, 10 * 60 + 40, 'bench'))
        self.assertEqual((seg['started'], seg['finished']), ('09:10', '10:40'))
        self.assertEqual(r['busy'], 90)
        self.assertEqual(self.cell(r, 10)['handed'], 1, "finished at 10:40 by the work order")
        self.assertEqual(r['first'], 9 * 60 + 10)

    def test_a_job_started_and_finished_in_odoo_is_on_the_chart(self):
        wo = self.job()
        self.ran(wo, self.asha, self.at(11, 0), self.at(11, 40))     # never scanned on the board
        self.clock(wo, self.asha, self.at(11, 0), self.at(11, 30))
        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60)
        r = self.row(data, self.asha)
        kinds = {(s['kind'], s['a'], s['b']) for s in r['segments'] if s['wo'] == wo.id}
        self.assertEqual(kinds, {('bench', 11 * 60, 11 * 60 + 40), ('timer', 11 * 60, 11 * 60 + 30)})
        self.assertEqual(r['busy'], 30, "hands-on is the time tracked")
        self.assertEqual(r['handed'], 1)
        strip = next(x for x in data['strip'] if x['date'] == fields.Date.to_string(self.day))
        self.assertGreaterEqual(strip['jobs'], 1, "the fortnight counts it too")

    def test_a_work_order_never_started_is_not_a_job(self):
        wo = self.job()
        # closed with its order: a planned start three weeks back, the end today - nobody worked it
        self.ran(wo, self.asha, self.at(9, 0, self.day - timedelta(days=21)), self.at(10, 0))
        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60)
        segs = [s for r in data['rows'] for s in r['segments'] if s['wo'] == wo.id]
        self.assertFalse(segs)
        self.assertEqual(data['kpis']['handed'], 0)

    def test_the_leads_clock_is_the_technicians(self):
        lead = self.env['res.users'].create({'name': 'Lena Lead', 'login': 'td_lena',
                                             'group_ids': [(4, self.env.ref('mrp.group_mrp_user').id)]})
        wo = self.job()
        self.stamp(wo, self.asha, self.at(9, 0), self.at(10, 0))
        self.ran(wo, self.asha, self.at(9, 0), self.at(10, 0))
        self.clock(wo, lead, self.at(9, 20), self.at(9, 50))         # the lead scanned for the room
        data = self.Day.get_day(day=fields.Date.to_string(self.day), slot=60)
        r = self.row(data, self.asha)
        run = next(s for s in r['segments'] if s['wo'] == wo.id and s['kind'] == 'timer')
        self.assertEqual((run['a'], run['b'], run['by']), (9 * 60 + 20, 9 * 60 + 50, lead.id))
        self.assertEqual(r['busy'], 30)
        self.assertNotIn(lead.id, [x['id'] for x in data['rows']], "the lead is not busy on Asha's job")
        self.assertEqual(data['names'][lead.id], 'Lena Lead', "named in the job's drawer")

    def test_a_running_job_is_due_at_its_end_date(self):
        wo = self.job()
        self.stamp(wo, self.asha, self.at(9, 0))
        self.ran(wo, self.asha, self.at(9, 0), due=self.at(9, 30))
        r = self.row(self.Day.get_day(day=fields.Date.to_string(self.day), slot=60), self.asha)
        seg = next(s for s in r['segments'] if s['wo'] == wo.id)
        self.assertTrue(seg['open'])
        self.assertEqual((seg['due'], seg['due_label']), (9 * 60 + 30, '09:30'))
        self.assertIsNone(seg['finished'], "a running job's End Date is a forecast, not an end")

    def test_a_job_from_yesterday_finished_today_is_carried_not_worked(self):
        wo = self.job()
        yesterday = self.day - timedelta(days=1)
        self.stamp(wo, self.asha, self.at(16, 0, yesterday), self.at(10, 0))
        self.ran(wo, self.asha, self.at(16, 0, yesterday), self.at(10, 0))
        self.clock(wo, self.asha, self.at(16, 0, yesterday), self.at(10, 0))   # the clock ran all night
        r = self.row(self.Day.get_day(day=fields.Date.to_string(self.day), slot=60), self.asha)
        self.assertEqual(r['busy'], 0, "not ten hours of work since midnight")
        self.assertEqual(r['carried'], 1)
        self.assertEqual(r['handed'], 1, "its finish today still counts")
        self.assertTrue(all(s['kind'] == 'carried' for s in r['segments'] if s['wo'] == wo.id))
