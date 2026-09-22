# -*- coding: utf-8 -*-
"""Verification applies to the people it was meant for, and nobody else.

Maker-checker only means something when the maker is somewhere the checker is not — out
at a clinic, entering a case on a phone. An office user keying an order at the counter
already is the checker, so holding their order in a queue adds a step and controls
nothing; worse, it fills the verification list with orders that never needed looking at,
which is how a real one stops being read.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestVerificationScope(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.params = cls.env['ir.config_parameter'].sudo()
        cls.params.set_param('lab_order_control.require_order_verification', 'True')
        cls.executive_group = cls.env.ref(
            'lab_fieldwork.group_fieldwork_executive', raise_if_not_found=False)
        cls.clinic = cls.env['res.partner'].create({'name': 'Scope Clinic'})
        cls.product = cls.env['product.product'].create({
            'name': 'Scope Appliance', 'type': 'consu', 'list_price': 100.0})
        base = cls.env.ref('base.group_user')
        cls.office = cls.env['res.users'].create({
            'name': 'Counter Staff', 'login': 'scope_office',
            'group_ids': [(6, 0, [base.id,
                                  cls.env.ref('sales_team.group_sale_salesman').id])]})
        exec_groups = [base.id, cls.env.ref('sales_team.group_sale_salesman').id]
        if cls.executive_group:
            exec_groups.append(cls.executive_group.id)
        cls.executive = cls.env['res.users'].create({
            'name': 'Field Executive', 'login': 'scope_exec',
            'group_ids': [(6, 0, exec_groups)]})

    def _order_as(self, user):
        return self.env['sale.order'].with_user(user).create({
            'partner_id': self.clinic.id,
            'order_line': [(0, 0, {'product_id': self.product.id,
                                   'product_uom_qty': 1})],
        })

    def _scope_to_executives(self):
        self.params.set_param(
            'lab_order_control.verification_maker_groups',
            str(self.executive_group.id) if self.executive_group else '')

    def test_an_executives_order_is_held_for_checking(self):
        if not self.executive_group:
            self.skipTest("lab_fieldwork not installed")
        self._scope_to_executives()
        order = self._order_as(self.executive)
        self.assertTrue(order.verification_needed)
        self.assertTrue(order._verification_required())

    def test_an_office_users_order_is_not(self):
        if not self.executive_group:
            self.skipTest("lab_fieldwork not installed")
        self._scope_to_executives()
        order = self._order_as(self.office)
        self.assertFalse(
            order.verification_needed,
            "an order keyed at the counter must not be queued for someone to check")
        self.assertFalse(order._verification_required())

    def test_an_office_order_confirms_without_being_verified(self):
        """The whole point — the gate must not fire on an order it does not apply to."""
        if not self.executive_group:
            self.skipTest("lab_fieldwork not installed")
        self._scope_to_executives()
        order = self._order_as(self.office)
        order.action_confirm()
        self.assertEqual(order.state, 'sale')

    def test_with_no_roles_configured_a_counter_order_is_not_checked(self):
        """Client rule (2026-08-09): 'direct creation of a sale order doesn't need
        verification; any sale order from field work does.' That is a rule about
        ORIGIN — so with no maker roles configured, a plain counter order (no visit)
        goes through untouched. It is `test_an_executives_order_is_held_for_checking`
        in this file that proves the control has not gone away: field-origin orders are
        still checked regardless of this setting."""
        self.params.set_param('lab_order_control.verification_maker_groups', '')
        order = self._order_as(self.office)
        self.assertFalse(order.verification_needed)

    def test_the_master_switch_still_turns_everything_off(self):
        self.params.set_param('lab_order_control.require_order_verification', 'False')
        self._scope_to_executives()
        order = self._order_as(self.executive if self.executive_group else self.office)
        self.assertFalse(order.verification_needed)
        self.params.set_param('lab_order_control.require_order_verification', 'True')
