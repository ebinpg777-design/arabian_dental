# -*- coding: utf-8 -*-
"""Pay now, saved replies, engagement, the number safety meter, Chats and the chat
import. (client, 2026-09-17)"""
import base64
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import HttpCase, TransactionCase, tagged

from .test_link_channel import BROWSER
from .test_smart_send import SmartSetup, number_vals

ANDROID_EXPORT = """12/09/26, 8:05 pm - Lab WhatsApp: Dear Dr Link, your work is ready.
12/09/26, 8:07 pm - Dr Link: Received, thank you
12/09/26, 8:08 pm - Dr Link: Will the next case
be ready by Monday?
13/09/26, 9:15 am - Lab WhatsApp: Yes, Monday afternoon.
13/09/26, 9:16 am - Dr Link: <Media omitted>
"""
IOS_EXPORT = """[14/09/26, 6:40:12 PM] Dr Link: Please share the invoice again
[14/09/26, 6:41:03 PM] Lab WhatsApp: Sending it now
"""


class PaySetup(SmartSetup):

    @classmethod
    def _setup_pay(cls):
        cls._setup_smart()
        cls.account.write({'upi_id': 'lab@upi', 'upi_payee_name': 'The Lab',
                           'daily_send_limit': 2})
        # `color` is an integer every contact has: it stands in for an amount here.
        cls.partner.color = 4800
        cls.partner.write({k: v for k, v in number_vals('+91 98765 11111').items()
                           if k in cls.partner._fields})
        cls.template.write({'add_payment_link': True, 'payment_amount_field': 'color',
                            'payment_reference_field': 'name'})


@tagged('post_install', '-at_install')
class TestPayAndChats(PaySetup, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_pay()

    # ------------------------------------------------------------------ pay now
    def test_the_message_carries_a_pay_link_with_the_amount(self):
        message = self.template.send(self.partner)
        self.assertEqual(message.pay_amount, 4800.0)
        self.assertEqual(message.pay_reference, 'Dr Link')
        text = message.get_open_payload()['text']
        self.assertIn('💳 Pay', text)
        self.assertIn('/wa/p/%s' % message.sudo().access_token, text)
        page = message.get_pay_page()
        self.assertEqual(page['upi_id'], 'lab@upi')
        self.assertTrue(page['qr'].startswith('<svg'))
        upi = next(a for a in page['apps'] if a['key'] == 'upi')
        self.assertTrue(upi['url'].startswith('upi://pay?pa=lab@upi&pn=The%20Lab&am=4800.00'))
        self.assertIn('tn=Dr%20Link', upi['url'])
        self.assertEqual({a['key'] for a in page['apps']}, {'upi', 'gpay', 'phonepe', 'paytm'})

    def test_no_upi_id_means_no_pay_link(self):
        self.account.upi_id = False
        message = self.template.send(self.partner)
        self.assertFalse(message.pay_amount)
        self.assertNotIn('/wa/p/', message.get_open_payload()['text'])

    def test_a_bad_amount_path_is_warned_about(self):
        self.template.payment_amount_field = 'nope'
        self.assertFalse(self.template.placeholder_ok)
        self.assertIn("amount field 'nope'", self.template.placeholder_warnings)

    def test_opening_the_pay_page_and_saying_paid_are_logged_once(self):
        message = self.template.send(self.partner)
        message.action_mark_sent()
        self.assertFalse(message._register_pay_open('WhatsApp/2.24'))
        self.assertTrue(message._register_pay_open(BROWSER))
        self.assertTrue(message.pay_opened_at)
        self.assertEqual(message.state, 'read')
        self.assertIn('opened the payment page', self.partner.message_ids[0].body)
        self.assertTrue(message._register_paid_claim())
        self.assertFalse(message._register_paid_claim())
        self.assertTrue(message.paid_claimed_at)
        self.assertEqual(len(message.reply_ids), 1)
        self.assertIn('Paid', message.reply_ids.body)
        self.assertIn('says they have paid', self.partner.message_ids[0].body)
        self.assertEqual(len(self.partner.message_ids.filtered(
            lambda m: 'says they have paid' in m.body)), 1)

    def test_a_claim_on_an_unsent_message_counts_for_nothing(self):
        message = self.template.send(self.partner)
        self.assertFalse(message._register_paid_claim())
        self.assertFalse(message.paid_claimed_at)

    # ------------------------------------------------------------------ safety
    def test_the_safety_meter_warns_past_the_comfortable_limit(self):
        Message = self.env['epg.whatsapp.message']
        before = Message._safety()['sent']          # the live database may have sent today
        self.assertEqual(Message._safety()['level'], 'ok')
        # The limit is the sum over every free sender in the database.
        senders = self.env['epg.whatsapp.account'].sudo().search([('channel', '=', 'link')])
        senders.write({'daily_send_limit': 1})
        for _i in range(len(senders)):
            self.template.send(self.partner).action_mark_sent()
        safety = Message._safety()
        self.assertEqual((safety['sent'], safety['limit'], safety['level']),
                         (before + len(senders), len(senders), 'high'))
        warnings = self.template.send(self.partner).get_open_payload()['warnings']
        self.assertTrue(any('comfortable' in w for w in warnings), warnings)
        self.assertEqual(self.env['epg.whatsapp.desk'].get_desk('all')['safety']['level'], 'high')

    # ------------------------------------------------------------------ saved replies
    def test_a_saved_reply_drops_in_with_the_record_filled(self):
        Snippet = self.env['epg.whatsapp.snippet']
        hours = Snippet.create({'name': 'Hours', 'body': 'We are open 9 to 6, {{name}}.'})
        Snippet.create({'name': 'Users only', 'body': 'x',
                        'model_id': self.env.ref('base.model_res_users').id})
        offered = [s['name'] for s in Snippet.for_model('res.partner')]
        self.assertIn('Hours', offered)
        self.assertNotIn('Users only', offered)
        self.assertEqual(hours.render('res.partner', self.partner.id),
                         'We are open 9 to 6, Dr Link.')
        composer = self.env['epg.whatsapp.composer'].create({
            'res_model': 'res.partner', 'res_id': self.partner.id,
            'number': '+91 98765 11111', 'body': 'Hello', 'account_id': self.account.id})
        composer.snippet_id = hours
        composer._onchange_snippet()
        self.assertEqual(composer.body, 'Hello\n\nWe are open 9 to 6, Dr Link.')
        self.assertFalse(composer.snippet_id)

    # ------------------------------------------------------------------ engagement
    def test_engagement_follows_what_the_doctor_does(self):
        Partner = self.env['res.partner']
        self.assertEqual(self.partner.whatsapp_engagement, 'never')
        message = self.template.send(self.partner)
        message.action_mark_sent()
        self.partner.invalidate_recordset()
        self.assertEqual(self.partner.whatsapp_engagement, 'quiet')
        self.assertIn(self.partner, Partner.search([('whatsapp_engagement', '=', 'quiet')]))
        self.assertNotIn(self.partner, Partner.search([('whatsapp_engagement', '=', 'never')]))
        self._attach(message)
        message._register_seen(BROWSER)
        self.partner.invalidate_recordset()
        self.assertEqual(self.partner.whatsapp_engagement, 'engaged')
        self.assertTrue(self.partner.whatsapp_last_seen)
        self.assertEqual(self.partner.whatsapp_sent_count, 1)
        self.assertIn(self.partner, Partner.search([('whatsapp_engagement', '=', 'engaged')]))
        message.conversation_id.write({'opt_out': True})
        self.partner.invalidate_recordset()
        self.assertEqual(self.partner.whatsapp_engagement, 'stopped')
        self.assertIn(self.partner, Partner.search([('whatsapp_engagement', '=', 'stopped')]))
        self.assertNotIn(self.partner, Partner.search([('whatsapp_engagement', '=', 'engaged')]))

    def test_a_campaign_can_keep_only_the_engaged(self):
        quiet = self._partner('Camp Quiet', '+91 98700 00011')
        self.template.send(quiet).action_mark_sent()
        fresh = self._partner('Camp Fresh', '+91 98700 00012')
        campaign = self.env['epg.whatsapp.campaign'].create({
            'name': 'Trim', 'account_id': self.account.id, 'body': 'x',
            'partner_ids': [(6, 0, (quiet | fresh).ids)]})
        self.assertEqual((campaign.quiet_count, campaign.stopped_count), (1, 0))
        campaign.action_drop_unengaged()
        self.assertEqual(campaign.partner_ids, fresh)

    # ------------------------------------------------------------------ chats
    def test_chats_list_and_thread(self):
        Desk = self.env['epg.whatsapp.desk']
        message = self.template.send(self.partner)
        message.action_mark_sent()
        message.log_reply('Thanks')
        chats = Desk.get_chats('Link')['chats']
        chat = next(c for c in chats if c['number'] == message.number)
        self.assertTrue(chat['awaiting'], "they wrote last")
        self.assertTrue(chat['last_inbound'])
        self.assertEqual(Desk.get_chats('zzz-nobody')['chats'], [])
        thread = Desk.get_thread(chat['id'])
        kinds = [b['kind'] for b in thread['bubbles']]
        self.assertEqual(kinds[0], 'day')
        msgs = [b for b in thread['bubbles'] if b['kind'] == 'msg']
        self.assertEqual([b['inbound'] for b in msgs], [False, True])
        self.assertEqual(msgs[0]['ticks'], 1)
        self.assertTrue(msgs[0]['pay'], "the Pay now amount rides on the bubble")
        self.assertFalse(msgs[1]['pay'])
        self.assertEqual(thread['partner_id'], self.partner.id)

    def test_composing_in_a_chat_creates_the_next_message(self):
        Desk = self.env['epg.whatsapp.desk']
        opened = Desk.get_chats('', self.partner.id)
        self.assertTrue(opened['open_id'], "a contact with a number gets a conversation")
        res = Desk.compose(opened['open_id'], 'Are you free on Monday?')
        message = self.env['epg.whatsapp.message'].browse(res['id'])
        self.assertEqual((res['channel'], message.state), ('link', 'ready'))
        self.assertEqual(message.partner_id, self.partner)
        self.assertEqual(message.user_id, self.env.user)
        reply_id = Desk.log_inbound(opened['open_id'], 'Yes, after 4')
        reply = self.env['epg.whatsapp.message'].browse(reply_id)
        self.assertEqual(reply.direction, 'inbound')
        self.assertIn('Yes, after 4', self.partner.message_ids[0].body)
        message.conversation_id.opt_out = True
        with self.assertRaises(UserError):
            Desk.compose(opened['open_id'], 'One more')

    # ------------------------------------------------------------------ import
    def _wizard(self, content, **vals):
        return self.env['epg.whatsapp.chat.import'].create(dict({
            'partner_id': self.partner.id,
            'file': base64.b64encode(content.encode()),
            'filename': 'WhatsApp Chat with Dr Link.txt',
        }, **vals))

    def test_an_exported_chat_brings_the_doctors_words_in(self):
        message = self.template.send(self.partner)
        message.action_mark_sent()
        wizard = self._wizard(ANDROID_EXPORT)
        self.assertEqual(wizard.line_count, 4, "the media line is skipped")
        self.assertEqual(wizard.doctor_name, 'Dr Link', "the name that is not ours")
        self.assertIn('Dr Link (2)', wizard.senders)
        wizard.action_import()
        inbound = self.env['epg.whatsapp.message'].search(
            [('partner_id', '=', self.partner.id), ('direction', '=', 'inbound')],
            order='sent_at')
        self.assertEqual(inbound.mapped('body'),
                         ['Received, thank you', 'Will the next case\nbe ready by Monday?'])
        self.assertEqual(inbound[0].sent_at.strftime('%Y-%m-%d %H:%M'), '2026-09-12 14:37',
                         "8:05 pm Kolkata, stored in UTC")
        self.assertTrue(message.conversation_id.last_inbound_at)
        self.assertTrue(self.env['ir.attachment'].search_count(
            [('res_model', '=', 'res.partner'), ('res_id', '=', self.partner.id),
             ('name', 'ilike', 'WhatsApp Chat')]))
        self.assertIn('imported', self.partner.message_ids[0].body)
        # Importing again adds nothing: every line is already known.
        self._wizard(ANDROID_EXPORT).action_import()
        self.assertEqual(self.env['epg.whatsapp.message'].search_count(
            [('partner_id', '=', self.partner.id), ('direction', '=', 'inbound')]), 2)

    def test_an_iphone_export_and_our_own_lines(self):
        wizard = self._wizard(IOS_EXPORT, include_ours=True, doctor_name='Dr Link')
        self.assertEqual(wizard.line_count, 2)
        wizard.action_import()
        rows = self.env['epg.whatsapp.message'].search(
            [('partner_id', '=', self.partner.id)], order='sent_at')
        self.assertEqual(rows.mapped('direction'), ['inbound', 'outbound'])
        self.assertEqual(rows[1].state, 'sent')

    def test_an_empty_or_foreign_file_is_refused(self):
        with self.assertRaises(UserError):
            self._wizard('just some notes\nno chat here', doctor_name='x').action_import()


@tagged('post_install', '-at_install')
class TestPayRoutes(PaySetup, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_pay()

    def test_the_pay_page_and_the_paid_button(self):
        message = self.template.send(self.partner)
        message.action_mark_sent()
        url = '/wa/p/%s' % message.sudo().access_token
        page = self.url_open(url, headers={'User-Agent': BROWSER})
        self.assertEqual(page.status_code, 200)
        self.assertIn('upi://pay?pa=lab@upi', page.text)
        self.assertIn('Or scan from another phone', page.text)
        message.invalidate_recordset()
        self.assertTrue(message.pay_opened_at)
        done = self.url_open(url, data={'confirm': '1'}, headers={'User-Agent': BROWSER})
        self.assertEqual(done.status_code, 200)
        self.assertIn('noted your payment', done.text)
        message.invalidate_recordset()
        self.assertTrue(message.paid_claimed_at)
        plain = self.template.send(self.partner)
        plain.sudo().write({'pay_amount': 0})
        self.assertEqual(self.url_open('/wa/p/%s' % plain.sudo().access_token).status_code, 404)


@tagged('post_install', '-at_install')
class TestReviewFixes(PaySetup, TransactionCase):
    """What the code review of 2026-09-17 pinned down."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_pay()

    def _sent_with_nudge(self):
        self.template.write({'nudge_after_hours': 1, 'nudge_text': 'Reminder.'})
        message = self.template.send(self.partner)
        self._attach(message)
        message.action_mark_sent()
        message.sent_at = fields.Datetime.now() - timedelta(hours=2)
        nudge = self.env['epg.whatsapp.message']._cron_nudge_unopened()
        self.assertEqual(nudge.state, 'ready')
        return message, nudge

    def test_opening_the_payment_page_stands_the_reminder_down(self):
        message, nudge = self._sent_with_nudge()
        message._register_pay_open(BROWSER)
        self.assertEqual(message.state, 'read')
        self.assertEqual(nudge.state, 'cancel')
        self.assertEqual(message.seen_count, 0, "the payment page is not a document open")

    def test_tapping_a_campaign_link_stands_the_reminder_down(self):
        message, nudge = self._sent_with_nudge()
        message._register_click(BROWSER)
        self.assertEqual(nudge.state, 'cancel')
        self.assertTrue(message.clicked_at)

    def test_a_month_first_export_is_read_month_first(self):
        us = ("[9/12/26, 8:05:14 PM] Dr Link: Received\n"
              "[9/25/26, 9:00:00 AM] Dr Link: Any update?\n")
        wizard = self._wizard(us, doctor_name='Dr Link')
        rows = wizard._parse(wizard._content())
        self.assertEqual([r[0].strftime('%Y-%m-%d') for r in rows], ['2026-09-12', '2026-09-25'])
        indian = "25/09/26, 9:00 am - Dr Link: Any update?\n12/09/26, 8:05 pm - Dr Link: Received\n"
        rows = self._wizard(indian, doctor_name='Dr Link')._parse(indian)
        self.assertEqual([r[0].strftime('%Y-%m-%d') for r in rows], ['2026-09-25', '2026-09-12'])
        undecidable = "09/12/26, 8:05 pm - Dr Link: Received\n"
        rows = self._wizard(undecidable, doctor_name='Dr Link')._parse(undecidable)
        self.assertEqual(rows[0][0].strftime('%Y-%m-%d'), '2026-12-09', "day first by default")

    def _wizard(self, content, **vals):
        return self.env['epg.whatsapp.chat.import'].create(dict({
            'partner_id': self.partner.id,
            'file': base64.b64encode(content.encode()),
            'filename': 'chat.txt',
        }, **vals))

    def test_campaign_stats_count_in_the_database(self):
        partner = self._partner('Stats One', '+91 98700 00021')
        campaign = self.env['epg.whatsapp.campaign'].create({
            'name': 'Stats', 'account_id': self.account.id, 'body': 'x',
            'partner_ids': [(6, 0, partner.ids)]})
        campaign.action_launch()
        message = campaign.message_ids
        message.action_mark_sent()
        message._register_click(BROWSER)
        message.log_reply('ok')
        campaign.invalidate_recordset()
        self.assertEqual((campaign.sent_count, campaign.clicked_count, campaign.replied_count,
                          campaign.queued_count, campaign.optout_count), (1, 1, 1, 0, 0))

    def test_a_reply_finds_its_conversation_even_when_the_message_had_none(self):
        message = self.env['epg.whatsapp.message'].sudo().create({
            'account_id': self.account.id, 'partner_id': self.partner.id,
            'number': '919876511111', 'body': 'made by hand', 'state': 'sent'})
        self.assertFalse(message.conversation_id)
        reply = message.log_reply('Noted')
        reply = self.env['epg.whatsapp.message'].browse(reply)
        self.assertTrue(reply.conversation_id)
        self.assertEqual(reply.conversation_id, message.conversation_id)
        self.assertTrue(message.conversation_id.last_inbound_at)
