# -*- coding: utf-8 -*-
"""Correct, on a job this bench has already passed on: who did it, and nothing else.

(client, 2026-10-15: "able to correct finished work orders by scanning, by clicking
Correct. Only Technician or Finishing Technician can be edited")
"""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCorrectFinished(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups = [(6, 0, [cls.env.ref('base.group_user').id, cls.env.ref('mrp.group_mrp_user').id])]
        cls.head = cls.env['res.users'].create({'name': 'Fix Head', 'login': 'fix_head', 'group_ids': groups})
        cls.asha = cls.env['res.users'].create({'name': 'Fix Asha', 'login': 'fix_asha', 'group_ids': groups})
        cls.ben = cls.env['res.users'].create({'name': 'Fix Ben', 'login': 'fix_ben', 'group_ids': groups})
        cls.outsider = cls.env['res.users'].create({'name': 'Fix Out', 'login': 'fix_out', 'group_ids': groups})
        cls.polish = cls.env['mrp.workcenter'].create({
            'name': 'Fix Polish', 'is_finisher': True, 'head_user_ids': [(6, 0, cls.head.ids)],
            'users': [(6, 0, (cls.asha | cls.ben).ids)]})
        cls.pack = cls.env['mrp.workcenter'].create({'name': 'Fix Pack'})
        product = cls.env['product.product'].create({'name': 'Fix Retainer', 'type': 'consu', 'is_storable': True})
        cls.mo = cls.env['mrp.production'].create({'product_id': product.id, 'product_qty': 1.0})
        cls.wo = cls.env['mrp.workorder'].create({
            'name': 'Polishing', 'sequence': 10, 'production_id': cls.mo.id,
            'workcenter_id': cls.polish.id})
        cls.env['mrp.workorder'].create({
            'name': 'Packing', 'sequence': 20, 'production_id': cls.mo.id,
            'workcenter_id': cls.pack.id})
        # done at the bench the way the board does it: taken, named, finished, handed on
        wo = cls.wo.with_user(cls.head)
        wo.action_station_accept()
        wo.action_assign_bench_user(cls.asha.id)
        wo.action_assign_finisher(cls.asha.id)
        wo.action_station_handover()
        cls.Station = cls.env['lab.station'].with_user(cls.head)

    def test_correct_finds_the_job_this_bench_passed_on(self):
        self.assertEqual(self.wo.state, 'done')
        found = self.Station.scan_to_change(self.mo.name, self.polish.id)
        self.assertEqual(found['job']['id'], self.wo.id, "the finished step here, not the open one at Packing")
        self.assertTrue(found['finished'])
        self.assertEqual((found['bench_user_id'], found['finisher_user_id']), (self.asha.id, self.asha.id))

    def test_only_the_names_change(self):
        handed = self.wo.handed_over_at
        result = self.Station.apply_change(self.wo.id, bench_user_id=self.ben.id, finisher_user_id=self.ben.id)
        self.assertIn('technician', result['message'])
        self.assertEqual((self.wo.bench_user_id, self.wo.finisher_user_id), (self.ben, self.ben))
        self.assertEqual((self.wo.state, self.wo.handed_over_at), ('done', handed), "still passed on, when it was")
        self.assertIn('Fix Asha', self.mo.message_ids[0].body, "the order's chatter says who it was before")

    def test_where_a_finished_job_stands_is_not_corrected(self):
        with self.assertRaises(UserError):
            self.Station.apply_change(self.wo.id, status='bench')
        self.assertEqual(self.wo.state, 'done')

    def test_the_technician_is_the_leads_call_the_finisher_anyones_at_the_bench(self):
        as_asha = self.env['lab.station'].with_user(self.asha)
        with self.assertRaises(UserError):
            as_asha.apply_change(self.wo.id, bench_user_id=self.ben.id)
        as_asha.apply_change(self.wo.id, finisher_user_id=self.ben.id)
        self.assertEqual(self.wo.finisher_user_id, self.ben)

    def test_a_finished_job_keeps_its_names(self):
        with self.assertRaises(UserError, msg="no technician"):
            self.Station.apply_change(self.wo.id, bench_user_id=False)
        with self.assertRaises(UserError, msg="no finisher, at a bench that records one"):
            self.Station.apply_change(self.wo.id, finisher_user_id=False)
        with self.assertRaises(UserError, msg="somebody who does not work here"):
            self.Station.apply_change(self.wo.id, bench_user_id=self.outsider.id)
        self.assertEqual(self.wo.bench_user_id, self.asha)

    def test_the_days_targets_are_counted_again(self):
        day = self.env['lab.station']._lab_time(self.wo.handed_over_at).date()
        Target = self.env['lab.work.target']
        asha = Target.create({'date': day, 'user_id': self.asha.id, 'workcenter_id': self.polish.id, 'target': 5})
        ben = Target.create({'date': day, 'user_id': self.ben.id, 'workcenter_id': self.polish.id, 'target': 5})
        (asha | ben).action_refresh()
        self.assertEqual((asha.done_count, ben.done_count), (1, 0))
        self.Station.apply_change(self.wo.id, bench_user_id=self.ben.id, finisher_user_id=self.ben.id)
        self.assertEqual((asha.done_count, ben.done_count), (0, 1), "the job counts for who did it")
