# -*- coding: utf-8 -*-
"""Print Order List from the orders ticked in the list. (client, 2026-09-18)

The wizard opens on exactly those orders: the period and the filters step aside,
only the grouping is asked, and the print says it is a selection.
"""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestOrderListSelection(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clinic = cls.env['res.partner'].create({'name': 'Selection Clinic'})
        cls.other_clinic = cls.env['res.partner'].create({'name': 'Other Clinic'})
        Order = cls.env['sale.order']
        cls.old = Order.create({'partner_id': cls.clinic.id,
                                'date_order': fields.Datetime.now() - timedelta(days=40)})
        cls.new = Order.create({'partner_id': cls.other_clinic.id})
        cls.left_out = Order.create({'partner_id': cls.clinic.id})

    def _wizard_of(self, orders):
        action = orders.action_print_order_list()
        self.assertEqual(action['res_model'], 'sale.order.list.report')
        return self.env['sale.order.list.report'].browse(action['res_id'])

    def test_the_wizard_opens_on_the_ticked_orders_only(self):
        wizard = self._wizard_of(self.old | self.new)
        self.assertEqual(wizard.order_count, 2)
        self.assertEqual(wizard._orders(), self.old | self.new, "oldest first")
        self.assertNotIn(self.left_out, wizard._orders())
        self.assertEqual(wizard.date_from,
                         fields.Datetime.context_timestamp(wizard, self.old.date_order).date(),
                         "the period on the print spans the selection")
        self.assertEqual(wizard.group_by, 'none', "a hand-picked list prints flat")

    def test_the_filters_step_aside_for_a_selection(self):
        wizard = self._wizard_of(self.old | self.new)
        wizard.write({'partner_ids': [(6, 0, self.clinic.ids)], 'type_fixed': True,
                      'date_from': fields.Date.context_today(wizard),
                      'date_to': fields.Date.context_today(wizard)})
        self.assertEqual(wizard._orders(), self.old | self.new,
                         "a clinic, a kind and a period do not narrow a selection")
        values = self.env['report.sale_custom.report_order_list']._get_report_values(wizard.ids)
        self.assertEqual(values['order_count'], 2)
        self.assertIn(('Selection', '2 orders picked in the list'), values['criteria'])
        self.assertFalse([c for c in values['criteria'] if c[0] in ('Customers', 'Appliance')])

    def test_grouping_still_applies_to_a_selection(self):
        wizard = self._wizard_of(self.old | self.new)
        wizard.group_by = 'partner'
        groups = self.env['report.sale_custom.report_order_list']._rows(wizard, wizard._orders())
        self.assertEqual(sorted(g['label'] for g in groups), ['Other Clinic', 'Selection Clinic'])

    def test_production_slips_from_a_selection_need_a_confirmed_order(self):
        wizard = self._wizard_of(self.old | self.new)
        with self.assertRaises(UserError):
            wizard.action_print_production_orders()

    def test_the_print_menu_prints_the_selection_in_one_click(self):
        report = self.env.ref('sale_custom.action_report_order_list_selection')
        self.assertEqual((report.binding_model_id.model, report.binding_type), ('sale.order', 'report'))
        values = self.env['report.sale_custom.report_order_list_selection']._get_report_values(
            (self.old | self.new).ids)
        self.assertEqual(values['order_count'], 2)
        self.assertIn(('Selection', '2 orders picked in the list'), values['criteria'])
        html = self.env['ir.actions.report']._render_qweb_html(
            'sale_custom.report_order_list_selection', (self.old | self.new).ids)[0].decode()
        self.assertIn('ORDER LIST', html)
        self.assertIn(self.old.name, html)
        self.assertIn(self.new.name, html)
        self.assertNotIn(self.left_out.name, html)

    def test_the_action_is_on_the_order_list(self):
        action = self.env.ref('sale_custom.action_print_order_list_selection')
        self.assertEqual(action.binding_model_id.model, 'sale.order')
        self.assertIn('list', action.binding_view_types)
        with self.assertRaises(UserError):
            self.env['sale.order'].browse().action_print_order_list()
