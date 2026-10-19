# -*- coding: utf-8 -*-
"""The figures the tax invoice prints under its lines, and the teeth cross.

(client, 2026-10-19: "GST should show in columns", "needs to show old balance and
total balance", "also round off", "the teeth map in the column")
"""
from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install')
class TestInvoicePrintFigures(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Group = cls.env['account.tax.group']
        Tax = cls.env['account.tax']
        sgst_group = Group.create({'name': 'SGST', 'company_id': cls.env.company.id})
        cgst_group = Group.create({'name': 'CGST', 'company_id': cls.env.company.id})
        children = Tax.create([
            {'name': 'IPF SGST 2.5%', 'amount': 2.5, 'type_tax_use': 'none', 'tax_group_id': sgst_group.id},
            {'name': 'IPF CGST 2.5%', 'amount': 2.5, 'type_tax_use': 'none', 'tax_group_id': cgst_group.id},
        ])
        cls.gst = Tax.create({
            'name': 'IPF GST 5%', 'amount_type': 'group', 'amount': 5.0, 'type_tax_use': 'sale',
            'children_tax_ids': [(6, 0, children.ids)]})
        cls.rounding = cls.env['account.cash.rounding'].create({
            'name': 'IPF whole rupee', 'rounding': 1.0, 'rounding_method': 'HALF-UP',
            'strategy': 'add_invoice_line',
            'profit_account_id': cls.company_data['default_account_revenue'].id,
            'loss_account_id': cls.company_data['default_account_expense'].id})
        cls.clinic = cls.env['res.partner'].create({'name': 'IPF Smile Clinic'})
        cls.doctor = cls.env['res.partner'].create({
            'name': 'IPF Dr Example', 'parent_id': cls.clinic.id, 'type': 'contact'})
        cls.day = fields.Date.to_date('2026-10-17')

    def _invoice(self, partner, price, date, move_type='out_invoice', post=True, teeth='17'):
        move = self.env['account.move'].create({
            'move_type': move_type, 'partner_id': partner.id, 'invoice_date': date,
            'invoice_cash_rounding_id': self.rounding.id,
            'invoice_line_ids': [(0, 0, {
                'name': '[CE1039] FULL METAL CROWN', 'quantity': 1, 'price_unit': price,
                'tax_ids': [(6, 0, self.gst.ids)], 'teeth': teeth, 'patient': 'Ravi Menon'})]})
        if post:
            move.action_post()
        return move

    def test_gst_in_columns_and_the_round_off(self):
        move = self._invoice(self.clinic, 380.95, self.day)
        names, table = move._dental_gst_table()
        self.assertEqual(names, ['SGST', 'CGST'], "one column per tax group, in tax order")
        cells = table[move.invoice_line_ids.id]
        self.assertEqual(cells['SGST'], (2.5, 9.52))
        self.assertEqual(cells['CGST'], (2.5, 9.52))
        self.assertAlmostEqual(move._dental_taxable(), 380.95, 2,
                               msg="the lines' value, not amount_untaxed with the rounding in it")
        self.assertAlmostEqual(move.amount_total, 400.0, 2)
        self.assertAlmostEqual(move._dental_round_off(), 0.01, 2)
        self.assertAlmostEqual(
            sum(move.invoice_line_ids.mapped('price_total')) + move._dental_round_off(),
            move.amount_total, 2, msg="the line totals and the round-off make the bill amount")
        self.assertAlmostEqual(sum(amount for _rate, amount in cells.values()), move.amount_tax, 2,
                               msg="the GST columns hold the posted tax to the paisa")

    def test_every_column_is_summed(self):
        move = self._invoice(self.clinic, 380.95, self.day)
        move.button_draft()
        move.write({'invoice_line_ids': [(0, 0, {
            'name': 'Second', 'quantity': 2, 'price_unit': 100.0, 'tax_ids': [(6, 0, self.gst.ids)]})]})
        move.action_post()
        tot = move._dental_column_totals()
        self.assertEqual(tot['qty'], 3)
        self.assertAlmostEqual(tot['taxable'], 580.95, 2)
        self.assertAlmostEqual(tot['gst']['SGST'], 14.52, 2)
        self.assertAlmostEqual(tot['gst']['CGST'], 14.52, 2)
        self.assertAlmostEqual(tot['total'], 609.99, 2)
        self.assertAlmostEqual(tot['taxable'] + sum(tot['gst'].values()), tot['total'], 2)
        html = self.env['ir.actions.report']._render_qweb_html('account.account_invoices', move.ids)[0].decode()
        self.assertIn('oi-sum', html)
        self.assertIn('oi-sheet-foot', html, "the closing block sits in the sheet's foot row")

    def test_amount_in_words_capitalised(self):
        move = self._invoice(self.clinic, 380.95, self.day)
        words = move._dental_words(move.amount_total)
        self.assertTrue(words)
        for word in words.replace(',', ' ').split():
            if word.lower() == 'and':
                self.assertEqual(word, 'and', "'and' stays small")
            else:
                self.assertTrue(word[0].isupper(), "every other word starts with a capital: %s" % words)
        self.assertIn(' and ', words)
        self.assertNotIn('And', words)
        with_paise = move._dental_words(1321.50).lower()
        self.assertEqual(with_paise.count('paise'), 1, "fifty paise, not 'fifty paise and zero paise'")
        self.assertIn('zero paise', move._dental_words(400.0).lower(), "whole rupees still say so")

    def test_old_balance_and_total_balance(self):
        # billed to the doctor: the clinic's account all the same
        self._invoice(self.doctor, 952.38, self.day - timedelta(days=5))
        self._invoice(self.clinic, 476.19, self.day + timedelta(days=3))  # later: not yet owed
        self._invoice(self.clinic, 476.19, self.day - timedelta(days=1), post=False)  # draft: not owed
        move = self._invoice(self.clinic, 380.95, self.day)
        old, total = move._dental_balances()
        self.assertAlmostEqual(old, 1000.0, 2, msg="posted, up to the invoice date, this one left out")
        self.assertAlmostEqual(total, 1400.0, 2, msg="old balance plus this bill")
        refund = self._invoice(self.clinic, 380.95, self.day, move_type='out_refund')
        r_old, r_total = refund._dental_balances()
        self.assertAlmostEqual(r_total, r_old - 400.0, 2, msg="a credit note brings the balance down")

    def test_the_labs_year_range_numbers_are_understood(self):
        """B2C/2026-27/19892: a fiscal-year range with the end year written short."""
        company = self.env.company
        company.write({'fiscalyear_last_month': '3', 'fiscalyear_last_day': 31})
        move = self._invoice(self.clinic, 100.0, fields.Date.to_date('2026-10-17'), post=False)
        move.name = 'B2C/2026-27/19892'
        self.assertEqual(move._deduce_sequence_number_reset(move.name), 'year_range')
        self.assertTrue(move._sequence_matches_date(), "October 2026 is in 2026-27")
        move.action_post()
        self.assertEqual(move.name, 'B2C/2026-27/19892', "re-posting keeps the number")
        move.button_draft()
        move.action_post()
        self.assertEqual(move.name, 'B2C/2026-27/19892')
        nxt = self._invoice(self.clinic, 100.0, fields.Date.to_date('2026-10-18'))
        self.assertEqual(nxt.name, 'B2C/2026-27/19893', "the next number keeps the lab's format")
        later = self._invoice(self.clinic, 100.0, fields.Date.to_date('2027-04-02'))
        self.assertEqual(later.name, 'B2C/2027-28/00001', "a new fiscal year starts a new range")

    def test_no_balances_on_a_bill(self):
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.clinic.id, 'invoice_date': self.day,
            'invoice_line_ids': [(0, 0, {'name': 'Gypsum', 'quantity': 1, 'price_unit': 10.0})]})
        self.assertIsNone(bill._dental_balances())

    def test_the_palmer_cross(self):
        Line = self.env['account.move.line']

        def cross(teeth):
            return Line.new({'teeth': teeth})._dental_palmer()

        self.assertEqual(cross('17'), {'ur': '7', 'ul': '', 'lr': '', 'll': '', 'rest': ''})
        lower = cross('31, 32, 33, 43, 42, 41')
        self.assertEqual((lower['ll'], lower['lr']), ('1 2 3', '3 2 1'),
                         "numbers run out from the midline on both sides")
        self.assertEqual(cross('16 15,14;11/21')['ur'], '6 5 4 1', "any separator")
        milk = cross('55, 61, 74, 81')
        self.assertEqual((milk['ur'], milk['ul'], milk['ll'], milk['lr']), ('E', 'A', 'D', 'A'))
        odd = cross('36, 6')
        self.assertEqual((odd['ll'], odd['rest']), ('6', '6'), "what is not a tooth is kept, not dropped")
        self.assertEqual(cross('46, 46')['lr'], '6', "a tooth written twice is drawn once")
        self.assertIsNone(cross('19, 58, upper'), "no real tooth at all: no cross, the text is printed as written")
        self.assertIsNone(cross(''))

    def test_the_print_shows_them(self):
        move = self._invoice(self.clinic, 380.95, self.day, teeth='17, 36')
        html = self.env['ir.actions.report']._render_qweb_html('account.account_invoices', move.ids)[0].decode()
        for text in ('SGST', 'CGST', 'Round off', 'Old balance', 'Total balance', 'oi-palmer', 'Bill amount',
                     '>Patient</th>', 'Ravi Menon'):
            self.assertIn(text, html)
        self.assertNotIn('>Jaw</th>', html, "no line has a jaw: no Jaw column")
        move.button_draft()
        move.invoice_line_ids[0].ul = 'upper'
        move.action_post()
        html = self.env['ir.actions.report']._render_qweb_html('account.account_invoices', move.ids)[0].decode()
        self.assertIn('>Jaw</th>', html, "one line has a jaw: the column appears")
        self.assertNotIn('Taxable value', html, "the GST is in the columns, not repeated in the totals")

    def test_the_letterhead_lines(self):
        company = self.env.company
        company.write({'street': 'Lab Tower,Main Road', 'street2': 'Market Junction ,', 'city': 'Example Town',
                       'zip': '600001', 'phone': '0400 100 200', 'email': 'lab@example.com',
                       'letterhead_address': False, 'letterhead_phones': False, 'letterhead_emails': False})
        rows = company._lab_letterhead()
        self.assertEqual([lines for _icon, lines in rows], [
            ['Lab Tower, Main Road, Market Junction', 'Example Town - 600001'],
            ['0400 100 200'], ['lab@example.com']], "empty fields fall back to the company's own")
        self.assertTrue(all(icon.startswith('data:image/svg+xml;base64,') for icon, _lines in rows),
                        "the icons travel inside the page")
        company.write({'letterhead_phones': '0400 100 200\n\n  0900   111 222 , 0900 333 444  ',
                       'letterhead_emails': 'accounts@example.com\nlab@example.com'})
        rows = company._lab_letterhead()
        self.assertEqual(rows[1][1], ['0400 100 200', '0900 111 222 , 0900 333 444'],
                         "one printed line per typed line, blank lines dropped")
        self.assertEqual(rows[2][1], ['accounts@example.com', 'lab@example.com'])
        company.write({'phone': False, 'letterhead_phones': False})
        self.assertEqual(len(company._lab_letterhead()), 2, "no phone at all: no phone line")
