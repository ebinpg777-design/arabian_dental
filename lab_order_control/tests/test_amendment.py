# -*- coding: utf-8 -*-
"""An amendment waiting on a case: marked by the floor, in the way of the invoice
until it is done.

(client, 2026-11-26: "a feature for manufacturing user to mark a SO has an amendment.
The SO with this check is only able to invoice after doing the amendment and changing
has-amendment to false. Show a badge to identify them easily.")
"""
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAmendment(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        G = cls.env.ref
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Amendment Clinic', 'is_company': True})
        cls.product = cls.env['product.product'].create({
            'name': 'Amendment Appliance', 'type': 'consu', 'list_price': 1000.0,
            'invoice_policy': 'order', 'taxes_id': [(5, 0, 0)]})
        # the floor: manufacturing rights and nothing on sales orders
        cls.floor = cls.env['res.users'].create({
            'name': 'Amend Floor', 'login': 'amend_floor',
            'group_ids': [(6, 0, [G('base.group_user').id, G('mrp.group_mrp_user').id])]})
        cls.office = cls.env['res.users'].create({
            'name': 'Amend Office', 'login': 'amend_office',
            'group_ids': [(6, 0, [G('base.group_user').id,
                                  G('sales_team.group_sale_salesman_all_leads').id])]})
        cls.nobody = cls.env['res.users'].create({
            'name': 'Amend Nobody', 'login': 'amend_nobody',
            'group_ids': [(6, 0, [G('base.group_user').id])]})

    def _order(self):
        order = self.env['sale.order'].create({
            'partner_id': self.clinic.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 1})],
        })
        order.action_confirm()
        return order

    def _mark(self, order, user, note='One more unit on 36'):
        wizard = self.env['lab.amendment.wizard'].with_user(user).create(
            {'order_id': order.id, 'note': note})
        wizard.action_confirm()

    def test_the_floor_marks_an_amendment_without_any_right_on_the_order(self):
        order = self._order()
        self.assertFalse(self.floor.has_group('sales_team.group_sale_salesman'))
        self._mark(order, self.floor)
        self.assertTrue(order.has_amendment)
        self.assertEqual(order.amendment_note, 'One more unit on 36')
        self.assertEqual(order.amendment_user_id, self.floor)
        self.assertTrue(order.amendment_date)
        self.assertIn('One more unit on 36', order.message_ids[0].body)
        self.assertIn('Amend Floor', order.message_ids[0].body, "the order says who marked it")

    def test_an_order_with_an_amendment_cannot_be_invoiced_until_it_is_done(self):
        order = self._order()
        self._mark(order, self.floor)
        with self.assertRaises(UserError) as refused:
            order._create_invoices()
        self.assertIn('One more unit on 36', str(refused.exception),
                      "the refusal says what is waiting")
        self.assertFalse(order.invoice_ids)
        # the amendment is done: the office says so, and it invoices
        order.with_user(self.office).action_amendment_done()
        self.assertFalse(order.has_amendment)
        self.assertFalse(order.amendment_note)
        self.assertIn('One more unit on 36', order.message_ids[0].body,
                      "what it was stays on the order's record")
        invoice = order._create_invoices()
        self.assertTrue(invoice)

    def test_one_amended_order_holds_back_a_batch(self):
        clean, amended = self._order(), self._order()
        self._mark(amended, self.office, 'Shade changed to A3')
        with self.assertRaises(UserError) as refused:
            (clean | amended)._create_invoices()
        self.assertIn(amended.name, str(refused.exception))
        self.assertFalse((clean | amended).invoice_ids)

    def test_a_second_amendment_is_added_to_the_first(self):
        order = self._order()
        self._mark(order, self.floor, 'One more unit on 36')
        self._mark(order, self.office, 'Shade changed to A3')
        self.assertEqual(order.amendment_note, 'One more unit on 36\nShade changed to A3')

    def test_the_floor_can_say_it_is_done(self):
        order = self._order()
        self._mark(order, self.floor)
        order.with_user(self.floor).action_amendment_done()
        self.assertFalse(order.has_amendment)

    def test_marking_is_for_the_floor_and_the_office(self):
        order = self._order()
        with self.assertRaises(AccessError):
            order.with_user(self.nobody)._mark_amendment('anything')
        with self.assertRaises(AccessError):
            order.with_user(self.nobody).action_amendment_done()
        with self.assertRaises(UserError, msg="a flag needs its words"):
            order.with_user(self.floor)._mark_amendment('   ')
        self.assertFalse(order.has_amendment)

    def test_a_copy_of_the_order_starts_clean(self):
        order = self._order()
        self._mark(order, self.floor)
        self.assertFalse(order.copy().has_amendment)

    def test_the_job_shows_and_raises_the_flag(self):
        order = self._order()
        mo = self.env['mrp.production'].create({
            'product_id': self.product.id, 'product_qty': 1.0})
        mo.sale_line_id = order.order_line[0].id
        self.assertEqual(mo.sale_id, order)
        action = mo.with_user(self.floor).action_amendment_needed()
        self.assertEqual(action['res_model'], 'lab.amendment.wizard')
        self.assertEqual(action['context']['default_order_id'], order.id)
        self._mark(order, self.floor)
        mo.invalidate_recordset()
        self.assertTrue(mo.has_amendment)
        self.assertEqual(mo.amendment_note, 'One more unit on 36')
        mo.with_user(self.floor).action_amendment_done()
        self.assertFalse(order.has_amendment)
