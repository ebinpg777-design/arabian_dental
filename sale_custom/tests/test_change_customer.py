# -*- coding: utf-8 -*-
"""A confirmed order made out to another customer, and still confirmed.

(Ortho client, 2026-10-15: "provide a button to change partner in confirmed sale orders
without changing the SO status")
"""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestChangeCustomer(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.route = cls.env['crm.team'].create({'name': 'CC Route Old'})
        cls.new_route = cls.env['crm.team'].create({'name': 'CC Route New', 'user_id': cls.env.user.id})
        cls.wrong = cls.env['res.partner'].create({'name': 'CC Wrong Clinic', 'team_id': cls.route.id})
        cls.right = cls.env['res.partner'].create({'name': 'CC Right Clinic', 'team_id': cls.new_route.id})
        cls.right_delivery = cls.env['res.partner'].create({
            'name': 'CC Right Clinic Branch', 'type': 'delivery', 'parent_id': cls.right.id, 'street': '2 Side Rd'})
        cls.product = cls.env['product.product'].create({
            'name': 'CC Appliance', 'type': 'consu', 'is_storable': True, 'list_price': 100.0})

    def _confirmed(self):
        order = self.env['sale.order'].create({
            'partner_id': self.wrong.id, 'patient': 'Ravi Menon',
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 1, 'price_unit': 100.0,
                                   'tax_ids': [(6, 0, [])]})]})
        order.action_confirm()
        return order

    def _wizard(self, order, **vals):
        action = order.action_open_change_customer()
        return self.env[action['res_model']].with_context(action['context']).create(dict({'partner_id': self.right.id}, **vals))

    def test_the_customer_changes_and_the_order_stays_confirmed(self):
        order = self._confirmed()
        amount, lines = order.amount_total, order.order_line
        wiz = self._wizard(order, reason='booked against the wrong clinic')
        self.assertEqual(wiz.partner_shipping_id, self.right_delivery, "the clinic's delivery address is offered")
        wiz.action_apply()
        self.assertEqual(order.state, 'sale', "still confirmed")
        self.assertEqual((order.partner_id, order.partner_shipping_id), (self.right, self.right_delivery))
        self.assertEqual(order.order_line, lines)
        self.assertEqual(order.amount_total, amount, "prices as they were")
        self.assertEqual(order.team_id, self.new_route, "the new clinic's route")
        open_transfers = order.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel'))
        self.assertTrue(open_transfers)
        self.assertEqual(open_transfers.partner_id, self.right_delivery, "the open delivery goes to the new address")
        note = order.message_ids[0].body
        self.assertIn('CC Wrong Clinic', note)
        self.assertIn('booked against the wrong clinic', note)

    def test_the_route_can_stay(self):
        order = self._confirmed()
        route = order.team_id
        self._wizard(order, move_route=False).action_apply()
        self.assertEqual(order.partner_id, self.right)
        self.assertEqual(order.team_id, route)

    def test_an_invoiced_order_is_refused(self):
        order = self._confirmed()
        order.order_line.qty_delivered = 1
        order._create_invoices()
        with self.assertRaises(UserError):
            order.action_open_change_customer()
        with self.assertRaises(UserError):
            order._change_customer(self.right)
        self.assertEqual(order.partner_id, self.wrong)

    def test_a_quotation_is_changed_on_the_order_itself(self):
        order = self.env['sale.order'].create({'partner_id': self.wrong.id})
        with self.assertRaises(UserError):
            order.action_open_change_customer()
