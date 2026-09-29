# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
from odoo.tests import tagged

from odoo.addons.ebshel_ai_suite.services.engines import EngineReply, ToolInvocation
from odoo.addons.ebshel_ai_suite.services.engines.errors import EngineUnavailable
from odoo.addons.ebshel_ai_suite.services.engines.mock import MockEngine
from odoo.addons.ebshel_ai_suite.tests.common import CommunityAICase


@tagged('ebshel_ai', 'post_install', '-at_install')
class TestLivechatAssistant(CommunityAICase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.helper = cls.env['community.ai.assistant'].create({
            'title': 'Web Helper', 'connection_id': cls.connection.id, 'require_confirmation': False})
        cls.livechat = cls.env['im_livechat.channel'].create({
            'name': 'Website support', 'cai_assistant_id': cls.helper.id, 'cai_max_replies': 3})
        cls.public = cls.env.ref('base.public_user')

    def open_chat(self, livechat=None, **info_kwargs):
        livechat = livechat or self.livechat
        guest = self.env['mail.guest'].create({'name': 'Visitor'})
        info = livechat._get_operator_info(lang='en_US', country_id=False, **info_kwargs)
        as_visitor = livechat.with_user(self.public).with_context(guest=guest).sudo()
        channel = self.env['discuss.channel'].with_user(self.public).with_context(guest=guest).sudo().create(
            as_visitor._get_livechat_discuss_channel_vals(**info))
        return channel, guest

    def visitor_says(self, channel, guest, text):
        channel.with_user(self.public).with_context(guest=guest).sudo().message_post(
            body=text, message_type='comment', subtype_xmlid='mail.mt_comment')

    def bodies(self, channel):
        messages = self.env['mail.message'].search([('model', '=', 'discuss.channel'), ('res_id', '=', channel.id),
                                                    ('message_type', '=', 'comment')], order='id')
        return [(m.author_id == channel.livechat_operator_id, str(m.body)) for m in messages]

    def test_ai_operator_session(self):
        channel, _guest = self.open_chat()
        operator = self.livechat.cai_operator_partner_id
        self.assertTrue(operator)
        self.assertFalse(operator.active, 'the AI author never shows up as a contact')
        self.assertEqual(channel.livechat_operator_id, operator)
        self.assertEqual(channel.cai_livechat_assistant_id, self.helper)
        self.assertEqual(channel.name, 'Web Helper')
        self.assertNotIn('cai_assistant_id', self.env['im_livechat.channel'].with_user(self.user_plain).fields_get(),
                         'the configuration is reserved to live chat managers')

    def test_answer_and_visitor_rights(self):
        channel, guest = self.open_chat()
        MockEngine.queue('Our warranty covers **two years**.')
        self.visitor_says(channel, guest, 'What does the warranty cover?')
        self.assertEqual(self.bodies(channel)[-1], (True, '<p>Our warranty covers <strong>two years</strong>.</p>'))
        request = MockEngine.received[-1]
        self.assertIn('website in a live chat', request.turns[0].content)
        self.assertEqual(request.turns[-1].content, 'What does the warranty cover?')
        self.assertEqual([t.name for t in request.tools], ['request_human_agent'])
        self.assertEqual(channel._cai_visitor_env().user, self.public)
        self.assertEqual(channel.cai_reply_count, 1)
        usage = self.env['community.ai.usage'].search([('purpose', '=', 'chat')], order='id desc', limit=1)
        self.assertEqual(usage.user_id, self.public, 'usage is accounted to the visitor, not to an admin')

    def test_handover_without_agents(self):
        channel, guest = self.open_chat()
        MockEngine.queue(EngineReply(text='I will get a colleague.',
                                     tool_invocations=[ToolInvocation('h', 'request_human_agent', {'reason': 'demo'})]))
        self.visitor_says(channel, guest, 'I want a demo')
        bodies = self.bodies(channel)
        self.assertEqual(bodies[-2], (True, '<p>I will get a colleague.</p>'))
        self.assertIn('leave your email', bodies[-1][1])
        self.assertEqual(channel.livechat_failure, 'no_agent')

    def test_engine_failure_and_reply_cap(self):
        channel, guest = self.open_chat()
        self.connection.retry_count = 0
        MockEngine.queue(EngineUnavailable(detail='down'))
        self.visitor_says(channel, guest, 'Hello?')
        self.assertIn('member of our team', self.bodies(channel)[-2][1])
        channel2, guest2 = self.open_chat()
        channel2.cai_reply_count = 3
        MockEngine.reset()
        self.visitor_says(channel2, guest2, 'One more question')
        self.assertFalse(MockEngine.received, 'the cap hands over without calling the engine')

    def test_stops_when_human_joined_or_ended(self):
        channel, guest = self.open_chat()
        channel.livechat_end_dt = '2026-01-01 00:00:00'
        self.visitor_says(channel, guest, 'Anyone?')
        self.assertFalse(MockEngine.received)
        self.assertFalse(channel._cai_should_answer(self.env['mail.message']))

    def test_regular_channels_unchanged(self):
        plain = self.env['im_livechat.channel'].create({'name': 'Humans only'})
        info = plain._get_operator_info(lang='en_US', country_id=False)
        self.assertNotEqual(info.get('operator_model'), 'community.ai.assistant')
        returning = self.livechat._get_operator_info(lang='en_US', country_id=False,
                                                     previous_operator_id=self.env.user.partner_id.id)
        self.assertNotEqual(returning.get('operator_model'), 'community.ai.assistant',
                            'a visitor coming back to a human is not captured by the AI')
