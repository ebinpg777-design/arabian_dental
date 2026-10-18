# -*- coding: utf-8 -*-
"""The scanner must find credit held against a partner however it was posted.

`payments_unmatched` asks `account.payment`, so it misses credit notes and any
receipt posted as a plain journal entry - and the lab's receipts are plain
journal entries, as Ortho's are. Ortho measured it on its own book (a58c4d0,
2026-10-04): it reported about a tenth of the credit actually held. These tests
measure the check as a delta over whatever the database already holds, so they
say something true on an empty database and on a copy of production alike.
"""
from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCreditsUnapplied(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.write({'fiscalyear_lock_date': False, 'sale_lock_date': False, 'tax_lock_date': False})
        cls.today = fields.Date.context_today(cls.env.user)
        cls.scanner = cls.env['ebshel.ledger.scanner']
        Account = cls.env['account.account']
        cls.receivable = Account.search([('account_type', '=', 'asset_receivable'),
                                         ('company_ids', 'in', cls.company.ids)], limit=1)
        cls.income = Account.search([('account_type', '=', 'income'), ('company_ids', 'in', cls.company.ids)], limit=1)
        cls.journal = cls.env['account.journal'].search(
            [('type', '=', 'general'), ('company_id', '=', cls.company.id)], limit=1)
        cls.customer = cls.env['res.partner'].create({'name': 'Credit Clinic', 'is_company': True,
                                                      'property_payment_term_id': False})
        # the account the customer's invoices go to: a receipt on another receivable
        # account could never be reconciled with them
        cls.receivable = cls.customer.property_account_receivable_id or cls.receivable

    # ------------------------------------------------------------------ helpers
    def _totals(self):
        """The receivable side's figures, straight from the scanner."""
        self.env.flush_all()   # the check reads SQL; scan() flushes, a direct call must too
        return self.scanner._credits_unapplied_totals(self.company)['asset_receivable']

    def _loose(self):
        return self._totals()['held']

    def _clearable(self):
        return self._totals()['clearable']

    def _invoice(self, amount, kind='out_invoice'):
        move = self.env['account.move'].create({
            'move_type': kind, 'partner_id': self.customer.id, 'invoice_date': self.today,
            'invoice_payment_term_id': False, 'invoice_date_due': self.today,
            'invoice_line_ids': [(0, 0, {'name': 'x', 'quantity': 1, 'price_unit': amount,
                                         'account_id': self.income.id, 'tax_ids': [(6, 0, [])]})]})
        move.action_post()
        return move

    def _journal_receipt(self, amount, partner=None):
        """A receipt the way this ledger really posts them: a plain entry, no account.payment."""
        move = self.env['account.move'].create({
            'move_type': 'entry', 'date': self.today, 'journal_id': self.journal.id,
            'line_ids': [
                (0, 0, {'name': 'Customer Payment', 'partner_id': (partner or self.customer).id,
                        'account_id': self.receivable.id, 'credit': amount, 'debit': 0}),
                (0, 0, {'name': 'Customer Payment', 'account_id': self.income.id, 'debit': amount, 'credit': 0}),
            ]})
        move.action_post()
        return move

    def _ar(self, move):
        return move.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable')

    # ------------------------------------------------------------------ tests
    def test_a_journal_receipt_is_seen_with_no_payment_record(self):
        """The reason this check exists: no account.payment points at it, yet the
        money is held against the customer."""
        before = self._loose()
        receipt = self._journal_receipt(5000)
        self.assertFalse(self.env['account.payment'].search_count([('move_id', '=', receipt.id)]),
                         "fixture must not go through account.payment, or it proves nothing")
        self.assertAlmostEqual(self._loose() - before, 5000, 2)

    def test_an_unapplied_credit_note_is_seen(self):
        before = self._loose()
        self._invoice(3000, kind='out_refund')
        self.assertAlmostEqual(self._loose() - before, 3000, 2)

    def test_clearable_is_the_part_facing_an_open_invoice(self):
        """7,000 held against a 2,500 invoice: 2,500 can be cleared, 4,500 is advance."""
        self._invoice(2500)
        before = self._totals()
        self._journal_receipt(7000)
        after = self._totals()
        self.assertAlmostEqual(after['held'] - before['held'], 7000, 2)
        self.assertAlmostEqual(after['clearable'] - before['clearable'], 2500, 2,
                               "only the part facing the open invoice can be cleared")

    def test_reconciling_it_makes_the_finding_go_away(self):
        before = self._loose()
        inv = self._invoice(4000)
        receipt = self._journal_receipt(4000)
        self.assertAlmostEqual(self._loose() - before, 4000, 2)
        (self._ar(inv) + self._ar(receipt)).reconcile()
        self.assertAlmostEqual(self._loose(), before, 2,
                               "once applied, the credit is no longer held against anyone")

    def test_a_credit_on_a_child_contact_faces_the_parents_invoice(self):
        """Grouping is by commercial partner, so a delivery address does not hide the offset."""
        child = self.env['res.partner'].create({'name': 'Ward 2', 'parent_id': self.customer.id})
        self._invoice(1800)
        before = self._clearable()
        self._journal_receipt(1800, partner=child)
        self.assertAlmostEqual(self._clearable() - before, 1800, 2,
                               "a credit booked on the child must offset the parent's open invoice")

    def test_a_draft_or_reconciled_line_is_not_counted(self):
        before = self._loose()
        draft = self.env['account.move'].create({
            'move_type': 'entry', 'date': self.today, 'journal_id': self.journal.id,
            'line_ids': [(0, 0, {'name': 'd', 'partner_id': self.customer.id, 'account_id': self.receivable.id,
                                 'credit': 9999, 'debit': 0}),
                          (0, 0, {'name': 'd', 'account_id': self.income.id, 'debit': 9999, 'credit': 0})]})
        self.assertTrue(draft.state == 'draft')
        self.assertAlmostEqual(self._loose(), before, 2, "a draft entry is not money held")

    def test_the_scan_keeps_one_finding_per_side(self):
        self._journal_receipt(1200)
        Finding = self.env['ebshel.ledger.finding']
        self.scanner.scan(keys=['credits_unapplied'])
        first = Finding.search([('key', '=', 'credits_unapplied'), ('company_id', '=', self.company.id)])
        self.scanner.scan(keys=['credits_unapplied'])
        again = Finding.search([('key', '=', 'credits_unapplied'), ('company_id', '=', self.company.id)])
        self.assertEqual(set(first.ids), set(again.ids),
                         "re-scanning must update the same finding, not pile up new ones")
        self.assertTrue(all(f.model == 'account.move.line' and f.res_ids for f in again))
