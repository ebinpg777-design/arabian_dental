# -*- coding: utf-8 -*-
"""A doctor who opts out hears nothing more — from an event, the composer or a retry.

Unticking WhatsApp Opt-in only added the number to phone.blacklist, and nothing read
either before sending. And archiving an event's template made the event fall back to
the model's generic template, so the reminder cron kept chasing everyone with generic
text. (2026-09-15)

No network: the sender is in simulation mode, and event sends are deferred to the queue.
"""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestWhatsappConsent(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.account = cls.env['epg.whatsapp.account'].create({
            'name': 'Consent Sender', 'phone_number_id': '424242',
            'access_token': 'tok', 'simulation_mode': True, 'channel': 'cloud_api'})
        cls.Message = cls.env['epg.whatsapp.message']
        cls.Template = cls.env['epg.whatsapp.template']
        cls.partner_model = cls.env['ir.model']._get_id('res.partner')
        # 'payment' is not seeded for res.partner, so this is the only template the
        # event can find — the test does not depend on what staging has configured.
        cls.template = cls.Template.create({
            'name': 'Consent Event', 'account_id': cls.account.id,
            'model_id': cls.partner_model, 'event': 'payment',
            'phone_field': 'whatsapp_number', 'body': 'Dear {{name}}'})
        cls.env['ir.config_parameter'].sudo().set_param(
            'lab_whatsapp.notify_payment', 'True')
        cls.doctor = cls.env['res.partner'].create({
            'name': 'Dr Consent', 'whatsapp_number': '+91 98470 11111'})
        cls.number = cls.doctor._whatsapp_number()

    def _messages(self, partner):
        return self.Message.search([('res_model', '=', 'res.partner'),
                                    ('res_id', '=', partner.id)])

    def _direct(self, **vals):
        """A message as the composer creates it, ready to send inside the window."""
        message = self.Message.create(dict({
            'account_id': self.account.id, 'number': self.number, 'body': 'Hello'},
            **vals))
        self.env['epg.whatsapp.conversation']._get_or_create(
            self.account, message.number)._note_inbound()
        return message

    # ------------------------------------------------------------------ opt-in
    def test_an_opted_in_doctor_is_still_messaged(self):
        """The control for everything below: consent checks must not block a yes."""
        message = self._direct(partner_id=self.doctor.id)
        message.action_send()
        self.assertEqual(message.state, 'simulated')

    def test_an_event_for_an_opted_out_doctor_is_logged_as_cancelled(self):
        self.doctor.whatsapp_optin = False
        self.doctor._whatsapp_send_event('payment')
        messages = self._messages(self.doctor)
        self.assertEqual(len(messages), 1, "the refusal is on the log, not silent")
        self.assertEqual(messages.state, 'cancel')
        self.assertIn('opted out', messages.error)
        self.assertFalse(messages.next_try_at)

    def test_the_composer_path_checks_the_opt_in_too(self):
        """A number the doctor added after unticking is not on the blacklist, so the
        tick itself has to be read."""
        self.doctor.whatsapp_optin = False
        self.doctor.whatsapp_number = '+91 98470 22222'
        message = self._direct(partner_id=self.doctor.id,
                               number=self.doctor._whatsapp_number())
        message.action_send()
        self.assertEqual(message.state, 'cancel')

    def test_a_retry_does_not_get_round_the_opt_out(self):
        message = self._direct(partner_id=self.doctor.id)
        self.doctor.whatsapp_optin = False
        message.action_retry()
        self.assertEqual(message.state, 'cancel')

    def test_sending_a_cancelled_message_again_asks_again(self):
        """A refusal is not a licence: action_send on it must re-check consent."""
        message = self._direct(partner_id=self.doctor.id)
        self.doctor.whatsapp_optin = False
        message.action_send()
        self.assertEqual(message.state, 'cancel')
        message.action_send()
        self.assertEqual(message.state, 'cancel')

    def test_the_queue_cancels_before_rendering_anything(self):
        self.doctor.whatsapp_optin = False
        message = self._direct(partner_id=self.doctor.id)
        self.assertEqual(message.state, 'draft')
        remaining = message._cancel_blocked()
        self.assertFalse(remaining)
        self.assertEqual(message.state, 'cancel')

    # ------------------------------------------------------------------ blacklist
    def test_unticking_blacklists_the_number_and_a_bare_send_respects_it(self):
        """No partner on the message at all: the blacklist alone must stop it."""
        self.doctor.whatsapp_optin = False
        self.assertTrue(self.env['phone.blacklist'].sudo().search_count(
            [('number', 'in', [self.number, '+' + self.number])]))
        message = self._direct()
        message.action_send()
        self.assertEqual(message.state, 'cancel')
        self.assertIn('blacklist', message.error)

    def test_a_blacklisted_number_blocks_an_opted_in_contact(self):
        self.env['phone.blacklist'].sudo()._add(['+' + self.number])
        self.assertTrue(self.doctor.whatsapp_optin)
        message = self._direct(partner_id=self.doctor.id)
        message.action_send()
        self.assertEqual(message.state, 'cancel')

    def test_ticking_back_in_lifts_the_block(self):
        self.doctor.whatsapp_optin = False
        self.doctor.whatsapp_optin = True
        message = self._direct(partner_id=self.doctor.id)
        message.action_send()
        self.assertEqual(message.state, 'simulated')

    # ------------------------------------------------------------------ fallback
    def test_an_event_without_its_template_sends_nothing(self):
        """No fallback to the generic template: archiving is how an event is stopped."""
        generic = self.env.ref('lab_whatsapp.tmpl_res_partner_manual')
        generic.active = True
        self.template.active = False
        self.assertFalse(self.Template._find('res.partner', 'payment'))
        self.doctor._whatsapp_send_event('payment')
        self.assertFalse(self._messages(self.doctor))

    def test_the_generic_template_is_still_found_when_asked_for_by_name(self):
        generic = self.env.ref('lab_whatsapp.tmpl_res_partner_manual')
        generic.active = True
        self.assertEqual(self.Template._find('res.partner', 'manual'), generic)


@tagged('post_install', '-at_install')
class TestStopLinkSwitchesTheContactOff(TransactionCase):
    """A doctor tapping a campaign's stop link is switched off for offers on the
    contact; a doctor who stops the number outright is switched off for everything."""

    def test_stop_link_clears_the_opt_in(self):
        account = self.env['epg.whatsapp.account'].create({'name': 'Stop Sender'})
        partner = self.env['res.partner'].create({
            'name': 'Dr Stop', 'whatsapp_number': '+91 98470 77777'})
        campaign = self.env['epg.whatsapp.campaign'].create({
            'name': 'Stop test', 'account_id': account.id,
            'partner_ids': [(6, 0, partner.ids)], 'body': 'News'})
        campaign.action_launch()
        message = campaign.message_ids
        message.action_mark_sent()
        message._register_opt_out()
        self.assertFalse(partner.whatsapp_marketing_optin, "no more offers")
        self.assertTrue(partner.whatsapp_optin, "their invoices and case updates still come")
        # The number itself stopped, by a STOP that is not under an offer.
        update = self.env.ref('lab_whatsapp.tmpl_res_partner_manual').send(partner, account=account)
        update.action_mark_sent()
        update._register_opt_out()
        self.assertFalse(partner.whatsapp_optin, "everything stops")
