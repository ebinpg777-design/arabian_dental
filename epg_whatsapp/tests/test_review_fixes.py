# -*- coding: utf-8 -*-
"""What the code review of 2026-10-04 pinned down. (port to 20.0)

Each test here failed before its fix: the access ones are run with every field
unread first (`invalidate_all`), because the ORM cache is per transaction and a
value fetched as root is handed to another user's environment without the rules
being asked again.
"""
import hashlib
import hmac
import json
from datetime import datetime, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import HttpCase, TransactionCase, freeze_time, tagged

from .test_link_channel import BROWSER, LinkSetup

SECRET = 'review-app-secret'


class ReviewSetup(LinkSetup):

    @classmethod
    def _setup_review(cls):
        cls._setup_link()
        base = cls.env.ref('base.group_user').id
        sender = cls.env.ref('epg_whatsapp.group_whatsapp_user').id
        cls.alice = cls.env['res.users'].create({
            'name': 'Alice Review', 'login': 'wa_review_alice',
            'group_ids': [(6, 0, [base, sender])]})
        cls.bob = cls.env['res.users'].create({
            'name': 'Bob Review', 'login': 'wa_review_bob',
            'group_ids': [(6, 0, [base, sender])]})
        cls.Desk = cls.env['epg.whatsapp.desk']

    def _hide_partner_from_senders(self, name):
        """A contact the senders' record rules hide (Odoo 20: a restriction row)."""
        secret = self.env['res.partner'].create({'name': name, 'phone': '+91 98765 33333'})
        self.env['ir.access'].create({
            'name': 'TEST: senders do not see %s' % name,
            'model_id': self.env.ref('base.model_res_partner').id,
            'operation': 'r',
            'domain': "[('name', '!=', %r)] "
                      "if user.has_group('epg_whatsapp.group_whatsapp_user') else []" % name,
        })
        self.env.invalidate_all()
        with self.assertRaises(AccessError, msg="the rule really does bite"):
            self.env['res.partner'].with_user(self.alice).browse(secret.id).display_name
        self.env.invalidate_all()
        return secret


@tagged('post_install', '-at_install')
class TestReviewAccess(ReviewSetup, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_review()

    # ------------------------------------------------ what a message may point at
    def test_a_sender_cannot_hang_an_attachment_they_cannot_read_on_a_message(self):
        """The public /wa/d link serves a message's attachment through sudo, and the
        token is the sender's to read: an attachment id chosen over RPC used to make
        "WhatsApp: send" a way to download any file in the database."""
        private = self.env['ir.attachment'].create({
            'name': 'salaries.pdf', 'raw': b'%PDF-1.4 secret', 'mimetype': 'application/pdf'})
        self.env.invalidate_all()
        with self.assertRaises(AccessError, msg="the file really is not Alice's"):
            private.with_user(self.alice).check_access('read')
        Message = self.env['epg.whatsapp.message'].with_user(self.alice)
        with self.assertRaises(AccessError):
            Message.create({'account_id': self.account.id, 'number': '919876511111',
                            'body': 'x', 'user_id': self.alice.id,
                            'attachment_id': private.id})
        mine = Message.create({'account_id': self.account.id, 'number': '919876511111',
                               'body': 'x', 'user_id': self.alice.id})
        with self.assertRaises(AccessError):
            mine.write({'attachment_id': private.id})
        # Her own message's document is hers: the report rendered on open still lands.
        own = self.env['ir.attachment'].create({
            'name': 'Invoice.pdf', 'raw': b'%PDF-1.4\n', 'mimetype': 'application/pdf',
            'res_model': mine._name, 'res_id': mine.id})
        mine.write({'attachment_id': own.id})
        self.assertEqual(mine.attachment_id, own)

    def test_a_sender_cannot_point_a_message_at_a_record_they_cannot_read(self):
        """The Desk renders a message's record through sudo, so the record must be
        one the person linking it may read."""
        secret = self._hide_partner_from_senders('Hidden Debtor')
        Message = self.env['epg.whatsapp.message'].with_user(self.alice)
        vals = {'account_id': self.account.id, 'number': '919876533333', 'body': 'x',
                'user_id': self.alice.id, 'template_id': self.template.id}
        with self.assertRaises(AccessError):
            Message.create(dict(vals, res_model='res.partner', res_id=secret.id))
        mine = Message.create(dict(vals, res_model='res.partner', res_id=self.partner.id))
        with self.assertRaises(AccessError):
            mine.write({'res_id': secret.id})

    # ------------------------------------------------ a contact the sender cannot open
    def _hidden_message(self, user, template=True):
        """A message to a hidden contact about NO record of theirs - as a real one is
        about a loan or an invoice. Sent through the res.partner template, the
        contact would be its record, prefetched through sudo for the Desk, and every
        later read would come from that cache: the test would pass without the fix.
        Likewise with a template, whose language lookup reads the contact first."""
        secret = self.env['res.partner'].search([('name', '=', 'Hidden Customer')]) or \
            self._hide_partner_from_senders('Hidden Customer')
        message = self.env['epg.whatsapp.message'].create({
            'account_id': self.account.id,
            'template_id': self.template.id if template else False,
            'partner_id': secret.id, 'number': '919876533333',
            'body': 'Your EMI is due on Monday.'})
        message.action_send()
        self.assertEqual(message.state, 'ready')
        message.write({'user_id': user.id})
        self.env.invalidate_all()
        return secret, message

    def test_the_send_dialog_and_confirm_survive_a_contact_the_sender_cannot_open(self):
        """The Desk was fixed for this (2026-09-19); opening the message and confirming
        it still read the contact's name and language under the sender's rules."""
        for template in (True, False):
            secret, message = self._hidden_message(self.alice, template=template)
            mine = message.with_user(self.alice)
            payload = mine.get_open_payload()
            self.assertEqual(payload['partner'], 'Hidden Customer')
            self.env.invalidate_all()
            mine.action_mark_opened()
            self.env.invalidate_all()
            mine.action_mark_sent()
            self.assertEqual(message.state, 'sent')
            self.assertIn('WhatsApp to Hidden Customer', secret.message_ids[0].body)

    def test_chats_survive_a_contact_the_sender_cannot_open(self):
        secret, message = self._hidden_message(self.alice)
        message.action_mark_sent()
        self.env.invalidate_all()
        Desk = self.Desk.with_user(self.alice)
        chats = Desk.get_chats()['chats']
        [chat] = [c for c in chats if c['id'] == message.conversation_id.id]
        self.assertEqual(chat['partner'], 'Hidden Customer')
        self.env.invalidate_all()
        thread = Desk.get_thread(chat['id'])
        self.assertEqual(thread['partner'], 'Hidden Customer')
        self.env.invalidate_all()
        self.assertEqual(message.conversation_id.with_user(self.alice).display_name,
                         'Hidden Customer')
        self.env.invalidate_all()
        res = Desk.compose(chat['id'], 'Your payment is due on Monday.')
        self.assertEqual(self.env['epg.whatsapp.message'].browse(res['id']).state, 'ready')
        # Answering from the tray opens the chat by contact; a hidden contact with
        # no conversation yet gets one, from their number.
        twin = self.env['res.partner'].create({'name': 'Hidden Customer',
                                               'phone': '+91 98765 33334'})
        self.env.invalidate_all()
        opened = Desk.get_chats('', twin.id)['open_id']
        self.assertEqual(self.env['epg.whatsapp.conversation'].browse(opened).number,
                         '919876533334')

    def test_the_tray_survives_an_answer_to_another_senders_message(self):
        """A reply to Bob's message waits in everyone's tray, and the tray named the
        template it answered by reading Bob's message under Alice's rules."""
        bobs = self.template.send(self.partner)
        bobs.write({'user_id': self.bob.id})
        bobs.action_mark_sent()
        bobs._register_quick_reply(1, BROWSER)
        self.env.invalidate_all()
        desk = self.Desk.with_user(self.alice).get_desk()
        [row] = [r for r in desk['inbox'] if r['id'] == bobs.reply_ids.id]
        self.assertEqual(row['about'], self.template.name)

    def test_a_thread_shows_a_sender_only_what_their_rules_let_them_read(self):
        """Chats read every message through sudo, handing any sender every message of
        every contact - what the rules of 2026-09-19 were written to stop."""
        bobs = self.template.send(self.partner)
        bobs.write({'user_id': self.bob.id, 'body': "Bob's private note to Ravi"})
        bobs.action_mark_sent()
        bobs.log_reply('Thanks Bob')
        self.env.invalidate_all()
        thread = self.Desk.with_user(self.alice).get_thread(bobs.conversation_id.id)
        texts = [b['text'] for b in thread['bubbles'] if b['kind'] == 'msg']
        self.assertNotIn("Bob's private note to Ravi", texts)
        self.assertIn('Thanks Bob', texts, "the contact's own words are the shared tray's")
        # Bob writes again: the newest message is his, and the list's preview line
        # must not show it to Alice either.
        again = self.template.send(self.partner)
        again.write({'user_id': self.bob.id, 'body': "Bob's second private note"})
        again.action_mark_sent()
        self.env.invalidate_all()
        chats = self.Desk.with_user(self.alice).get_chats()['chats']
        [chat] = [c for c in chats if c['id'] == bobs.conversation_id.id]
        self.assertEqual(chat['last'], 'Thanks Bob')
        everything = self.Desk.get_thread(bobs.conversation_id.id)
        self.assertIn("Bob's private note to Ravi",
                      [b['text'] for b in everything['bubbles'] if b['kind'] == 'msg'])

    def test_a_placeholder_walks_fields_not_the_server(self):
        """Any sender may write a campaign, and its {{placeholders}} were walked with
        getattr() through anything: {{env.cr._cnx.dsn}} put the database server's
        address, port, user and database name in the preview (and
        {{env.cr._cnx.info.password}} the password, where libpq keeps it). A
        placeholder is a record's fields now - no private names, and nothing past
        a value that is not a record."""
        campaign = self.env['epg.whatsapp.campaign'].with_user(self.alice).create({
            'name': 'Leak', 'account_id': self.account.id, 'optout': False,
            'partner_ids': [(6, 0, self.partner.ids)],
            'body': 'A {{env.cr._cnx.dsn}} B {{_cr.dbname}} C {{name}}'})
        self.assertNotIn('host=', campaign.preview)
        self.assertNotIn(self.env.cr.dbname, campaign.preview)
        self.assertEqual(campaign.preview, 'A  B  C Dr Link')
        Template = self.env['epg.whatsapp.template']
        self.assertIsNone(Template._field_value(self.partner, 'env.cr._cnx.dsn'))
        self.assertEqual(Template._field_value(self.partner, 'name'), 'Dr Link')
        self.assertEqual(Template._resolve(self.partner, 'country_id.name'),
                         self.partner.country_id.name or '')
        for path in ('env.cr', '_cr', 'name.upper'):
            self.assertFalse(Template._check_path('res.partner', path)[0], path)
        self.assertTrue(Template._check_path('res.partner', 'display_name')[0])

    def test_a_campaign_stopped_by_a_colleague_stops_everywhere(self):
        partners = self.env['res.partner'].create([
            {'name': 'Stop One', 'phone': '+91 98700 10001'},
            {'name': 'Stop Two', 'phone': '+91 98700 10002'}])
        campaign = self.env['epg.whatsapp.campaign'].with_user(self.alice).create({
            'name': 'Festival', 'account_id': self.account.id, 'body': 'Hello {{name}}',
            'partner_ids': [(6, 0, partners.ids)], 'optout': False})
        campaign.action_launch()
        messages = campaign.sudo().message_ids
        self.assertEqual(set(messages.mapped('user_id')), {self.alice})
        self.env.invalidate_all()
        campaign.with_user(self.bob).action_stop()
        self.assertEqual(set(messages.mapped('state')), {'cancel'},
                         "nothing of a stopped campaign is left on Alice's Desk")


@tagged('post_install', '-at_install')
class TestReviewLogic(ReviewSetup, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_review()

    def test_an_indian_mobile_that_starts_with_91_still_gets_the_country_code(self):
        Template = self.env['epg.whatsapp.template']
        india = self.env.ref('base.in')
        self.env.company.country_id = india
        self.assertEqual(Template._normalise_number('91234 56789'), '919123456789')
        self.assertEqual(Template._normalise_number('919876543210'), '919876543210')
        self.assertEqual(Template._normalise_number('09123456789'), '919123456789')
        self.assertEqual(Template._normalise_number('+91 91234 56789'), '919123456789')

    def test_a_contacts_own_country_reads_their_local_number(self):
        Template = self.env['epg.whatsapp.template']
        self.env.company.country_id = self.env.ref('base.in')
        dubai = self.env['res.partner'].create({
            'name': 'Dubai Contact', 'phone': '050 123 4567',
            'country_id': self.env.ref('base.ae').id})
        self.assertEqual(Template._normalise_number(dubai.phone, dubai), '971501234567')
        if 'whatsapp_number' not in dubai._fields:
            self.assertEqual(self.env['epg.whatsapp.campaign']._number_of(dubai),
                             '971501234567', "a campaign reads the contact's own country")

    def test_a_contact_who_only_wrote_to_us_is_not_never_messaged(self):
        writer = self.env['res.partner'].create({'name': 'Only Wrote', 'phone': '+91 98700 20001'})
        self.env['epg.whatsapp.message'].create({
            'account_id': self.account.id, 'partner_id': writer.id, 'number': '919870020001',
            'body': 'Hello?', 'direction': 'inbound', 'state': 'received'})
        self.assertEqual(writer.whatsapp_engagement, 'engaged')
        Partner = self.env['res.partner']
        self.assertIn(writer, Partner.search([('whatsapp_engagement', '=', 'engaged')]))
        self.assertNotIn(writer, Partner.search([('whatsapp_engagement', '=', 'never')]))

    def test_a_message_held_back_longer_than_the_expiry_is_not_dropped_when_due(self):
        """A template's Wait Before Sending of four days on a sender that drops
        unsent messages after three: every one of them was cancelled the moment it
        came due, because lateness was counted from when it was written."""
        self.account.expire_hours = 72
        self.template.delay_hours = 96
        message = self.template.send(self.partner)
        self.assertEqual(message.state, 'draft')
        self.assertTrue(message.scheduled_at)
        with freeze_time(fields.Datetime.now() + timedelta(hours=97)), \
                patch.object(self.env.cr, 'commit', lambda: None):
            self.env['epg.whatsapp.message']._cron_send_queue()
        self.assertEqual(message.state, 'ready', message.error)

    def test_today_starts_at_local_midnight_for_a_user_with_no_timezone(self):
        """context_today() is UTC for such a user while the midnight was placed in
        the company's zone: at 01:30 in Kolkata "today" began the day before."""
        self.env.user.tz = False
        self.env.company.partner_id.tz = 'Asia/Kolkata'
        Desk = self.Desk.with_context(tz=False)
        with freeze_time('2026-10-04 20:00:00'):          # 01:30 on the 5th in Kolkata
            self.assertEqual(Desk._today_start(), datetime(2026, 10, 4, 18, 30))
            self.assertEqual(Desk._local_midnight(Desk.env['epg.whatsapp.message']
                                                  ._local_now().date().replace(day=1)),
                             datetime(2026, 9, 30, 18, 30))

    def test_a_new_campaign_opens_without_a_free_sender(self):
        """The broadcast text is computed as the form opens, and it read the sender's
        address with ensure_one(): with no WhatsApp app / Web sender the new
        campaign form died on "Expected singleton"."""
        self.env['epg.whatsapp.account'].search([]).write({'active': False})
        campaign = self.env['epg.whatsapp.campaign'].new({'name': 'Draft', 'body': 'Hi'})
        self.assertFalse(campaign.account_id)
        self.assertIn('/wa/c/', campaign.broadcast_text or '')

    def test_an_older_message_arriving_late_does_not_close_the_window(self):
        conv = self.env['epg.whatsapp.conversation']._get_or_create(
            self.account, '919876511111', self.partner)
        now = fields.Datetime.now()
        conv._note_inbound(now)
        conv._note_inbound(now - timedelta(hours=30))
        self.assertEqual(conv.last_inbound_at, now)
        self.assertTrue(conv.window_open)

    def test_an_unreadable_answer_from_meta_is_still_a_send(self):
        """`_send` never raises: an exception after Meta had the message rolled its
        "sent" back with the queue's batch, and the next run sent it again."""
        self.account.write({'channel': 'cloud_api', 'simulation_mode': False,
                            'phone_number_id': '123', 'access_token': 'token'})

        class Answer:
            status_code = 200
            text = '<html>proxy</html>'

            @staticmethod
            def json():
                raise ValueError('not JSON')

        with patch('odoo.addons.epg_whatsapp.models.epg_whatsapp_account.requests.post',
                   return_value=Answer()):
            self.assertEqual(self.account._send('919876511111', 'Hello'), (True, ''))

    def test_one_broken_automation_does_not_silence_the_others(self):
        """Audiences are any sender's to edit: one whose rules stopped reading made
        the daily run raise before any automation had put anything on the Desk."""
        Audience = self.env['epg.whatsapp.audience']
        good = Audience.create({'name': 'Ravi only',
                                'domain': "[('id', '=', %d)]" % self.partner.id})
        broken = Audience.create({'name': 'Broken', 'domain': "[('nope', '=', 1)]"})
        Automation = self.env['epg.whatsapp.automation']
        Automation.search([]).write({'active': False})
        bad = Automation.create({'name': 'A broken one', 'sequence': 1,
                                 'audience_id': broken.id, 'account_id': self.account.id,
                                 'body': 'Hello', 'optout': False})
        fine = Automation.create({'name': 'A fine one', 'sequence': 2,
                                  'audience_id': good.id, 'account_id': self.account.id,
                                  'body': 'Hello {{name}}', 'optout': False})
        with self.assertLogs('odoo.addons.epg_whatsapp.models.automation', 'ERROR'):
            Automation._cron_run()
        self.assertEqual(fine.message_ids.partner_id, self.partner)
        self.assertFalse(bad.message_ids)
        self.assertEqual((bad.run_count, fine.run_count), (0, 1))

    def test_a_templates_messages_open_grouped_by_status(self):
        action = self.template.action_view_messages()
        view = self.env.ref('epg_whatsapp.view_message_search')
        filters = set(view.arch_db.split('<filter name="')[i].split('"')[0]
                      for i in range(1, view.arch_db.count('<filter name="') + 1))
        for key in action['context']:
            if key.startswith('search_default_'):
                self.assertIn(key[len('search_default_'):], filters,
                              "a default that names no filter is silently dropped")

    def test_the_interested_count_is_counted_for_the_whole_list(self):
        partners = self.env['res.partner'].create([
            {'name': 'Lead One', 'phone': '+91 98700 30001'},
            {'name': 'Lead Two', 'phone': '+91 98700 30002'}])
        campaigns = self.env['epg.whatsapp.campaign'].create([{
            'name': 'Offer %s' % i, 'account_id': self.account.id, 'body': 'Hi',
            'partner_ids': [(6, 0, partners.ids)], 'quick_replies': 'Yes\nNo',
            'lead_reply': 'Yes', 'optout': False} for i in (1, 2)])
        for campaign in campaigns:
            campaign.action_launch()
        first, second = campaigns
        for message in first.message_ids:
            message.action_mark_sent()
            message._register_quick_reply(1, BROWSER)
        second.message_ids[:1].action_mark_sent()
        second.message_ids[:1]._register_quick_reply(2, BROWSER)
        campaigns.invalidate_recordset()
        self.assertEqual(campaigns.mapped('interested_count'), [2, 0])


@tagged('post_install', '-at_install')
class TestReviewWebhook(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.account = cls.env['epg.whatsapp.account'].create({
            'name': 'Review Cloud', 'channel': 'cloud_api', 'phone_number_id': '555000111',
            'access_token': 'token', 'app_secret': SECRET, 'simulation_mode': True})
        # Stored the way a person types it, with spaces.
        cls.partner = cls.env['res.partner'].create({
            'name': 'Spaced Number', 'phone': '+91 98765 40404'})

    def _post(self, payload):
        raw = json.dumps(payload).encode()
        sig = 'sha256=' + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
        return self.url_open('/whatsapp/webhook', data=raw, headers={
            'Content-Type': 'application/json', 'X-Hub-Signature-256': sig})

    def _inbound(self, wamid, text):
        return {'entry': [{'id': 'waba', 'changes': [{'field': 'messages', 'value': {
            'metadata': {'phone_number_id': '555000111'},
            'messages': [{'id': wamid, 'from': '919876540404', 'timestamp': '1790000000',
                          'type': 'text', 'text': {'body': text}}]}}]}]}

    def test_a_retried_callback_is_kept_once_and_finds_the_contact(self):
        payload = self._inbound('wamid.REVIEW1', 'When is my EMI due?')
        self.assertEqual(self._post(payload).status_code, 200)
        self.assertEqual(self._post(payload).status_code, 200, "Meta retries")
        inbound = self.env['epg.whatsapp.message'].search(
            [('external_id', '=', 'wamid.REVIEW1')])
        self.assertEqual(len(inbound), 1, "one message, however often Meta sends it")
        self.assertEqual(inbound.partner_id, self.partner,
                         "matched on the digits, whatever the spaces in the stored number")

    def test_a_document_link_never_runs_html_on_the_companys_address(self):
        account = self.env['epg.whatsapp.account'].create({'name': 'Review Link'})
        message = self.env['epg.whatsapp.message'].create({
            'account_id': account.id, 'partner_id': self.partner.id,
            'number': '919876540404', 'body': 'Your statement.'})
        message.action_send()
        page = self.env['ir.attachment'].create({
            'name': 'statement.html', 'raw': b'<script>alert(1)</script>',
            'mimetype': 'text/html', 'res_model': message._name, 'res_id': message.id})
        message.attachment_id = page
        self.assertEqual(page.mimetype, 'text/html', "kept as HTML: made by an admin")
        response = self.url_open('/wa/d/%s' % message.sudo().access_token)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers['Content-Disposition'].startswith('attachment'))
        self.assertEqual(response.headers.get('X-Content-Type-Options'), 'nosniff')
        pdf = self.env['ir.attachment'].create({
            'name': 'statement.pdf', 'raw': b'%PDF-1.4\n', 'mimetype': 'application/pdf',
            'res_model': message._name, 'res_id': message.id})
        message.attachment_id = pdf
        response = self.url_open('/wa/d/%s' % message.sudo().access_token)
        self.assertTrue(response.headers['Content-Disposition'].startswith('inline'),
                        "a PDF still opens in the phone's browser")

    def test_json_that_is_not_metas_envelope_is_refused_cleanly(self):
        response = self.url_open('/whatsapp/webhook', data=b'[1, 2]',
                                 headers={'Content-Type': 'application/json'})
        self.assertEqual(response.status_code, 400)
