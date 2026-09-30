# -*- coding: utf-8 -*-
"""A technician's day, slot by slot, from the board's stamps.

One day, built by hand in the lab's own time: Asha picks a crown up at 09:10 and
hands it on at 10:40, starts a second at 11:00 that a timer says took 25 minutes,
and stands between 13:00 and 14:00 while a job waits at her station. The grid
must say exactly that - and must not count yesterday's forgotten case as work.
"""
from datetime import datetime, time, timedelta

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
        cls.bench = cls.env['mrp.workcenter'].create({'name': 'TD Ceramics', 'users': [(6, 0, cls.asha.ids)]})
        cls.other = cls.env['mrp.workcenter'].create({'name': 'TD Casting'})
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
