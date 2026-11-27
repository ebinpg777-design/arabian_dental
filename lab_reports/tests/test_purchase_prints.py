# -*- coding: utf-8 -*-
from odoo import Command
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestPurchasePrints(TransactionCase):
    """The purchase print-outs on the tax invoice's stationery (2026-11-27).

    Rendered as HTML: a PDF needs the server's report assets over HTTP, which the
    test runner cannot serve to itself."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vendor = cls.env['res.partner'].create({
            'name': 'PRINT TEST VENDOR', 'street': 'Shop 4,Main Road', 'city': 'Kochi',
            'vat': '32ABCDE1234F1Z5', 'email': 'sales@vendor.example',
        })
        cls.product = cls.env['product.product'].create({
            'name': 'Zirconia disc 98.5 x 14 mm', 'purchase_ok': True,
        })
        cls.tax = cls.env['account.tax'].create({
            'name': 'Test GST 12% P', 'amount': 12.0, 'amount_type': 'percent',
            'type_tax_use': 'purchase',
        })
        cls.order = cls.env['purchase.order'].create({
            'partner_id': cls.vendor.id,
            'partner_ref': 'Q-77',
            'order_line': [
                Command.create({'product_id': cls.product.id, 'product_qty': 3,
                                'price_unit': 12.345, 'tax_ids': [Command.set(cls.tax.ids)]}),
                Command.create({'product_id': cls.product.id, 'product_qty': 2,
                                'price_unit': 100.0, 'name': '\nShade A2\n\nlatest batch'}),
            ],
        })

    def _html(self, xmlid, records):
        html, _ = self.env['ir.actions.report']._render_qweb_html(xmlid, records.ids)
        return html.decode()

    def test_an_unconfirmed_order_prints_as_a_request_for_quotation(self):
        html = self._html('purchase.action_report_purchase_order', self.order)
        self.assertIn('REQUEST FOR QUOTATION', html)
        self.assertIn('oi-letterhead', html, "the invoice's letterhead card")
        self.assertIn('PRINT TEST VENDOR', html)
        self.assertIn('Q-77', html)
        self.order.button_confirm()
        html = self._html('purchase.action_report_purchase_order', self.order)
        self.assertIn('PURCHASE ORDER', html)
        self.assertNotIn('REQUEST FOR QUOTATION', html)
        self.assertIn('Amount in words', html)

    def test_the_rfq_print_asks_for_prices_instead_of_showing_them(self):
        html = self._html('purchase.report_purchase_quotation', self.order)
        self.assertIn('REQUEST FOR QUOTATION', html)
        self.assertNotIn('Amount in words', html)
        self.assertNotIn('>Rate<', html)

    def test_rates_keep_a_third_decimal_only_when_there_is_one(self):
        order = self.order
        places = order.currency_id.decimal_places
        if self.env['decimal.precision'].precision_get('Product Price') > places:
            self.assertTrue(order._lab_rate(12.345).endswith('12.345'))
        self.assertTrue(order._lab_rate(185.71).endswith('185.71'))
        self.assertTrue(order._lab_rate(100).endswith('100.00'))

    def test_labels_lose_blank_lines(self):
        label = self.order._lab_label(self.order.order_line[1])
        self.assertNotIn('\n\n', label)
        self.assertIn('Shade A2', label)

    def test_gst_columns_add_up_to_the_order_tax(self):
        names, table = self.order._lab_gst_table()
        tot = self.order._lab_column_totals((names, table))
        self.assertAlmostEqual(sum(tot['gst'].values()), self.order.amount_tax, places=2)
        self.assertAlmostEqual(tot['taxable'], self.order.amount_untaxed, places=2)

    def test_quantities_of_different_units_are_not_summed(self):
        dozen = self.env.ref('uom.product_uom_dozen', raise_if_not_found=False)
        if not dozen:
            self.skipTest('no dozen unit')
        self.order.order_line[1].uom_id = dozen
        self.assertEqual(self.order._lab_column_totals()['qty_text'], '')

    def test_purchase_prints_use_the_invoice_paper_format(self):
        lab = self.env.ref('sale_custom.paperformat_lab_invoice')
        for xmlid in ('purchase.action_report_purchase_order', 'purchase.report_purchase_quotation',
                      'ebshel_purchase_request.action_report_rfq'):
            report = self.env.ref(xmlid)
            self.assertEqual(report.get_paperformat(), lab, xmlid)
        # a paper format someone chose is theirs
        chosen = self.env['report.paperformat'].create({'name': 'Chosen', 'format': 'A5'})
        report = self.env.ref('purchase.action_report_purchase_order')
        report.paperformat_id = chosen
        self.assertEqual(report.get_paperformat(), chosen)

    def test_purchase_request_rfq_prints_on_the_lab_stationery(self):
        rfq = self.env['ebshel.purchase.rfq'].create({
            'rfq_line_ids': [Command.create({'product_id': self.product.id, 'qty': 4,
                                             'uom_id': self.product.uom_id.id,
                                             'remarks': 'Same brand as last time'})],
            'terms_conditions': '<p>Delivery to the lab.</p>',
        })
        html = self._html('ebshel_purchase_request.action_report_rfq', rfq)
        self.assertIn('oi-letterhead', html)
        self.assertIn('REQUEST FOR QUOTATION', html)
        self.assertIn('Same brand as last time', html)
        self.assertIn('Delivery to the lab.', html)
        self.assertIn('op-fill', html, 'blank columns for the vendor')
