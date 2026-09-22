# -*- coding: utf-8 -*-
"""Asking a doctor to release a case the bench has put down. (client, 2026-09-19)

A held case cannot move until the doctor says something, and nothing told them
it was waiting - it simply aged in a queue. Four ways of asking the same
question, because the right one depends on who is being written to and how long
it has been: the plain ask, the same with the case laid out, a soft nudge, and
one that answers itself with taps.
"""
from odoo.tests import TransactionCase, tagged

from odoo.addons.lab_whatsapp.models.epg_whatsapp_template import HOLD_EVENTS


@tagged('post_install', '-at_install')
class TestHoldMessages(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Template = cls.env['epg.whatsapp.template']
        cls.doctor = cls.env['res.partner'].create({
            'name': 'Dr Holdup', 'whatsapp_number': '+91 90000 33333'})
        cls.order = cls.env['sale.order'].create({
            'partner_id': cls.doctor.id, 'patient': 'Meera K',
            'appliance_type': 'removable'})

    def _template(self, event):
        return self.Template._find('sale.order', event)

    def test_all_four_ways_of_asking_exist_on_the_order(self):
        for event in HOLD_EVENTS:
            template = self._template(event)
            self.assertTrue(template, "%s is missing" % event)
            self.assertEqual(template.model, 'sale.order')

    def test_each_one_says_which_case_and_to_whom(self):
        """A message a doctor cannot tie to a patient is a message they ignore."""
        for event in HOLD_EVENTS:
            body = self._template(event).body
            self.assertIn('{{partner_id.name|doctor}}', body, event)
            self.assertIn('{{name}}', body, event)
            self.assertIn('{{patient}}', body, event)

    def test_the_render_fills_the_case_in(self):
        rendered = self._template('hold_ask').render(self.order)
        self.assertIn(self.order.name, rendered)
        self.assertIn('Meera K', rendered)
        self.assertIn('Holdup', rendered)
        self.assertNotIn('{{', rendered, "nothing left unresolved")

    def test_the_detailed_one_names_the_reason_and_the_work(self):
        self.order.write({'hold_reason': 'missing_info'})
        rendered = self._template('hold_details').render(self.order)
        self.assertIn('Missing Information', rendered, "the label, not the key")
        self.assertIn('Removable', rendered)
        self.assertNotIn('{{', rendered)

    def test_the_one_with_choices_offers_them_as_taps(self):
        replies = self._template('hold_options').quick_replies.splitlines()
        self.assertEqual(len(replies), 4)
        self.assertTrue(any('Start it now' in r for r in replies))
        # Anything but "start now" needs a person to see it, or a doctor asking
        # to cancel is answered by nobody.
        alerts = self._template('hold_options').alert_replies
        self.assertIn('Cancel the case', alerts)
        self.assertNotIn('Start it now', alerts)

    def test_a_held_case_opens_on_the_hold_message_not_the_welcome(self):
        self.assertEqual(self.order._whatsapp_suggested_event(), 'manual')
        self.order.write({'verification_state': 'on_hold'})
        self.assertEqual(self.order._whatsapp_suggested_event(), 'hold_ask')
        self.assertEqual(self.order._whatsapp_suggested_template(),
                         self._template('hold_ask'))
