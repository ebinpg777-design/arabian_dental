# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from ..services.prompt_renderer import PromptRenderError, PromptRenderer
from .common import CommunityAICase


@tagged('ebshel_ai')
class TestPromptRenderer(CommunityAICase):

    def test_variable_substitution_and_filters(self):
        renderer = PromptRenderer(variables={'partner_name': 'Azure Interior', 'items': ['a', 'b'],
                                             'order': {'total': 42}})
        text = renderer.render('Customer: {{ partner_name | upper }} / {{ items | join("-") }} / '
                               '{{ order.total }} / {{ missing | default("n/a") }} / {{ partner_name | truncate(5) }}')
        self.assertEqual(text, 'Customer: AZURE INTERIOR / a-b / 42 / n/a / Azure…')

    def test_conditionals(self):
        template = '{% if vip %}VIP{% else %}standard{% endif %}|{% if not vip %}no{% endif %}'
        self.assertEqual(PromptRenderer(variables={'vip': True}).render(template), 'VIP|')
        self.assertEqual(PromptRenderer(variables={'vip': False}).render(template), 'standard|no')

    def test_record_paths(self):
        partner = self.env['res.partner'].create({
            'name': 'Prompt Partner', 'email': 'prompt@example.com',
            'parent_id': self.env['res.partner'].create({'name': 'Parent Co', 'is_company': True}).id,
        })
        renderer = PromptRenderer(self.env, record=partner)
        self.assertEqual(renderer.render('{{ record.name }} <{{ record.email }}> of {{ record.parent_id.name }}'),
                         'Prompt Partner <prompt@example.com> of Parent Co')
        self.assertEqual(renderer.render('{{ record.parent_id }}'), 'Parent Co')

    def test_missing_variables(self):
        self.assertEqual(PromptRenderer(variables={}).render('Hello {{ name }}!'), 'Hello !')
        with self.assertRaises(PromptRenderError) as caught:
            PromptRenderer(variables={}, strict=True).render('{{ name }} {{ other.value }}')
        self.assertEqual(caught.exception.missing, ['name', 'other.value'])
        # values tested by a condition are not "missing"
        PromptRenderer(variables={}, strict=True).render('{% if name %}{{ name }}{% endif %}')

    def test_malicious_templates(self):
        partner = self.env['res.partner'].create({'name': 'Target'})
        user = self.user_ai
        malicious = [
            "{{ __import__('os').system('id') }}",
            '{{ record._cr }}',
            '{{ record.write }}',
            '{{ record.unlink }}',
            '{{ 1 + 1 }}',
            '{{ name | exec }}',
            '{% for x in items %}{% endfor %}',
            '{% if a and b %}{% endif %}',
            '{% if a %}unclosed',
            '{% endif %}',
            '{{ record.user_ids.password }}',
        ]
        for template in malicious:
            with self.subTest(template=template), self.assertRaises(PromptRenderError):
                PromptRenderer(self.env, record=partner, variables={'name': 'x'}).render(template)
        users_record = self.env['res.users'].browse(user.id)
        with self.assertRaises(PromptRenderError):
            PromptRenderer(self.env, record=users_record).render('{{ record.password }}')
        with self.assertRaises(PromptRenderError):
            PromptRenderer(self.env, record=users_record).render('{{ record.api_key_ids }}')

    def test_variable_values_are_not_evaluated(self):
        text = PromptRenderer(variables={'note': '{{ secret }} {% if x %}'}).render('Note: {{ note }}')
        self.assertEqual(text, 'Note: {{ secret }} {% if x %}')

    def test_prompt_model_validation(self):
        Prompt = self.env['community.ai.prompt']
        with self.assertRaises(ValidationError):
            Prompt.create({'name': 'Bad', 'body': '{% if record.name %}unclosed'})
        prompt = Prompt.create({'name': 'Good', 'code': 'test.good', 'body': 'Hi {{ record.name }} {{ extra }}'})
        self.assertEqual(prompt.placeholder_list, 'record.name, extra')
        self.assertEqual(Prompt._cai_get_by_code('test.good'), prompt)
        partner = self.env['res.partner'].create({'name': 'Bob'})
        self.assertEqual(prompt.render(record=partner, variables={'extra': '!'})[1], 'Hi Bob !')
