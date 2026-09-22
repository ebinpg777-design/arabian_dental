# -*- coding: utf-8 -*-
"""Print Production Orders, from the Order List wizard.

The office already builds a filtered list of orders here - a period, a clinic, a
route, a product. "Print the production orders for what I'm looking at" should not
need a second wizard with the same four fields re-typed: the button reuses this
wizard's own domain and hands the matching orders to the slip report. Only
CONFIRMED orders qualify - a quotation has nothing on the bench yet - and a filter
wide enough to catch thousands of orders is refused rather than ground through.
(client, 2026-08-29)
"""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestOrderListProductionPrint(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clinic = cls.env['res.partner'].create({'name': 'Order List Clinic'})
        cls.other_clinic = cls.env['res.partner'].create({'name': 'Order List Clinic 2'})
        cls.product = cls.env['product.product'].create({
            'name': 'Order List Appliance', 'type': 'consu', 'list_price': 100.0})
        cls.Wizard = cls.env['sale.order.list.report']

    def _order(self, partner=None, state='sale', date_order=None):
        order = self.env['sale.order'].create({
            'partner_id': (partner or self.clinic).id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 1})],
        })
        if date_order:
            order.date_order = date_order
        if state == 'sale':
            order.action_confirm()
        elif state == 'cancel':
            order.action_confirm()
            order.action_cancel()
        return order

    def _wizard(self, **vals):
        return self.Wizard.create(dict({
            'date_from': '2020-01-01', 'date_to': '2030-01-01'}, **vals))

    def test_only_confirmed_orders_are_printed(self):
        confirmed = self._order(state='sale')
        self._order(state='sale')  # a draft would need its own partner-less order;
        draft = self.env['sale.order'].create({
            'partner_id': self.clinic.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 1})]})
        wiz = self._wizard(partner_ids=[(6, 0, self.clinic.ids)])
        action = wiz.action_print_production_orders()
        self.assertEqual(action['report_name'],
                         'sale_custom.report_production_slips_sale')
        printed_ids = set(action['context']['active_ids'])
        self.assertIn(confirmed.id, printed_ids)
        self.assertNotIn(draft.id, printed_ids, "a quotation has nothing to make yet")

    def test_the_filter_narrows_the_orders_printed(self):
        mine = self._order(partner=self.clinic, state='sale')
        self._order(partner=self.other_clinic, state='sale')
        wiz = self._wizard(partner_ids=[(6, 0, self.clinic.ids)])
        action = wiz.action_print_production_orders()
        self.assertEqual(set(action['context']['active_ids']), {mine.id},
                         "the other clinic's order must not be on this sheet")

    def test_cancelled_orders_are_never_printed(self):
        self._order(state='cancel')
        wiz = self._wizard()
        with self.assertRaises(UserError):
            wiz.action_print_production_orders()

    def test_nothing_confirmed_refuses_with_a_clear_reason(self):
        self.env['sale.order'].create({
            'partner_id': self.clinic.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 1})]})
        wiz = self._wizard(partner_ids=[(6, 0, self.clinic.ids)])
        with self.assertRaises(UserError) as caught:
            wiz.action_print_production_orders()
        self.assertIn('confirmed', str(caught.exception).lower())

    def test_a_huge_filter_is_refused_not_ground_through(self):
        wiz = self._wizard(partner_ids=[(6, 0, self.clinic.ids)])
        self.env['ir.config_parameter'].sudo().set_param(
            'sale_custom.production_slip_print_limit', '1')
        self._order(state='sale')
        self._order(state='sale')
        with self.assertRaises(UserError) as caught:
            wiz.action_print_production_orders()
        self.assertIn('narrow', str(caught.exception).lower())

    def test_the_report_actually_renders(self):
        self._order(state='sale')
        wiz = self._wizard(partner_ids=[(6, 0, self.clinic.ids)])
        report = self.env.ref('sale_custom.action_report_production_slips_sale')
        action = wiz.action_print_production_orders()
        pdf, kind = report.with_context(
            force_report_rendering=True)._render_qweb_pdf(
                report.report_name, action['context']['active_ids'])
        self.assertEqual(kind, 'pdf')
        self.assertTrue(pdf.startswith(b'%PDF'))
