# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
import json

from odoo.fields import Command
from odoo.tests import HttpCase, tagged
from odoo.tests.common import new_test_user

from ..services.mcp_server import API_KEY_SCOPE

URL = '/ebshel_ai/mcp'


@tagged('ebshel_ai', 'post_install', '-at_install')
class TestMcpServer(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login='cai_mcp_user', name='MCP User',
                                 groups='base.group_user,base.group_partner_manager,ebshel_ai_suite.group_cai_user')
        cls.outsider = new_test_user(cls.env, login='cai_mcp_outsider', groups='base.group_user')
        cls.key = cls.env['res.users.apikeys'].with_user(cls.user).sudo()._generate(API_KEY_SCOPE, 'mcp test', False)
        cls.rpc_key = cls.env['res.users.apikeys'].with_user(cls.user).sudo()._generate('rpc', 'other scope', False)
        cls.outsider_key = cls.env['res.users.apikeys'].with_user(cls.outsider).sudo()._generate(
            API_KEY_SCOPE, 'outsider', False)
        cls.partner = cls.env['res.partner'].create({'name': 'MCP Partner', 'email': 'mcp@example.com',
                                                     'city': 'Ghent'})
        cls.env['ir.config_parameter'].sudo().set_param('ebshel_ai_suite.mcp_enabled', 'True')

    def post(self, payload, key=None, content_type='application/json'):
        headers = {'Authorization': f'Bearer {self.key if key is None else key}', 'Content-Type': content_type}
        return self.url_open(URL, data=json.dumps(payload), headers=headers)

    def rpc(self, method, params=None, request_id=1):
        response = self.post({'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params or {}})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def call(self, name, **arguments):
        return self.rpc('tools/call', {'name': name, 'arguments': arguments})['result']

    def test_transport_and_authentication(self):
        self.assertEqual(self.url_open(URL, headers={'Authorization': f'Bearer {self.key}'}).status_code, 405)
        self.assertEqual(self.post({}, key='').status_code, 401)
        self.assertEqual(self.post({}, key='not-a-key').status_code, 401)
        self.assertEqual(self.post({}, key=self.rpc_key).status_code, 401, 'keys of other scopes are refused')
        self.assertEqual(self.post({}, key=self.outsider_key).status_code, 403, 'the AI user group is required')
        self.assertEqual(self.post({}, content_type='text/plain').status_code, 415)
        notification = self.post({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        self.assertEqual(notification.status_code, 202)
        self.assertEqual(self.rpc('unknown/method')['error']['code'], -32601)
        self.env['ir.config_parameter'].sudo().set_param('ebshel_ai_suite.mcp_enabled', False)
        self.assertEqual(self.post({'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}).status_code, 403)

    def test_handshake_and_catalogue(self):
        init = self.rpc('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                                       'clientInfo': {'name': 'test', 'version': '1'}})['result']
        self.assertEqual(init['protocolVersion'], '2025-06-18')
        self.assertIn('tools', init['capabilities'])
        tools = {t['name']: t for t in self.rpc('tools/list')['result']['tools']}
        self.assertIn('search_records', tools)
        self.assertTrue(tools['search_records']['annotations']['readOnlyHint'])
        self.assertEqual(tools['search_records']['inputSchema']['type'], 'object')
        self.assertNotIn('create_records', tools, 'write tools are opt-in')
        context = self.call('get_context')['structuredContent']
        self.assertEqual(context['user']['id'], self.user.id)
        self.assertFalse(context['write_tools_enabled'])

    def test_read_tools(self):
        found = self.call('search_records', model='res.partner', domain=[['name', '=', 'MCP Partner']],
                          fields=['name', 'email', 'city'])
        self.assertFalse(found['isError'])
        self.assertEqual(found['structuredContent']['records'],
                         [{'id': self.partner.id, 'name': 'MCP Partner', 'email': 'mcp@example.com',
                           'city': 'Ghent'}])
        groups = self.call('aggregate_records', model='res.partner', group_by=['city'],
                           domain=[['city', '=', 'Ghent']])['structuredContent']['groups']
        self.assertEqual(groups, [{'city': 'Ghent', '__count': 1}])
        described = self.call('describe_model', model='res.partner')['structuredContent']
        self.assertIn('email', {f['name'] for f in described['fields']})
        models_ = {m['model'] for m in self.call('list_models', search='partner')['structuredContent']['models']}
        self.assertIn('res.partner', models_)

    def test_refusals(self):
        for model in ('res.groups', 'ir.config_parameter', 'community.ai.connection', 'res.users.apikeys'):
            result = self.call('search_records', model=model)
            self.assertTrue(result['isError'], model)
        # users are readable with the connected user's rights, but never their credentials
        self.assertFalse(self.call('search_records', model='res.users', fields=['name'])['isError'])
        self.assertTrue(self.call('search_records', model='res.users', fields=['password'])['isError'])
        self.assertTrue(self.call('search_records', model='res.users', domain=[['password', '!=', False]])['isError'])
        self.assertTrue(self.call('search_records', model='res.partner',
                                  domain=[['user_ids.password', '!=', False]])['isError'])
        self.assertTrue(self.call('search_records', model='res.partner', domain="[('id', '>', 0)]")['isError'])
        self.assertTrue(self.call('create_records', model='res.partner', values=[{'name': 'X'}])['isError'])
        self.assertFalse(self.env['res.partner'].search([('name', '=', 'X')]))

    def test_write_tools_when_enabled(self):
        self.env['ir.config_parameter'].sudo().set_param('ebshel_ai_suite.mcp_allow_write', 'True')
        created = self.call('create_records', model='res.partner', values=[{'name': 'Made by MCP', 'city': 'Lyon'}])
        self.assertFalse(created['isError'], created)
        partner = self.env['res.partner'].browse(created['structuredContent']['created'][0]['id'])
        self.assertEqual((partner.name, partner.city, partner.create_uid), ('Made by MCP', 'Lyon', self.user))
        updated = self.call('update_records', model='res.partner', ids=[partner.id], values={'city': 'Paris'})
        self.assertFalse(updated['isError'])
        self.assertEqual(partner.city, 'Paris')
        self.assertTrue(self.call('update_records', model='res.company', ids=[1], values={'name': 'x'})['isError'])
        self.assertTrue(self.call('update_records', model='res.partner', ids=[partner.id],
                                  values={'create_uid': 1})['isError'])
        audit = self.env['community.ai.audit'].search([('operation', '=', 'mcp:update_records')])
        self.assertEqual(audit.user_id, self.user)

    def test_exposed_capability(self):
        partner_fields = self.env['ir.model.fields'].search([('model', '=', 'res.partner'),
                                                             ('name', 'in', ['name', 'city'])])
        self.env['community.ai.capability'].create({
            'name': 'Find customers', 'technical_identifier': 'mcp_find_customers', 'handler_key': 'search_records',
            'model_id': self.env.ref('base.model_res_partner').id, 'description': 'Find customers',
            'field_ids': [Command.set(partner_fields.ids)], 'mcp_exposed': True,
        })
        tools = {t['name'] for t in self.rpc('tools/list')['result']['tools']}
        self.assertIn('cap_mcp_find_customers', tools)
        result = self.call('cap_mcp_find_customers', query='MCP Partner')
        self.assertFalse(result['isError'], result)
        self.assertIn('MCP Partner', result['content'][0]['text'])
