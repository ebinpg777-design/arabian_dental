# -*- coding: utf-8 -*-
"""Smarter free sending: one WhatsApp per doctor, best time and snooze, reminders for
unopened documents, campaigns, warnings and history. (client, 2026-09-17)"""
from datetime import datetime, time, timedelta
from unittest.mock import patch

import pytz

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import HttpCase, TransactionCase, tagged

from .test_link_channel import BROWSER, LinkSetup

TZ = pytz.timezone('Asia/Kolkata')


def number_vals(number):
    """The contact field messages go to: WhatsApp Number where the lab has it."""
    return {'phone': number, 'whatsapp_number': number}


class SmartSetup(LinkSetup):

    @classmethod
    def _setup_smart(cls):
        cls._setup_link()
        cls.env.user.tz = 'Asia/Kolkata'
        cls.Message = cls.env['epg.whatsapp.message'].with_context(tz='Asia/Kolkata')

    def _partner(self, name, number):
        vals = {'name': name}
        vals.update({k: v for k, v in number_vals(number).items()
                     if k in self.env['res.partner']._fields})
        return self.env['res.partner'].create(vals)

    def _sent(self, message, hours_ago=0):
        message.action_mark_sent()
        message.sent_at = fields.Datetime.now() - timedelta(hours=hours_ago)
        return message


@tagged('post_install', '-at_install')
class TestSmartSend(SmartSetup, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_smart()

    # ------------------------------------------------------------------ bundles
    def test_two_messages_for_one_doctor_go_as_one(self):
        first = self.template.send(self.partner)
        self._attach(first)
        second = self.template.send(self.partner)
        payload = (first | second).get_open_payload()
        self.assertEqual(sorted(payload['ids']), sorted((first | second).ids))
        self.assertEqual(payload['text'].count('Dear Dr Link, your work is ready.'), 2)
        self.assertEqual(payload['text'].count('_The Lab_'), 1, "the footer is said once")
        self.assertTrue(payload['text'].endswith('_The Lab_'))
        self.assertIn('┈┈┈', payload['text'])
        self.assertEqual(len(payload['replies']), 4)
        before = len(self.partner.message_ids)
        (first | second).action_mark_sent()
        self.assertEqual(set((first | second).mapped('state')), {'sent'})
        self.assertEqual(len(self.partner.message_ids), before + 2)

    def test_a_bundle_is_for_one_number_only(self):
        other = self.env['res.partner'].create({'name': 'Dr Other', 'phone': '+91 98765 22222'})
        with self.assertRaises(UserError):
            (self.template.send(self.partner) | self.template.send(other)).get_open_payload()

    def test_a_refused_message_leaves_the_rest_of_the_bundle(self):
        good = self.template.send(self.partner)
        bad = self.template.send(self.partner)
        original = type(self.Message)._send_blocked_reason

        def blocked(message):
            return 'Opted out' if message.id == bad.id else original(message)
        with patch.object(type(self.Message), '_send_blocked_reason', blocked):
            payload = (good | bad).get_open_payload()
        self.assertEqual(payload['ids'], good.ids)
        self.assertEqual(payload['blocked_others'], ['Opted out'])
        self.assertEqual(bad.state, 'cancel')

    def test_the_desk_groups_by_doctor(self):
        first = self.template.send(self.partner)
        second = self.template.send(self.partner)
        group = next(g for g in self.env['epg.whatsapp.desk'].get_desk('all')['groups']
                     if first.id in g['ids'])
        self.assertEqual(sorted(group['ids']), sorted((first | second).ids))
        self.assertEqual(group['count'], 2)

    # ------------------------------------------------------------------ snooze / best time
    def test_snooze_takes_it_off_the_desk_until_then(self):
        message = self.template.send(self.partner)
        Desk = self.env['epg.whatsapp.desk']
        label = message.with_context(tz='Asia/Kolkata').action_snooze('tomorrow')
        self.assertTrue(label)
        local = pytz.utc.localize(message.scheduled_at).astimezone(TZ)
        now_local = pytz.utc.localize(fields.Datetime.now()).astimezone(TZ)
        self.assertEqual(local.date(), now_local.date() + timedelta(days=1))
        self.assertEqual((local.hour, local.minute), (9, 0))
        desk = Desk.get_desk('all')
        self.assertNotIn(message.id, [i for g in desk['groups'] for i in g['ids']])
        self.assertIn(message.id, [c['id'] for c in desk['later']])
        # Due tomorrow: not dropped for being old today.
        message.ready_at = fields.Datetime.now() - timedelta(hours=100)
        message.scheduled_at = fields.Datetime.now() + timedelta(hours=5)
        self.Message._expire_ready()
        self.assertEqual(message.state, 'ready')
        message.action_unsnooze()
        self.assertFalse(message.scheduled_at)

    def test_snoozing_an_opened_message_puts_it_back_as_ready(self):
        message = self.template.send(self.partner)
        message.action_mark_opened()
        message.action_snooze('hour')
        self.assertEqual(message.state, 'ready')
        self.assertFalse(message.opened_at)

    def test_best_time_is_learned_from_opens_and_replies(self):
        with self.assertRaises(UserError):
            self.template.send(self.partner).action_snooze('best')
        yesterday = pytz.utc.localize(fields.Datetime.now()).astimezone(TZ).date() - timedelta(days=1)
        for minute in (5, 20, 40):
            old = self._sent(self.template.send(self.partner), hours_ago=30)
            old.seen_at = TZ.localize(datetime.combine(yesterday, time(20, minute))).astimezone(
                pytz.utc).replace(tzinfo=None)
        best = self.Message._best_times([old.number])[old.number]
        self.assertEqual(best['hour'], 20)
        self.assertIn('8 PM', best['label'])
        waiting = self.template.send(self.partner)
        waiting.with_context(tz='Asia/Kolkata').action_snooze('best')
        self.assertEqual(pytz.utc.localize(waiting.scheduled_at).astimezone(TZ).hour, 20)
        payload = waiting.get_open_payload()
        self.assertEqual(payload['best_time']['hour'], 20)

    # ------------------------------------------------------------------ warnings / history
    def test_the_same_notice_twice_is_warned_about(self):
        self._sent(self.template.send(self.partner), hours_ago=3)
        again = self.template.send(self.partner)
        warnings = again.get_open_payload()['warnings']
        self.assertTrue(any('already sent' in w for w in warnings), warnings)
        recent = self._sent(self.template.send(self.partner))
        recent.sent_at = fields.Datetime.now() - timedelta(minutes=4)
        warnings = self.template.send(self.partner).get_open_payload()['warnings']
        self.assertTrue(any('minutes ago' in w for w in warnings), warnings)

    def test_history_shows_what_went_before(self):
        old = self._sent(self.template.send(self.partner), hours_ago=5)
        old.log_reply('Thanks!')
        payload = self.template.send(self.partner).get_open_payload()
        texts = [h['text'] for h in payload['history']]
        self.assertIn('Thanks!', texts)
        self.assertIn(old.body[:160], texts)
        self.assertTrue(any(h['inbound'] for h in payload['history']))

    # ------------------------------------------------------------------ reminders
    def _with_document(self):
        self.template.write({'nudge_after_hours': 24,
                             'nudge_text': 'Dear {{name}}, a gentle reminder.'})
        message = self.template.send(self.partner)
        self._attach(message)
        return self._sent(message, hours_ago=25)

    def test_an_unopened_document_gets_one_reminder(self):
        original = self._with_document()
        nudges = self.Message._cron_nudge_unopened()
        self.assertEqual(len(nudges), 1)
        self.assertEqual(nudges.state, 'ready')
        self.assertEqual(nudges.nudge_of_id, original)
        self.assertEqual(nudges.attachment_id, original.attachment_id)
        self.assertTrue(original.nudge_created)
        text = nudges.get_open_payload()['text']
        self.assertTrue(text.startswith('Dear Dr Link, a gentle reminder.'), text)
        self.assertIn('/wa/d/', text)
        self.assertNotIn('Tap to reply', text)
        self.assertFalse(self.Message._cron_nudge_unopened(), "one reminder, not one a run")

    def test_a_document_opened_in_time_is_not_reminded(self):
        original = self._with_document()
        original._register_seen(BROWSER)
        self.assertFalse(self.Message._cron_nudge_unopened())

    def test_the_reminder_stands_down_when_the_original_is_opened(self):
        original = self._with_document()
        nudge = self.Message._cron_nudge_unopened()
        original._register_seen(BROWSER)
        self.assertEqual(nudge.state, 'cancel')

    def test_opening_the_reminder_counts_for_the_original(self):
        original = self._with_document()
        nudge = self.Message._cron_nudge_unopened()
        nudge.action_mark_sent()
        nudge._register_seen(BROWSER)
        self.assertEqual(original.state, 'read')
        self.assertTrue(original.seen_at)

    # ------------------------------------------------------------------ campaigns
    def _campaign(self, **vals):
        partners = self._partner('Camp One', '+91 98700 00001') | \
            self._partner('Camp Two', '+91 98700 00002') | \
            self._partner('Camp Twin', '+91 98700 00002') | \
            self.env['res.partner'].create({'name': 'Camp Nobody'})
        return self.env['epg.whatsapp.campaign'].create(dict({
            'name': 'Holidays', 'account_id': self.account.id,
            'partner_ids': [(6, 0, partners.ids)],
            'body': 'Dear {{name}}, we are closed on Friday.',
            'link_url': 'https://example.com/holidays', 'link_label': 'Details',
            'quick_replies': '👍 Noted', 'daily_limit': 1,
        }, **vals))

    def test_a_campaign_is_paced_and_personal(self):
        campaign = self._campaign()
        self.assertEqual(campaign.recipient_count, 4)
        self.assertEqual(campaign.reachable_count, 3)
        self.assertIn('Dear Camp One, we are closed on Friday.', campaign.preview)
        campaign.action_launch()
        self.assertEqual(campaign.state, 'running')
        messages = campaign.message_ids.sorted('id')
        self.assertEqual(len(messages), 2, "one per number: the twin shares a number")
        self.assertEqual(set(messages.mapped('state')), {'ready'})
        self.assertFalse(messages[0].scheduled_at)
        tomorrow = pytz.utc.localize(messages[1].scheduled_at).astimezone(TZ)
        self.assertEqual(tomorrow.hour, 10)
        text = messages[0].get_open_payload()['text']
        self.assertIn('Dear Camp One', text)
        self.assertIn('🔗 Details', text)
        self.assertIn('/wa/l/', text)
        self.assertIn('/wa/o/', text)
        self.assertIn('👍 Noted', text)
        self.assertEqual(campaign.queued_count, 2)
        with self.assertRaises(UserError):
            campaign.action_launch()

    def test_a_campaign_link_click_and_stop(self):
        campaign = self._campaign(daily_limit=10)
        campaign.action_launch()
        first, second = campaign.message_ids.sorted('id')
        first.action_mark_sent()
        self.assertFalse(first._register_click('facebookexternalhit/1.1'))
        self.assertTrue(first._register_click(BROWSER))
        self.assertEqual(first.state, 'read')
        second.action_mark_sent()
        queued = self.env['epg.whatsapp.message'].create({
            'account_id': self.account.id, 'number': second.number, 'body': 'later'})
        queued.action_send()
        self.assertEqual(queued.state, 'ready')
        self.assertTrue(second._register_opt_out())
        # A stop under an offer stops offers: the contact's marketing switch, not
        # the number - what is queued about their own work still goes.
        self.assertFalse(second.partner_id.whatsapp_marketing_optin)
        self.assertFalse(second.conversation_id.opt_out)
        self.assertEqual(queued.state, 'ready', "a message about their own work still goes")
        campaign.invalidate_recordset()
        self.assertEqual((campaign.sent_count, campaign.clicked_count, campaign.optout_count),
                         (2, 1, 1))
        self.assertFalse(second._register_opt_out(), "a second stop changes nothing")

    def test_stopping_a_campaign_cancels_what_has_not_gone(self):
        campaign = self._campaign()
        campaign.action_launch()
        campaign.action_stop()
        self.assertEqual(set(campaign.message_ids.mapped('state')), {'cancel'})
        self.assertEqual(campaign.state, 'cancel')

    def test_a_campaign_needs_a_free_sender_and_a_real_link(self):
        campaign = self._campaign(link_url='javascript:alert(1)')
        with self.assertRaises(UserError):
            campaign.action_launch()


@tagged('post_install', '-at_install')
class TestSmartRoutes(SmartSetup, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_smart()

    def _launched(self):
        partner = self._partner('Route Camp', '+91 98700 00009')
        campaign = self.env['epg.whatsapp.campaign'].create({
            'name': 'Route', 'account_id': self.account.id, 'partner_ids': [(6, 0, partner.ids)],
            'body': 'Hello', 'link_url': 'https://example.com/x'})
        campaign.action_launch()
        message = campaign.message_ids
        message.action_mark_sent()
        return message

    def test_the_tracked_link_redirects_and_counts(self):
        message = self._launched()
        response = self.url_open('/wa/l/%s' % message.sudo().access_token,
                                 headers={'User-Agent': BROWSER}, allow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], 'https://example.com/x')
        message.invalidate_recordset()
        self.assertTrue(message.clicked_at)

    def test_the_stop_link_asks_before_it_stops(self):
        message = self._launched()
        url = '/wa/o/%s' % message.sudo().access_token
        page = self.url_open(url, headers={'User-Agent': 'WhatsApp/2.23'})
        self.assertEqual(page.status_code, 200)
        self.assertIn('Too many messages', page.text)
        message.invalidate_recordset()
        self.assertFalse(message.opted_out_at, "a GET never opts anyone out")
        done = self.url_open(url, data={'confirm': '1'}, headers={'User-Agent': BROWSER})
        self.assertEqual(done.status_code, 200)
        self.assertIn('unsubscribed', done.text)
        message.invalidate_recordset()
        self.assertTrue(message.opted_out_at)
        self.assertFalse(message.partner_id.whatsapp_marketing_optin, "offers stop")
        self.assertFalse(message.conversation_id.opt_out, "their own updates still come")
        self.assertIn('own cases and invoices still come', done.text)
