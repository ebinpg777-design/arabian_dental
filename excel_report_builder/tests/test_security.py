# -*- coding: utf-8 -*-
"""Security of the report builder: formulas, export filters, template ownership."""
import json
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.excel_report_builder.models import dynamic_report_template
from odoo.addons.excel_report_builder.models.report_dynamic_xlsx_export import (
    FormulaError,
    _collect_export_data,
    _compute_formula_value,
    evaluate_formula,
    parse_domain,
)

EXPORT_LOGGER = 'odoo.addons.excel_report_builder.models.report_dynamic_xlsx_export'


@tagged('post_install', '-at_install')
class TestReportBuilderSecurity(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Template = cls.env['dynamic.export.template']
        group_user = cls.env.ref('base.group_user')
        cls.owner = cls.env['res.users'].create({
            'name': 'Report Owner', 'login': 'erb_owner_test',
            'group_ids': [(6, 0, [group_user.id])],
        })
        cls.other = cls.env['res.users'].create({
            'name': 'Report Other', 'login': 'erb_other_test',
            'group_ids': [(6, 0, [group_user.id])],
        })

    def _template_vals(self, **extra):
        vals = {
            'name': 'Partners',
            'res_model': 'res.partner',
            'fields_json': json.dumps([{'name': 'name', 'string': 'Name', 'type': 'char'}]),
            'domain': "[('name', 'like', 'ERB Scope')]",
        }
        vals.update(extra)
        return vals

    # ------------------------------------------------------------------
    # Computed-column formulas
    # ------------------------------------------------------------------
    def test_formula_arithmetic_still_works(self):
        row = {'price': 2.5, 'qty': '4'}
        self.assertEqual(_compute_formula_value('{price} * {qty} + round(0.6)', row), 11.0)
        self.assertEqual(_compute_formula_value('max({price}, {qty}) - abs(-1)', row), 3.0)
        self.assertEqual(_compute_formula_value('({qty} - {price}) ** 2', row), 2.25)
        self.assertEqual(_compute_formula_value('{qty} > {price}', row), 1)
        self.assertEqual(_compute_formula_value('{price} / 0', row), '')

    def test_formula_escapes_are_refused(self):
        payloads = [
            "().__class__.__bases__[0].__subclasses__()",
            "__import__('os').getcwd()",
            "[c for c in ().__class__.__mro__]",
            "(lambda: 1)()",
            "'a' * 10",
            "{price}.__class__",
            "open('/etc/passwd')",
            "round(1, ndigits=2)",
            "9 ** 9 ** 9",
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(FormulaError):
                    evaluate_formula(payload, lambda column: 1)
                with self.assertLogs(EXPORT_LOGGER, 'WARNING'):
                    self.assertEqual(_compute_formula_value(payload, {'price': 1}), '')

    def test_a_column_value_cannot_become_code(self):
        self.assertEqual(_compute_formula_value('{x} + 1', {'x': "__import__('os')"}), 1)

    # ------------------------------------------------------------------
    # Export filters
    # ------------------------------------------------------------------
    def test_json_and_python_domains_both_parse(self):
        self.assertEqual(parse_domain(self.env, '[["active", "=", true]]'), [['active', '=', True]])
        self.assertEqual(
            parse_domain(self.env, "[('active', '=', True), ('user_id', '=', uid)]"),
            [('active', '=', True), ('user_id', '=', self.env.uid)])
        self.assertEqual(parse_domain(self.env, ''), [])
        self.assertEqual(parse_domain(self.env, '[]'), [])
        dated = parse_domain(
            self.env,
            "[('create_date', '>=', (context_today() - relativedelta(days=1)).strftime('%Y-%m-%d'))]")
        self.assertEqual(len(dated), 1)

    def test_an_unreadable_domain_is_refused_not_ignored(self):
        """Falling back to [] used to export the whole model."""
        for raw in ("[('name', '=', ", "__import__('os')", "42", "{'name': 1}"):
            with self.subTest(raw=raw), self.assertRaises(UserError):
                parse_domain(self.env, raw)

    # ------------------------------------------------------------------
    # Template ownership
    # ------------------------------------------------------------------
    def test_a_private_template_is_closed_to_other_users(self):
        template = self.Template.with_user(self.owner).create(self._template_vals())
        self.assertEqual(template.user_id, self.owner)
        Other = self.Template.with_user(self.other)
        self.assertFalse(Other.search([('id', '=', template.id)]))
        self.assertNotIn(template.id, [t['id'] for t in Other.get_templates('res.partner')])

        template.is_shared = True
        self.assertIn(template.id, [t['id'] for t in Other.get_templates('res.partner')])
        with self.assertRaises(AccessError):
            template.with_user(self.other).write({'name': 'Hijacked'})
        with self.assertRaises(AccessError):
            template.with_user(self.other).unlink()

    def test_a_template_cannot_be_handed_to_somebody_else(self):
        template = self.Template.with_user(self.owner).create(self._template_vals())
        with self.assertRaises(AccessError):
            template.write({'user_id': self.other.id})
        with self.assertRaises(AccessError):
            self.Template.with_user(self.owner).create(self._template_vals(user_id=self.other.id))
        self.assertEqual(template.user_id, self.owner)

    def test_a_quick_export_does_not_put_ids_in_the_url(self):
        template = self.Template.with_user(self.owner).create(self._template_vals())
        action = self.Template.with_user(self.owner).generate_report_from_action(template.id)
        self.assertIn('ids=&', action['url'])

    def test_a_scheduled_export_only_holds_what_its_owner_may_read(self):
        self.env['res.partner'].create([{'name': 'ERB Scope Visible'}, {'name': 'ERB Scope Hidden'}])
        self.env['ir.rule'].create({
            'name': 'Report builder test: hide one partner',
            'model_id': self.env['ir.model']._get('res.partner').id,
            'domain_force': "[('name', '!=', 'ERB Scope Hidden')]",
        })
        template = self.Template.with_user(self.owner).create(self._template_vals(
            schedule_enabled=True, schedule_email_to='report-test@example.com'))
        template.sudo().schedule_next = fields.Datetime.now() - timedelta(hours=1)

        builds = []

        def fake_build(env, params):
            rows = _collect_export_data(
                env, params['model'], params['field_list'], params['field_type'],
                params['groupby'], params['domain'])
            builds.append({'uid': env.uid, 'su': env.su, 'names': sorted(r['name'] for r in rows)})
            return b'xlsx'

        MailMail = type(self.env['mail.mail'])
        with patch.object(dynamic_report_template, 'build_workbook', side_effect=fake_build), \
                patch.object(MailMail, 'send', autospec=True) as send:
            self.Template._cron_send_scheduled()   # the cron runs as superuser

        owner_builds = [build for build in builds if build['uid'] == self.owner.id]
        self.assertEqual(len(owner_builds), 1, builds)
        self.assertFalse(owner_builds[0]['su'])
        self.assertEqual(owner_builds[0]['names'], ['ERB Scope Visible'])
        self.assertTrue(send.called)
        self.assertTrue(template.schedule_last_sent)
