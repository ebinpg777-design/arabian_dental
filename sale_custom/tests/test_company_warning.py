# -*- coding: utf-8 -*-
"""The company-warning dialog on confirmation.

It returned on the first flagged order, so a multi-order confirm left the rest
unconfirmed without a word, and it marked the warning seen before the dialog was
answered, so cancelling it skipped the warning the next time. (2026-09-15)
"""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCompanyWarning(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.company_warning = True
        cls.clinic = cls.env['res.partner'].create({'name': 'Warning Clinic'})
        cls.product = cls.env['product.product'].create({
            'name': 'Warning Appliance', 'type': 'consu', 'list_price': 100.0})

    def _order(self):
        return self.env['sale.order'].create({
            'partner_id': self.clinic.id, 'company_id': self.company.id,
            'order_line': [(0, 0, {'product_id': self.product.id,
                                   'product_uom_qty': 1, 'price_unit': 100.0,
                                   'tax_ids': [(6, 0, [])]})],
        })

    def _answer(self, action):
        self.assertEqual(action['res_model'], 'confirm.wizard')
        return self.env['confirm.wizard'].with_context(action['context']).create({})

    def test_one_flagged_order_is_confirmed_from_the_dialog(self):
        order = self._order()
        wizard = self._answer(order.action_confirm())
        self.assertEqual(order.state, 'draft', "nothing happens before the answer")
        wizard.action_confirm()
        self.assertEqual(order.state, 'sale')
        self.assertTrue(order.company_warning)

    def test_cancelling_the_dialog_shows_the_warning_again(self):
        order = self._order()
        self._answer(order.action_confirm())
        self.assertFalse(order.company_warning,
                         "an unanswered dialog is not an acknowledged warning")
        self._answer(order.action_confirm())
        self.assertEqual(order.state, 'draft')

    def test_a_selection_confirms_the_clear_ones_and_asks_once_for_the_rest(self):
        seen = self._order()
        seen.company_warning = True
        first, second = self._order(), self._order()
        wizard = self._answer((seen | first | second).action_confirm())
        self.assertEqual(seen.state, 'sale', "an order needing no warning goes ahead")
        self.assertEqual((first | second).mapped('state'), ['draft', 'draft'])
        self.assertEqual(wizard.sale_ids, first | second,
                         "every flagged order is in the one dialog, not just the first")
        wizard.action_confirm()
        self.assertEqual((first | second).mapped('state'), ['sale', 'sale'])

    def test_a_company_without_the_warning_confirms_straight_away(self):
        self.company.company_warning = False
        order = self._order()
        order.action_confirm()
        self.assertEqual(order.state, 'sale')
