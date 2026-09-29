# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
from odoo.tests import HttpCase, tagged

from ..services.engines.mock import MockEngine


@tagged('post_install', '-at_install')
class TestCommunityAIUi(HttpCase):
    """Browser tours driving the OWL console against the offline mock engine."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.connection = cls.env['community.ai.connection'].create({
            'name': 'UI mock', 'engine_type': 'mock', 'endpoint_url': 'mock://ok', 'default_model': 'mock-small',
        })
        cls.env.company.cai_connection_id = cls.connection
        cls.env.company.cai_assistant_id = cls.env.ref('ebshel_ai_suite.assistant_business')
        source = cls.env['community.ai.source'].create({
            'name': 'Warranty Policy', 'source_kind': 'text',
            'content_text': 'All products carry a two-year warranty covering manufacturing defects.',
        })
        source.action_cai_index()
        cls.env.ref('ebshel_ai_suite.assistant_business').knowledge_ids = [(4, source.id)]

    def setUp(self):
        super().setUp()
        MockEngine.reset()
        self.addCleanup(MockEngine.reset)

    def test_console_tour(self):
        self.start_tour('/odoo', 'ebshel_ai_suite_console_tour', login='admin')
        partner = self.env['res.partner'].search([('name', '=', 'Tour Created Contact')])
        self.assertEqual(len(partner), 1, 'the confirmed operation must have created the contact')
        audit = self.env['community.ai.audit'].search([('operation', '=', 'create_record'),
                                                      ('execution_status', '=', 'done')])
        self.assertTrue(audit)

    def test_page_tour(self):
        self.start_tour('/odoo/action-ebshel_ai_suite.action_cai_console_page', 'ebshel_ai_suite_page_tour',
                        login='admin')

    def test_assist_field_tour(self):
        partner = self.env['res.partner'].create({'name': 'Tour Widget Partner',
                                                  'comment': '<p>Long internal note to shorten.</p>'})
        self.start_tour(f'/odoo/res.partner/{partner.id}', 'ebshel_ai_suite_assist_field_tour', login='admin')
        self.assertIn('[mock]', str(partner.comment), 'the AI proposal must have been saved by the user')
