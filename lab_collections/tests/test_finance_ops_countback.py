# -*- coding: utf-8 -*-
"""The EOD page and cheque allocation read money owed from the countback."""
from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestFinanceOpsCountback(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.perf = cls.env['lab.collection.performance']
        cls.receivable = cls.env['account.account'].search([
            ('account_type', '=', 'asset_receivable'),
            ('company_ids', 'in', cls.company.id)], limit=1)
        cls.income = cls.env['account.account'].search([
            ('account_type', '=', 'income'),
            ('company_ids', 'in', cls.company.id)], limit=1)
        cls.misc = cls.env['account.journal'].search([
            ('type', '=', 'general'), ('company_id', '=', cls.company.id)], limit=1)
        cls.clinic = cls.env['res.partner'].create({'name': 'Countback Clinic'})
        cls.product = cls.env['product.product'].create(
            {'name': 'Countback Crown', 'lst_price': 1000.0})

    def _invoice(self, amount, day):
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.clinic.id,
            'invoice_date': day, 'date': day, 'company_id': self.company.id,
            'invoice_line_ids': [(0, 0, {
                'product_id': self.product.id, 'quantity': 1, 'price_unit': amount,
                'tax_ids': [(6, 0, [])]})],
        })
        invoice.action_post()
        return invoice

    def _journal_receipt(self, amount, day):
        """A receipt the way this ledger records one: a journal entry, never
        reconciled, so the invoice keeps its residual."""
        move = self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': self.misc.id, 'date': day,
            'line_ids': [
                (0, 0, {'account_id': self.income.id, 'debit': amount}),
                (0, 0, {'account_id': self.receivable.id, 'credit': amount,
                        'partner_id': self.clinic.id}),
            ]})
        move.action_post()
        return move

    def test_the_eod_page_states_what_the_countback_says_is_owed(self):
        self._invoice(700.0, '2026-01-10')
        report = self.env['lab.eod.report'].create({
            'date': fields.Date.context_today(self.env.user),
            'company_id': self.company.id})
        summary = self.perf.sudo().outstanding_summary(self.company)
        self.assertAlmostEqual(report.outstanding_total, summary['total'], 2)
        self.assertAlmostEqual(report.overdue_total, summary['overdue'], 2)

    def test_a_cheque_skips_an_invoice_a_journal_receipt_already_paid(self):
        if not (self.receivable and self.income and self.misc):
            self.fail("the database has no chart of accounts to post against")
        paid = self._invoice(400.0, '2026-01-05')
        still_open = self._invoice(600.0, '2026-01-20')
        self._journal_receipt(400.0, '2026-01-25')
        self.assertTrue(paid.amount_residual, "the receipt must not reconcile the invoice")
        cheque = self.env['lab.cheque'].create({
            'cheque_number': 'CB-001', 'partner_id': self.clinic.id,
            'amount': 1000.0, 'company_id': self.company.id})
        cheque.action_load_open_invoices()
        self.assertEqual(cheque.allocation_ids.move_id, still_open)
        self.assertAlmostEqual(cheque.allocation_ids.amount, 600.0, 2)
