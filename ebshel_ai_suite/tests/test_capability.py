# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
from odoo.exceptions import ValidationError
from odoo.fields import Command
from odoo.tests import tagged
from odoo.tests.common import new_test_user

from ..services.capability_runner import CapabilityRunner
from ..services.capability_schema import ArgumentError, validate_arguments
from ..services.domain_guard import DomainGuard, DomainRejected
from ..services.engines import ToolInvocation
from .common import CommunityAICase


def call(name, **arguments):
    return ToolInvocation(call_id='t1', name=name, arguments=arguments)


@tagged('ebshel_ai')
class TestCapabilities(CommunityAICase):

    def runner(self, user=None, **kwargs):
        env = self.env(user=user or self.user_ai)
        session = env['community.ai.session'].create({'assistant_id': self.assistant.id})
        return CapabilityRunner(env, self.assistant.with_env(env), session, **kwargs)

    def test_tool_specs(self):
        specs = {spec.name: spec for spec in self.runner().tool_specs()}
        self.assertEqual(set(specs), {'t_search_partners', 't_create_partner'})
        search = specs['t_search_partners']
        self.assertEqual(search.parameters['type'], 'object')
        self.assertEqual(search.parameters['properties']['limit']['type'], 'integer')
        self.assertIn('res.partner', search.description)

    def test_valid_parameters(self):
        self.env['res.partner'].create([{'name': 'Azure Interior', 'city': 'Ghent'},
                                        {'name': 'Azure Hidden', 'city': 'Lyon'}])
        outcome = self.runner().invoke(call('t_search_partners', query='Azure',
                                            filters=[{'field': 'city', 'operator': '=', 'value': 'Ghent'}]))
        self.assertEqual(outcome.status, 'executed')
        names = [row['name'] for row in outcome.payload['records']]
        self.assertEqual(names, ['Azure Interior'])

    def test_missing_and_invalid_parameters(self):
        read_cap = self.env['community.ai.capability'].create({
            'name': 'Read', 'technical_identifier': 't_read_partner', 'handler_key': 'read_record',
            'model_id': self.env.ref('base.model_res_partner').id, 'description': 'Read'})
        self.assistant.capability_ids = [Command.link(read_cap.id)]
        runner = self.runner()
        self.assertEqual(runner.invoke(call('t_read_partner')).status, 'rejected')
        self.assertIn('Missing required', runner.invoke(call('t_read_partner')).payload['error'])
        self.assertEqual(runner.invoke(call('t_read_partner', record_id='abc')).status, 'rejected')
        self.assertEqual(runner.invoke(call('t_read_partner', record_id=1, sql='DROP TABLE')).status, 'rejected')
        self.assertEqual(runner.invoke(call('unknown_tool')).status, 'rejected')
        self.assertEqual(runner.invoke(ToolInvocation('x', 't_read_partner', {'__unparsable__': '{'})).status,
                         'rejected')

    def test_schema_validation_rules(self):
        schema = {'status': {'type': 'string', 'enum': ['draft', 'done']},
                  'count': {'type': 'integer', 'minimum': 1, 'maximum': 5},
                  'tags': {'type': 'array', 'items': {'type': 'string'}}}
        self.assertEqual(validate_arguments(schema, {'count': '3'}), {'count': 3})
        for bad in ({'status': 'cancel'}, {'count': 9}, {'count': True}, {'tags': 'x'}, {'extra': 1}):
            with self.subTest(bad=bad), self.assertRaises(ArgumentError):
                validate_arguments(schema, bad)
        with self.assertRaises(ValidationError):
            self.cap_search.argument_schema = {'Bad Name': {'type': 'string'}}
        with self.assertRaises(ValidationError):
            self.cap_search.argument_schema = {'x': {'type': 'python'}}

    def test_confirmation_requirement(self):
        self.cap_create.requires_confirmation = True
        runner = self.runner()
        outcome = runner.invoke(call('t_create_partner', values={'name': 'Needs Approval'}))
        self.assertEqual(outcome.status, 'awaiting_confirmation')
        self.assertFalse(self.env['res.partner'].search([('name', '=', 'Needs Approval')]))
        operation = outcome.operation
        self.assertEqual(operation.state, 'proposed')
        self.assertIn('Needs Approval', operation.preview_text)
        # the approval executes with the user's rights and is audited
        operation.with_user(self.user_ai).cai_decide(True)
        self.assertEqual(operation.state, 'executed')
        self.assertTrue(self.env['res.partner'].search([('name', '=', 'Needs Approval')]))
        audit = self.env['community.ai.audit'].search([('operation_id', '=', operation.id),
                                                      ('execution_status', '=', 'done')])
        self.assertEqual(audit.confirmation_status, 'approved')

    def test_rejection(self):
        self.cap_create.requires_confirmation = True
        outcome = self.runner().invoke(call('t_create_partner', values={'name': 'Rejected One'}))
        outcome.operation.with_user(self.user_ai).cai_decide(False)
        self.assertEqual(outcome.operation.state, 'rejected')
        self.assertFalse(self.env['res.partner'].search([('name', '=', 'Rejected One')]))

    def test_untrusted_data_forces_confirmation(self):
        outcome = self.runner(tainted=True).invoke(call('t_create_partner', values={'name': 'Tainted'}))
        self.assertEqual(outcome.status, 'awaiting_confirmation')
        self.assertTrue(outcome.operation.forced_by_untrusted_data)
        direct = self.runner().invoke(call('t_create_partner', values={'name': 'Direct'}))
        self.assertEqual(direct.status, 'executed')
        self.assertEqual(direct.payload['created']['name'], 'Direct')

    def test_failed_execution(self):
        runner = self.runner()
        not_allowed = runner.invoke(call('t_create_partner', values={'name': 'X', 'comment': 'no'}))
        self.assertEqual(not_allowed.status, 'failed')
        self.assertIn('may not be set', not_allowed.payload['error'])
        bad_value = runner.invoke(call('t_create_partner', values={'name': 'Y', 'is_company': 'perhaps'}))
        self.assertEqual(bad_value.status, 'failed')
        audit = self.env['community.ai.audit'].search([('capability_id', '=', self.cap_create.id),
                                                      ('execution_status', '=', 'failed')])
        self.assertEqual(len(audit), 2)

    def test_access_rights_apply(self):
        """The AI acts with the user's rights: without 'Contact Creation' the same call is denied."""
        limited = new_test_user(self.env, login='cai_limited', groups='base.group_user,ebshel_ai_suite.group_cai_user')
        self.assertFalse(self.env['res.partner'].with_user(limited).has_access('create'))
        outcome = self.runner(limited).invoke(call('t_create_partner', values={'name': 'Hack'}))
        self.assertEqual(outcome.status, 'rejected')
        self.assertIn('Access denied', outcome.payload['error'])
        self.assertFalse(self.env['res.partner'].search([('name', '=', 'Hack')]))
        allowed = self.runner().invoke(call('t_create_partner', values={'name': 'Allowed'}))
        self.assertEqual(allowed.status, 'executed')

    def test_group_restricted_capability(self):
        self.cap_search.group_ids = [Command.set(self.env.ref('ebshel_ai_suite.group_cai_manager').ids)]
        self.assertNotIn('t_search_partners', [s.name for s in self.runner().tool_specs()])
        self.assertIn('t_search_partners', [s.name for s in self.runner(self.user_manager).tool_specs()])
        self.assertEqual(self.runner().invoke(call('t_search_partners', query='x')).status, 'rejected')

    def test_forbidden_targets(self):
        Capability = self.env['community.ai.capability']
        for model in ('base.model_res_users', 'base.model_ir_config_parameter', 'base.model_res_groups'):
            with self.subTest(model=model), self.assertRaises(ValidationError):
                Capability.create({'name': 'Bad', 'technical_identifier': 't_bad_target', 'handler_key': 'read_record',
                                   'model_id': self.env.ref(model).id, 'description': 'x'})
        with self.assertRaises(ValidationError):
            Capability.create({'name': 'Bad', 'technical_identifier': 't_bad_write', 'handler_key': 'update_record',
                               'model_id': self.env.ref('base.model_res_country').id, 'description': 'x',
                               'field_ids': [Command.set(self.env['ir.model.fields'].search(
                                   [('model', '=', 'res.country'), ('name', '=', 'name')]).ids)]})
        with self.assertRaises(ValidationError):
            Capability.create({'name': 'No fields', 'technical_identifier': 't_no_fields',
                               'handler_key': 'create_record', 'description': 'x',
                               'model_id': self.env.ref('base.model_res_partner').id})

    def test_domain_guard(self):
        env = self.env(user=self.user_ai)
        guard = DomainGuard(env, 'res.partner')
        self.assertEqual(guard.validate([['name', 'ilike', 'a'], ['city', '=', 'X']]),
                         ['&', ('name', 'ilike', 'a'), ('city', '=', 'X')])
        self.assertEqual(guard.validate(['|', ['city', '=', 'A'], ['city', '=', 'B']]),
                         ['|', ('city', '=', 'A'), ('city', '=', 'B')])
        rejected = [
            [['password', '=', 'x']],
            [['name', 'child_of', 1]],
            [['name', '=', 'x'], '|'],
            'name = 1; DROP TABLE res_partner',
            [['user_ids.password', '=', 'x']],
            [['create_date', '>', 'yesterday']],
            [['is_company', '=', 'yes']],
            [['name', 'in', []]],
            [['parent_id.parent_id.parent_id.name', '=', 'deep']],
            [['__last_update', '=', 1]],
        ]
        for domain in rejected:
            with self.subTest(domain=domain), self.assertRaises(DomainRejected):
                guard.validate(domain)
        self.assertEqual(guard.validate_order('name desc, id'), 'name desc, id asc')
        with self.assertRaises(DomainRejected):
            guard.validate_order('name; DROP')

    def test_open_view_and_count(self):
        Capability = self.env['community.ai.capability']
        fields_ = self.env['ir.model.fields'].search([('model', '=', 'res.partner'),
                                                     ('name', 'in', ['city', 'is_company'])])
        caps = Capability.create([
            {'name': 'Open', 'technical_identifier': 't_open', 'handler_key': 'open_view', 'description': 'o',
             'model_id': self.env.ref('base.model_res_partner').id, 'field_ids': [Command.set(fields_.ids)]},
            {'name': 'Count', 'technical_identifier': 't_count', 'handler_key': 'count_records', 'description': 'c',
             'model_id': self.env.ref('base.model_res_partner').id, 'field_ids': [Command.set(fields_.ids)]},
        ])
        self.assistant.capability_ids = [Command.link(cap.id) for cap in caps]
        self.env['res.partner'].create([{'name': f'Count {i}', 'city': 'Countville'} for i in range(3)])
        runner = self.runner()
        opened = runner.invoke(call('t_open', filters=[{'field': 'city', 'operator': '=', 'value': 'Countville'}]))
        self.assertEqual(opened.status, 'executed')
        self.assertEqual(opened.navigation['res_model'], 'res.partner')
        self.assertEqual(opened.navigation['domain'], [('city', '=', 'Countville')])
        self.assertNotIn('navigation', opened.payload)
        counted = runner.invoke(call('t_count', filters=[{'field': 'city', 'operator': '=', 'value': 'Countville'}]))
        self.assertEqual(counted.payload['count'], 3)
        grouped = runner.invoke(call('t_count', group_by='city',
                                     filters=[{'field': 'city', 'operator': '=', 'value': 'Countville'}]))
        self.assertEqual(grouped.payload['groups'], [{'group': 'Countville', 'count': 3}])

    def test_results_are_sanitized(self):
        self.env['res.partner'].create({'name': 'Leaky', 'email': 'token=sk-abcdefghijklmnopqrstuvwxyz@x.com'})
        outcome = self.runner().invoke(call('t_search_partners', query='Leaky'))
        self.assertNotIn('sk-abcdefghijklmnopqrstuvwxyz', outcome.model_text())
