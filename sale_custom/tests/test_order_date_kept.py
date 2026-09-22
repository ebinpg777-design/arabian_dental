# -*- coding: utf-8 -*-
"""Confirming an order keeps the Order Date it was registered with.

Core overwrites `date_order` with the moment of confirmation; the lab's order date is
the day the case was registered, and it must not move. (client, 2026-09-14)
"""
from datetime import datetime

from freezegun import freeze_time

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestOrderDateKept(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clinic = cls.env['res.partner'].create({'name': 'Order Date Clinic'})
        cls.product = cls.env['product.product'].create({
            'name': 'Order Date Appliance', 'type': 'consu', 'list_price': 100.0})

    def _order(self, **vals):
        return self.env['sale.order'].create(dict({
            'partner_id': self.clinic.id,
            'order_line': [(0, 0, {'product_id': self.product.id,
                                   'product_uom_qty': 1, 'price_unit': 100.0,
                                   'tax_ids': [(6, 0, [])]})],
        }, **vals))

    def test_a_back_dated_order_keeps_its_date_on_confirm(self):
        placed = datetime(2026, 8, 3, 5, 30)
        order = self._order(date_order=placed)
        order.action_confirm()
        self.assertEqual(order.state, 'sale')
        self.assertEqual(order.date_order, placed)

    def test_confirming_several_orders_keeps_each_date(self):
        first = self._order(date_order=datetime(2026, 7, 1, 4, 0))
        second = self._order(date_order=datetime(2026, 7, 9, 11, 15))
        (first | second).action_confirm()
        self.assertEqual(first.date_order, datetime(2026, 7, 1, 4, 0))
        self.assertEqual(second.date_order, datetime(2026, 7, 9, 11, 15))

    def test_an_order_created_without_a_date_keeps_its_creation_date(self):
        with freeze_time('2026-09-10 06:00:00'):
            order = self._order()
        self.assertEqual(order.date_order, datetime(2026, 9, 10, 6, 0))
        with freeze_time('2026-09-14 09:45:00'):
            order.action_confirm()
        self.assertEqual(order.date_order, datetime(2026, 9, 10, 6, 0))
