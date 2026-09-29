# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
import base64
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.fields import Command
from odoo.tests import tagged
from odoo.tests.common import new_test_user

from ..services.conversation_runner import ConversationRunner
from ..services.engines import EngineReply, EngineSettings, ToolInvocation
from ..services.engines.errors import EngineUnavailable
from ..services.engines.mock import MockEngine
from ..services.engines.openai_compat import OpenAICompatibleEngine
from ..services.text_tools import markdown_to_html
from .common import CommunityAICase

PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==')


@tagged('ebshel_ai')
class TestSkillsetsAndPresets(CommunityAICase):

    def start(self, user=None, **kwargs):
        Session = self.env['community.ai.session'].with_user(user or self.user_ai)
        return Session.browse(Session.cai_start(**kwargs)['id'])

    def test_skillset_capabilities_and_instructions(self):
        read_cap = self.env['community.ai.capability'].create({
            'name': 'Skill read', 'technical_identifier': 't_skill_read', 'handler_key': 'read_record',
            'model_id': self.env.ref('base.model_res_partner').id, 'description': 'Read'})
        skillset = self.env['community.ai.skillset'].create({
            'name': 'Partner reading', 'instructions': 'Always read before answering.',
            'capability_ids': [Command.set(read_cap.ids)]})
        self.assistant.skillset_ids = [Command.set(skillset.ids)]
        effective = self.assistant._cai_effective_capabilities()
        self.assertIn(read_cap, effective)
        self.assertIn(self.cap_search, effective)
        self.assertIn('Always read before answering.', self.assistant._cai_system_text())
        skillset.active = False
        self.assertNotIn(read_cap, self.assistant._cai_effective_capabilities())
        self.assistant.reply_style = 'rigorous'
        self.assertIn('meticulous', self.assistant._cai_system_text())

    def test_status_events(self):
        session = self.start(assistant_id=self.assistant.id)
        events = list(ConversationRunner(self.env(user=self.user_ai), session).iter_events('hello'))
        statuses = [e['text'] for e in events if e['type'] == 'status']
        self.assertIn('Analyzing your request…', statuses)

    def test_test_button(self):
        action = self.assistant.action_cai_test()
        self.assertEqual(action['tag'], 'ebshel_ai_suite.console_page')
        self.assertEqual(action['params']['assistant_id'], self.assistant.id)

    def test_context_presets(self):
        Preset = self.env['community.ai.context.preset']
        Preset.search([]).active = False
        other_assistant = self.env['community.ai.assistant'].create({'title': 'Contact expert'})
        generic = Preset.create({'name': 'Generic', 'purpose': 'assist', 'sequence': 50,
                                 'quick_prompt_ids': [Command.create({'title': 'Hi', 'prompt': 'Say hi'})]})
        specific = Preset.create({
            'name': 'Partners', 'purpose': 'assist', 'needs_record': True, 'assistant_id': other_assistant.id,
            'model_ids': [Command.set(self.env.ref('base.model_res_partner').ids)],
            'instructions': 'PARTNER CONTEXT RULES',
            'quick_prompt_ids': [Command.create({'title': 'Profile', 'prompt': 'Profile this contact'})]})
        partner = self.env['res.partner'].create({'name': 'Preset partner'})
        Session = self.env['community.ai.session'].with_user(self.user_ai)
        self.assertEqual(Session.cai_launch_info()['preset']['id'], generic.id)
        info = Session.cai_launch_info('res.partner', partner.id)['preset']
        self.assertEqual((info['id'], info['assistant_id']), (specific.id, other_assistant.id))
        self.assertEqual(info['buttons'][0]['prompt'], 'Profile this contact')
        payload = Session.cai_start(context_model='res.partner', context_res_id=partner.id, preset_id=specific.id)
        self.assertEqual(payload['assistant']['id'], other_assistant.id)
        Session.browse(payload['id']).cai_send('Profile this contact')
        self.assertIn('PARTNER CONTEXT RULES', MockEngine.received[-1].turns[0].content)
        # a preset id that does not match the context is ignored
        mismatch = Session.cai_start(preset_id=specific.id)
        self.assertFalse(Session.browse(mismatch['id']).preset_id)
        specific.group_ids = [Command.set(self.env.ref('ebshel_ai_suite.group_cai_manager').ids)]
        self.assertEqual(Session.cai_launch_info('res.partner', partner.id)['preset']['id'], generic.id)

    def test_writing_shortcuts(self):
        Preset = self.env['community.ai.context.preset']
        Preset.search([('purpose', '=', 'write')]).active = False
        Preset.create({'name': 'W', 'purpose': 'write',
                       'quick_prompt_ids': [Command.create({'title': 'Pirate', 'prompt': 'Talk like a pirate'})]})
        options = self.env['community.ai.text.service'].with_user(self.user_ai).cai_writing_options('res.partner')
        self.assertEqual(options['shortcuts'][0]['title'], 'Pirate')
        service = self.env['community.ai.text.service'].with_user(self.user_ai)
        with self.assertRaises(UserError):
            service.cai_transform_text('custom', 'hello', {})
        service.cai_transform_text('custom', 'hello', {'instruction': 'Talk like a pirate'})
        self.assertIn('Talk like a pirate', MockEngine.received[-1].turns[1].content)

    def test_open_view_grouping(self):
        fields_ = self.env['ir.model.fields'].search([('model', '=', 'res.partner'), ('name', 'in', ['city'])])
        cap = self.env['community.ai.capability'].create({
            'name': 'Open', 'technical_identifier': 't_open_grouped', 'handler_key': 'open_view', 'description': 'o',
            'model_id': self.env.ref('base.model_res_partner').id, 'field_ids': [Command.set(fields_.ids)]})
        self.assistant.capability_ids = [Command.link(cap.id)]
        session = self.start(assistant_id=self.assistant.id)
        session.cai_send('[[call:t_open_grouped {"group_by": "create_date", "interval": "month"}]]')
        self.assertEqual(session.exchange_ids[-1].navigation_action['context']['group_by'], ['create_date:month'])
        session.cai_send('[[call:t_open_grouped {"group_by": "email"}]]')
        self.assertEqual(session.exchange_ids.filtered(lambda e: e.speaker_type == 'tool')[-1].tool_status, 'failed')

    def test_source_link_wizard(self):
        wizard = self.env['community.ai.source.link.wizard'].create({
            'urls': 'https://example.com/a\n\nhttps://example.com/b\nhttps://example.com/a',
            'assistant_ids': [Command.set(self.assistant.ids)]})
        wizard.action_cai_add()
        sources = self.env['community.ai.source'].search([('url', 'like', 'https://example.com/')])
        self.assertEqual(len(sources), 2)
        self.assertEqual(set(sources.mapped('state')), {'queued'})
        self.assertEqual(sources.assistant_ids, self.assistant)
        with self.assertRaises(UserError):
            self.env['community.ai.source.link.wizard'].create({'urls': 'ftp://x'}).action_cai_add()


@tagged('ebshel_ai')
class TestConversationFilesAndActions(CommunityAICase):

    def session(self, user=None, **kwargs):
        Session = self.env['community.ai.session'].with_user(user or self.user_ai)
        return Session.browse(Session.cai_start(self.assistant.id, **kwargs)['id'])

    def test_text_file_is_used_as_temporary_source(self):
        session = self.session()
        staged = session.cai_stage_file('policy.txt', base64.b64encode(b'Refunds are possible within 14 days.'))
        self.assertFalse(staged['is_image'])
        self.assertEqual(session._cai_payload()['staged_files'][0]['name'], 'policy.txt')
        session.cai_send('How long for refunds?')
        user_ex = session.exchange_ids.filtered(lambda e: e.speaker_type == 'user')
        self.assertEqual(user_ex.file_ids.name, 'policy.txt')
        reference = next(t.content for t in MockEngine.received[-1].turns if 'Reference material' in t.content)
        self.assertIn('Refunds are possible within 14 days.', reference)
        self.assertIn('attached file policy.txt', reference)
        self.assertFalse(session._cai_payload()['staged_files'])

    def test_image_file_is_sent_to_the_engine(self):
        session = self.session()
        session.cai_stage_file('receipt.png', base64.b64encode(PNG))
        session.cai_send('Which expense matches this receipt?')
        anchor = MockEngine.received[-1].turns[-1]
        self.assertEqual(anchor.images, [('image/png', PNG)])
        self.assertIn('with 1 image(s)', session.exchange_ids[-1].message_body)

    def test_file_limits_and_ownership(self):
        session = self.session()
        with self.assertRaises(UserError):
            session.cai_stage_file('empty.txt', '')
        with self.assertRaises(UserError):
            session.cai_stage_file('bin.dat', base64.b64encode(b'\x00\x01\x02' * 10))
        staged = session.cai_stage_file('a.txt', base64.b64encode(b'hello'))
        session.cai_unstage_file(staged['id'])
        self.assertFalse(session.file_ids)
        other = new_test_user(self.env, login='cai_other_files',
                              groups='base.group_user,ebshel_ai_suite.group_cai_user')
        with self.assertRaises(AccessError):
            session.with_user(other).cai_stage_file('x.txt', base64.b64encode(b'x'))
        own = session.cai_stage_file('b.txt', base64.b64encode(b'secret notes'))
        self.assertFalse(self.env['community.ai.session.file'].with_user(other).search([('id', '=', own['id'])]))

    def test_answer_actions(self):
        partner = self.env['res.partner'].create({'name': 'Reply target'})
        session = self.session(context_model='res.partner', context_res_id=partner.id)
        MockEngine.queue('Dear **customer**,\n\n- item <b>one</b>')
        session.cai_send('Draft a reply')
        answer = session.exchange_ids[-1]
        self.assertTrue(answer._cai_payload()['can_post'])
        action = answer.with_user(self.user_ai).cai_compose_action('note')
        self.assertEqual(action['res_model'], 'mail.compose.message')
        self.assertEqual(action['context']['default_res_ids'], partner.ids)
        self.assertEqual(action['context']['default_subtype_xmlid'], 'mail.mt_note')
        self.assertEqual(str(action['context']['default_body']),
                         '<p>Dear <strong>customer</strong>,</p><ul><li>item &lt;b&gt;one&lt;/b&gt;</li></ul>')
        no_context = self.session()
        no_context.cai_send('hi')
        with self.assertRaises(UserError):
            no_context.exchange_ids[-1].with_user(self.user_ai).cai_compose_action('message')

    def test_markdown_escaping(self):
        html = markdown_to_html('# Title\n\n**<script>alert(1)</script>**')
        self.assertNotIn('<script>', html)
        self.assertIn('<strong>&lt;script&gt;', html)

    def test_generated_image_in_chat(self):
        cap = self.env.ref('ebshel_ai_suite.capability_generate_image')
        self.assistant.capability_ids = [Command.link(cap.id)]
        session = self.session()
        session.cai_send('[[call:generate_image {"prompt": "A red bicycle in a studio"}]]')
        final = session.exchange_ids[-1]
        self.assertEqual(final.media[0]['type'], 'image')
        attachment = self.env['ir.attachment'].browse(final.media[0]['attachment_id'])
        self.assertEqual((attachment.res_model, attachment.res_id), ('community.ai.session', session.id))
        self.assertEqual(attachment.raw[:4], b'\x89PNG')


@tagged('ebshel_ai')
class TestServerActionTools(CommunityAICase):

    def setUp(self):
        super().setUp()
        self.partner = self.env['res.partner'].create({'name': 'Tool target'})
        self.tool_action = self.env['ir.actions.server'].create({
            'name': 'Set job title', 'model_id': self.env.ref('base.model_res_partner').id, 'state': 'code',
            'code': "record.write({'function': cai_args.get('job')})\ncai_output['job_set'] = cai_args.get('job')",
        })
        action = self.tool_action.action_cai_offer_as_tool()
        self.tool = self.env['community.ai.capability'].browse(action['res_id'])
        self.tool.write({'argument_schema': {'job': {'type': 'string', 'required': True}},
                         'requires_confirmation': False})

    def test_offer_as_tool(self):
        self.assertEqual(self.tool.handler_key, 'server_action')
        self.assertEqual(self.tool.execution_type, 'write')
        schema = self.tool._cai_schema()
        self.assertEqual(set(schema), {'record_id', 'job'})
        self.assertEqual(self.tool_action.action_cai_offer_as_tool()['res_id'], self.tool.id, 'no duplicate')
        self.tool.declared_read_only = True
        self.assertEqual(self.tool.execution_type, 'read')
        with self.assertRaises(ValidationError):
            self.env['community.ai.capability'].create({
                'name': 'Bad', 'technical_identifier': 't_bad_sa', 'handler_key': 'server_action',
                'model_id': self.env.ref('base.model_res_partner').id, 'description': 'x'})

    def test_tool_from_chat(self):
        self.assistant.write({'capability_ids': [Command.link(self.tool.id)], 'require_confirmation': False})
        Session = self.env['community.ai.session'].with_user(self.user_ai)
        session = Session.browse(Session.cai_start(self.assistant.id)['id'])
        session.cai_send('[[call:%s {"record_id": %d, "job": "Buyer"}]]' % (self.tool.technical_identifier,
                                                                            self.partner.id))
        tool_ex = session.exchange_ids.filtered(lambda e: e.speaker_type == 'tool')
        self.assertEqual(tool_ex.tool_status, 'executed')
        self.assertIn('job_set', tool_ex.message_body)
        self.assertEqual(self.partner.function, 'Buyer')

    def decision(self, auto):
        return self.env['ir.actions.server'].create({
            'name': 'AI: decide job', 'model_id': self.env.ref('base.model_res_partner').id,
            'state': 'cai_decision', 'cai_instruction': 'Pick a job for {{ record.name }}',
            'cai_capability_ids': [Command.set(self.tool.ids)], 'cai_auto_execute': auto,
        })

    def test_ai_decision_auto_execute_binds_record(self):
        decoy = self.env['res.partner'].create({'name': 'Decoy'})
        MockEngine.queue(EngineReply(tool_invocations=[ToolInvocation(
            'c1', self.tool.technical_identifier, {'job': 'CTO', 'record_id': decoy.id})]), 'Chose CTO.')
        result = self.decision(True).with_context(active_model='res.partner', active_id=self.partner.id,
                                                  active_ids=self.partner.ids).run()
        self.assertEqual(self.partner.function, 'CTO')
        self.assertFalse(decoy.function, 'the AI cannot redirect a tool to another record')
        self.assertIn('Chose CTO.', result['params']['message'])
        request = MockEngine.received[0]
        self.assertNotIn('record_id', request.tools[0].parameters['properties'])
        self.assertIn('Pick a job for Tool target', request.turns[1].content)
        audit = self.env['community.ai.audit'].search([('operation', '=', 'server_action')], limit=1)
        self.assertEqual(audit.confirmation_status, 'preapproved')

    def test_ai_decision_with_approval(self):
        MockEngine.queue(EngineReply(tool_invocations=[ToolInvocation(
            'c1', self.tool.technical_identifier, {'job': 'CEO'})]), 'Proposed CEO.')
        self.decision(False).with_context(active_model='res.partner', active_id=self.partner.id,
                                          active_ids=self.partner.ids).run()
        self.assertFalse(self.partner.function)
        operation = self.env['community.ai.operation'].search([('capability_id', '=', self.tool.id)])
        self.assertEqual(operation.state, 'proposed')
        with self.assertRaises(AccessError):
            operation.with_user(self.user_ai).cai_decide(True)
        operation.with_user(self.user_manager).cai_decide(True)
        self.assertEqual(self.partner.function, 'CEO')

    def test_ai_decision_tool_model_constraint(self):
        other = self.env['community.ai.capability'].create({
            'name': 'Activity search', 'technical_identifier': 't_act_search', 'handler_key': 'search_records',
            'model_id': self.env.ref('mail.model_mail_activity').id, 'description': 'x'})
        with self.assertRaises(ValidationError):
            self.env['ir.actions.server'].create({
                'name': 'Bad', 'model_id': self.env.ref('base.model_res_partner').id, 'state': 'cai_decision',
                'cai_instruction': 'x', 'cai_capability_ids': [Command.set(other.ids)]})


@tagged('ebshel_ai', 'post_install', '-at_install')   # generated views are filtered while modules load
class TestTemplatesFieldsVoice(CommunityAICase):

    def template(self):
        return self.env['mail.template'].create({
            'name': 'AI template', 'model_id': self.env.ref('base.model_res_partner').id,
            'body_html': '<p>Hello <t t-out="object.name"/>,</p>'
                         '<div class="o_cai_prompt">Write one welcoming sentence</div><p>Regards</p>'})

    def test_template_prompt_blocks(self):
        partners = self.env['res.partner'].create([{'name': 'Ann'}, {'name': 'Bob'}])
        MockEngine.queue('Welcome aboard, Ann!', 'Welcome aboard, Bob!')
        rendered = self.template()._render_field('body_html', partners.ids)
        self.assertEqual(rendered[partners[0].id], '<p>Hello Ann,</p><div class="o_cai_generated">'
                                                   '<p>Welcome aboard, Ann!</p></div><p>Regards</p>')
        self.assertIn('Welcome aboard, Bob!', rendered[partners[1].id])
        self.assertNotIn('o_cai_prompt', rendered[partners[1].id])
        self.assertIn('Ann', MockEngine.received[0].turns[1].content, 'the record is sent as context')

    def test_template_prompt_block_failures_are_invisible(self):
        partner = self.env['res.partner'].create({'name': 'Cid'})
        MockEngine.queue(EngineUnavailable(detail='down'))
        self.connection.retry_count = 0
        rendered = self.template()._render_field('body_html', partner.ids)[partner.id]
        self.assertEqual(rendered, '<p>Hello Cid,</p><p>Regards</p>')
        plain = new_test_user(self.env, login='cai_tpl_plain', groups='base.group_user,mail.group_mail_template_editor')
        rendered = self.template().with_user(plain)._render_field('body_html', partner.ids)[partner.id]
        self.assertNotIn('Write one welcoming sentence', rendered)

    def field_rule(self, **values):
        return self.env['community.ai.field.rule'].create(dict({
            'name': 'Job', 'model_id': self.env.ref('base.model_res_partner').id,
            'field_id': self.env.ref('base.field_res_partner__function').id, 'instruction': 'Job of {{ record.name }}',
        }, **values))

    def test_field_refresh_button(self):
        rule = self.field_rule()
        partner = self.env['res.partner'].create({'name': 'Refresh me', 'function': 'Old'})
        MockEngine.queue({'value': 'New title'})
        result = self.env['community.ai.field.rule'].with_user(self.user_ai).cai_generate_for_field(
            'res.partner', partner.id, 'function')
        self.assertTrue(result['written'], 'the refresh button regenerates even filled values')
        self.assertEqual(partner.function, 'New title')
        with self.assertRaises(UserError):
            self.env['community.ai.field.rule'].with_user(self.user_ai).cai_generate_for_field(
                'res.partner', partner.id, 'email')
        rule.action_cai_add_form_button()
        self.assertTrue(rule.form_view_id)
        arch = self.env['res.partner'].get_view(view_type='form')['arch']
        self.assertIn('cai_ai_field', arch)
        rule.action_cai_remove_form_button()
        self.assertFalse(rule.form_view_id.exists())

    def test_fill_empty_cron(self):
        rule = self.field_rule(fill_empty_daily=True)
        empty = self.env['res.partner'].create({'name': 'Needs a job'})
        self.env['community.ai.field.rule']._cai_cron_fill_empty()
        job = self.env['community.ai.job'].search([('field_rule_id', '=', rule.id), ('res_id', '=', empty.id)])
        self.assertEqual(job.state, 'queued')
        self.env['community.ai.field.rule']._cai_cron_fill_empty()
        self.assertEqual(self.env['community.ai.job'].search_count(
            [('field_rule_id', '=', rule.id), ('res_id', '=', empty.id)]), 1, 'no duplicate jobs')

    def test_transcription(self):
        service = self.env['community.ai.text.service'].with_user(self.user_ai)
        result = service.cai_transcribe(base64.b64encode(b'fake-audio-bytes'), 'audio/webm', summarize=True)
        self.assertIn('ship on Friday', result['text'])
        self.assertTrue(result['summary'])
        self.assertIn('action items', MockEngine.received[-1].turns[1].content)
        usage = self.env['community.ai.usage'].search([('purpose', '=', 'voice')])
        self.assertEqual(len(usage), 1)
        with self.assertRaises(UserError):
            service.cai_transcribe('', 'audio/webm')
        with self.assertRaises(AccessError):
            self.env['community.ai.text.service'].with_user(self.user_plain).cai_transcribe('eA==')

    def test_openai_transcription_request(self):
        class Response:
            status_code = 200
            text = '{"text": "hello there"}'

            @staticmethod
            def json():
                return {'text': 'hello there'}

        engine = OpenAICompatibleEngine(EngineSettings(engine_type='openai_compatible', secret='sk-x' * 5,
                                                       transcription_model='whisper-1'))
        with patch('requests.request', return_value=Response()) as call:
            self.assertEqual(engine.transcribe(b'abc', 'audio/webm;codecs=opus'), 'hello there')
        kwargs = call.call_args.kwargs
        self.assertEqual(kwargs['data'], {'model': 'whisper-1'})
        self.assertEqual(kwargs['files']['file'][0], 'recording.webm')
        self.assertNotIn('Content-Type', kwargs['headers'])

    def test_web_search_backends(self):
        cap = self.env.ref('ebshel_ai_suite.capability_web_lookup')
        self.assistant.capability_ids = [Command.link(cap.id)]
        Session = self.env['community.ai.session'].with_user(self.user_ai)
        session = Session.browse(Session.cai_start(self.assistant.id)['id'])
        session.cai_send('[[call:web_search {"query": "odoo"}]]')
        self.assertEqual(session.exchange_ids.filtered(lambda e: e.speaker_type == 'tool')[-1].tool_status, 'failed')
        params = self.env['ir.config_parameter']
        params.set_param('ebshel_ai_suite.web_backend', 'brave')
        params.set_param('ebshel_ai_suite.brave_api_key', 'brave-key')

        class Response:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {'web': {'results': [{'title': 'Odoo', 'url': 'https://odoo.example', 'description': 'ERP'}]}}

        with patch('requests.get', return_value=Response()) as call:
            session.cai_send('[[call:web_search {"query": "odoo erp"}]]')
        self.assertEqual(call.call_args.kwargs['headers']['X-Subscription-Token'], 'brave-key')
        tool_ex = session.exchange_ids.filtered(lambda e: e.speaker_type == 'tool')[-1]
        self.assertEqual(tool_ex.tool_status, 'executed')
        self.assertIn('https://odoo.example', tool_ex.message_body)
