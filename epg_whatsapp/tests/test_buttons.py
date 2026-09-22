# -*- coding: utf-8 -*-
"""Link buttons, the message page, and a template's timing and conditions.
(client, 2026-09-17)

A message the doctor can act on from the bubble: the buttons are tracked links
in the text, or real buttons on a page reached through one link. A template
says when it applies, how long it waits, and what its second and third sending
say instead.
"""
from datetime import datetime, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import HttpCase, TransactionCase, tagged

from .test_link_channel import BROWSER, LinkSetup

BUTTONS = ('Track my case | https://lab.example/track/{{name}}\n'
           'Call the lab | tel:+919447000000\n'
           'Broken |\n'
           'No link | ftp://x\n'
           'Empty number | tel:{{comment}}')


class ButtonsSetup(LinkSetup):

    @classmethod
    def _setup_buttons(cls):
        cls._setup_link()
        cls.template.buttons = BUTTONS


@tagged('post_install', '-at_install')
class TestButtons(ButtonsSetup, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_buttons()

    def test_link_buttons_are_tracked_links_in_the_text(self):
        message = self.template.send(self.partner)
        self.assertEqual(message._button_rows(), [
            ('Track my case', 'https://lab.example/track/Dr Link'),
            ('Call the lab', 'tel:+919447000000')],
            "a line without a label, a link or a scheme WhatsApp opens is no button")
        token = message.sudo().access_token
        text = message._link_text()
        self.assertIn('🔗 Track my case\n%s/wa/b/%s/1' % (message._base_url(), token), text)
        self.assertIn('/wa/b/%s/2' % token, text)
        self.assertNotIn('lab.example', text, "the doctor taps our link, which counts")
        self.assertNotIn('Broken', text)
        self.assertIn('/wa/r/%s/1' % token, text, "the answers are still there")

    def test_a_page_template_sends_one_link(self):
        self.template.button_style = 'page'
        message = self.template.send(self.partner)
        token = message.sudo().access_token
        text = message._link_text()
        self.assertIn('👉 Tap here to open\n%s/wa/m/%s' % (message._base_url(), token), text)
        self.assertNotIn('/wa/r/', text)
        self.assertNotIn('/wa/b/', text)
        page = message._page()
        self.assertEqual([b['label'] for b in page['buttons']], ['Track my case', 'Call the lab'])
        self.assertEqual([r['label'] for r in page['replies']], ['✅ Received', '📞 Call me'])
        self.assertEqual(page['headline'], 'Hello Dr Link')

    def test_a_tapped_button_is_a_click_on_the_record(self):
        message = self.template.send(self.partner)
        message.action_mark_sent()
        self.assertEqual(message._register_button(1, BROWSER), 'https://lab.example/track/Dr Link')
        self.assertTrue(message.clicked_at)
        self.assertTrue(message.seen_at)
        self.assertTrue(any('tapped “Track my case”' in (m.body or '')
                            for m in self.partner.message_ids))
        self.assertEqual(message._register_button(9, BROWSER), '', "no ninth button")
        self.assertEqual(self.partner.whatsapp_engagement, 'engaged')

    def test_a_preview_bot_is_sent_on_without_a_click(self):
        message = self.template.send(self.partner)
        message.action_mark_sent()
        self.assertEqual(message._register_button(1, 'WhatsApp/2.23.20 A'),
                         'https://lab.example/track/Dr Link')
        self.assertFalse(message.clicked_at)

    def test_send_only_when_skips_records_that_do_not_match(self):
        self.template.filter_domain = "[('city', '=', 'Kochi')]"
        self.assertFalse(self.template.send(self.partner), "not in Kochi: not sent")
        self.partner.city = 'Kochi'
        self.assertTrue(self.template.send(self.partner))
        self.template.filter_domain = "this is not a rule"
        self.assertTrue(self.template.send(self.partner),
                        "a rule that cannot be read lets the message through")

    def test_a_delayed_template_waits_for_the_queue(self):
        self.template.delay_hours = 20
        message = self.template.send(self.partner)
        self.assertEqual(message.state, 'draft')
        self.assertAlmostEqual(message.scheduled_at, fields.Datetime.now() + timedelta(hours=20),
                               delta=timedelta(minutes=1))
        with patch.object(self.env.cr, 'commit', lambda: None):
            self.Message._cron_send_queue()
            self.assertEqual(message.state, 'draft', "not its hour yet")
            message.scheduled_at = fields.Datetime.now() - timedelta(minutes=1)
            self.Message._cron_send_queue()
        self.assertEqual(message.state, 'ready', "its hour came: on the Desk")

    def test_the_second_and_third_time_say_something_else(self):
        self.template.write({'body_2': 'Second: {{name}}', 'body_3': 'Third: {{name}}'})
        first = self.template.send(self.partner)
        second = self.template.send(self.partner)
        third = self.template.send(self.partner)
        fourth = self.template.send(self.partner)
        self.assertEqual(first.body, 'Dear Dr Link, your work is ready.')
        self.assertEqual(second.body, 'Second: Dr Link')
        self.assertEqual(third.body, 'Third: Dr Link')
        self.assertEqual(fourth.body, 'Third: Dr Link', "from the third time on")
        self.template.body_2 = False
        self.assertEqual(self.template.render(self.partner, 2), 'Third: Dr Link',
                         "no second-time text: the next one that is written")
        self.assertEqual(self.template.render(self.partner, 1), 'Dear Dr Link, your work is ready.')

    def test_quiet_hours_hold_a_message_until_the_lab_opens(self):
        self.account.write({'send_from_hour': 9, 'send_until_hour': 18})
        tz = self.Message._lab_tz()
        night = tz.localize(datetime(2026, 9, 17, 22, 0))
        with patch.object(type(self.Message), '_local_now', return_value=night):
            opening = self.account._next_send_moment()
            self.assertEqual(opening, self.Message._utc(tz.localize(datetime(2026, 9, 18, 9, 0))))
            message = self.template.send(self.partner)
        self.assertEqual(message.state, 'ready')
        self.assertEqual(message.scheduled_at, opening, "prepared, and on the Desk at nine")
        day = tz.localize(datetime(2026, 9, 17, 11, 0))
        with patch.object(type(self.Message), '_local_now', return_value=day):
            self.assertFalse(self.account._next_send_moment())
            self.assertFalse(self.template.send(self.partner).scheduled_at)
        self.account.write({'send_from_hour': 0, 'send_until_hour': 0})
        with patch.object(type(self.Message), '_local_now', return_value=night):
            self.assertFalse(self.account._next_send_moment(), "no quiet hours: any time")

    def test_the_placeholder_check_reads_the_buttons_and_the_later_texts(self):
        self.template.write({'buttons': 'Go | https://x.example/{{nonsense_field}}'})
        self.assertFalse(self.template.placeholder_ok)
        self.assertIn('buttons', self.template.placeholder_warnings)
        self.template.write({'buttons': False, 'body_3': '{{another_nonsense}}'})
        self.assertIn('third-time', self.template.placeholder_warnings)
        self.template.body_3 = False
        self.assertTrue(self.template.placeholder_ok)


@tagged('post_install', '-at_install')
class TestButtonRoutes(ButtonsSetup, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_buttons()

    def test_the_button_route_counts_and_goes_on(self):
        message = self.template.send(self.partner)
        message.action_mark_sent()
        token = message.sudo().access_token
        hop = self.url_open('/wa/b/%s/1' % token, allow_redirects=False,
                            headers={'User-Agent': BROWSER})
        self.assertEqual(hop.status_code, 302)
        self.assertEqual(hop.headers['Location'], 'https://lab.example/track/Dr%20Link')
        message.invalidate_recordset()
        self.assertTrue(message.clicked_at)
        self.assertEqual(self.url_open('/wa/b/%s/7' % token).status_code, 404)

    def test_the_message_page_has_real_buttons(self):
        self.template.button_style = 'page'
        message = self.template.send(self.partner)
        self._attach(message)
        message.action_mark_sent()
        token = message.sudo().access_token
        page = self.url_open('/wa/m/%s' % token, headers={'User-Agent': BROWSER})
        self.assertEqual(page.status_code, 200)
        for needle in ('Hello Dr Link', '/wa/d/%s' % token, '/wa/r/%s/1' % token,
                       '/wa/b/%s/2' % token, 'Track my case', 'Invoice 42.pdf'):
            self.assertIn(needle, page.text)
        self.assertNotIn("Don't want these messages", page.text,
                         "no stop link under a message about the doctor's own work")
        message.invalidate_recordset()
        self.assertTrue(message.page_opened_at)
        self.assertTrue(message.seen_at)
        self.assertEqual(self.url_open('/wa/m/no-such-token-xyz-12345').status_code, 404)
