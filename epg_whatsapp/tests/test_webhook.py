# -*- coding: utf-8 -*-
"""The rules that decide whether a message can legally be sent, and what became of it.

These are the parts that simulation mode cannot exercise: in simulation every send
"works", so the 24-hour window, the signature check and the receipt ordering are exactly
the things that stay untested until the account goes live and starts failing.
"""
import hashlib
import hmac
import json
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged

SECRET = 'top-secret-app-secret'


@tagged('post_install', '-at_install')
class TestWhatsappWindow(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.account = cls.env['epg.whatsapp.account'].create({
            'name': 'Test Sender',
            'phone_number_id': '111222333',
            'access_token': 'token',
            'app_secret': SECRET,
            'simulation_mode': True,
            'channel': 'cloud_api',
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Dr Test', 'phone': '+91 98765 43210'})
        cls.Conversation = cls.env['epg.whatsapp.conversation']
        cls.Message = cls.env['epg.whatsapp.message']

    def _conv(self, number='919876543210'):
        return self.Conversation._get_or_create(self.account, number, self.partner)

    def _message(self, conv, **vals):
        return self.Message.create(dict({
            'account_id': self.account.id,
            'conversation_id': conv.id,
            'number': conv.number,
            'body': 'Your case is ready.',
        }, **vals))

    # ------------------------------------------------------------------ the window
    def test_a_number_that_never_wrote_has_no_open_window(self):
        conv = self._conv()
        self.assertEqual(conv.window_state, 'never')
        self.assertFalse(conv.window_open)

    def test_an_inbound_message_opens_the_window_for_24_hours(self):
        conv = self._conv()
        conv._note_inbound()
        self.assertTrue(conv.window_open)
        self.assertEqual(conv.window_state, 'open')

    def test_the_window_closes_after_24_hours(self):
        conv = self._conv()
        conv._note_inbound(fields.Datetime.now() - timedelta(hours=25))
        self.assertFalse(conv.window_open)
        self.assertEqual(conv.window_state, 'closed')

    def test_outside_the_window_a_message_without_a_template_is_not_sent(self):
        """The whole point: WhatsApp would reject this, so we must not claim it went.

        Simulation mode would happily report success for it, which is exactly how this
        stays invisible until the day real messages start bouncing.
        """
        conv = self._conv()
        conv._note_inbound(fields.Datetime.now() - timedelta(hours=48))
        msg = self._message(conv)
        msg.action_send()
        self.assertEqual(msg.state, 'error')
        self.assertIn('24-hour', msg.error)

    def test_outside_the_window_an_approved_template_is_used_instead(self):
        conv = self._conv()
        conv._note_inbound(fields.Datetime.now() - timedelta(hours=48))
        template = self.env['epg.whatsapp.template'].create({
            'name': 'Case Ready',
            'model_id': self.env.ref('base.model_res_partner').id,
            'body': 'Ready',
            'meta_template_name': 'case_ready',
        })
        msg = self._message(conv, template_id=template.id)
        msg.action_send()
        self.assertEqual(msg.state, 'simulated')
        self.assertTrue(msg.sent_as_template,
                        "a send outside the window must be recorded as a template send")

    def test_inside_the_window_plain_text_is_used(self):
        conv = self._conv()
        conv._note_inbound()
        msg = self._message(conv)
        msg.action_send()
        self.assertEqual(msg.state, 'simulated')
        self.assertFalse(msg.sent_as_template)

    # ------------------------------------------------------------------ opt-out
    def test_nothing_is_sent_to_a_number_that_opted_out(self):
        conv = self._conv()
        conv._note_inbound()
        conv.opt_out = True
        msg = self._message(conv)
        msg.action_send()
        self.assertEqual(msg.state, 'cancel')
        self.assertNotEqual(msg.state, 'error',
                            "an opt-out is a decision to respect, not a failure to retry")

    def test_a_blocked_message_is_cancelled_with_its_reason_not_sent(self):
        """The hook business modules use for consent: every send path consults it."""
        from unittest.mock import patch
        conv = self._conv()
        conv._note_inbound()
        msg = self._message(conv)
        allowed = self._message(conv)
        with patch.object(type(self.Message), '_send_blocked_reason',
                          lambda m: 'Blocked for the test.' if m == msg else False):
            (msg | allowed).action_send()
        self.assertEqual(msg.state, 'cancel')
        self.assertEqual(msg.error, 'Blocked for the test.')
        self.assertFalse(msg.next_try_at, "a consent decision is never retried")
        self.assertEqual(allowed.state, 'simulated')

    def test_by_default_nothing_is_blocked(self):
        conv = self._conv()
        self.assertFalse(self._message(conv)._send_blocked_reason())

    # ------------------------------------------------------------------ receipts
    def test_receipts_move_a_message_forward_only(self):
        """Meta delivers these out of order; a late 'delivered' must not undo a 'read'."""
        conv = self._conv()
        conv._note_inbound()
        msg = self._message(conv)
        msg.write({'state': 'sent', 'external_id': 'wamid.TEST'})

        self.Message._apply_status('wamid.TEST', 'read')
        self.assertEqual(msg.state, 'read')
        self.assertTrue(msg.read_at)
        # A read receipt implies delivery even when that callback never arrived.
        self.assertTrue(msg.delivered_at)

        self.Message._apply_status('wamid.TEST', 'delivered')
        self.assertEqual(msg.state, 'read', "a late receipt must not downgrade a read message")

    def test_a_failed_receipt_records_the_reason(self):
        conv = self._conv()
        msg = self._message(conv)
        msg.write({'state': 'sent', 'external_id': 'wamid.FAIL'})
        self.Message._apply_status('wamid.FAIL', 'failed', error='Number not on WhatsApp',
                                   error_code='131026')
        self.assertEqual(msg.state, 'error')
        self.assertEqual(msg.error_code, '131026')

    # ------------------------------------------------------------------ signature
    def test_a_correctly_signed_payload_is_accepted(self):
        body = json.dumps({'hello': 'world'}).encode()
        sig = 'sha256=' + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        self.assertTrue(self.account._valid_signature(body, sig))

    def test_a_forged_payload_is_rejected(self):
        body = json.dumps({'hello': 'world'}).encode()
        bad = 'sha256=' + hmac.new(b'wrong-secret', body, hashlib.sha256).hexdigest()
        self.assertFalse(self.account._valid_signature(body, bad))

    def test_a_sender_with_no_app_secret_accepts_nothing(self):
        """Fail closed. An unverifiable callback could forge an inbound message, and a
        forged inbound message opens the free-form window for a number of the
        attacker's choosing."""
        self.account.app_secret = False
        body = b'{}'
        self.assertFalse(self.account._valid_signature(body, 'sha256=anything'))

    # ------------------------------------------------------------------ retry
    def test_a_failure_schedules_a_retry_then_eventually_gives_up(self):
        conv = self._conv()
        msg = self._message(conv)
        for expected in range(1, 5):
            msg._fail('boom')
            self.assertEqual(msg.try_count, expected)
            self.assertTrue(msg.next_try_at, "a retry should be scheduled")
        msg._fail('boom')
        self.assertFalse(msg.next_try_at,
                         "after the last attempt it must stop retrying, or one bad "
                         "number becomes a permanent background load")
