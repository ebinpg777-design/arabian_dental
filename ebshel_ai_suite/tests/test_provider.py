# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
import json
from unittest.mock import patch

import requests

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged

from ..services.engines import ChatTurn, EngineRequest, EngineSettings, ToolInvocation, ToolSpec
from ..services.engines.errors import (
    EngineAuthError,
    EngineBadResponse,
    EngineRateLimited,
    EngineTimeout,
)
from ..services.engines.gemini import GeminiEngine
from ..services.engines.mock import MockEngine
from ..services.engines.openai_compat import OpenAICompatibleEngine
from ..services.llm_gateway import LLMGateway
from .common import CommunityAICase

SECRET = 'sk-test-THISISASECRETKEY1234567890'


class FakeResponse:
    def __init__(self, status=200, payload=None, text=None, lines=None):
        self.status_code = status
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload)
        self._lines = lines or []

    def json(self):
        if self._payload is None:
            raise ValueError('no json')
        return self._payload

    def iter_lines(self, decode_unicode=True):
        yield from self._lines

    def close(self):
        pass


def settings(**kw):
    values = dict(engine_type='openai_compatible', secret=SECRET, model='gpt-test', timeout=5)
    values.update(kw)
    return EngineSettings(**values)


@tagged('ebshel_ai')
class TestProviders(CommunityAICase):

    # -- mock engine / connection ------------------------------------------
    def test_valid_credentials(self):
        self.connection.action_cai_test_connection()
        self.assertEqual(self.connection.last_check_state, 'ok')
        self.connection.action_cai_refresh_models()
        self.assertIn('mock-small', self.connection.available_models)

    def test_invalid_credentials(self):
        self.connection.endpoint_url = 'mock://auth-error'
        self.connection.action_cai_test_connection()
        self.assertEqual(self.connection.last_check_state, 'failed')
        with self.assertRaises(EngineAuthError) as caught:
            LLMGateway(self.env).generate([ChatTurn('user', 'hi')], connection=self.connection)
        self.assertIn('could not authenticate', caught.exception.public_message)
        self.assertNotIn('Mock:', caught.exception.public_message, 'raw provider detail must stay hidden')

    def test_timeout_is_logged_as_failed_usage(self):
        self.connection.endpoint_url = 'mock://timeout'
        # no assertRaises here: it would roll back the usage line through a savepoint
        try:
            LLMGateway(self.env).generate([ChatTurn('user', 'hi')], connection=self.connection)
        except EngineTimeout as exc:
            self.assertIn('took too long', exc.public_message)
        else:
            self.fail('EngineTimeout not raised')
        usage = self.env['community.ai.usage'].search([('connection_id', '=', self.connection.id)], limit=1)
        self.assertEqual((usage.status, usage.error_code), ('failed', 'timeout'))

    def test_no_connection_configured(self):
        self.env.company.cai_connection_id = False
        self.env['community.ai.connection'].search([]).active = False
        session = self.env['community.ai.session'].with_user(self.user_ai).cai_start(self.assistant.id)
        self.assistant.connection_id = False
        events = self.env['community.ai.session'].with_user(self.user_ai).browse(session['id']).cai_send('hi')['events']
        self.assertEqual(events[-1]['type'], 'error')
        self.assertIn('No AI connection is configured', events[-1]['message'])

    def test_malformed_response(self):
        self.connection.endpoint_url = 'mock://malformed'
        with self.assertRaises(EngineBadResponse):
            LLMGateway(self.env).generate([ChatTurn('user', 'hi')], connection=self.connection)

    def test_retry_on_rate_limit(self):
        self.connection.retry_count = 1
        MockEngine.queue(EngineRateLimited(detail='429'), 'second attempt works')
        with patch('odoo.addons.ebshel_ai_suite.services.llm_gateway.time.sleep'):
            reply = LLMGateway(self.env).generate([ChatTurn('user', 'hi')], connection=self.connection)
        self.assertEqual(reply.text, 'second attempt works')

    def test_usage_records_tokens_and_cost(self):
        self.connection.write({'cost_input_per_million': 1000.0, 'cost_output_per_million': 2000.0})
        LLMGateway(self.env).generate([ChatTurn('user', 'one two three')], connection=self.connection,
                                      purpose='text')
        usage = self.env['community.ai.usage'].search([('connection_id', '=', self.connection.id)], limit=1)
        self.assertEqual(usage.status, 'success')
        self.assertEqual(usage.purpose, 'text')
        self.assertGreater(usage.token_total, 0)
        self.assertAlmostEqual(usage.estimated_cost,
                               (usage.input_tokens * 1000 + usage.output_tokens * 2000) / 1e6, places=6)

    # -- secrets -------------------------------------------------------------
    def test_secret_is_write_only(self):
        conn = self.connection.with_user(self.user_admin)
        conn.secret_input = SECRET
        self.assertEqual(self.connection.sudo().secret_key, SECRET)
        self.assertTrue(conn.has_secret)
        self.assertEqual(conn.secret_hint, '•••• 7890')
        conn.invalidate_recordset(['secret_input'])
        self.assertFalse(conn.secret_input, 'the stored key is never sent back to the client')
        with self.assertRaises(AccessError):
            conn.read(['secret_key'])   # AI admins are not Settings administrators
        with self.assertRaises(AccessError):
            self.connection.with_user(self.user_ai).read(['name'])

    def test_environment_secret(self):
        with self.assertRaises(ValidationError):
            self.connection.write({'secret_source': 'environment', 'secret_env_var': 'PGPASSWORD'})
        self.connection.write({'secret_source': 'environment', 'secret_env_var': 'CAI_TEST_KEY'})
        with patch.dict('os.environ', {'CAI_TEST_KEY': SECRET}):
            self.assertEqual(self.connection._cai_engine_settings().secret, SECRET)

    def test_secret_not_in_repr_or_errors(self):
        self.assertNotIn(SECRET, repr(settings()))
        engine = OpenAICompatibleEngine(settings())
        with patch('requests.request', return_value=FakeResponse(401, text=f'bad key {SECRET}')):
            with self.assertRaises(EngineAuthError) as caught:
                engine.complete(EngineRequest(turns=[ChatTurn('user', 'x')]))
        self.assertNotIn(SECRET, caught.exception.detail)

    # -- OpenAI-compatible adapter ------------------------------------------
    def test_openai_payload_and_tool_parsing(self):
        payload = {
            'model': 'gpt-test',
            'choices': [{'finish_reason': 'tool_calls', 'message': {'content': None, 'tool_calls': [
                {'id': 'call_1', 'type': 'function',
                 'function': {'name': 'search', 'arguments': '{"query": "Azure"}'}}]}}],
            'usage': {'prompt_tokens': 12, 'completion_tokens': 4},
        }
        engine = OpenAICompatibleEngine(settings(temperature=0.2, max_tokens=50))
        with patch('requests.request', return_value=FakeResponse(200, payload)) as call:
            reply = engine.complete(EngineRequest(
                turns=[ChatTurn('system', 'rules'), ChatTurn('user', 'find Azure')],
                tools=[ToolSpec('search', 'Search', {'type': 'object', 'properties': {}})], json_output=True))
        sent = json.loads(call.call_args.kwargs['data'])
        headers = call.call_args.kwargs['headers']
        self.assertEqual(headers['Authorization'], f'Bearer {SECRET}')
        self.assertEqual(sent['response_format'], {'type': 'json_object'})
        self.assertEqual(sent['tools'][0]['function']['name'], 'search')
        self.assertEqual((sent['temperature'], sent['max_tokens']), (0.2, 50))
        self.assertEqual(reply.tool_invocations[0].arguments, {'query': 'Azure'})
        self.assertEqual((reply.input_tokens, reply.output_tokens), (12, 4))

    def test_openai_assistant_tool_turn_encoding(self):
        turn = ChatTurn('assistant', '', tool_invocations=[ToolInvocation('c1', 'search', {'q': 1})])
        encoded = OpenAICompatibleEngine._encode_turn(turn)
        self.assertEqual(encoded['tool_calls'][0]['function']['arguments'], '{"q": 1}')
        tool = OpenAICompatibleEngine._encode_turn(ChatTurn('tool', 'result', tool_call_id='c1'))
        self.assertEqual(tool, {'role': 'tool', 'tool_call_id': 'c1', 'content': 'result'})

    def test_openai_malformed_and_errors(self):
        engine = OpenAICompatibleEngine(settings())
        request = EngineRequest(turns=[ChatTurn('user', 'x')])
        with patch('requests.request', return_value=FakeResponse(200, {'unexpected': True})):
            with self.assertRaises(EngineBadResponse):
                engine.complete(request)
        with patch('requests.request', return_value=FakeResponse(200, None, text='<html>')):
            with self.assertRaises(EngineBadResponse):
                engine.complete(request)
        with patch('requests.request', return_value=FakeResponse(429, {'error': 'slow down'})):
            with self.assertRaises(EngineRateLimited):
                engine.complete(request)
        with patch('requests.request', side_effect=requests.exceptions.ReadTimeout('timed out')):
            with self.assertRaises(EngineTimeout):
                engine.complete(request)

    def test_openai_malformed_tool_arguments(self):
        payload = {'choices': [{'message': {'tool_calls': [
            {'id': 'c', 'function': {'name': 'search', 'arguments': '{not json'}}]}}]}
        with patch('requests.request', return_value=FakeResponse(200, payload)):
            reply = OpenAICompatibleEngine(settings()).complete(EngineRequest(turns=[ChatTurn('user', 'x')]))
        self.assertIn('__unparsable__', reply.tool_invocations[0].arguments)

    def test_openai_streaming(self):
        chunks = [
            {'choices': [{'delta': {'content': 'Hel'}}]},
            {'choices': [{'delta': {'content': 'lo'}}]},
            {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'c1', 'function': {'name': 'sea',
                                                                                        'arguments': '{"q":'}}]}}]},
            {'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': {'name': 'rch', 'arguments': '"x"}'}}]},
                          'finish_reason': 'tool_calls'}]},
            {'choices': [], 'usage': {'prompt_tokens': 5, 'completion_tokens': 2}},
        ]
        lines = [f'data: {json.dumps(c)}' for c in chunks] + ['', 'data: [DONE]']
        with patch('requests.request', return_value=FakeResponse(200, {}, lines=lines)):
            pieces = list(OpenAICompatibleEngine(settings()).stream(EngineRequest(turns=[ChatTurn('user', 'x')])))
        self.assertEqual(''.join(p.text for p in pieces), 'Hello')
        final = pieces[-1]
        self.assertTrue(final.done)
        self.assertEqual((final.tool_invocations[0].name, final.tool_invocations[0].arguments), ('search', {'q': 'x'}))
        self.assertEqual((final.input_tokens, final.output_tokens), (5, 2))

    # -- Gemini adapter -----------------------------------------------------
    def test_gemini_payload_and_parsing(self):
        payload = {
            'candidates': [{'finishReason': 'STOP', 'content': {'parts': [
                {'text': 'Looking up'}, {'functionCall': {'name': 'search', 'args': {'query': 'Azure'}}}]}}],
            'usageMetadata': {'promptTokenCount': 9, 'candidatesTokenCount': 3},
        }
        engine = GeminiEngine(settings(engine_type='gemini', model='gemini-test'))
        turns = [ChatTurn('system', 'rules'), ChatTurn('user', 'a'), ChatTurn('user', 'b'),
                 ChatTurn('assistant', '', tool_invocations=[ToolInvocation('g1', 'search', {'q': 1})]),
                 ChatTurn('tool', '{"status": "executed"}', tool_call_id='g1', tool_name='search')]
        schema = {'type': 'object', 'additionalProperties': False,
                  'properties': {'q': {'type': 'string', 'minimum': 1}}}
        with patch('requests.request', return_value=FakeResponse(200, payload)) as call:
            reply = engine.complete(EngineRequest(turns=turns, tools=[ToolSpec('search', 'S', schema)]))
        sent = json.loads(call.call_args.kwargs['data'])
        self.assertEqual(call.call_args.kwargs['headers']['x-goog-api-key'], SECRET)
        self.assertEqual(sent['systemInstruction']['parts'][0]['text'], 'rules')
        self.assertEqual(sent['contents'][0]['role'], 'user')
        self.assertEqual(len(sent['contents'][0]['parts']), 2, 'consecutive user turns are merged')
        self.assertEqual(sent['contents'][2]['parts'][0]['functionResponse']['response'], {'status': 'executed'})
        declared = sent['tools'][0]['functionDeclarations'][0]['parameters']
        self.assertNotIn('additionalProperties', declared)
        self.assertNotIn('minimum', declared['properties']['q'])
        self.assertEqual(reply.text, 'Looking up')
        self.assertEqual(reply.tool_invocations[0].arguments, {'query': 'Azure'})
        self.assertEqual((reply.input_tokens, reply.output_tokens), (9, 3))

    def test_gemini_malformed(self):
        engine = GeminiEngine(settings(engine_type='gemini', model='gemini-test'))
        with patch('requests.request', return_value=FakeResponse(200, {'candidates': []})):
            with self.assertRaises(EngineBadResponse):
                engine.complete(EngineRequest(turns=[ChatTurn('user', 'x')]))
