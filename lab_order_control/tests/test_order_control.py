# -*- coding: utf-8 -*-
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestLabOrderControl(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.params = cls.env['ir.config_parameter'].sudo()
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Test Clinic', 'is_company': True, 'is_clinic': True,
            'credit_limit': 10000.0,
        })
        cls.product = cls.env['product.product'].create({
            'name': 'Test Appliance', 'type': 'consu', 'list_price': 4000.0})
        # A second user so maker != checker can actually be exercised.
        cls.checker = cls.env['res.users'].create({
            'name': 'Checker', 'login': 'lab_checker_test',
            'group_ids': [(6, 0, [
                cls.env.ref('sales_team.group_sale_manager').id,
                cls.env.ref('lab_order_control.group_lab_order_checker').id,
                cls.env.ref('lab_order_control.group_lab_credit_override').id,
            ])],
        })

    def _order(self, qty=1):
        return self.env['sale.order'].create({
            'partner_id': self.clinic.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id, 'product_uom_qty': qty})],
        })

    def _require_verification(self, value='True'):
        self.params.set_param(
            'lab_order_control.require_order_verification', value)
        self.addCleanup(
            self.params.set_param,
            'lab_order_control.require_order_verification', 'False')

    # ------------------------------------------------------------------ maker/checker
    def test_confirm_blocked_until_verified(self):
        """Client rule (2026-08-09): verification is by ORIGIN. A plain counter order
        (no visit) does not need it, so the gate is exercised here on an order flagged
        as field-origin — a bare `_order()` would now confirm straight through, on
        purpose, per that rule."""
        self._require_verification()
        order = self._order()
        order.verification_needed = True   # stand in for a field-origin order
        with self.assertRaises(UserError):
            order.action_confirm()
        order.action_submit_for_verification()
        self.assertEqual(order.verification_state, 'to_verify')
        order.with_user(self.checker).action_verify()
        self.assertEqual(order.verification_state, 'verified')
        order.action_confirm()
        self.assertEqual(order.state, 'sale')

    def test_maker_cannot_be_the_checker(self):
        """The whole value of the control is that a second person looks."""
        order = self._order()
        order.action_submit_for_verification()
        # The creator holds the checker role here, and must still be refused.
        order.entered_by_id = self.checker
        with self.assertRaises(UserError):
            order.with_user(self.checker).action_verify()

    def test_self_verification_can_be_relaxed_for_a_one_person_counter(self):
        self.params.set_param('lab_order_control.allow_self_verification', 'True')
        self.addCleanup(
            self.params.set_param,
            'lab_order_control.allow_self_verification', 'False')
        order = self._order()
        order.entered_by_id = self.checker
        order.action_submit_for_verification()
        order.with_user(self.checker).action_verify()
        self.assertEqual(order.verification_state, 'verified')

    def test_send_back_requires_a_remark(self):
        order = self._order()
        order.action_submit_for_verification()
        with self.assertRaises(UserError):
            order.with_user(self.checker).action_reject_verification()
        order.verification_note = 'Wrong shade specified'
        order.with_user(self.checker).action_reject_verification()
        self.assertEqual(order.verification_state, 'rejected')

    def test_submitting_an_empty_order_is_refused(self):
        order = self.env['sale.order'].create({'partner_id': self.clinic.id})
        with self.assertRaises(UserError):
            order.action_submit_for_verification()

    def test_verification_off_leaves_confirmation_alone(self):
        order = self._order()
        order.action_confirm()
        self.assertEqual(order.state, 'sale')

    # ------------------------------------------------------------------ credit
    def test_within_limit_raises_no_warning(self):
        order = self._order(qty=1)     # 4 000 < 10 000
        self.assertFalse(order.credit_warning)

    def test_over_limit_warns_but_confirms_under_warn_policy(self):
        self.clinic.credit_limit_policy = 'warn'
        order = self._order(qty=10)    # 40 000 > 10 000
        self.assertTrue(order.credit_warning)
        order.action_confirm()
        self.assertEqual(order.state, 'sale')

    def test_over_limit_blocks_under_block_policy(self):
        self.clinic.credit_limit_policy = 'block'
        order = self._order(qty=10)
        with self.assertRaises(UserError):
            order.action_confirm()

    def test_credit_release_requires_a_reason_and_then_confirms(self):
        self.clinic.credit_limit_policy = 'block'
        order = self._order(qty=10)
        with self.assertRaises(UserError):
            order.with_user(self.checker).action_release_credit_block()
        order.credit_override_reason = 'Cheque in hand, clearing tomorrow'
        order.with_user(self.checker).action_release_credit_block()
        self.assertEqual(order.credit_override_by_id, self.checker)
        order.action_confirm()
        self.assertEqual(order.state, 'sale')

    def test_no_check_policy_skips_the_gate_entirely(self):
        self.clinic.credit_limit_policy = 'none'
        order = self._order(qty=10)
        self.assertFalse(order.credit_warning)
        order.action_confirm()
        self.assertEqual(order.state, 'sale')

    def test_exposure_counts_confirmed_uninvoiced_orders(self):
        """Receivables alone understate risk — committed lab work counts too."""
        self.clinic.credit_limit_policy = 'none'
        order = self._order(qty=1)
        order.action_confirm()
        self.clinic.invalidate_recordset()
        self.assertGreater(self.clinic.credit_uncovered_order_amount, 0.0)
        self.assertGreaterEqual(
            self.clinic.credit_exposure, self.clinic.credit_uncovered_order_amount)

    def test_exposure_counts_tax_once_on_a_part_invoiced_order(self):
        """The uninvoiced part is the taxed total less the taxed invoice - taking the
        untaxed invoiced amount off a taxed total left the invoice's tax counted."""
        self.clinic.credit_limit_policy = 'none'
        tax = self.env['account.tax'].create({
            'name': 'Exposure 18%', 'amount': 18.0, 'amount_type': 'percent',
            'type_tax_use': 'sale', 'price_include_override': 'tax_excluded'})
        product = self.env['product.product'].create({
            'name': 'Exposure Appliance', 'type': 'consu', 'invoice_policy': 'order'})
        order = self.env['sale.order'].create({
            'partner_id': self.clinic.id,
            'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': 2,
                                   'price_unit': 100.0, 'tax_ids': [(6, 0, tax.ids)]})],
        })
        order.action_confirm()
        self.assertAlmostEqual(order.amount_total, 236.0)
        invoice = order._create_invoices()
        invoice.invoice_line_ids.filtered(
            lambda l: l.product_id == product).quantity = 1
        invoice.action_post()
        self.assertAlmostEqual(invoice.amount_total, 118.0)
        self.assertNotEqual(order.invoice_status, 'invoiced')

        self.clinic.invalidate_recordset()
        # Only this order's share: other confirmed orders of the clinic add to it.
        others = sum(
            o.amount_total - o.amount_invoiced
            for o in self.env['sale.order'].search([
                ('partner_id', '=', self.clinic.id), ('state', '=', 'sale'),
                ('invoice_status', '!=', 'invoiced'), ('id', '!=', order.id)]))
        self.assertAlmostEqual(
            self.clinic.credit_uncovered_order_amount - others, 118.0,
            msg="236 taxed less the 118 taxed invoice, not less its 100 untaxed")
