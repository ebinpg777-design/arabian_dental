# -*- coding: utf-8 -*-
"""Epic C — doctor calls (R11) and the pending-information bucket (R14).

The behaviours worth pinning are the ones that are easy to regress into a rubber stamp:
an unresolved query must block verification, unanswered calls must park the order by
themselves, and a manager must be told once rather than every day.
"""
from lxml import etree

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools.safe_eval import safe_eval


@tagged('post_install', '-at_install')
class TestEpicCDoctorCalls(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.params = cls.env['ir.config_parameter'].sudo()
        cls.params.set_param('lab_order_control.require_order_verification', 'True')
        cls.params.set_param('lab_order_control.doctor_call_attempts_before_hold', '3')
        cls.params.set_param('lab_order_control.hold_warning_days', '5')
        # These tests are about the doctor-call gate, not the maker-checker rule — which
        # has its own tests in test_maker_scope.py. Without this the acting user trips
        # the self-verification ban and every case here fails for the wrong reason.
        cls.params.set_param('lab_order_control.allow_self_verification', 'True')
        cls.env.user.group_ids = [
            (4, cls.env.ref('lab_order_control.group_lab_order_checker').id)]
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Epic C Clinic', 'phone': '+91 98765 00000'})
        cls.product = cls.env['product.product'].create({
            'name': 'Epic C Appliance', 'type': 'consu', 'list_price': 500.0})

    def _order(self):
        return self.env['sale.order'].create({
            'partner_id': self.clinic.id,
            'order_line': [(0, 0, {'product_id': self.product.id,
                                   'product_uom_qty': 1})],
        })

    def _call(self, order, response, **kw):
        return self.env['lab.doctor.call.log'].create(
            dict({'order_id': order.id, 'response': response}, **kw))

    # ------------------------------------------------------------------ R11
    def test_an_open_doctor_query_blocks_verification(self):
        """The whole point of the gate: don't rubber-stamp an order still in question."""
        order = self._order()
        order.write({'verification_state': 'to_verify',
                     'call_doctor_required': True,
                     'call_doctor_reason': 'shade_confirmation'})
        self.assertTrue(order.call_pending)
        with self.assertRaises(UserError):
            order.action_verify()

    def test_resolving_the_query_needs_evidence(self):
        order = self._order()
        order.write({'call_doctor_required': True, 'call_doctor_reason': 'other'})
        with self.assertRaises(UserError):
            order.action_resolve_doctor_call()      # no answered call, no note

        self._call(order, 'answered', remarks='Doctor confirmed shade A2')
        order.action_resolve_doctor_call()
        self.assertTrue(order.call_doctor_resolved)
        self.assertFalse(order.call_pending)

    def test_a_resolved_query_lets_verification_through(self):
        order = self._order()
        order.write({'verification_state': 'to_verify',
                     'call_doctor_required': True, 'call_doctor_reason': 'other'})
        self._call(order, 'answered')
        order.action_resolve_doctor_call()
        order.action_verify()
        self.assertEqual(order.verification_state, 'verified')

    def test_the_phone_number_defaults_from_the_order(self):
        order = self._order()
        log = self._call(order, 'no_answer')
        self.assertEqual(log.phone, self.clinic.phone)

    # ------------------------------------------------------------------ R14
    def test_three_unanswered_calls_park_the_order_by_themselves(self):
        order = self._order()
        order.verification_state = 'to_verify'
        self._call(order, 'no_answer')
        self._call(order, 'switched_off')
        self.assertEqual(order.verification_state, 'to_verify',
                         "two attempts is not yet a reason to give up")
        self._call(order, 'busy')
        self.assertEqual(order.verification_state, 'on_hold')
        self.assertEqual(order.hold_reason, 'awaiting_doctor_response')
        self.assertTrue(order.next_followup_date,
                        "a hold with no follow-up date is how an order gets forgotten")

    def test_an_answered_call_does_not_count_towards_the_hold(self):
        order = self._order()
        order.verification_state = 'to_verify'
        for _i in range(4):
            self._call(order, 'answered')
        self.assertEqual(order.verification_state, 'to_verify')

    def test_a_held_order_cannot_be_verified_without_resuming(self):
        order = self._order()
        order._put_on_hold(reason='missing_info', note='No shade given')
        self.assertEqual(order.verification_state, 'on_hold')
        with self.assertRaises(UserError):
            order.action_verify()

    def test_resuming_returns_it_to_the_queue_not_to_verified(self):
        """It was held because something was unknown — it gets looked at again."""
        order = self._order()
        order._put_on_hold(reason='missing_info')
        order.action_resume_from_hold()
        self.assertEqual(order.verification_state, 'to_verify')
        self.assertFalse(order.hold_reason)

    def test_days_on_hold_ages(self):
        order = self._order()
        order._put_on_hold(reason='other')
        order.hold_date = fields.Datetime.subtract(fields.Datetime.now(), days=7)
        order._compute_days_on_hold()
        self.assertEqual(order.days_on_hold, 7)

    def test_the_cron_warns_managers_once_not_every_day(self):
        order = self._order()
        order._put_on_hold(reason='other')
        order.hold_date = fields.Datetime.subtract(fields.Datetime.now(), days=9)
        order.next_followup_date = fields.Date.context_today(order)

        self.env['sale.order']._cron_pending_info_followup()
        self.assertTrue(order.hold_warned)
        first = self.env['mail.message'].search_count(
            [('model', '=', 'sale.order'), ('res_id', '=', order.id)])

        # Second run on an unchanged order must not notify again.
        self.env['sale.order']._cron_pending_info_followup()
        second = self.env['mail.message'].search_count(
            [('model', '=', 'sale.order'), ('res_id', '=', order.id)])
        self.assertEqual(
            first, second,
            "a daily manager ping about the same stuck order is how people learn to "
            "ignore notifications entirely")

    def test_a_second_hold_is_warned_about_again(self):
        """One warning per HOLD, not per order: resumed and held again is a new stall,
        and the flag from the first one used to keep the manager from ever hearing."""
        order = self._order()
        order._put_on_hold(reason='other')
        order.hold_date = fields.Datetime.subtract(fields.Datetime.now(), days=9)
        self.env['sale.order']._cron_pending_info_followup()
        self.assertTrue(order.hold_warned)

        order.action_resume_from_hold()
        self.assertFalse(order.hold_warned, "resuming closes the hold, warning and all")
        order._put_on_hold(reason='missing_info')
        self.assertFalse(order.hold_warned, "a new hold starts unwarned")
        order.hold_date = fields.Datetime.subtract(fields.Datetime.now(), days=9)
        self.env['sale.order']._cron_pending_info_followup()
        self.assertTrue(order.hold_warned, "and the new stall reaches the manager")

    def test_zero_call_attempts_is_saved_and_switches_the_automatic_hold_off(self):
        """Core deletes an integer parameter saved as 0, so the getter read its
        default of 3 and '0 disables the automatic hold' could never be saved."""
        key = 'lab_order_control.doctor_call_attempts_before_hold'
        settings = self.env['res.config.settings'].create({
            'lab_doctor_call_attempts_before_hold': 0,
            'lab_hold_warning_days': 0,
        })
        # Read before core saves, as set_values does: deleting the parameter
        # invalidates the cache, and an unsaved value would be lost with it.
        zeros = settings._zero_param_names()
        # What core's set_values does with an integer 0, then this module's answer.
        self.params.set_param(key, False)
        self.params.set_param('lab_order_control.hold_warning_days', False)
        settings._keep_zero_params(zeros)

        Order = self.env['sale.order']
        self.assertEqual(Order._doctor_call_attempts_before_hold(), 0)
        self.assertEqual(Order._hold_warning_days(), 0)
        self.assertEqual(
            # default_get is what the settings screen loads config parameters
            # through; get_values only carries this module's own extras.
            self.env['res.config.settings'].default_get(
                ['lab_doctor_call_attempts_before_hold'])[
                'lab_doctor_call_attempts_before_hold'], 0,
            "the settings screen must show the 0 that was saved, not the default")

        order = self._order()
        order.verification_state = 'to_verify'
        for response in ('no_answer', 'busy', 'switched_off', 'no_answer'):
            self._call(order, response)
        self.assertEqual(order.verification_state, 'to_verify',
                         "0 attempts means the order is never parked by itself")

    def test_a_non_zero_setting_is_left_to_core(self):
        key = 'lab_order_control.doctor_call_attempts_before_hold'
        self.params.set_param(key, '4')
        self.env['res.config.settings'].new(
            {'lab_doctor_call_attempts_before_hold': 4,
             'lab_hold_warning_days': 5})._keep_zero_params()
        self.assertEqual(self.params.get_param(key), '4')

    # ------------------------------------------------------ R14: on hold, findable
    def test_a_verified_order_can_still_be_put_on_hold(self):
        """Something can come up after the second person has already signed off -
        (client, 2026-08-29): the Put On Hold button has to reach that order too,
        not only the ones still waiting on the first check."""
        order = self._order()
        order.write({'verification_state': 'to_verify'})
        order.action_verify()
        self.assertEqual(order.verification_state, 'verified')
        order._put_on_hold(reason='patient_postponed')
        self.assertEqual(order.verification_state, 'on_hold')
        self.assertEqual(order.hold_reason, 'patient_postponed')

    def test_the_put_on_hold_button_reaches_a_verified_order(self):
        """The model side above proves the action works from 'verified' - this is
        what actually gates the button in the form, so a regression there would
        pass the test above and still leave the button unreachable."""
        arch = etree.fromstring(self.env['sale.order'].get_view(view_type='form')['arch'])
        node = arch.xpath("//button[@name='action_put_on_hold']")[0]
        invisible = node.get('invisible')
        for state in ('to_verify', 'draft', 'verified'):
            self.assertFalse(
                safe_eval(invisible, {'verification_state': state}),
                "%s must see the Put On Hold button" % state)
        for state in ('on_hold', 'rejected'):
            self.assertTrue(
                safe_eval(invisible, {'verification_state': state}),
                "%s already has its own controls, not this button" % state)

    def test_the_search_filter_finds_only_held_orders(self):
        held = self._order()
        held._put_on_hold(reason='other')
        elsewhere = self._order()
        arch = etree.fromstring(
            self.env['sale.order'].get_view(view_type='search')['arch'])
        node = arch.xpath("//filter[@name='on_hold']")[0]
        found = self.env['sale.order'].search(
            [('id', 'in', (held | elsewhere).ids)] + safe_eval(node.get('domain')))
        self.assertEqual(found, held)
