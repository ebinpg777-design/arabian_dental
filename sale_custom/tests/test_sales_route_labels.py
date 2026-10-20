# -*- coding: utf-8 -*-
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSalesRouteLabels(TransactionCase):
    """"Sales Team" reads "Sales Route" everywhere (client, 2026-10-20)."""

    def test_the_route_models_are_called_routes(self):
        self.assertEqual(self.env['ir.model']._get('crm.team').name, 'Sales Route')
        self.assertEqual(self.env['ir.model']._get('crm.team.member').name, 'Sales Route Member')

    def test_field_labels(self):
        for model, field, label in (
            ('sale.order', 'team_id', 'Sales Route'),
            ('account.move', 'team_id', 'Sales Route'),
            ('account.bank.statement.line', 'team_id', 'Sales Route'),
            ('account.invoice.report', 'team_id', 'Sales Route'),
            ('sale.report', 'team_id', 'Sales Route'),
            ('res.partner', 'team_id', 'Sales Route'),
            ('crm.team', 'name', 'Sales Route'),
            ('crm.team.member', 'crm_team_id', 'Sales Route'),
            ('res.users', 'sale_team_id', 'User Sales Route'),
            ('res.users', 'crm_team_ids', 'Sales Routes'),
            ('daily.sales', 'team_id', 'Sales Route'),
        ):
            description = self.env[model].fields_get([field], ['string'])[field]['string']
            self.assertEqual(description, label, '%s.%s' % (model, field))
        # and in the database, which chatter tracking and exports read
        fields_left = self.env['ir.model.fields'].search_read(
            [('field_description', 'ilike', 'sales team')], ['model', 'name'])
        self.assertFalse(fields_left)

    def test_the_menu_and_the_actions(self):
        self.assertEqual(self.env.ref('sale.sales_team_config').name, 'Sales Routes')
        for xmlid in ('sales_team.crm_team_action_config', 'sales_team.crm_team_action_pipeline'):
            self.assertEqual(self.env.ref(xmlid).name, 'Sales Routes', xmlid)
        self.assertEqual(self.env.ref('sales_team.crm_team_member_action').name, 'Sales Route Members')

    def test_no_screen_says_sales_team(self):
        for model, types in (
            ('sale.order', ['search', 'list', 'form']),
            ('sale.report', ['search']),
            ('account.move', ['search', 'list', 'form']),
            ('account.invoice.report', ['search']),
            ('crm.team', ['search', 'list', 'form']),
            ('crm.team.member', ['search', 'kanban', 'form']),
            ('daily.sales', ['search']),
        ):
            views = self.env[model].get_views([(False, view_type) for view_type in types])['views']
            for view_type, view in views.items():
                self.assertNotIn('Sales Team', view['arch'], '%s %s' % (model, view_type))
