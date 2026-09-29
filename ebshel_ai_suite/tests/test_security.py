# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
from odoo.exceptions import AccessError, UserError
from odoo.fields import Command
from odoo.tests import tagged
from odoo.tests.common import new_test_user

from ..services.engines.errors import EngineConfigError
from ..services.llm_gateway import LLMGateway
from .common import CommunityAICase


@tagged('ebshel_ai')
class TestSecurity(CommunityAICase):

    def test_unauthorized_user(self):
        env = self.env(user=self.user_plain)
        with self.assertRaises(AccessError):
            env['community.ai.session'].cai_start(self.assistant.id)
        with self.assertRaises(AccessError):
            env['community.ai.text.service'].cai_transform_text('improve', 'hello')
        with self.assertRaises(AccessError):
            env['community.ai.assistant'].search([])
        self.assertFalse(self.assistant._cai_is_usable_by(self.user_plain))

    def test_configuration_permissions(self):
        env = self.env(user=self.user_ai)
        with self.assertRaises(AccessError):
            env['community.ai.connection'].search([])
        with self.assertRaises(AccessError):
            env['community.ai.assistant'].create({'title': 'Rogue'})
        with self.assertRaises(AccessError):
            env['community.ai.capability'].browse(self.cap_search.id).write({'requires_confirmation': False})
        with self.assertRaises(AccessError):
            env['community.ai.exchange'].create({'session_id': 1, 'speaker_type': 'tool'})
        manager_env = self.env(user=self.user_manager)
        manager_env['community.ai.assistant'].create({'title': 'Manager-made'})
        with self.assertRaises(AccessError):
            manager_env['community.ai.connection'].create({'name': 'x', 'engine_type': 'mock'})
        with self.assertRaises(UserError):
            self.connection.with_user(self.user_manager).action_cai_test_connection()

    def test_conversations_are_private(self):
        other = new_test_user(self.env, login='cai_other', groups='base.group_user,ebshel_ai_suite.group_cai_user')
        mine = self.env['community.ai.session'].with_user(self.user_ai).cai_start(self.assistant.id)
        theirs = self.env['community.ai.session'].with_user(other).cai_start(self.assistant.id)
        visible = self.env['community.ai.session'].with_user(self.user_ai).search([])
        self.assertIn(mine['id'], visible.ids)
        self.assertNotIn(theirs['id'], visible.ids)
        with self.assertRaises(AccessError):
            self.env['community.ai.session'].with_user(self.user_ai).browse(theirs['id']).cai_send('hi')
        managers_view = self.env['community.ai.session'].with_user(self.user_manager).search([])
        self.assertIn(theirs['id'], managers_view.ids)
        with self.assertRaises(AccessError):   # managers can read but not speak on behalf of someone else
            self.env['community.ai.session'].with_user(self.user_manager).browse(theirs['id']).cai_send('hi')

    def test_assistant_group_restriction(self):
        self.assistant.user_group_ids = [Command.set(self.env.ref('ebshel_ai_suite.group_cai_manager').ids)]
        with self.assertRaises(UserError):
            self.env['community.ai.session'].with_user(self.user_ai).cai_start(self.assistant.id)
        payload = self.env['community.ai.session'].with_user(self.user_manager).cai_start(self.assistant.id)
        self.assertEqual(payload['assistant']['id'], self.assistant.id)

    def test_multi_company_isolation(self):
        company_b = self.env['res.company'].create({'name': 'Company B'})
        user_b = new_test_user(self.env, login='cai_b', groups='base.group_user,ebshel_ai_suite.group_cai_user',
                               company_id=company_b.id, company_ids=[Command.set(company_b.ids)])
        assistant_b = self.env['community.ai.assistant'].create({'title': 'B only', 'company_id': company_b.id})
        connection_b = self.env['community.ai.connection'].create({'name': 'B', 'engine_type': 'mock',
                                                                   'company_id': company_b.id})
        self.assertNotIn(assistant_b, self.env['community.ai.assistant'].with_user(self.user_ai)._cai_usable_assistants())
        self.assertIn(assistant_b, self.env['community.ai.assistant'].with_user(user_b)._cai_usable_assistants())
        self.assertFalse(self.env['community.ai.assistant'].with_user(self.user_ai).search(
            [('id', '=', assistant_b.id)]))
        # a connection of another company cannot be used by the gateway
        with self.assertRaises(EngineConfigError):
            LLMGateway(self.env(user=self.user_ai)).resolve_connection(connection=connection_b)
        # record context of another company's record is refused
        partner_b = self.env['res.partner'].create({'name': 'B partner', 'company_id': company_b.id})
        payload = self.env['community.ai.session'].with_user(self.user_ai).cai_start(
            self.assistant.id, 'res.partner', partner_b.id)
        self.assertIsNone(payload['context'])

    def test_context_model_sanitized(self):
        Session = self.env['community.ai.session'].with_user(self.user_ai)
        for model, res_id in (('ir.config_parameter', 1), ('res.users', self.user_admin.id),
                              ('community.ai.connection', self.connection.id), ('nonexistent', 1),
                              ('res.partner', 'abc')):
            payload = Session.cai_start(self.assistant.id, model, res_id)
            self.assertIsNone(payload['context'], model)

    def test_technical_errors_admin_only(self):
        self.connection.endpoint_url = 'mock://unavailable'
        session = self.env['community.ai.session'].with_user(self.user_ai).cai_start(self.assistant.id)
        record = self.env['community.ai.session'].with_user(self.user_ai).browse(session['id'])
        events = record.cai_send('hello')['events']
        error = next(e for e in events if e['type'] == 'error')
        self.assertIn('unreachable', error['message'])
        self.assertNotIn('Mock', error['message'])
        exchange = self.env['community.ai.exchange'].with_user(self.user_ai).browse(error['exchange']['id'])
        with self.assertRaises(AccessError):
            exchange.read(['error_detail'])
        self.assertIn('connection refused', exchange.with_user(self.user_admin).error_detail)

    def test_audit_is_append_only(self):
        entry = self.env['community.ai.audit']._cai_log(user=self.user_ai, operation='test', result_summary='x')
        with self.assertRaises(UserError):
            entry.write({'operation': 'tampered'})
        with self.assertRaises(UserError):
            entry.unlink()
        with self.assertRaises(AccessError):
            self.env['community.ai.audit'].with_user(self.user_ai).create({'operation': 'fake'})
        self.assertEqual(self.env['community.ai.audit'].with_user(self.user_ai).search([('id', '=', entry.id)]), entry)

    def test_automation_run_as_restricted(self):
        Automation = self.env['community.ai.automation'].with_user(self.user_manager)
        values = {'name': 'A', 'model_id': self.env.ref('base.model_res_partner').id, 'instruction': 'x',
                  'trigger_event': 'manual'}
        with self.assertRaises(AccessError):
            Automation.create(dict(values, run_as_user_id=self.user_admin.id))
        automation = Automation.create(values)
        self.assertEqual(automation.run_as_user_id, self.user_manager)
        admin_owned = self.env['community.ai.automation'].with_user(self.user_admin).create(values)
        self.assertEqual(admin_owned.run_as_user_id, self.user_admin)
        admin_owned.with_user(self.user_manager).write({'instruction': 'changed by manager'})
        self.assertEqual(admin_owned.run_as_user_id, self.user_manager,
                         'a manager cannot make an administrator-owned automation run their instructions')

    def test_operation_decided_by_author_only(self):
        self.cap_create.requires_confirmation = True
        session = self.env['community.ai.session'].with_user(self.user_ai).cai_start(self.assistant.id)
        record = self.env['community.ai.session'].with_user(self.user_ai).browse(session['id'])
        record.cai_send('[[call:t_create_partner {"values": {"name": "Pending Partner"}}]]')
        operation = self.env['community.ai.operation'].search([('session_id', '=', session['id'])])
        self.assertEqual(operation.state, 'proposed')
        with self.assertRaises(AccessError):
            operation.with_user(self.user_manager).cai_decide(True)
        self.assertFalse(self.env['res.partner'].search([('name', '=', 'Pending Partner')]))
