# -*- coding: utf-8 -*-
"""An amendment marked, read and cleared from the station board.

(client, 2026-11-26: "How can I set the amend through station board")
The flag itself is lab_order_control's - it stops the order being invoiced. These
tests are about the board's way in: a scan finds the case, the card wears the flag,
and the bench can say the amendment is done.
"""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestStationAmendment(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups = [(6, 0, [cls.env.ref('base.group_user').id, cls.env.ref('mrp.group_mrp_user').id])]

        def user(name, login):
            return cls.env['res.users'].create({'name': name, 'login': login, 'group_ids': groups})

        cls.tech = user('Amend Tech', 'amend_tech')
        cls.outsider = user('Amend Out', 'amend_out')
        cls.bench = cls.env['mrp.workcenter'].create({
            'name': 'Amend Ceramic', 'scan_confirm': False,
            'users': [(6, 0, cls.tech.ids)]})
        cls.pack = cls.env['mrp.workcenter'].create({'name': 'Amend Pack'})
        cls.product = cls.env['product.product'].create(
            {'name': 'Amend Crown', 'type': 'consu', 'is_storable': True})
        cls.clinic = cls.env['res.partner'].create({'name': 'Amend Clinic'})
        cls.Station = cls.env['lab.station'].with_user(cls.tech)

    def setUp(self):
        super().setUp()
        if 'has_amendment' not in self.env['sale.order']._fields:
            self.skipTest("lab_order_control is not installed: no amendments to mark")

    def _order(self):
        return self.env['sale.order'].create({
            'partner_id': self.clinic.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 1})]})

    def _job(self, order, at=None):
        mo = self.env['mrp.production'].create({'product_id': self.product.id, 'product_qty': 1.0})
        # the lab's mrp.production.sale_id is a stored related on the sale line
        mo.sale_line_id = order.order_line[:1]
        wo = self.env['mrp.workorder'].create({
            'name': 'Build up', 'sequence': 10, 'production_id': mo.id,
            'workcenter_id': (at or self.bench).id})
        return mo, wo

    def _card(self, board, wo):
        return next(c for c in board['incoming'] + board['working'] if c['id'] == wo.id)

    def test_a_scan_marks_the_order_and_the_card_wears_it(self):
        order = self._order()
        mo, wo = self._job(order)
        found = self.Station.scan_to_amend(mo.name, self.bench.id)
        self.assertEqual((found['order_id'], found['has_amendment']), (order.id, False))
        self.assertEqual(found['production'], mo.name)
        result = self.Station.mark_amendment(order.id, 'add 16', self.bench.id)
        self.assertTrue(order.has_amendment)
        self.assertEqual(order.amendment_note, 'add 16')
        self.assertEqual(order.amendment_user_id, self.tech, "marked in the name of the bench")
        card = self._card(result['board'], wo)
        self.assertTrue(card['amendment'])
        self.assertEqual(card['amendment_note'], 'add 16')
        self.assertTrue(result['board']['amendments'], "the board offers the control")

    def test_the_badge_shows_it_adds_to_it_and_clears_it(self):
        order = self._order()
        mo, wo = self._job(order)
        self.Station.mark_amendment(order.id, 'add 16', self.bench.id)
        found = self.Station.amendment_of_job(wo.id, self.bench.id)
        self.assertTrue(found['has_amendment'])
        self.assertEqual((found['note'], found['marked_by']), ('add 16', 'Amend Tech'))
        self.Station.mark_amendment(order.id, 'shade A3', self.bench.id)
        self.assertEqual(order.amendment_note, 'add 16\nshade A3', "a second one is added, not written over")
        result = self.Station.amendment_done(order.id, self.bench.id)
        self.assertFalse(order.has_amendment)
        self.assertFalse(self._card(result['board'], wo)['amendment'])

    def test_a_case_at_another_bench_or_with_nothing_open_is_found(self):
        order = self._order()
        mo, _wo = self._job(order, at=self.pack)
        self.assertEqual(self.Station.scan_to_amend(mo.name, self.bench.id)['order_id'], order.id,
                         "the call often comes after the job has moved on")
        made = self._order()
        found = self.Station.scan_to_amend(made.name.lower(), self.bench.id)
        self.assertEqual(found['order_id'], made.id, "a case with nothing left open, by its order number")

    def test_only_somebody_posted_here_and_no_patterns(self):
        order = self._order()
        mo, _wo = self._job(order)
        with self.assertRaises(UserError):
            self.env['lab.station'].with_user(self.outsider).scan_to_amend(mo.name, self.bench.id)
        with self.assertRaises(UserError):
            self.env['lab.station'].with_user(self.outsider).mark_amendment(order.id, 'x', self.bench.id)
        with self.assertRaises(UserError, msg="a typed % is not a wildcard"):
            self.Station.scan_to_amend('%', self.bench.id)
        self.assertFalse(order.has_amendment)

    def test_a_cancelled_order_is_refused(self):
        order = self._order()
        mo, _wo = self._job(order)
        order.with_context(disable_cancel_warning=True).action_cancel()
        with self.assertRaises(UserError):
            self.Station.scan_to_amend(order.name, self.bench.id)
