# -*- coding: utf-8 -*-
"""An executive corrects a submitted case until the office has verified its order.
(client, 2026-11-24: "executive can edit the submitted cases")"""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCaseCorrection(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        G = cls.env.ref
        groups = [G('base.group_user').id, G('lab_fieldwork.group_fieldwork_executive').id]
        cls.exec_user = cls.env['res.users'].create({
            'name': 'Fix Exec', 'login': 'fix_exec', 'group_ids': [(6, 0, groups)]})
        route = cls.env['crm.team'].create({'name': 'Fix Route', 'user_id': cls.exec_user.id})
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Fix Clinic', 'is_clinic': True, 'team_id': route.id})
        cls.crown = cls.env['product.product'].create({'name': 'ZZ Fix Crown', 'sale_ok': True,
                                                       'list_price': 1000})
        cls.bridge = cls.env['product.product'].create({'name': 'ZZ Fix Bridge', 'sale_ok': True,
                                                        'list_price': 3000})
        cls.shade = cls.env['product.colour'].create({'name': 'ZZ-FIX-A2'})
        cls.Case = cls.env['lab.case'].with_user(cls.exec_user)

    def _ordered(self):
        case = self.Case.create({
            'received_via': 'courier', 'partner_id': self.clinic.id, 'patient': 'Fix Patient',
            'line_ids': [(0, 0, {'product_id': self.crown.id, 'ul': 'upper', 'quantity': 1}),
                         (0, 0, {'product_id': self.bridge.id, 'ul': 'lower', 'quantity': 3})]})
        case.action_submit()
        self.assertEqual(case.state, 'registered')
        return case, case.sudo().sale_order_id

    def test_each_line_knows_its_order_line(self):
        case, order = self._ordered()
        self.assertEqual(case.line_ids.sudo().sale_line_id.order_id, order)
        self.assertEqual(case.line_ids[0].sudo().sale_line_id.product_id, self.crown)

    def test_corrections_go_onto_the_same_order(self):
        case, order = self._ordered()
        self.assertTrue(case.order_editable)
        # the office has re-priced the crown before anyone verified
        crown_line = case.line_ids[0].sudo().sale_line_id
        crown_line.price_unit = 777
        case.action_reset_to_draft()
        self.assertEqual(case.state, 'draft')
        self.assertEqual(order.verification_state, 'draft',
                         "out of the office's queue while it is being corrected")
        with self.assertRaises(UserError):
            order.with_user(self.env.ref('base.user_admin')).action_verify()
        bridge_case_line = case.line_ids[1]
        bridge_order_line = bridge_case_line.sudo().sale_line_id
        case.write({
            'patient': 'Fix Patient Corrected',
            'line_ids': [(1, bridge_case_line.id, {'quantity': 4, 'colour_id': self.shade.id})],
        })
        case.action_submit()
        self.assertEqual(case.state, 'registered')
        self.assertEqual(case.sudo().sale_order_id, order, "the same order, the same number")
        self.assertEqual(order.patient, 'Fix Patient Corrected')
        self.assertEqual((bridge_order_line.product_uom_qty, bridge_order_line.color_scheme),
                         (4, self.shade))
        self.assertEqual(crown_line.price_unit, 777, "an unchanged line keeps the office's price")
        self.assertEqual(order.verification_state, 'to_verify', "back in the queue")
        self.assertIn('Corrected from the field', order.message_ids[0].body)

    def test_a_line_added_or_taken_off(self):
        case, order = self._ordered()
        case.action_reset_to_draft()
        bridge_line = case.line_ids[1].sudo().sale_line_id
        case.write({'line_ids': [(2, case.line_ids[1].id),
                                 (0, 0, {'product_id': self.crown.id, 'ul': 'lower', 'quantity': 2})]})
        self.assertFalse(bridge_line.exists(), "the bridge is off the order too")
        case.action_submit()
        made = order.order_line.filtered(lambda l: not l.display_type and not l.is_urgent_service)
        self.assertEqual(sorted(made.mapped('product_uom_qty')), [1, 2])
        self.assertEqual(made.product_id, self.crown)
        self.assertFalse(self.Case.search([('id', '!=', case.id), ('patient', '=', 'Fix Patient')]),
                         "no second case, no second order")

    def test_once_verified_it_is_the_offices(self):
        case, order = self._ordered()
        order.write({'verification_state': 'verified'})
        case.invalidate_recordset(['order_editable'])
        self.assertFalse(case.order_editable)
        with self.assertRaises(UserError):
            case.action_reset_to_draft()

    def test_an_order_verified_while_it_was_being_corrected_is_not_changed(self):
        case, order = self._ordered()
        case.action_reset_to_draft()
        order.write({'verification_state': 'verified'})
        with self.assertRaises(UserError):
            case.action_submit()

    def test_a_case_that_became_an_order_is_not_cancelled_from_the_slip(self):
        case, _order = self._ordered()
        case.action_reset_to_draft()
        with self.assertRaises(UserError):
            case.action_cancel()

    def test_an_old_case_is_paired_with_its_lines_when_opened(self):
        case, order = self._ordered()
        case.line_ids.sudo().write({'sale_line_id': False})
        case.action_reset_to_draft()
        self.assertEqual(case.line_ids.sudo().sale_line_id.order_id, order)
