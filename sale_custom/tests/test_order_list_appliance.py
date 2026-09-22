# -*- coding: utf-8 -*-
"""The Appliance Type filter and grouping on the Order List. (client, 2026-09-18)

"The removable cases this month", "what still has no type": tick-boxes on the
wizard, and a grouping that counts the four kinds side by side.
"""
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestOrderListAppliance(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clinic = cls.env['res.partner'].create({'name': 'Appliance Filter Clinic'})
        Order = cls.env['sale.order']
        cls.fixed = Order.create({'partner_id': cls.clinic.id, 'appliance_type': 'fixed'})
        cls.removable = Order.create({'partner_id': cls.clinic.id, 'appliance_type': 'removable'})
        cls.other = Order.create({'partner_id': cls.clinic.id, 'appliance_type': 'other'})
        cls.untyped = Order.create({'partner_id': cls.clinic.id})

    def _wizard(self, **vals):
        vals.setdefault('partner_ids', [(6, 0, self.clinic.ids)])
        return self.env['sale.order.list.report'].create(vals)

    def test_nothing_ticked_is_every_kind(self):
        self.assertEqual(self._wizard()._orders(),
                         self.fixed | self.removable | self.other | self.untyped)

    def test_a_ticked_kind_narrows_the_list(self):
        self.assertEqual(self._wizard(type_fixed=True)._orders(), self.fixed)
        self.assertEqual(self._wizard(type_fixed=True, type_removable=True)._orders(),
                         self.fixed | self.removable)

    def test_not_set_finds_the_untyped_alone_or_with_a_kind(self):
        self.assertEqual(self._wizard(type_unset=True)._orders(), self.untyped)
        self.assertEqual(self._wizard(type_other=True, type_unset=True)._orders(),
                         self.other | self.untyped)

    def test_grouping_by_appliance_counts_the_kinds_in_the_labs_order(self):
        wizard = self._wizard(group_by='appliance')
        renderer = self.env['report.sale_custom.report_order_list']
        groups = renderer._rows(wizard, wizard._orders())
        self.assertEqual([g['label'] for g in groups], ['Fixed', 'Removable', 'Other', 'Not set'])
        self.assertEqual([len(g['rows']) for g in groups], [1, 1, 1, 1])
        values = renderer._get_report_values(wizard.ids)
        self.assertEqual(values['group_label'], 'Appliance Type')

    def test_the_print_names_the_filter(self):
        wizard = self._wizard(type_fixed=True, type_removable=True, type_unset=True)
        values = self.env['report.sale_custom.report_order_list']._get_report_values(wizard.ids)
        self.assertIn(('Appliance', 'Fixed, Removable, Not set'), values['criteria'])
        self.assertEqual(values['order_count'], 3)
        plain = self._wizard()
        values = self.env['report.sale_custom.report_order_list']._get_report_values(plain.ids)
        self.assertFalse([c for c in values['criteria'] if c[0] == 'Appliance'])

    def test_the_boxes_are_on_the_wizard(self):
        arch = self.env['sale.order.list.report'].get_view(
            self.env.ref('sale_custom.view_order_list_report_form').id, 'form')['arch']
        for name in ('type_fixed', 'type_removable', 'type_clear_retainer', 'type_other', 'type_unset'):
            self.assertIn('name="%s"' % name, arch)


@tagged('post_install', '-at_install')
class TestOrderListPriority(TransactionCase):
    """The Priority filter on the Order List print wizard. (client, 2026-09-19)

    "The urgent cases this month" is what the counter is asked when a doctor
    rings about a promise. Ticked the same way the appliance kinds are, because
    it is the same kind of question and two at once is ordinary.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clinic = cls.env['res.partner'].create({'name': 'Priority Filter Clinic'})
        Order = cls.env['sale.order']
        cls.urgent = Order.create({'partner_id': cls.clinic.id, 'priority': 'urgent'})
        cls.normal = Order.create({'partner_id': cls.clinic.id, 'priority': 'normal'})
        cls.low = Order.create({'partner_id': cls.clinic.id, 'priority': 'low'})

    def _wizard(self, **vals):
        vals.setdefault('partner_ids', [(6, 0, self.clinic.ids)])
        return self.env['sale.order.list.report'].create(vals)

    def test_nothing_ticked_is_every_priority(self):
        self.assertEqual(self._wizard()._orders(),
                         self.urgent | self.normal | self.low)

    def test_a_ticked_priority_narrows_the_list(self):
        self.assertEqual(self._wizard(prio_urgent=True)._orders(), self.urgent)
        self.assertEqual(self._wizard(prio_urgent=True, prio_normal=True)._orders(),
                         self.urgent | self.normal)

    def test_the_priority_filter_stacks_with_the_others(self):
        """Two filters are an AND, not a wider net: urgent AND fixed."""
        self.urgent.appliance_type = 'fixed'
        self.normal.appliance_type = 'fixed'
        self.assertEqual(
            self._wizard(prio_urgent=True, type_fixed=True)._orders(), self.urgent)

    def test_the_print_names_the_filter(self):
        wizard = self._wizard(prio_urgent=True, prio_low=True)
        values = self.env['report.sale_custom.report_order_list']._get_report_values(wizard.ids)
        self.assertIn(('Priority', 'Urgent, Low'), values['criteria'])
        self.assertEqual(values['order_count'], 2)
        plain = self._wizard()
        values = self.env['report.sale_custom.report_order_list']._get_report_values(plain.ids)
        self.assertFalse([c for c in values['criteria'] if c[0] == 'Priority'])

    def test_the_boxes_are_on_the_wizard(self):
        arch = self.env['sale.order.list.report'].get_view(
            self.env.ref('sale_custom.view_order_list_report_form').id, 'form')['arch']
        for name in ('prio_urgent', 'prio_normal', 'prio_low'):
            self.assertIn('name="%s"' % name, arch)


@tagged('post_install', '-at_install')
class TestRushColours(TransactionCase):
    """Emergency red, urgent orange - on the Order List print and the Due list.
    (client, 2026-09-19)

    `emergency` is a priority level of its own, added to the selection by
    lab_order_control: the broken appliance for a patient in the chair. It sits
    ABOVE urgent, and 36 live orders carry it. Anything that treats urgent as
    rushed must treat emergency the same, or the more urgent case gets the
    slacker handling - which is exactly what the due-date promise was doing.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clinic = cls.env['res.partner'].create({'name': 'Rush Colour Clinic'})
        cls.product = cls.env['product.product'].create(
            {'name': 'Rush Work', 'list_price': 1000.0})

    def _order(self, priority='normal'):
        return self.env['sale.order'].create({
            'partner_id': self.clinic.id, 'priority': priority,
            'order_line': [(0, 0, {'product_id': self.product.id,
                                   'product_uom_qty': 1, 'price_unit': 1000.0})]})

    def test_emergency_is_a_priority_the_lab_can_actually_pick(self):
        keys = dict(self.env['sale.order']._fields['priority'].selection)
        self.assertIn('emergency', keys, "lab_order_control adds it")
        self.assertEqual(keys['emergency'], 'Emergency')

    def test_an_emergency_is_owed_the_same_day_as_an_urgent_case(self):
        """The bug this found: comparing against 'urgent' alone let an emergency
        fall through to the two-day lead, so the MORE urgent case was promised
        LATER. SO283635 on the live database was ordered 21 Aug, due 23 Aug."""
        today = fields.Date.context_today(self.env['sale.order'])
        self.assertEqual(self._order('emergency')._due_date_on_confirm(), today)
        self.assertEqual(self._order('urgent')._due_date_on_confirm(), today)
        self.assertEqual(self._order('normal')._due_date_on_confirm(),
                         today + timedelta(days=2), "an ordinary case still gets its lead")

    def test_an_emergency_marks_its_lines_urgent_like_an_urgent_case(self):
        order = self._order('normal')
        order.priority = 'emergency'
        order._onchange_priority()
        self.assertTrue(all(order.order_line.mapped('is_urgent')))

    def test_the_due_list_paints_by_rush_not_by_lateness(self):
        """Every row in that list is past due by definition, so colouring by
        days_overdue washed the whole sheet and said nothing."""
        arch = self.env['sale.order'].get_view(
            self.env.ref('sale_custom.view_order_due_list').id, 'list')['arch']
        self.assertIn("decoration-danger=\"priority == 'emergency'\"", arch)
        self.assertIn("decoration-warning=\"priority == 'urgent'\"", arch)
        self.assertNotIn('days_overdue &gt; 0', arch)
        self.assertNotIn('days_overdue > 0', arch)

    def test_the_print_tints_both_tiers_and_keys_only_colours_it_used(self):
        wizard = self.env['sale.order.list.report'].create(
            {'partner_ids': [(6, 0, self.clinic.ids)], 'group_by': 'none'})
        renderer = self.env['report.sale_custom.report_order_list']
        self._order('normal')
        self.assertFalse(renderer._get_report_values(wizard.ids)['has_rush'],
                         "no legend for colours the sheet does not carry")
        self._order('emergency')
        self._order('urgent')
        self.assertTrue(renderer._get_report_values(wizard.ids)['has_rush'])
        html = self.env['ir.actions.report']._render_qweb_html(
            'sale_custom.report_order_list', wizard.ids)[0].decode()
        # two row tiers each, plus one legend chip each
        self.assertEqual(html.count('#f8d0d0'), 3, "emergency red, both tiers + legend")
        self.assertEqual(html.count('#ffe2c2'), 3, "urgent orange, both tiers + legend")

    def test_the_print_can_be_filtered_to_the_emergencies(self):
        emergency, urgent = self._order('emergency'), self._order('urgent')
        self._order('normal')
        wizard = self.env['sale.order.list.report'].create(
            {'partner_ids': [(6, 0, self.clinic.ids)], 'prio_emergency': True})
        self.assertEqual(wizard._orders(), emergency)
        wizard.prio_urgent = True
        self.assertEqual(wizard._orders(), emergency | urgent)
        values = self.env['report.sale_custom.report_order_list']._get_report_values(wizard.ids)
        self.assertIn(('Priority', 'Emergency, Urgent'), values['criteria'])

    def test_the_boxes_are_on_the_wizard(self):
        arch = self.env['sale.order.list.report'].get_view(
            self.env.ref('sale_custom.view_order_list_report_form').id, 'form')['arch']
        for name in ('prio_emergency', 'prio_urgent', 'prio_normal', 'prio_low'):
            self.assertIn('name="%s"' % name, arch)
