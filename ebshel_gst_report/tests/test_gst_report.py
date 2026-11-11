# Part of ebshel_gst_report. See LICENSE file for full copyright and licensing details.
import io

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.addons.l10n_in.tests.common import L10nInTestInvoicingCommon

try:
    import openpyxl
except ImportError:  # pragma: no cover
    openpyxl = None


@tagged('post_install_l10n', 'post_install', '-at_install')
class TestGstReport(L10nInTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner_a.write({'l10n_in_gst_treatment': 'regular'})
        cls.partner_b.write({'l10n_in_gst_treatment': 'regular'})
        cls.consumer = cls.env['res.partner'].create({
            'name': 'Walk-in Patient', 'l10n_in_gst_treatment': 'consumer',
            'state_id': cls.default_company.state_id.id, 'country_id': cls.country_in.id})
        cls.product_a.write({'l10n_in_hsn_code': '90212100'})
        cls.nil_tax = cls.env['account.tax'].search([
            ('company_id', '=', cls.default_company.id), ('type_tax_use', '=', 'sale'),
            ('l10n_in_tax_type', '=', 'nil_rated')], limit=1) or cls.env['account.tax'].create({
            'name': 'Nil Rated (test)', 'amount': 0.0, 'type_tax_use': 'sale', 'l10n_in_tax_type': 'nil_rated',
            'company_id': cls.default_company.id})
        cls.day = '2023-05-20'

        def invoice(partner, lines, move_type='out_invoice', post=True):
            move = cls.env['account.move'].create({
                'move_type': move_type, 'partner_id': partner.id, 'invoice_date': cls.day,
                'invoice_line_ids': [Command.create(dict({'quantity': 1.0}, **line)) for line in lines],
            })
            if post:
                move.action_post()
            return move
        # intra-state registered: 2 x 1000 less 10 % at 5 %, plus 100 at 5 %
        cls.intra = invoice(cls.partner_a, [
            {'product_id': cls.product_a.id, 'quantity': 2.0, 'price_unit': 1000.0, 'discount': 10.0,
             'tax_ids': [Command.set(cls.sgst_sale_5.ids)]},
            {'product_id': cls.product_a.id, 'price_unit': 100.0, 'tax_ids': [Command.set(cls.sgst_sale_5.ids)]},
        ])
        # inter-state registered at 18 %
        cls.inter = invoice(cls.partner_b, [
            {'product_id': cls.product_a.id, 'price_unit': 500.0, 'tax_ids': [Command.set(cls.igst_sale_18.ids)]},
        ])
        # a consumer, intra-state, with a nil-rated line too
        cls.to_consumer = invoice(cls.consumer, [
            {'product_id': cls.product_a.id, 'price_unit': 300.0, 'tax_ids': [Command.set(cls.sgst_sale_5.ids)]},
            {'product_id': cls.product_a.id, 'price_unit': 40.0, 'tax_ids': [Command.set(cls.nil_tax.ids)]},
        ])
        cls.draft = invoice(cls.partner_a, [
            {'product_id': cls.product_a.id, 'price_unit': 300.0, 'tax_ids': [Command.set(cls.sgst_sale_5.ids)]},
        ], post=False)
        cls.refund = invoice(cls.partner_a, [
            {'product_id': cls.product_a.id, 'price_unit': 200.0, 'tax_ids': [Command.set(cls.sgst_sale_5.ids)]},
        ], move_type='out_refund')

    def _wizard(self, **vals):
        return self.env['gst.report.wizard'].create(dict({
            'date_from': self.day, 'date_to': self.day, 'report_type': 'all',
            'company_ids': [Command.set(self.default_company.ids)]}, **vals))

    def _sections(self):
        sections = self._wizard()._sections()
        sections.pop('_vouchers')
        return sections

    def test_b2b_sums_the_registered_customers(self):
        b2b = {g['partner']: g for g in self._sections()['b2b']}
        self.assertEqual(set(b2b), {self.partner_a.name, self.partner_b.name},
                         "registered customers only; the consumer and the draft are out")
        a, b = b2b[self.partner_a.name], b2b[self.partner_b.name]
        self.assertEqual((a['count'], a['taxable'], a['cgst'], a['sgst'], a['igst']), (1, 1900.0, 47.5, 47.5, 0.0))
        self.assertEqual(a['tax'], 95.0, "total tax is the sum of the four, not a blank")
        self.assertEqual(a['total'], 1995.0)
        self.assertEqual((b['taxable'], b['igst'], b['tax'], b['total']), (500.0, 90.0, 90.0, 590.0))
        self.assertEqual(a['vat'], self.partner_a.vat)

    def test_b2b_detailed_is_one_row_per_invoice_and_rate(self):
        rows = self._sections()['b2b_detailed']
        self.assertEqual([(r['name'], r['rate'], r['taxable']) for r in rows],
                         [(self.intra.name, 5.0, 1900.0), (self.inter.name, 18.0, 500.0)])
        self.assertTrue(rows[0]['pos'].endswith(self.partner_a.state_id.name))
        self.assertEqual(rows[0]['date'], self.intra.invoice_date)

    def test_b2cs_groups_the_consumers_by_state_and_rate(self):
        rows = self._sections()['b2cs']
        self.assertEqual([(r['pos_state'], r['rate'], r['taxable'], r['cgst'], r['sgst']) for r in rows],
                         [(self.consumer.state_id.name, 0.0, 40.0, 0.0, 0.0),
                          (self.consumer.state_id.name, 5.0, 300.0, 7.5, 7.5)])

    def test_hsn_nets_the_credit_note_off(self):
        hsn = {(g['hsn'], g['rate']): g for g in self._sections()['hsn']}
        five = hsn[('90212100', 5.0)]
        self.assertEqual((five['quantity'], five['taxable']), (3.0, 2000.0),
                         "2 + 1 + 1 - 1 refund; 1800 + 100 + 300 - 200")
        self.assertEqual(five['tax'], 100.0)
        self.assertEqual(hsn[('90212100', 18.0)]['taxable'], 500.0)
        split = {(g['klass'], g['rate']): g for g in self._sections()['hsn_gst']}
        self.assertEqual(split[('B2B', 5.0)]['taxable'], 1700.0, "1900 - 200, the consumer apart")
        self.assertEqual(split[('B2C', 5.0)]['taxable'], 300.0)
        invcr = {(g['klass'], g['rate']): g for g in self._sections()['invcr']}
        self.assertEqual((invcr[('B2B', 5.0)]['taxable'], invcr[('B2B', 5.0)]['cr_taxable']), (1900.0, 200.0))

    def test_credit_notes_documents_and_nil_rated(self):
        sections = self._sections()
        self.assertEqual([(r['name'], r['taxable'], r['tax']) for r in sections['cdnr']],
                         [(self.refund.name, 200.0, 10.0)])
        self.assertEqual(sections['cdnur'], [])
        self.assertEqual(sections['exp'], [])
        docs = {g['move_type']: g for g in sections['docs']}
        self.assertEqual((docs['out_invoice']['total'], docs['out_invoice']['cancelled']), (3, 0),
                         "posted invoices with a number; the draft has none")
        self.assertEqual(docs['out_refund']['net'], 1)
        self.assertEqual(docs['out_invoice']['first'], min(self.intra.name, self.inter.name, self.to_consumer.name))
        nil = sections['nil_rated']
        self.assertEqual(nil[('intra', 'unregistered')]['nil_rated'], 40.0)
        self.assertEqual(nil[('inter', 'registered')]['nil_rated'], 0.0)

    def test_the_workbook_opens_with_a_sheet_per_section(self):
        if openpyxl is None:
            self.skipTest("openpyxl is not installed")
        wizard = self._wizard()
        self.assertIn('3 invoice(s) and 1 credit note(s)', wizard.summary)
        book = openpyxl.load_workbook(io.BytesIO(wizard._xlsx()), read_only=True)
        self.assertEqual(book.sheetnames, ['B2B', 'B2B Detailed', 'B2CS', 'B2CS Detailed', 'HSN', 'HSN B2C-B2B',
                                           'EXP', 'CDNR', 'CDNUR', 'DOCS', 'Nil Rated', 'HSN INV-CR', 'Tax Check'])
        rows = list(book['B2B'].iter_rows(values_only=True))
        self.assertEqual(rows[3][1], 'GSTR-1  -  Voucher Register')
        self.assertEqual(rows[6][1], 'Particulars')
        total = [r for r in rows if r[1] == 'Grand Total'][0]
        self.assertEqual((total[3], total[4], total[10]), (2, 2400.0, 2585.0), "count, taxable, invoice amount")
        self.assertEqual(wizard._filename(), 'GST Report 20-05-2023.xlsx')
        single = self._wizard(report_type='hsn')
        self.assertEqual(openpyxl.load_workbook(io.BytesIO(single._xlsx()), read_only=True).sheetnames, ['HSN'])

    def test_the_tax_check_names_the_wrong_tax(self):
        """An intra-state customer charged IGST, and an inter-state one charged CGST + SGST."""
        wrong_igst = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.partner_a.id, 'invoice_date': self.day,
            'invoice_line_ids': [Command.create({'product_id': self.product_a.id, 'quantity': 1.0,
                                                 'price_unit': 100.0, 'tax_ids': [Command.set(self.igst_sale_18.ids)]})]})
        wrong_igst.action_post()
        wrong_cgst = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.partner_b.id, 'invoice_date': self.day,
            'invoice_line_ids': [Command.create({'product_id': self.product_a.id, 'quantity': 1.0,
                                                 'price_unit': 50.0, 'tax_ids': [Command.set(self.sgst_sale_5.ids)]})]})
        wrong_cgst.action_post()
        rows = {r['name']: r for r in self._sections()['tax_check']}
        self.assertEqual(set(rows), {wrong_igst.name, wrong_cgst.name}, "the right ones are not listed")
        self.assertEqual((rows[wrong_igst.name]['kind'], rows[wrong_igst.name]['expected'], rows[wrong_igst.name]['tax']),
                         ('IGST', 'CGST + SGST', 18.0))
        self.assertEqual((rows[wrong_cgst.name]['kind'], rows[wrong_cgst.name]['expected']), ('CGST + SGST', 'IGST'))

    def test_a_register_needs_one_state(self):
        other = self.env['res.company'].create({'name': 'Elsewhere', 'country_id': self.country_in.id})
        with self.assertRaises(UserError):
            self._wizard(company_ids=[Command.set((self.default_company + other).ids)])._check()
        with self.assertRaises(UserError):
            self._wizard(date_from='2023-05-21')._check()
