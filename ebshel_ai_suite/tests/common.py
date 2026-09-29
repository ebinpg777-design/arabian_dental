# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
from odoo.fields import Command
from odoo.tests.common import TransactionCase, new_test_user

from ..services.engines.mock import MockEngine


class CommunityAICase(TransactionCase):
    """Shared fixture: an offline mock connection, users per role and an assistant."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        MockEngine.reset()
        cls.connection = cls.env['community.ai.connection'].create({
            'name': 'Test mock', 'engine_type': 'mock', 'endpoint_url': 'mock://ok', 'default_model': 'mock-small',
            'embedding_model': 'mock-embed', 'image_model': 'mock-image',
        })
        cls.env.company.cai_connection_id = cls.connection
        cls.user_ai = new_test_user(cls.env, login='cai_user',
                                    groups='base.group_user,base.group_partner_manager,ebshel_ai_suite.group_cai_user',
                                    name='AI User')
        cls.user_plain = new_test_user(cls.env, login='cai_plain', groups='base.group_user', name='Plain User')
        cls.user_manager = new_test_user(cls.env, login='cai_manager',
                                         groups='base.group_user,base.group_partner_manager,ebshel_ai_suite.group_cai_manager',
                                         name='AI Manager')
        cls.user_admin = new_test_user(cls.env, login='cai_admin',
                                       groups='base.group_user,base.group_partner_manager,ebshel_ai_suite.group_cai_admin',
                                       name='AI Admin')
        Capability = cls.env['community.ai.capability']
        partner_fields = cls.env['ir.model.fields'].search([
            ('model', '=', 'res.partner'), ('name', 'in', ['name', 'email', 'city', 'is_company'])])
        cls.cap_search = Capability.create({
            'name': 'T Search partners', 'technical_identifier': 't_search_partners', 'handler_key': 'search_records',
            'model_id': cls.env.ref('base.model_res_partner').id, 'description': 'Search partners',
            'field_ids': [Command.set(partner_fields.ids)], 'requires_confirmation': False,
        })
        cls.cap_create = Capability.create({
            'name': 'T Create partner', 'technical_identifier': 't_create_partner', 'handler_key': 'create_record',
            'model_id': cls.env.ref('base.model_res_partner').id, 'description': 'Create partner',
            'field_ids': [Command.set(partner_fields.ids)], 'requires_confirmation': False,
        })
        cls.assistant = cls.env['community.ai.assistant'].create({
            'title': 'Test Assistant', 'connection_id': cls.connection.id,
            'capability_ids': [Command.set([cls.cap_search.id, cls.cap_create.id])],
            'require_confirmation': False,
        })

    def setUp(self):
        super().setUp()
        MockEngine.reset()
        self.addCleanup(MockEngine.reset)
