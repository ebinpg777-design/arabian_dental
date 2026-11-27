# -*- coding: utf-8 -*-
"""A case is new work, a rework or a trial, and its order is that kind.

(client, 2026-11-27: "In the case entry able to enter the Rework SO with new entry
sequence. Also another type of SO: Trial SOs, unit price zero, another sequence.
Executive can enter the Trial and Rework SOs")
"""
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCaseKind(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        G = cls.env.ref
        groups = [G('base.group_user').id, G('lab_fieldwork.group_fieldwork_executive').id]
        cls.exec_user = cls.env['res.users'].create({
            'name': 'Kind Exec', 'login': 'kind_exec', 'group_ids': [(6, 0, groups)]})
        route = cls.env['crm.team'].create({'name': 'Kind Route', 'user_id': cls.exec_user.id})
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Kind Clinic', 'is_clinic': True, 'team_id': route.id})
        cls.crown = cls.env['product.product'].create(
            {'name': 'ZZ Kind Crown', 'sale_ok': True, 'list_price': 1000})
        cls.reason = cls.env['lab.rework.reason'].search([], limit=1)
        cls.Case = cls.env['lab.case'].with_user(cls.exec_user)

    def _case(self, **vals):
        return self.Case.create(dict({
            'received_via': 'courier', 'partner_id': self.clinic.id, 'patient': 'Kind Patient',
            'line_ids': [(0, 0, {'product_id': self.crown.id, 'ul': 'upper', 'quantity': 2})],
        }, **vals))

    def _order(self, case):
        case.action_submit()
        return case.sudo().sale_order_id

    def test_new_work_is_an_ordinary_charged_order(self):
        order = self._order(self._case())
        self.assertFalse(order.is_rework or order.is_trial)
        self.assertFalse(order.name.startswith(('RE', 'TR')))
        self.assertEqual(order.order_line.price_unit, 1000)

    def test_a_rework_case_makes_a_rework_order(self):
        self.assertTrue(self.reason, "the module ships rework reasons")
        case = self._case(case_kind='rework', rework_reason_id=self.reason.id,
                          rework_note='loose at the margin')
        order = self._order(case)
        self.assertTrue(order.is_rework)
        self.assertTrue(order.name.startswith('RE'), order.name)
        self.assertEqual(order.rework_reason_id, self.reason)
        self.assertEqual(order.rework_note, 'loose at the margin')
        self.assertEqual(order.order_line.price_unit, 0.0, "a rework is not charged")
        self.assertFalse(order.repeat_patient_note, "a rework IS the same patient again")

    def test_a_rework_needs_its_reason(self):
        case = self._case(case_kind='rework')
        with self.assertRaises(UserError):
            case.action_submit()
        self.assertFalse(case.sudo().sale_order_id)

    def test_a_trial_case_makes_a_free_trial_order_on_its_own_numbers(self):
        first = self._order(self._case(case_kind='trial'))
        second = self._order(self._case(case_kind='trial', patient='Kind Patient Two'))
        self.assertTrue(first.is_trial and not first.is_rework)
        self.assertTrue(first.name.startswith('TR') and second.name.startswith('TR'))
        self.assertNotEqual(first.name, second.name)
        self.assertEqual(first.order_line.price_unit, 0.0, "a trial is not charged")
        self.assertEqual(first.amount_total, 0.0)

    def test_the_office_can_tick_trial_too(self):
        order = self.env['sale.order'].create({
            'partner_id': self.clinic.id,
            'order_line': [(0, 0, {'product_id': self.crown.id, 'product_uom_qty': 1})]})
        was = order.name
        self.assertEqual(order.order_line.price_unit, 1000)
        order.is_trial = True
        self.assertTrue(order.name.startswith('TR'))
        self.assertEqual(order.order_line.price_unit, 0.0)
        order.is_trial = False
        self.assertEqual(order.name, was, "the number it had comes back")
        with self.assertRaises(ValidationError):
            order.write({'is_trial': True, 'is_rework': True})
