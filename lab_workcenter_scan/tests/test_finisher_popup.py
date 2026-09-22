# -*- coding: utf-8 -*-
"""The second popup, driven through a real browser.

The server side of this was easy to prove with a shell call; the complaint from the
floor was that the POPUP never appeared, which is the half a Python test cannot see.
So this drives the actual board in a browser: the bench presses Hand on, and the
"Who finished this?" dialog must open with the bench's people in it.
(client, 2026-09-09)
"""
from odoo import fields
from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestFinisherPopup(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.lead = cls.env['res.users'].create({
            'name': 'Bench Lead', 'login': 'fin_lead', 'password': 'fin_lead',
            'group_ids': [(4, cls.env.ref('mrp.group_mrp_user').id)],
        })
        cls.hand = cls.env['res.users'].create({
            'name': 'Polishing Hand', 'login': 'fin_hand', 'password': 'fin_hand',
            'group_ids': [(4, cls.env.ref('mrp.group_mrp_user').id)],
        })
        cls.product = cls.env['product.product'].create({
            'name': 'Finisher Appliance', 'is_storable': True})
        cls.component = cls.env['product.product'].create({
            'name': 'Finisher Wire', 'is_storable': True})
        # A bench that records who FINISHED the work, and one that does not, so the
        # job has somewhere to be handed on to.
        cls.bench = cls.env['mrp.workcenter'].create({
            'name': 'Finishing Bench', 'is_finisher': True,
            'users': [(6, 0, (cls.lead | cls.hand).ids)],
            'head_user_ids': [(6, 0, cls.lead.ids)],
        })
        cls.after = cls.env['mrp.workcenter'].create({'name': 'Packing Bench'})
        cls.bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.product.product_tmpl_id.id,
            'product_qty': 1.0,
            'bom_line_ids': [(0, 0, {'product_id': cls.component.id,
                                     'product_qty': 1.0})],
            'operation_ids': [
                (0, 0, {'name': 'Finish it', 'workcenter_id': cls.bench.id}),
                (0, 0, {'name': 'Pack it', 'workcenter_id': cls.after.id}),
            ],
        })

    def _accepted_job(self, with_technician=True):
        mo = self.env['mrp.production'].create({
            'product_id': self.product.id, 'bom_id': self.bom.id, 'product_qty': 1.0})
        mo.action_confirm()
        step = mo.workorder_ids.sorted('sequence')[0]
        self.assertEqual(step.workcenter_id, self.bench)
        step.write({'accepted_by_id': self.lead.id,
                    'accepted_at': fields.Datetime.now()})
        if with_technician:
            step.bench_user_id = self.hand.id
        step.button_start(raise_on_invalid_state=False)
        return mo, step

    def test_the_server_asks_for_a_finisher(self):
        """The board's own call, before any browser is involved."""
        mo, step = self._accepted_job()
        answer = self.env['lab.station'].with_user(self.lead).handover(step.id)
        self.assertEqual(answer.get('action'), 'needs_finisher')
        self.assertEqual(answer.get('stage'), 'finisher')
        self.assertTrue(answer.get('people'), "the bench's people must be offered")
        self.assertTrue(answer.get('can_assign'),
                        "anyone at the bench may name the finisher")

    def test_the_popup_opens_in_a_browser(self):
        """Hand on at a finishing bench must put the question on the screen."""
        mo, step = self._accepted_job()
        self.start_tour('/odoo/action-%s' % self.env.ref(
            'lab_workcenter_scan.action_station_board').id,
            'lab_finisher_popup', login='fin_lead')
        # The tour only proves the dialog appeared; the job must still be waiting for
        # its answer, not handed on behind the popup's back.
        step.invalidate_recordset()
        self.assertFalse(step.finisher_user_id)
        self.assertNotEqual(step.state, 'done')

    def test_naming_the_finisher_hands_the_job_on(self):
        """The one tap the popup exists for."""
        mo, step = self._accepted_job()
        Station = self.env['lab.station'].with_user(self.lead)
        answer = Station.assign_finisher_and_handover(step.id, self.hand.id)
        step.invalidate_recordset()
        self.assertEqual(step.finisher_user_id, self.hand)
        self.assertEqual(step.state, 'done', "naming the finisher hands it on")
        self.assertNotIn('needs', str(answer.get('action') or ''))

    def test_a_bench_with_no_finisher_flag_never_asks(self):
        """Off everywhere by default: an ordinary bench must not grow a second popup."""
        self.bench.is_finisher = False
        mo, step = self._accepted_job()
        answer = self.env['lab.station'].with_user(self.lead).handover(step.id)
        self.assertNotEqual(answer.get('action'), 'needs_finisher')
        step.invalidate_recordset()
        self.assertEqual(step.state, 'done')

    def test_both_questions_come_one_after_the_other(self):
        """Nobody named at all: "who did it" first, then "who finished it"."""
        mo, step = self._accepted_job(with_technician=False)
        Station = self.env['lab.station'].with_user(self.lead)
        first = Station.handover(step.id)
        self.assertEqual(first.get('action'), 'needs_person')
        second = Station.assign_and_handover(step.id, self.hand.id)
        self.assertEqual(second.get('action'), 'needs_finisher',
                         "the second question must follow the first")
        step.invalidate_recordset()
        self.assertNotEqual(step.state, 'done',
                            "the job must not move until both are answered")
