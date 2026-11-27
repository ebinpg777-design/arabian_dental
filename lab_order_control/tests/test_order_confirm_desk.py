# -*- coding: utf-8 -*-
"""Confirm Orders: find by number, confirm through the order's own Confirm.
(client, 2026-11-27)"""
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestOrderConfirmDesk(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_str('lab_order_control.require_order_verification', 'False')
        cls.env.company.company_warning = False
        cls.clinic = cls.env['res.partner'].create({'name': 'Desk Clinic', 'is_company': True})
        cls.product = cls.env['product.product'].create({
            'name': 'Desk Crown', 'type': 'consu', 'list_price': 500.0})
        base = cls.env.ref('base.group_user')
        cls.confirmer = cls.env['res.users'].create({
            'name': 'Desk Confirmer', 'login': 'desk_confirmer',
            'group_ids': [(6, 0, [base.id, cls.env.ref('lab_order_control.group_order_confirm_desk').id])]})
        cls.stranger = cls.env['res.users'].create({
            'name': 'Desk Stranger', 'login': 'desk_stranger',
            'group_ids': [(6, 0, [base.id, cls.env.ref('sales_team.group_sale_salesman').id])]})
        cls.Desk = cls.env['lab.order.confirm.desk'].with_user(cls.confirmer)

    def _order(self, **vals):
        order = self.env['sale.order'].create(dict({
            'partner_id': self.clinic.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 2})],
        }, **vals))
        return order

    def test_the_group_reads_every_order(self):
        self.assertTrue(self.confirmer.has_group('sales_team.group_sale_salesman_all_leads'))

    def test_find_by_the_number_as_typed(self):
        order = self._order()
        digits = order.name.lstrip('S')
        for typed in (order.name, order.name.lower(), digits, ' %s ' % digits):
            found = self.Desk.find(typed)
            self.assertEqual(found['order'] and found['order']['id'], order.id, typed)
        card = self.Desk.find(order.name)['order']
        self.assertTrue(card['can_confirm'])
        self.assertEqual(card['clinic'], 'Desk Clinic')
        self.assertEqual(card['lines'][0]['qty'], '2')

    def test_nothing_found_is_said(self):
        found = self.Desk.find('S99999999')
        self.assertFalse(found['order'])
        self.assertFalse(found['choices'])

    def test_confirm_and_confirm_again(self):
        order = self._order()
        res = self.Desk.confirm(order.id)
        self.assertTrue(res.get('done'), res)
        self.assertEqual(order.state, 'sale')
        self.assertEqual(res['card']['state'], 'sale')
        again = self.Desk.confirm(order.id)
        self.assertIn('already confirmed', again['error'])

    def test_the_company_warning_is_asked_first(self):
        self.env.company.company_warning = True
        order = self._order()
        res = self.Desk.confirm(order.id)
        self.assertEqual(res['action']['res_model'], 'confirm.wizard')
        self.assertNotEqual(order.state, 'sale', "not confirmed until the question is answered")

    def test_the_waiting_list(self):
        order = self._order(verification_state='to_verify')
        office = self._order()  # entered at the office, never sent for checking
        rows = self.Desk.waiting(limit=500)['rows']
        ids = [r['id'] for r in rows]
        self.assertIn(order.id, ids)
        self.assertNotIn(office.id, ids)
        self.Desk.confirm(order.id)
        self.assertNotIn(order.id, [r['id'] for r in self.Desk.waiting(limit=500)['rows']])

    def test_only_for_the_group(self):
        with self.assertRaises(AccessError):
            self.env['lab.order.confirm.desk'].with_user(self.stranger).find('S1')
