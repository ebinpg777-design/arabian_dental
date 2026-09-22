# -*- coding: utf-8 -*-
"""WhatsApp app / Web: free sending through the lab's own WhatsApp. (client, 2026-09-17)

What is pinned here is the promise to the person at the desk and to the lab's books:
nothing leaves the server on its own, what was sent is logged on the record, and the
links carry back the only signals there are - the document opened, the answer tapped.
"""
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import quote

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, TransactionCase, tagged

BROWSER = 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/126 Mobile Safari/537.36'


class LinkSetup:

    @classmethod
    def _setup_link(cls):
        cls.account = cls.env['epg.whatsapp.account'].create({
            'name': 'Lab WhatsApp', 'whatsapp_number': '+91 90000 11111'})
        cls.partner = cls.env['res.partner'].create({
            'name': 'Dr Link', 'phone': '+91 98765 11111'})
        cls.template = cls.env['epg.whatsapp.template'].create({
            'name': 'Link Hello', 'model_id': cls.env.ref('base.model_res_partner').id,
            'account_id': cls.account.id, 'phone_field': 'phone',
            'header_text': 'Hello {{name}}',
            'body': 'Dear {{name}}, your work is ready.',
            'footer_text': 'The Lab',
            'quick_replies': '✅ Received\n📞 Call me',
        })
        cls.Message = cls.env['epg.whatsapp.message']

    def _attach(self, message):
        message.attachment_id = self.env['ir.attachment'].create({
            'name': 'Invoice 42.pdf', 'datas': 'JVBERi0xLjQK', 'mimetype': 'application/pdf',
            'res_model': message._name, 'res_id': message.id}).id


@tagged('post_install', '-at_install')
class TestLinkChannel(LinkSetup, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_link()

    def _chatter(self, record):
        return record.message_ids.filtered(lambda m: 'WhatsApp' in (m.body or '') or
                                           'replied' in (m.body or '') or
                                           'opened' in (m.body or ''))

    def test_a_new_sender_is_free_by_default(self):
        self.assertEqual(self.account.channel, 'link')

    def test_nothing_leaves_the_server_on_the_free_channel(self):
        with patch('odoo.addons.epg_whatsapp.models.epg_whatsapp_account.requests.post') as post:
            message = self.template.send(self.partner)
        post.assert_not_called()
        self.assertEqual(message.state, 'ready')
        self.assertEqual(message.channel, 'link')
        self.assertTrue(message.sudo().access_token)
        self.assertTrue(message.ready_at)
        self.assertTrue(message.link_expires_at > fields.Datetime.now())
        self.assertEqual(message._reply_labels(), ['✅ Received', '📞 Call me'])

    def test_a_deferred_event_is_prepared_by_the_queue(self):
        message = self.template.send(self.partner, defer=True)
        self.assertEqual(message.state, 'draft')
        with patch.object(self.env.cr, 'commit', lambda: None):
            self.Message._cron_send_queue()
        self.assertEqual(message.state, 'ready')

    def test_the_text_handed_to_whatsapp_has_everything(self):
        message = self.template.send(self.partner)
        self._attach(message)
        payload = message.get_open_payload()
        text = payload['text']
        token = message.sudo().access_token
        self.assertTrue(text.startswith('*Hello Dr Link*'))
        self.assertIn('Dear Dr Link, your work is ready.', text)
        self.assertIn('📄 Invoice 42.pdf', text)
        self.assertIn('/wa/d/%s' % token, text)
        self.assertIn('✅ Received → ', text)
        self.assertIn('/wa/r/%s/2' % token, text)
        self.assertTrue(text.endswith('_The Lab_'))
        self.assertEqual(payload['number'], '919876511111')
        self.assertEqual(payload['sender_number'], '+91 90000 11111')
        self.assertEqual(payload['replies'], ['✅ Received', '📞 Call me'])

    def test_open_then_confirm_logs_it_on_the_record(self):
        message = self.template.send(self.partner)
        before = len(self.partner.message_ids)
        message.action_mark_opened()
        self.assertEqual(message.state, 'opened')
        self.assertEqual(message.opened_by_id, self.env.user)
        self.assertTrue(message.final_text)
        message.action_mark_sent()
        self.assertEqual(message.state, 'sent')
        self.assertEqual(message.sent_by_id, self.env.user)
        self.assertEqual(len(self.partner.message_ids), before + 1)
        note = self.partner.message_ids[0]
        self.assertIn('WhatsApp to Dr Link', note.body)
        self.assertIn('your work is ready', note.body)
        self.assertEqual(message.conversation_id.last_outbound_at, message.sent_at)

    def test_not_sent_puts_it_back_on_the_desk(self):
        message = self.template.send(self.partner)
        message.action_mark_opened()
        message.action_mark_not_sent()
        self.assertEqual(message.state, 'ready')
        self.assertFalse(message.opened_at)

    def test_the_document_link_is_a_read_receipt_but_a_previewer_is_not(self):
        message = self.template.send(self.partner)
        self._attach(message)
        message.action_mark_opened()
        message.action_mark_sent()
        self.assertFalse(message._register_seen('WhatsApp/2.24.1 A'))
        self.assertFalse(message.seen_at)
        self.assertTrue(message._register_seen(BROWSER))
        self.assertEqual(message.state, 'read')
        self.assertEqual(message.seen_count, 1)
        self.assertIn('opened Invoice 42.pdf', self.partner.message_ids[0].body)
        self.assertEqual(self.partner.message_ids[0].author_id, self.partner)
        message._register_seen(BROWSER)
        self.assertEqual(message.seen_count, 2)
        self.assertEqual(len(self.partner.message_ids.filtered(
            lambda m: 'opened Invoice' in m.body)), 1, "the first open is logged once")

    def test_opening_the_document_proves_an_unconfirmed_message_was_sent(self):
        message = self.template.send(self.partner)
        self._attach(message)
        message.action_mark_opened()          # sent in WhatsApp, never confirmed here
        message._register_seen(BROWSER)
        self.assertEqual(message.state, 'read')
        self.assertTrue(message.sent_at)

    def test_a_link_on_a_message_never_opened_counts_for_nothing(self):
        message = self.template.send(self.partner)
        self._attach(message)
        self.assertFalse(message._register_seen(BROWSER))
        self.assertEqual(message.state, 'ready')

    def test_a_quick_reply_lands_on_the_chatter_once(self):
        message = self.template.send(self.partner)
        message.action_mark_opened()
        message.action_mark_sent()
        self.assertEqual(message._register_quick_reply(1, BROWSER), '✅ Received')
        self.assertEqual(message._register_quick_reply(1, BROWSER), '✅ Received')
        self.assertEqual(message.reply_ids.mapped('body'), ['✅ Received'])
        reply = message.reply_ids
        self.assertEqual(reply.direction, 'inbound')
        self.assertEqual(reply.res_id, self.partner.id)
        self.assertEqual(message.state, 'read')
        self.assertEqual(message.reply_summary, '✅ Received')
        self.assertIn('✅ Received', self.partner.message_ids[0].body)
        self.assertEqual(message._register_quick_reply(9, BROWSER), '')
        self.assertEqual(message._register_quick_reply(2, 'facebookexternalhit/1.1'), '')

    def test_a_reply_typed_in_whatsapp_can_be_logged(self):
        message = self.template.send(self.partner)
        message.action_mark_sent()
        message.log_reply('Will collect tomorrow')
        self.assertEqual(message.reply_ids.body, 'Will collect tomorrow')
        self.assertIn('Will collect tomorrow', self.partner.message_ids[0].body)
        with self.assertRaises(UserError):
            message.log_reply('   ')

    def test_unsent_messages_expire(self):
        message = self.template.send(self.partner)
        message.ready_at = fields.Datetime.now() - timedelta(hours=73)
        opened = self.template.send(self.partner)
        opened.write({'state': 'opened',
                      'ready_at': fields.Datetime.now() - timedelta(hours=100)})
        self.Message._expire_ready()
        self.assertEqual(message.state, 'cancel')
        self.assertIn('72 hours', message.error)
        self.assertEqual(opened.state, 'opened', "an opened message may have gone")

    def test_a_queue_retry_never_turns_an_api_failure_into_desk_work(self):
        failed = self.Message.create({
            'account_id': self.account.id, 'number': '919876511111', 'body': 'old',
            'state': 'error', 'next_try_at': fields.Datetime.now() - timedelta(minutes=5)})
        with patch.object(self.env.cr, 'commit', lambda: None):
            self.Message._cron_send_queue()
        self.assertEqual(failed.state, 'error')

    def test_the_consent_check_still_applies_when_opening(self):
        message = self.template.send(self.partner)
        with patch.object(type(self.Message), '_send_blocked_reason',
                          lambda self: 'Opted out'):
            payload = message.get_open_payload()
        self.assertEqual(payload['blocked'], 'Opted out')
        self.assertNotIn('text', payload)
        self.assertEqual(message.state, 'cancel')

    def test_the_qr_code_is_drawn(self):
        svg = self.Message._qr_svg('https://wa.me/919876511111?text=%s' % quote('hi'))
        self.assertTrue(svg.startswith('<svg'))

    def test_the_composer_opens_whatsapp_without_a_template(self):
        composer = self.env['epg.whatsapp.composer'].create({
            'res_model': 'res.partner', 'res_id': self.partner.id,
            'number': '+91 98765 11111', 'body': 'A quick note', 'account_id': self.account.id})
        action = composer.action_send()
        self.assertEqual(action['tag'], 'epg_whatsapp_send')
        message = self.Message.browse(action['params']['message_id'])
        self.assertEqual(message.state, 'ready')
        self.assertEqual(message.partner_id, self.partner)
        self.assertEqual(message.user_id, self.env.user)
        queued = self.env['epg.whatsapp.composer'].create({
            'res_model': 'res.partner', 'res_id': self.partner.id,
            'number': '+91 98765 11111', 'body': 'Later', 'account_id': self.account.id})
        self.assertEqual(queued.action_queue()['tag'], 'display_notification')

    def test_the_desk(self):
        mine = self.template.send(self.partner)
        other_user = self.env['res.users'].create({
            'name': 'Other Desk', 'login': 'other_desk_wa',
            'group_ids': [(6, 0, [self.env.ref('epg_whatsapp.group_whatsapp_user').id])]})
        theirs = self.template.send(self.partner)
        theirs.user_id = other_user
        Desk = self.env['epg.whatsapp.desk']
        desk = Desk.get_desk('mine')
        ids = [i for g in desk['groups'] for i in g['ids']]
        self.assertIn(mine.id, ids)
        self.assertNotIn(theirs.id, ids)
        self.assertIn(theirs.id, [i for g in Desk.get_desk('all')['groups'] for i in g['ids']])
        mine.action_mark_sent()
        self.account.api_cost_per_message = 0.80
        desk = Desk.get_desk('mine')
        self.assertIn(mine.id, [c['id'] for c in desk['recent']])
        self.assertGreaterEqual(desk['savings']['count'], 1)
        self.assertTrue(desk['savings']['priced'])
        self.assertEqual(Desk.with_user(other_user).pending_count(),
                         Desk.with_user(other_user).get_desk('mine')['counts']['ready'])
        self.assertTrue(Desk.update_body(theirs.id, 'Edited'))
        self.assertEqual(theirs.body, 'Edited')

    def test_the_open_preference_is_the_users_own(self):
        Desk = self.env['epg.whatsapp.desk']
        self.assertTrue(Desk.set_open_with('phone'))
        self.assertEqual(self.env.user.whatsapp_open_with, 'phone')
        self.assertFalse(Desk.set_open_with('fax'))

    def test_only_whatsapp_users_reach_the_desk(self):
        outsider = self.env['res.users'].create({
            'name': 'No WA', 'login': 'no_wa_desk',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(AccessError):
            self.env['epg.whatsapp.desk'].with_user(outsider).get_desk()
        self.assertEqual(self.env['epg.whatsapp.desk'].with_user(outsider).pending_count(), 0)

    def test_a_cloud_api_sender_still_sends_and_logs(self):
        self.account.write({'channel': 'cloud_api', 'simulation_mode': True})
        self.env['epg.whatsapp.conversation']._get_or_create(
            self.account, '919876511111', self.partner)._note_inbound()
        message = self.template.send(self.partner)
        self.assertEqual(message.state, 'simulated')
        self.assertIn('via Meta Cloud API', self.partner.message_ids[0].body)


@tagged('post_install', '-at_install')
class TestLinkRoutes(LinkSetup, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_link()

    def _sent_message(self):
        message = self.template.send(self.partner)
        self._attach(message)
        message.action_mark_sent()
        return message

    def test_the_document_link_serves_the_pdf_and_counts_the_open(self):
        message = self._sent_message()
        token = message.sudo().access_token
        response = self.url_open('/wa/d/%s' % token, headers={'User-Agent': BROWSER})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))
        message.invalidate_recordset()
        self.assertEqual(message.state, 'read')
        self.assertEqual(message.seen_count, 1)

    def test_a_previewer_gets_the_pdf_but_is_not_counted(self):
        message = self._sent_message()
        response = self.url_open('/wa/d/%s' % message.sudo().access_token,
                                 headers={'User-Agent': 'WhatsApp/2.23.20.0'})
        self.assertEqual(response.status_code, 200)
        message.invalidate_recordset()
        self.assertEqual(message.seen_count, 0)

    def test_a_quick_reply_link_thanks_the_doctor(self):
        message = self._sent_message()
        response = self.url_open('/wa/r/%s/1' % message.sudo().access_token,
                                 headers={'User-Agent': BROWSER})
        self.assertEqual(response.status_code, 200)
        self.assertIn('Received', response.text)
        message.invalidate_recordset()
        self.assertEqual(message.reply_ids.mapped('body'), ['✅ Received'])

    def test_bad_and_expired_links(self):
        self.assertEqual(self.url_open('/wa/d/not-a-real-token-at-all-xyz').status_code, 404)
        message = self._sent_message()
        message.link_expires_at = fields.Datetime.now() - timedelta(days=1)
        response = self.url_open('/wa/d/%s' % message.sudo().access_token,
                                 headers={'User-Agent': BROWSER})
        self.assertEqual(response.status_code, 410)
        self.assertEqual(self.url_open('/wa/r/%s/7' % message.sudo().access_token).status_code, 404)


@tagged('post_install', '-at_install')
class TestDeskWithoutRights(LinkSetup, TransactionCase):
    """The person at the Desk sends what the lab wrote about records they may not
    open themselves - and the PDF is paid for once, when the message is opened."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_link()
        cls.clerk = cls.env['res.users'].create({
            'name': 'Desk Clerk', 'login': 'desk_clerk_wa',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('epg_whatsapp.group_whatsapp_user').id])]})
        # A record the clerk cannot read at all: scheduled actions are administrators'.
        cls.secret = cls.env['ir.cron'].sudo().create({
            'name': 'epg_whatsapp.test_secret', 'model_id': cls.env.ref('base.model_res_partner').id,
            'state': 'code', 'code': 'pass', 'active': False})

    def _about_secret(self):
        message = self.Message.sudo().create({
            'account_id': self.account.id, 'template_id': self.template.id,
            'partner_id': self.partner.id, 'number': '919876511111',
            'body': 'About a record you may not open.',
            'res_model': 'ir.cron', 'res_id': self.secret.id})
        message.action_send()
        return message

    def test_the_desk_names_a_record_the_clerk_cannot_open(self):
        with self.assertRaises(AccessError):
            self.secret.with_user(self.clerk).check_access('read')
        message = self._about_secret()
        desk = self.env['epg.whatsapp.desk'].with_user(self.clerk).get_desk('all')
        card = next(c for g in desk['groups'] for c in g['cards'] if c['id'] == message.id)
        self.assertEqual(card['record'], 'epg_whatsapp.test_secret')
        payload = message.with_user(self.clerk).get_open_payload()
        self.assertEqual(payload['record'], 'epg_whatsapp.test_secret')
        self.assertTrue(payload['text'].startswith('*Hello'), "the header still renders")

    def test_the_desk_prepares_drafts_without_rendering_and_the_open_renders(self):
        Message = self.Message
        # Any report will do: the question is WHEN it is rendered, not how.
        report = self.env['ir.actions.report'].sudo().create({
            'name': 'Desk Test Report', 'model': 'res.partner',
            'report_type': 'qweb-pdf', 'report_name': 'epg_whatsapp.desk_test_none'})
        self.template.report_id = report
        deferred = self.template.send(self.partner, defer=True)
        self.assertEqual(deferred.state, 'draft')
        rendered = []

        def spy(message, record):
            rendered.append(message.id)
            message.attachment_id = self.env['ir.attachment'].sudo().create({
                'name': 'rendered.pdf', 'datas': 'JVBERi0xLjQK',
                'mimetype': 'application/pdf'}).id
        with patch.object(type(Message), '_attach_report', spy):
            desk = self.env['epg.whatsapp.desk'].get_desk('all')
            card = next(c for g in desk['groups'] for c in g['cards'] if c['id'] == deferred.id)
            self.assertEqual(deferred.state, 'ready')
            self.assertEqual(rendered, [], "listing the Desk renders nothing")
            self.assertEqual(card['document'], report.name, "the card still names the document")
            deferred.get_open_payload()
            self.assertEqual(rendered, [deferred.id], "opening renders it, once")
            deferred.get_open_payload()
            self.assertEqual(rendered, [deferred.id])
        self.assertTrue(deferred.attachment_id)

    def test_the_qr_is_drawn_on_request(self):
        message = self.template.send(self.partner)
        self.assertNotIn('qr', message.get_open_payload())
        self.assertTrue(message.get_qr().startswith('<svg'))
        message.action_mark_sent()
        self.assertEqual(message.get_qr(), '')

    def test_a_refused_draft_is_reported_not_swallowed(self):
        message = self.template.send(self.partner, defer=True)
        with patch.object(type(self.Message), '_send_blocked_reason', lambda self: 'Opted out'):
            payload = message.get_open_payload()
        self.assertEqual(payload['blocked'], 'Opted out')
        self.assertEqual(message.state, 'cancel')

    def test_expiry_drops_the_stale_and_leaves_the_due(self):
        stale = self.template.send(self.partner)
        stale.ready_at = fields.Datetime.now() - timedelta(hours=80)
        due = self.template.send(self.partner)
        due.write({'ready_at': fields.Datetime.now() - timedelta(hours=80),
                   'scheduled_at': fields.Datetime.now() + timedelta(hours=2)})
        fresh = self.template.send(self.partner)
        self.assertEqual(self.Message._expire_ready(), 1)
        self.assertEqual((stale.state, due.state, fresh.state), ('cancel', 'ready', 'ready'))
