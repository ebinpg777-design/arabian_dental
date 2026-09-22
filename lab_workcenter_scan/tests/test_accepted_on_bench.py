# -*- coding: utf-8 -*-
"""A job somebody has accepted is on their bench, whatever the routing thinks.

The board's Arrived column asks for the case's FIRST unfinished step, and that is
right: a bench must not be offered work whose earlier step is still out. "On the
bench" used to ask the same question, and that was wrong - it is not a prediction
but a record of who is holding what.

Aswathy Sajeevan's chip read 7 while her board showed six passed on and "On the
bench: 0". The seventh was MO/285447, accepted at 15:42 and still in progress:
she had started the Labial Bow while the Adams Clasp before it was still open, so
it was not a first step and no column would show it. Fifty-three jobs across nine
benches were invisible the same way. (client, 2026-09-12)
"""
from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAcceptedOnBench(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        mrp_user = cls.env.ref('mrp.group_mrp_user').id
        cls.tech = cls.env['res.users'].create({
            'name': 'Bench Holder', 'login': 'acc_tech', 'group_ids': [(4, mrp_user)]})
        cls.first_bench = cls.env['mrp.workcenter'].create({'name': 'Acc First'})
        cls.second_bench = cls.env['mrp.workcenter'].create({
            'name': 'Acc Second', 'users': [(6, 0, cls.tech.ids)]})
        cls.product = cls.env['product.product'].create({
            'name': 'Acc Appliance', 'is_storable': True})
        cls.bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.product.product_tmpl_id.id,
            'product_qty': 1.0,
            'operation_ids': [
                (0, 0, {'name': 'Step one', 'workcenter_id': cls.first_bench.id}),
                (0, 0, {'name': 'Step two', 'workcenter_id': cls.second_bench.id}),
            ]})
        cls.Station = cls.env['lab.station']

    def _case_started_out_of_order(self):
        """Step two accepted while step one is still open - Aswathy's case."""
        mo = self.env['mrp.production'].create({
            'product_id': self.product.id, 'bom_id': self.bom.id, 'product_qty': 1.0})
        mo.action_confirm()
        steps = mo.workorder_ids.sorted('sequence')
        first, second = steps[0], steps[1]
        second.write({'bench_user_id': self.tech.id,
                      'accepted_by_id': self.tech.id,
                      'accepted_at': fields.Datetime.now()})
        self.assertNotIn(first.state, ('done', 'cancel'),
                         "the earlier step must still be open for this to be the case")
        return mo, first, second

    def test_an_accepted_second_step_is_on_the_bench(self):
        mo, first, second = self._case_started_out_of_order()
        board = self.Station.get_station(self.second_bench.id)
        on_bench = [c['id'] for c in board['working']]
        self.assertIn(second.id, on_bench,
                      "a job this person accepted must show on their bench even "
                      "though the step before it is still open")
        self.assertEqual(board['totals']['working'], len(on_bench) or
                         board['totals']['working'])

    def test_it_shows_under_that_person_s_filter(self):
        """The complaint was that filtering by the person showed nothing."""
        mo, first, second = self._case_started_out_of_order()
        board = self.Station.get_station(self.second_bench.id, person_id=self.tech.id)
        self.assertIn(second.id, [c['id'] for c in board['working']])

    def test_arrived_still_refuses_a_job_whose_earlier_step_is_open(self):
        """The queue stays a prediction: only the accepted column changed."""
        mo = self.env['mrp.production'].create({
            'product_id': self.product.id, 'bom_id': self.bom.id, 'product_qty': 1.0})
        mo.action_confirm()
        second = mo.workorder_ids.sorted('sequence')[1]
        board = self.Station.get_station(self.second_bench.id)
        self.assertNotIn(second.id, [c['id'] for c in board['incoming']],
                         "an unaccepted second step must not be offered while the "
                         "first step is still out")

    def test_a_finished_job_leaves_the_bench(self):
        mo, first, second = self._case_started_out_of_order()
        second.write({'state': 'done'})
        board = self.Station.get_station(self.second_bench.id)
        self.assertNotIn(second.id, [c['id'] for c in board['working']])

    def test_the_chip_and_the_column_now_agree(self):
        """The symptom: a tally that counted work no column would show."""
        mo, first, second = self._case_started_out_of_order()
        second.bench_assigned_at = fields.Datetime.now()
        board = self.Station.get_station(self.second_bench.id)
        chip = next(p for p in board['bench_people'] if p['id'] == self.tech.id)
        on_bench = [c['id'] for c in board['working']]
        self.assertEqual(chip['day_count'], 1)
        self.assertIn(second.id, on_bench,
                      "the job the chip counted must be visible on the board")
