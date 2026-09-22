# -*- coding: utf-8 -*-
"""Answers that need a person, and the tray they wait in. (client, 2026-09-18)

A tapped "⚠️ Something is missing" used to be a chatter note in a conversation
nobody had open. It is now a to-do on the case and a line on the Desk until
somebody answers it.
"""
from odoo.tests import TransactionCase, tagged

from .test_link_channel import BROWSER, LinkSetup


@tagged('post_install', '-at_install')
class TestInbox(LinkSetup, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_link()
        cls.Desk = cls.env['epg.whatsapp.desk']
        cls.template.write({'quick_replies': '✅ All fine\n⚠️ Something is missing',
                            'alert_replies': '⚠️ Something is missing'})

    def _sent(self):
        message = self.template.send(self.partner)
        message.action_mark_sent()
        return message

    def test_an_answer_that_needs_a_person_becomes_a_to_do(self):
        message = self._sent()
        message._register_quick_reply(1, BROWSER)
        self.assertFalse(self.partner.activity_ids, "“All fine” asks for nothing")
        second = self._sent()
        second._register_quick_reply(2, BROWSER)
        activity = self.partner.activity_ids
        self.assertEqual(len(activity), 1)
        self.assertIn('Something is missing', activity.summary)
        self.assertIn(self.partner.display_name, activity.summary)

    def test_the_to_do_goes_to_whoever_the_record_belongs_to(self):
        owner = self.env['res.users'].create({
            'name': 'Route Rep', 'login': 'wa_inbox_rep',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        self.partner.user_id = owner
        message = self._sent()
        message._register_quick_reply(2, BROWSER)
        self.assertEqual(self.partner.activity_ids.user_id, owner)
        # The template may name somebody else - accounts, the lab manager.
        chosen = self.env['res.users'].create({
            'name': 'Accounts', 'login': 'wa_inbox_acc',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        self.template.alert_user_id = chosen
        again = self._sent()
        again._register_quick_reply(2, BROWSER)
        self.assertIn(chosen, self.partner.activity_ids.mapped('user_id'))

    def test_every_reply_waits_in_the_tray(self):
        message = self._sent()
        message._register_quick_reply(1, BROWSER)
        desk = self.Desk.get_desk()
        [row] = [r for r in desk['inbox'] if r['partner_id'] == self.partner.id]
        self.assertEqual(row['text'], '✅ All fine')
        self.assertEqual(row['about'], self.template.name)
        self.assertTrue(row['when'])

    def test_answering_the_doctor_clears_the_tray(self):
        message = self._sent()
        message._register_quick_reply(1, BROWSER)
        reply = message.reply_ids
        self.assertFalse(reply.handled_at)
        answer = self.template.send(self.partner)
        answer.action_mark_sent()
        self.assertTrue(reply.handled_at, "we answered them: nothing is waiting")
        self.assertFalse([r for r in self.Desk.get_desk()['inbox'] if r['id'] == reply.id])

    def test_a_reply_can_be_ticked_off_by_hand(self):
        message = self._sent()
        message._register_quick_reply(1, BROWSER)
        reply = message.reply_ids
        self.Desk.mark_handled(reply.id)
        self.assertTrue(reply.handled_at)
        self.assertEqual(reply.handled_by_id, self.env.user)
        before = reply.handled_at
        self.Desk.mark_handled(reply.id)
        self.assertEqual(reply.handled_at, before, "ticking twice changes nothing")

    def test_the_page_carries_the_facts_a_module_gives_it(self):
        message = self._sent()
        self.assertEqual(message._page()['facts'], [], "nothing to say about a contact")
