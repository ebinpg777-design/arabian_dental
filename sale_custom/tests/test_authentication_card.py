# -*- coding: utf-8 -*-
from datetime import date

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import Form, TransactionCase, tagged


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

    # -- the Authentication Card button and its wizard (client, 2026-10-20) ------------
    def _wizard(self, records):
        return self.env['lab.auth.card.wizard'].with_context(
            active_model=records._name, active_ids=records.ids).create({})

    def test_the_button_opens_the_wizard_on_the_orders(self):
        action = (self.order_a | self.order_b).action_open_auth_card_wizard()
        self.assertEqual((action['res_model'], action['target']), ('lab.auth.card.wizard', 'new'))
        wiz = self._wizard(self.order_a | self.order_b)
        self.assertEqual(wiz.line_ids.order_id, self.order_a | self.order_b)
        self.assertTrue(all(wiz.line_ids.mapped('selected')))
        self.assertEqual(wiz.stock, 'pre_printed', "the lab prints on its pre-printed cards")
        self.assertEqual((wiz.card_count, wiz.selected_count, wiz.order_count), (2, 2, 2))

    def test_a_cancelled_order_is_not_offered(self):
        self.order_b.state = 'cancel'
        self.assertEqual(self._wizard(self.order_a | self.order_b).line_ids.order_id, self.order_a)

    def test_deselected_orders_are_left_out_and_copies_repeat(self):
        wiz = self._wizard(self.order_a | self.order_b)
        wiz.line_ids.filtered(lambda line: line.order_id == self.order_b).selected = False
        wiz.copies = 2
        self.assertEqual(wiz.card_count, 2)
        self.assertEqual([order for order, _card in wiz._cards_to_print()], [self.order_a, self.order_a])
        html = self._html('sale_custom.report_authentication_card_wizard_values', wiz)
        self.assertEqual(html.count('NISAM'), 2)
        self.assertNotIn('RIYA', html)
        self.assertNotIn('AUTHENTICATION CARD', html, "pre-printed stock: the values only")
        self.assertEqual(wiz.action_print()['report_name'], 'sale_custom.report_authentication_card_wizard_values')
        wiz.stock = 'blank'
        self.assertEqual(wiz.action_print()['report_name'], 'sale_custom.report_authentication_card_wizard')
        self.assertIn('AUTHENTICATION CARD', self._html('sale_custom.report_authentication_card_wizard', wiz))

    def test_select_all_clear_and_nothing_ticked(self):
        wiz = self._wizard(self.order_a | self.order_b)
        wiz.action_select_none()
        self.assertEqual(wiz.selected_count, 0)
        with self.assertRaises(UserError):
            wiz.action_print()
        wiz.action_select_all()
        self.assertEqual(wiz.selected_count, 2)

    def test_from_invoices_each_card_carries_its_invoice_date(self):
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.clinic.id, 'invoice_date': '2026-08-08',
            'invoice_line_ids': [
                (0, 0, {'name': 'Crown', 'quantity': 1, 'price_unit': 100.0,
                        'sale_line_ids': [(6, 0, self.order_a.order_line.ids)]}),
                (0, 0, {'name': 'Crown', 'quantity': 1, 'price_unit': 100.0,
                        'sale_line_ids': [(6, 0, self.order_b.order_line.ids)]}),
            ],
        })
        self.assertEqual(invoice.action_open_auth_card_wizard()['res_model'], 'lab.auth.card.wizard')
        wiz = self._wizard(invoice)
        self.assertEqual(wiz.source, 'invoice')
        self.assertEqual(wiz.line_ids.order_id, self.order_a | self.order_b)
        self.assertEqual(set(wiz.line_ids.mapped('issue_date')), {date(2026, 8, 8)})
        self.assertIn('08/08/2026', self._html('sale_custom.report_authentication_card_wizard_values', wiz))

    def test_the_preview_shows_the_ticked_cards(self):
        wiz = self._wizard(self.order_a | self.order_b)
        self.assertIn('NISAM', wiz.preview_html)
        self.assertIn('RIYA', wiz.preview_html)
        wiz.line_ids.filtered(lambda line: line.order_id == self.order_b).selected = False
        self.assertNotIn('RIYA', wiz.preview_html)

    def test_the_print_menu_no_longer_lists_the_old_entries(self):
        for xmlid in ('sale_custom.action_report_authentication_card',
                      'sale_custom.action_report_authentication_card_values',
                      'sale_custom.action_report_authentication_card_invoice',
                      'sale_custom.action_report_authentication_card_invoice_values'):
            self.assertFalse(self.env.ref(xmlid).binding_model_id, xmlid)

    def test_the_wizard_saves_as_the_browser_does(self):
        # through the form, as the client: read-only columns (the order) must still be
        # sent on save, or the lines arrive without their order (found 2026-10-20)
        form = Form(self.env['lab.auth.card.wizard'].with_context(
            active_model='sale.order', active_ids=(self.order_a | self.order_b).ids))
        with form.line_ids.edit(1) as line:
            line.selected = False
        wiz = form.save()
        self.assertEqual(wiz.line_ids.order_id, self.order_a | self.order_b)
        self.assertEqual(wiz.line_ids.filtered('selected').order_id, self.order_a)
        self.assertEqual(wiz.action_print()['report_name'], 'sale_custom.report_authentication_card_wizard_values')
