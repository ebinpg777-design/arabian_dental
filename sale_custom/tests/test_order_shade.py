# -*- coding: utf-8 -*-
"""The order's shade, carried to its lines. (client, 2026-11-24)"""
from odoo.tests import Form, TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestOrderShade(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Shade = cls.env['product.colour']
        cls.a2 = Shade.create({'name': 'ZZ-SH-A2'})
        cls.a3 = Shade.create({'name': 'ZZ-SH-A3'})
        cls.b1 = Shade.create({'name': 'ZZ-SH-B1'})
        cls.clinic = cls.env['res.partner'].create({'name': 'ZZ Shade Clinic'})
        cls.crown = cls.env['product.product'].create({'name': 'ZZ Shade Crown', 'sale_ok': True})

    def _order(self, **vals):
        return self.env['sale.order'].create(dict({
            'partner_id': self.clinic.id,
            'order_line': [(0, 0, {'product_id': self.crown.id}), (0, 0, {'product_id': self.crown.id})],
        }, **vals))

    def test_lines_take_the_orders_shade(self):
        order = self._order(color_scheme=self.a2.id)
        self.assertEqual(order.order_line.color_scheme, self.a2)

    def test_a_line_may_have_its_own(self):
        order = self.env['sale.order'].create({
            'partner_id': self.clinic.id, 'color_scheme': self.a2.id,
            'order_line': [(0, 0, {'product_id': self.crown.id}),
                           (0, 0, {'product_id': self.crown.id, 'color_scheme': self.b1.id})]})
        self.assertEqual(order.order_line.mapped('color_scheme'), self.a2 | self.b1)

    def test_changing_the_order_moves_the_lines_that_followed_it(self):
        order = self.env['sale.order'].create({
            'partner_id': self.clinic.id, 'color_scheme': self.a2.id,
            'order_line': [(0, 0, {'product_id': self.crown.id}),
                           (0, 0, {'product_id': self.crown.id, 'color_scheme': self.b1.id})]})
        followed, own = order.order_line
        order.color_scheme = self.a3
        self.assertEqual(followed.color_scheme, self.a3)
        self.assertEqual(own.color_scheme, self.b1, "a line set on its own keeps its shade")

    def test_a_line_added_later_takes_it(self):
        order = self._order(color_scheme=self.a2.id)
        line = self.env['sale.order.line'].create({'order_id': order.id, 'product_id': self.crown.id})
        self.assertEqual(line.color_scheme, self.a2)

    def test_the_form_does_the_same(self):
        with Form(self.env['sale.order']) as form:
            form.partner_id = self.clinic
            with form.order_line.new() as line:
                line.product_id = self.crown
            form.color_scheme = self.a2
            with form.order_line.new() as line:
                line.product_id = self.crown
        order = form.save()
        self.assertEqual(order.order_line.color_scheme, self.a2)

    def test_an_order_with_no_shade_leaves_the_lines_alone(self):
        order = self.env['sale.order'].create({
            'partner_id': self.clinic.id,
            'order_line': [(0, 0, {'product_id': self.crown.id, 'color_scheme': self.b1.id})]})
        self.assertEqual(order.order_line.color_scheme, self.b1)
