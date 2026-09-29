# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
{
    'name': 'Ebshel AI Suite',
    'version': '19.0.1.0.0',
    'category': 'Productivity',
    'summary': 'Provider-agnostic AI assistants, tools, knowledge retrieval and automation for Odoo Community',
    'description': """
Ebshel AI Suite
==================

An independently engineered AI framework for Odoo Community:

* pluggable LLM engines (OpenAI-compatible, Google Gemini, self-hosted, mock)
* assistants with conversations, record context and knowledge retrieval
* registered, schema-validated capabilities with confirmation and auditing
* natural-language search, AI-assisted fields, AI automations
* usage tracking, limits and privacy-first defaults
""",
    'author': 'Ebshel Technologies',
    'website': 'https://ebshel.com',
    'license': 'LGPL-3',
    'depends': ['base', 'web', 'mail'],
    'external_dependencies': {'python': ['requests']},
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'security/record_rules.xml',
        'data/capability_data.xml',
        'data/default_data.xml',
        'data/skillset_data.xml',
        'data/context_preset_data.xml',
        'data/image_data.xml',
        'data/web_data.xml',
        'data/prompt_data.xml',
        'data/cron.xml',
        'views/connection_views.xml',
        'views/session_views.xml',
        'views/capability_views.xml',
        'views/operation_views.xml',
        'views/prompt_views.xml',
        'wizard/source_link_wizard_views.xml',
        'views/source_views.xml',
        'views/skillset_views.xml',
        'views/context_preset_views.xml',
        'views/assistant_views.xml',
        'views/field_rule_views.xml',
        'views/automation_views.xml',
        'views/job_views.xml',
        'views/usage_views.xml',
        'views/audit_views.xml',
        'views/privacy_views.xml',
        'views/res_config_settings_views.xml',
        'views/res_partner_views.xml',
        'views/ir_actions_server_views.xml',
        'wizard/text_wizard_views.xml',
        'wizard/image_wizard_views.xml',
        'wizard/mail_compose_views.xml',
        'views/menus.xml',
    ],
    'demo': [
        'demo/demo_data.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'ebshel_ai_suite/static/src/core/**/*',
            'ebshel_ai_suite/static/src/components/**/*',
            'ebshel_ai_suite/static/src/commands/**/*',
            'ebshel_ai_suite/static/src/fields/**/*',
            'ebshel_ai_suite/static/src/editor/**/*',
            'ebshel_ai_suite/static/src/scss/**/*',
        ],
        'web.assets_tests': [
            'ebshel_ai_suite/static/tests/tours/**/*',
        ],
    },
    'application': True,
    'installable': True,
}
