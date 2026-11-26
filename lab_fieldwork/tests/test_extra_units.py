# -*- coding: utf-8 -*-
"""The first unit at one price, the rest as the product's additional units.

(client, 2026-11-26: "when the executive adds the first product with more than 1
qty, split it into the other product")
"""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestExtraUnits(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        G = cls.env.ref
        groups = [G('base.group_user').id, G('lab_fieldwork.group_fieldwork_executive').id]
        cls.exec_user = cls.env['res.users'].create({
            'name': 'Units Exec', 'login': 'units_exec', 'group_ids': [(6, 0, groups)]})
        route = cls.env['crm.team'].create({'name': 'Units Route', 'user_id': cls.exec_user.id})
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Units Clinic', 'is_clinic': True, 'team_id': route.id})
        Product = cls.env['product.product']
        cls.more = Product.create({'name': 'ZZ RPD ADDITIONAL PER UNIT', 'sale_ok': True,
                                   'list_price': 190.48})
        cls.first = Product.create({'name': 'ZZ RPD SINGLE UNIT', 'sale_ok': True,
                                    'list_price': 333.33,
                                    'lab_extra_unit_product_id': cls.more.id})
        cls.crown = Product.create({'name': 'ZZ Crown', 'sale_ok': True, 'list_price': 1000})
        cls.Case = cls.env['lab.case'].with_user(cls.exec_user)

    def _case(self, *lines):
        return self.Case.create({
            'received_via': 'courier', 'partner_id': self.clinic.id, 'patient': 'Units Patient',
            'line_ids': [(0, 0, dict({'ul': 'upper'}, **line)) for line in lines]})

    def _lines(self, case):
        return [(l.product_id, l.quantity, l.teeth or '', l.ul)
                for l in case.line_ids.sorted(lambda l: (l.sequence, l.id))]

    def test_more_than_one_unit_is_split(self):
        case = self._case({'product_id': self.first.id, 'quantity': 4, 'teeth': '11, 12, 21, 22'})
        self.assertEqual(self._lines(case), [(self.first, 1, '11', 'upper'),
                                             (self.more, 3, '12, 21, 22', 'upper')])
        self.assertIn('ZZ RPD ADDITIONAL PER UNIT', ' '.join(case.message_ids.mapped('body')))

    def test_one_unit_and_other_products_are_left_alone(self):
        case = self._case({'product_id': self.first.id, 'quantity': 1, 'teeth': '11'},
                          {'product_id': self.crown.id, 'quantity': 3})
        self.assertEqual(self._lines(case), [(self.first, 1, '11', 'upper'),
                                             (self.crown, 3, '', 'upper')])

    def test_raising_the_quantity_later_adds_to_the_additional_line(self):
        case = self._case({'product_id': self.first.id, 'quantity': 2})
        first = case.line_ids.filtered(lambda l: l.product_id == self.first)
        first.quantity = 3
        self.assertEqual(sorted((l.product_id.name, l.quantity) for l in case.line_ids),
                         [('ZZ RPD ADDITIONAL PER UNIT', 3), ('ZZ RPD SINGLE UNIT', 1)])

    def test_each_jaw_has_its_own_first_unit(self):
        case = self._case({'product_id': self.first.id, 'quantity': 3, 'ul': 'upper'},
                          {'product_id': self.first.id, 'quantity': 2, 'ul': 'lower'})
        self.assertEqual(sorted((l.product_id.name, l.ul, l.quantity) for l in case.line_ids),
                         [('ZZ RPD ADDITIONAL PER UNIT', 'lower', 1),
                          ('ZZ RPD ADDITIONAL PER UNIT', 'upper', 2),
                          ('ZZ RPD SINGLE UNIT', 'lower', 1),
                          ('ZZ RPD SINGLE UNIT', 'upper', 1)])

    def test_teeth_that_do_not_match_the_units_stay_on_the_first_line(self):
        case = self._case({'product_id': self.first.id, 'quantity': 3, 'teeth': '11, 12'})
        self.assertEqual(self._lines(case), [(self.first, 1, '11, 12', 'upper'),
                                             (self.more, 2, '', 'upper')])

    def test_the_order_gets_both_products(self):
        case = self._case({'product_id': self.first.id, 'quantity': 3, 'teeth': '11, 12, 21'})
        case.action_submit()
        order = case.sudo().sale_order_id
        self.assertTrue(order)
        self.assertEqual(sorted((l.product_id.name, l.product_uom_qty, l.price_unit)
                                for l in order.order_line),
                         [('ZZ RPD ADDITIONAL PER UNIT', 2, 190.48),
                          ('ZZ RPD SINGLE UNIT', 1, 333.33)])
