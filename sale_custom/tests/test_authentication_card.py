# -*- coding: utf-8 -*-
from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAuthenticationCard(TransactionCase):
    """The card printed with a finished case: from the order, and from the invoice
    for every order it bills."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clinic = cls.env['res.partner'].create({'name': 'Abhayam DC'})
        product = cls.env['product.product'].create({'name': 'Crown', 'type': 'consu', 'list_price': 100.0})

        def order(patient):
            return cls.env['sale.order'].create({
                'partner_id': cls.clinic.id, 'patient': patient,
                'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': 1})],
            })

        cls.order_a = order('Nisam')
        cls.order_b = order('Riya')
        cls.Report = cls.env['ir.actions.report']

    def _html(self, report, records):
        html, _kind = self.Report._render_qweb_html(report, records.ids)
        return html.decode()

    def test_the_card_says_who_what_and_when(self):
        card = self.order_a._card_values()
        when = fields.Datetime.context_timestamp(self.order_a, self.order_a.date_order)
        self.assertEqual({key: card[key] for key in ('patient', 'doctor', 'job', 'issue_date')}, {
            'patient': 'NISAM', 'doctor': 'ABHAYAM DC', 'job': self.order_a.name,
            'issue_date': when.strftime('%d/%m/%Y'),
        })
        # the foot of the card: the lab's general e-mail (the letterhead's last) and
        # the website without its scheme
        company = self.order_a.company_id
        self.assertLessEqual(len(card['emails']), 1)
        self.assertEqual(card['website'], (company.website or '').replace('https://', '')
                         .replace('http://', '').rstrip('/'))
        html = self._html('sale_custom.report_authentication_card', self.order_a)
        for text in ('AUTHENTICATION CARD', 'PATIENT NAME', 'NISAM', 'ABHAYAM DC',
                     self.order_a.name, card['issue_date'], 'QUALITY'):
            self.assertIn(text, html)

    def test_pre_printed_stock_gets_the_values_only(self):
        html = self._html('sale_custom.report_authentication_card_values', self.order_a)
        self.assertNotIn('AUTHENTICATION CARD', html)
        self.assertNotIn('PATIENT NAME', html)
        self.assertNotIn('QUALITY', html)
        self.assertIn('NISAM', html)
        self.assertIn(self.order_a.name, html)

    def test_an_invoice_prints_one_card_per_order(self):
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.clinic.id,
            'invoice_line_ids': [
                (0, 0, {'name': 'Crown', 'quantity': 1, 'price_unit': 100.0,
                        'sale_line_ids': [(6, 0, self.order_a.order_line.ids)]}),
                (0, 0, {'name': 'Crown', 'quantity': 1, 'price_unit': 100.0,
                        'sale_line_ids': [(6, 0, self.order_b.order_line.ids)]}),
            ],
        })
        self.assertEqual(invoice._card_orders(), self.order_a | self.order_b)
        html = self._html('sale_custom.report_authentication_card_invoice', invoice)
        self.assertEqual(html.count('AUTHENTICATION CARD'), 2)
        for text in (self.order_a.name, self.order_b.name, 'NISAM', 'RIYA'):
            self.assertIn(text, html)

    def test_an_invoice_typed_in_from_an_order_name_still_finds_it(self):
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.clinic.id,
            'invoice_origin': self.order_b.name,
            'invoice_line_ids': [(0, 0, {'name': 'Crown', 'quantity': 1, 'price_unit': 100.0})],
        })
        self.assertEqual(invoice._card_orders(), self.order_b)

    def test_the_doctor_line_is_the_billing_name_as_odoo_17_printed(self):
        if 'billing_name' not in self.clinic._fields:
            self.skipTest("no billing name on contacts in this database")
        self.clinic.billing_name = 'Dr. Nisam Abhayam'
        self.assertEqual(self.order_a._card_values()['doctor'], 'DR. NISAM ABHAYAM')

    def test_a_card_from_an_invoice_carries_the_invoice_date(self):
        card = self.order_a._card_values(issue_date=fields.Date.to_date('2026-08-08'))
        self.assertEqual(card['issue_date'], '08/08/2026')

    def test_the_pre_printed_stock_prints_on_odoo_17s_80_by_50_page(self):
        for xmlid in ('sale_custom.action_report_authentication_card_values',
                      'sale_custom.action_report_authentication_card_invoice_values'):
            paper = self.env.ref(xmlid).paperformat_id
            self.assertEqual((paper.page_width, paper.page_height, paper.margin_top, paper.margin_left),
                             (80, 50, 0, 0), xmlid)
