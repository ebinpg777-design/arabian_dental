# -*- coding: utf-8 -*-
"""Marketing: audiences, ideas, the flyer page, broadcasts, automations,
interest, follow-ups and stop reasons. (client, 2026-09-17)"""
import base64
from datetime import datetime, timedelta

from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import HttpCase, TransactionCase, tagged

from .test_link_channel import BROWSER
from .test_smart_send import SmartSetup

# A 1x1 PNG.
PNG = base64.b64encode(base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII='))


class MarketingSetup(SmartSetup):

    @classmethod
    def _setup_marketing(cls):
        cls._setup_smart()
        cls.Audience = cls.env['epg.whatsapp.audience']
        cls.Campaign = cls.env['epg.whatsapp.campaign']
        cls.Message = cls.env['epg.whatsapp.message']
        cls.kochi = cls._partner_cls('Dr Kochi One', '+91 98700 00101', city='Kochi')
        cls.kochi2 = cls._partner_cls('Dr Kochi Two', '+91 98700 00102', city='Kochi')
        cls.trichur = cls._partner_cls('Dr Trichur', '+91 98700 00103', city='Thrissur')
        cls.nonumber = cls.env['res.partner'].create({'name': 'Dr No Number', 'city': 'Kochi'})
        cls.everyone = cls.Audience.create({'name': 'Kochi', 'domain': "[('city', '=', 'Kochi')]"})

    @classmethod
    def _partner_cls(cls, name, number, **vals):
        Partner = cls.env['res.partner']
        vals.update({'name': name, 'phone': number})
        if 'whatsapp_number' in Partner._fields:
            vals['whatsapp_number'] = number
        return Partner.create(vals)


@tagged('post_install', '-at_install')
class TestMarketing(MarketingSetup, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_marketing()

    # ------------------------------------------------------------------ audiences
    def test_an_audience_counts_and_lists_who_matches(self):
        self.assertEqual(self.everyone.count, 2, "Kochi doctors with a number")
        self.assertEqual(self.everyone._partners(), self.kochi | self.kochi2)
        self.assertIn('Dr Kochi', self.everyone.sample)
        self.everyone.require_number = False
        self.assertEqual(self.everyone.count, 3)

    def test_an_audience_leaves_out_who_stopped_and_can_leave_out_the_quiet(self):
        message = self.template.send(self.kochi2)
        message.action_mark_sent()
        message._register_opt_out()
        self.everyone.invalidate_recordset()
        self.assertEqual(self.everyone._partners(), self.kochi)
        quiet = self.template.send(self.kochi)
        quiet.action_mark_sent()
        self.everyone.exclude_quiet = True
        self.assertEqual(self.everyone._partners(), self.env['res.partner'])

    def test_relative_dates_and_the_recency_rule(self):
        audience = self.Audience.create({
            'name': 'Old contacts', 'require_number': False,
            'domain': "[('create_date', '<', context_today() - relativedelta(days=1))]"})
        self.assertNotIn(self.kochi, audience._partners(), "made today")
        recent = self.Audience.create({
            'name': 'Written to lately', 'require_number': False,
            'domain': "[('city', '=', 'Kochi')]",
            'recency_field': 'create_date', 'recency_kind': 'newer', 'recency_days': 1})
        self.assertIn(self.kochi, recent._partners())
        through = self.Audience.create({
            'name': 'Nothing planned in 30 days', 'require_number': False,
            'domain': "[('city', '=', 'Kochi')]",
            'recency_field': 'activity_ids.date_deadline', 'recency_kind': 'older',
            'recency_days': 30})
        # A to-do due today: the contact has something newer than 30 days.
        self.kochi.activity_schedule('mail.mail_activity_data_todo',
                                     date_deadline=fields.Date.today())
        self.assertNotIn(self.kochi, through._partners())
        self.assertIn(self.kochi2, through._partners())
        bad = self.Audience.create({'name': 'Bad', 'domain': "[('nope', '=', 1)]"})
        self.assertTrue(bad.problem)
        self.assertEqual(bad.count, 0)
        with self.assertRaises(UserError):
            self.Audience.create({'name': 'Bad path', 'recency_field': 'city'})._partners()

    def test_the_starter_fills_the_rules(self):
        audience = self.Audience.new({'name': 'x', 'starter': 'engaged'})
        audience._onchange_starter()
        self.assertIn('engaged', audience.domain)
        self.assertFalse(audience.starter)

    def test_a_preview_action_carries_no_python_dates(self):
        audience = self.Audience.create({
            'name': 'Rel', 'domain': "[('create_date', '>=', context_today() - relativedelta(days=1))]"})
        domain = audience.action_preview()['domain']
        self.assertTrue(all(isinstance(leaf[2], (str, bool, int, list)) for leaf in domain
                            if isinstance(leaf, tuple)))

    # ------------------------------------------------------------------ campaigns
    def test_a_campaign_loads_its_audience(self):
        campaign = self.Campaign.create({
            'name': 'Kochi news', 'account_id': self.account.id, 'body': 'Hi {{name}}',
            'audience_id': self.everyone.id})
        campaign.action_load_audience()
        self.assertEqual(campaign.partner_ids, self.kochi | self.kochi2)
        campaign.partner_ids = False
        campaign.action_launch()
        self.assertEqual(len(campaign.message_ids), 2, "an empty list is filled at launch")

    def test_an_idea_fills_the_campaign(self):
        idea = self.env.ref('epg_whatsapp.idea_miss_you')
        campaign = self.Campaign.new({'idea_id': idea.id, 'account_id': self.account.id})
        campaign._onchange_idea()
        self.assertEqual(campaign.body, idea.body)
        self.assertEqual(campaign.lead_reply, idea.lead_reply)
        self.assertEqual(campaign.name, idea.name)

    def test_send_on_and_best_time_pace_the_launch(self):
        later = fields.Datetime.now() + timedelta(days=2)
        campaign = self.Campaign.create({
            'name': 'Later', 'account_id': self.account.id, 'body': 'x',
            'partner_ids': [(6, 0, (self.kochi | self.kochi2).ids)], 'send_on': later})
        campaign.action_launch()
        stamps = campaign.message_ids.mapped('scheduled_at')
        self.assertTrue(all(s and abs((s - later).total_seconds()) < 60 for s in stamps))
        # Best time: three opens around 20:00 make the doctor's hour known.
        for _i in range(3):
            old = self.template.send(self.trichur)
            old.action_mark_sent()
            old.write({'seen_at': fields.Datetime.now().replace(hour=14, minute=35)})
        timed = self.Campaign.create({
            'name': 'Best', 'account_id': self.account.id, 'body': 'x',
            'partner_ids': [(6, 0, self.trichur.ids)], 'at_best_time': True})
        timed.action_launch()
        moment = timed.message_ids.scheduled_at
        self.assertTrue(moment)
        self.assertEqual(moment.hour, 14, "stored in UTC: 20:05 Kolkata is 14:35 UTC")

    def test_the_flyer_link_replaces_the_plain_link(self):
        campaign = self.Campaign.create({
            'name': 'Aligners', 'account_id': self.account.id, 'body': 'Dear {{name}}, *new*!',
            'image': PNG, 'headline': 'Clear aligners', 'link_url': 'https://example.com/a',
            'quick_replies': '👍 Interested', 'lead_reply': '👍 Interested',
            'partner_ids': [(6, 0, self.kochi.ids)]})
        campaign.action_launch()
        message = campaign.message_ids
        text = message.get_open_payload()['text']
        self.assertIn('🖼 Clear aligners', text)
        self.assertIn('/wa/f/%s' % message.sudo().access_token, text)
        self.assertNotIn('/wa/l/', text, "the link lives on the flyer")
        flyer = campaign._flyer(message)
        self.assertEqual(flyer['headline'], 'Clear aligners')
        self.assertTrue(flyer['image'].endswith('/img'))
        self.assertEqual(flyer['link']['url'], '%s/wa/l/%s' % (
            self.Message._base_url(), message.sudo().access_token))
        self.assertEqual(flyer['replies'][0]['label'], '👍 Interested')
        self.assertIn('<b>new</b>', str(flyer['lines']))

    def test_the_interested_reply_becomes_a_to_do_for_the_salesperson(self):
        rep = self.env['res.users'].create({
            'name': 'Rep', 'login': 'rep_wa_marketing',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        self.kochi.user_id = rep
        campaign = self.Campaign.create({
            'name': 'Offer', 'account_id': self.account.id, 'body': 'x',
            'quick_replies': '👍 Interested\n📞 Call me', 'lead_reply': '👍 Interested',
            'partner_ids': [(6, 0, (self.kochi | self.kochi2).ids)]})
        campaign.action_launch()
        one, two = campaign.message_ids.sorted('id')
        (one | two).action_mark_sent()
        one._register_quick_reply(1, BROWSER)
        two._register_quick_reply(2, BROWSER)
        activity = self.kochi.activity_ids
        self.assertEqual(len(activity), 1)
        self.assertEqual(activity.user_id, rep)
        self.assertIn('interested', activity.summary)
        self.assertFalse(self.kochi2.activity_ids, "'Call me' is not the interested reply")
        campaign.invalidate_recordset()
        self.assertEqual(campaign.interested_count, 1)

    def test_a_follow_up_goes_to_the_silent(self):
        campaign = self.Campaign.create({
            'name': 'News', 'account_id': self.account.id, 'body': 'x',
            'quick_replies': '👍 Noted', 'link_url': 'https://example.com',
            'partner_ids': [(6, 0, (self.kochi | self.kochi2 | self.trichur).ids)]})
        campaign.action_launch()
        messages = campaign.message_ids.sorted('id')
        messages.action_mark_sent()
        messages[0]._register_click(BROWSER)
        messages[1]._register_quick_reply(1, BROWSER)
        follow = self.Campaign.browse(campaign.action_follow_up()['res_id'])
        self.assertEqual(follow.partner_ids, self.trichur)
        self.assertEqual(follow.state, 'draft')
        self.assertEqual(follow.body, 'x')
        self.assertFalse(follow.message_ids)
        copy = self.Campaign.browse(campaign.action_duplicate()['res_id'])
        self.assertEqual(copy.partner_ids, campaign.partner_ids)
        self.assertNotEqual(copy.access_token, campaign.access_token)

    # ------------------------------------------------------------------ broadcast
    def _broadcast(self):
        campaign = self.Campaign.create({
            'name': 'Onam', 'account_id': self.account.id, 'mode': 'broadcast',
            'body': 'Dear {{name}}, happy Onam! 🌼', 'quick_replies': '🙏 Thank you',
            'lead_reply': '🙏 Thank you', 'image': PNG, 'headline': 'Happy Onam',
            'partner_ids': [(6, 0, (self.kochi | self.kochi2).ids)]})
        return campaign

    def test_a_broadcast_is_one_text_with_the_campaigns_links(self):
        campaign = self._broadcast()
        text = campaign.broadcast_text
        base = campaign._page_base()
        self.assertIn('Dear Doctor, happy Onam!', text)
        self.assertNotIn('{{', text)
        self.assertIn('🖼 Happy Onam\n%s' % base, text)
        self.assertIn('🙏 Thank you → %s/r/1' % base, text)
        self.assertIn('%s/o_' % base, text)
        with self.assertRaises(UserError):
            campaign.action_launch()
        campaign.action_mark_broadcast_sent()
        self.assertEqual(campaign.state, 'done')
        self.assertEqual(set(campaign.message_ids.mapped('state')), {'sent'})
        self.assertEqual(campaign.message_ids[0].final_text, text)
        self.assertIn('WhatsApp to Dr Kochi', self.kochi.message_ids[0].body)
        self.assertNotIn('/wa/c/', self.kochi.message_ids[0].body, "campaign links stay out of the chatter")

    def test_a_broadcast_answer_and_stop_come_back_by_number(self):
        campaign = self._broadcast()
        campaign.action_mark_broadcast_sent()
        self.assertEqual(campaign._broadcast_reply(1, '98700 00101'), '🙏 Thank you')
        own = campaign._own_message_for(self.kochi)
        self.assertEqual(own.reply_ids.mapped('body'), ['🙏 Thank you'])
        self.assertTrue(self.kochi.activity_ids, "the interested reply made a to-do")
        # Someone not on the list answers too: kept, on their contact if there is one.
        self.assertEqual(campaign._broadcast_reply(1, '+91 98700 00103'), '🙏 Thank you')
        stray = self.Message.search([('campaign_id', '=', campaign.id), ('partner_id', '=', self.trichur.id)])
        self.assertEqual(stray.direction, 'inbound')
        self.assertEqual(campaign._broadcast_reply(9, '98700 00101'), '')
        self.assertEqual(campaign._broadcast_reply(1, '12'), '')
        self.assertTrue(campaign._broadcast_stop('9870000102', reason='too_many'))
        self.assertFalse(self.kochi2.whatsapp_marketing_optin, "offers stop")
        self.assertFalse(campaign._own_message_for(self.kochi2).conversation_id.opt_out,
                         "their own case updates still come")
        self.assertEqual(campaign._own_message_for(self.kochi2).optout_reason, 'too_many')
        campaign.invalidate_recordset()
        self.assertEqual((campaign.replied_count, campaign.optout_count), (1, 1))

    # ------------------------------------------------------------------ automations
    def test_an_automation_sends_once_and_waits_before_repeating(self):
        automation = self.env['epg.whatsapp.automation'].create({
            'name': 'Miss you', 'audience_id': self.everyone.id, 'account_id': self.account.id,
            'body': 'Dear {{name}}, we miss you.', 'repeat_days': 30, 'daily_limit': 1,
            'hour': 23})
        self.assertEqual(automation.due_count, 2)
        # Ten in the morning, whatever the clock says: 23:00 is still to come.
        morning = self.Message._lab_tz().localize(datetime(2026, 9, 17, 10, 0))
        with patch.object(type(self.Message), '_local_now', return_value=morning):
            first = automation._run()
        self.assertEqual(len(first), 1, "one a day")
        self.assertEqual(first.state, 'ready')
        self.assertEqual(first.automation_id, automation)
        self.assertTrue(first.scheduled_at, "at 23:00, so not before then")
        second = automation._run()
        self.assertEqual(len(second), 1)
        self.assertNotEqual(first.partner_id, second.partner_id)
        self.assertFalse(automation._run(), "everyone has had it")
        automation.invalidate_recordset()
        self.assertEqual((automation.run_count, automation.waiting_count, automation.due_count), (3, 2, 0))
        self.env.cr.execute("UPDATE epg_whatsapp_message SET create_date = %s WHERE id = %s",
                            (fields.Datetime.now() - timedelta(days=31), first.id))
        first.invalidate_recordset()
        self.assertEqual(automation._candidates(), first.partner_id, "30 days later it may come again")
        automation.active = False
        self.assertFalse(automation._run())

    def test_the_cron_runs_every_active_automation(self):
        automation = self.env['epg.whatsapp.automation'].create({
            'name': 'Cron', 'audience_id': self.everyone.id, 'account_id': self.account.id,
            'body': 'x', 'daily_limit': 5, 'hour': 0})
        self.env['epg.whatsapp.automation']._cron_run()
        self.assertEqual(len(automation.message_ids), 2)
        self.assertFalse(automation.message_ids[0].scheduled_at, "hour 0 has passed: now")

    def test_a_stop_reason_is_kept(self):
        message = self.template.send(self.kochi)
        message.action_mark_sent()
        message._register_opt_out('not_relevant')
        self.assertEqual(message.optout_reason, 'not_relevant')

    def test_a_marketing_stop_keeps_case_updates_coming(self):
        campaign = self.Campaign.create({
            'name': 'Offer', 'account_id': self.account.id, 'body': 'Offer for {{name}}',
            'partner_ids': [(6, 0, self.kochi.ids)]})
        campaign.action_launch()
        offer = campaign._own_message_for(self.kochi)
        offer.action_mark_sent()
        # A second offer already waiting for them, and an invoice about their own work.
        later = self.Campaign.create({
            'name': 'Offer two', 'account_id': self.account.id, 'body': 'Again {{name}}',
            'partner_ids': [(6, 0, self.kochi.ids)]})
        later.action_launch()
        waiting = later._own_message_for(self.kochi)
        self.assertEqual(waiting.state, 'ready')
        self.assertTrue(offer._register_opt_out('too_many'))
        self.assertFalse(self.kochi.whatsapp_marketing_optin)
        self.assertEqual(waiting.state, 'cancel', "the waiting offer is withdrawn")
        self.assertFalse(offer.conversation_id.opt_out, "the number itself is not stopped")
        self.assertNotEqual(self.kochi.whatsapp_engagement, 'stopped')
        update = self.template.send(self.kochi)
        self.assertEqual(update.state, 'ready', "a message about their own work still goes")
        self.assertNotIn(self.kochi, self.everyone._partners(), "no audience holds them now")
        third = self.Campaign.create({
            'name': 'Offer three', 'account_id': self.account.id, 'body': 'Third {{name}}',
            'partner_ids': [(6, 0, (self.kochi | self.kochi2).ids)]})
        third.action_launch()
        refused = third._own_message_for(self.kochi)
        self.assertEqual(refused.state, 'cancel', "hand-picked, and still refused")
        self.assertIn('switched off', refused.error)
        self.assertEqual(third._own_message_for(self.kochi2).state, 'ready')
        alone = self.Campaign.create({
            'name': 'Offer four', 'account_id': self.account.id, 'body': 'Four {{name}}',
            'partner_ids': [(6, 0, self.kochi.ids)]})
        with self.assertRaises(UserError, msg="nobody left to send to") as caught:
            alone.action_launch()
        self.assertIn('switched off', str(caught.exception))
        self.assertTrue(any('marketing' in (m.body or '') for m in self.kochi.message_ids))


@tagged('post_install', '-at_install')
class TestMarketingRoutes(MarketingSetup, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_marketing()

    def test_the_personal_flyer_page(self):
        campaign = self.Campaign.create({
            'name': 'Aligners', 'account_id': self.account.id, 'body': 'Dear {{name}}, *new*!',
            'image': PNG, 'headline': 'Clear aligners', 'quick_replies': '👍 Interested',
            'partner_ids': [(6, 0, self.kochi.ids)]})
        campaign.action_launch()
        message = campaign.message_ids
        message.action_mark_sent()
        token = message.sudo().access_token
        page = self.url_open('/wa/f/%s' % token, headers={'User-Agent': BROWSER})
        self.assertEqual(page.status_code, 200)
        self.assertIn('Clear aligners', page.text)
        self.assertIn('<b>new</b>', page.text)
        self.assertIn('/wa/r/%s/1' % token, page.text)
        self.assertIn('/wa/f/%s/img' % token, page.text)
        message.invalidate_recordset()
        self.assertTrue(message.clicked_at, "opening the flyer is opening the link")
        img = self.url_open('/wa/f/%s/img' % token)
        self.assertEqual(img.status_code, 200)
        self.assertTrue(img.headers['Content-Type'].startswith('image/'))
        self.assertEqual(self.url_open('/wa/f/not-a-token-at-all-xyz-1234').status_code, 404)

    def test_the_broadcast_page_counts_opens_once_and_takes_a_number(self):
        campaign = self.Campaign.create({
            'name': 'Onam', 'account_id': self.account.id, 'mode': 'broadcast',
            'body': 'Happy Onam', 'quick_replies': '🙏 Thank you', 'image': PNG,
            'partner_ids': [(6, 0, self.kochi.ids)]})
        campaign.action_mark_broadcast_sent()
        url = '/wa/c/%s' % campaign.access_token
        page = self.url_open(url, headers={'User-Agent': BROWSER})
        self.assertEqual(page.status_code, 200)
        self.assertIn('/r/1', page.text)
        self.url_open(url, headers={'User-Agent': BROWSER})
        campaign.invalidate_recordset()
        self.assertEqual(campaign.broadcast_opens, 1, "the same browser counts once")
        self.url_open(url, headers={'User-Agent': 'WhatsApp/2.23'})
        campaign.invalidate_recordset()
        self.assertEqual(campaign.broadcast_opens, 1, "a previewer counts for nothing")
        ask = self.url_open(url + '/r/1', headers={'User-Agent': BROWSER})
        self.assertIn('name="number"', ask.text)
        done = self.url_open(url + '/r/1', data={'number': '98700 00101'},
                             headers={'User-Agent': BROWSER})
        self.assertIn('Thank you', done.text)
        self.assertEqual(campaign._own_message_for(self.kochi).reply_ids.mapped('body'),
                         ['🙏 Thank you'])
        stop = self.url_open(url + '/o', data={'number': '98700 00101', 'reason': 'too_many'},
                             headers={'User-Agent': BROWSER})
        self.assertIn('unsubscribed', stop.text)
        self.assertFalse(self.kochi.whatsapp_marketing_optin)
        self.assertFalse(campaign._own_message_for(self.kochi).conversation_id.opt_out)

    def test_the_personal_stop_page_takes_a_reason(self):
        message = self.template.send(self.kochi)
        message.action_mark_sent()
        url = '/wa/o/%s' % message.sudo().access_token
        ask = self.url_open(url, headers={'User-Agent': BROWSER})
        self.assertIn('Too many messages', ask.text)
        self.url_open(url, data={'reason': 'wrong_number'}, headers={'User-Agent': BROWSER})
        message.invalidate_recordset()
        self.assertTrue(message.opted_out_at)
        self.assertEqual(message.optout_reason, 'wrong_number')
