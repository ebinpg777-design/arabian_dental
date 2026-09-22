# -*- coding: utf-8 -*-
"""As on Date vs Period, and a customer's own standard discount.

As on Date is the running balance, full history - what a statement would show.
Period narrows the OPEN ITEMS to ones billed inside a window, but the countback that
decides what is still open still has to run through the whole history first: a
payment made before the window, or an overpayment carried from before it, can be
exactly what closed an item that sits inside it. Getting that wrong reads as more
outstanding than is real, in whichever direction the mistake runs.
"""
from odoo.exceptions import ValidationError
from odoo import fields
from odoo.tests import Form, TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestOutstandingDiscount(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.Discount = cls.env['epg.outstanding.discount']
        cls.clinic = cls.env['res.partner'].create({'name': 'Discount Test Clinic'})
        cls.receivable = cls.env['account.account'].search([
            ('account_type', '=', 'asset_receivable'),
            ('company_ids', 'in', [cls.company.id])], limit=1)
        cls.other = cls.env['account.account'].search([
            ('account_type', '=', 'income'),
            ('company_ids', 'in', [cls.company.id])], limit=1)
        cls.journal = cls.env['account.journal'].search(
            [('type', '=', 'general'), ('company_id', '=', cls.company.id)], limit=1)

    def _entry(self, date, receivable_amount):
        """A posted journal entry touching the receivable account by
        `receivable_amount` - positive books a debit (an invoice), negative a
        credit (a receipt) - the shape _open_items actually reads. Matches how
        this system's own receipts are booked, per the model's own docstring."""
        debit, credit = max(receivable_amount, 0.0), max(-receivable_amount, 0.0)
        move = self.env['account.move'].create({
            'move_type': 'entry', 'date': date, 'journal_id': self.journal.id,
            'line_ids': [
                (0, 0, {'account_id': self.receivable.id, 'partner_id': self.clinic.id,
                        'debit': debit, 'credit': credit}),
                (0, 0, {'account_id': self.other.id, 'partner_id': self.clinic.id,
                        'debit': credit, 'credit': debit}),
            ],
        })
        move.action_post()
        return move

    def _discount(self, **vals):
        return self.Discount.create(dict({
            'partner_id': self.clinic.id,
            'account_id': self.other.id, 'journal_id': self.journal.id,
        }, **vals))

    # ---------------------------------------------------------------- as on date
    def test_as_of_is_the_running_balance_unaffected_by_period_fields(self):
        """Default mode - the exact behaviour this had before Period existed."""
        self._entry('2026-01-10', 1000.0)
        self._entry('2026-01-20', -1000.0)   # pays January off in full
        self._entry('2026-02-10', 1000.0)
        self._entry('2026-03-10', 1000.0)
        discount = self._discount(date='2026-03-31')
        self.assertEqual(discount.computation_mode, 'as_of')
        self.assertEqual(discount.outstanding_total, 2000.0)
        self.assertEqual(discount.open_invoice_count, 2)

    # ------------------------------------------------------------------- period
    def test_period_keeps_only_items_billed_inside_the_window(self):
        self._entry('2026-01-10', 1000.0)
        self._entry('2026-01-20', -1000.0)   # pays January off in full
        self._entry('2026-02-10', 1000.0)
        self._entry('2026-03-10', 1000.0)
        discount = self._discount(
            computation_mode='period', period_from='2026-02-01', period_to='2026-02-28')
        self.assertEqual(discount.outstanding_total, 1000.0,
                         "March's item exists but was not billed inside February")
        self.assertEqual(discount.open_invoice_count, 1)

    def test_a_period_containing_only_a_paid_invoice_shows_zero(self):
        self._entry('2026-01-10', 1000.0)
        self._entry('2026-01-20', -1000.0)
        discount = self._discount(
            computation_mode='period', period_from='2026-01-01', period_to='2026-01-31')
        self.assertEqual(discount.outstanding_total, 0.0,
                         "paid off in full - not the invoice's face value")
        self.assertEqual(discount.open_invoice_count, 0)

    def test_an_overpayment_from_before_the_window_still_closes_an_item_inside_it(self):
        """The countback must run through the WHOLE history before the window is
        applied - restricting the ledger query itself to period_from..period_to
        would miss this and show February as open when it is not."""
        self._entry('2026-01-10', 500.0)
        self._entry('2026-01-20', -1500.0)    # clears January AND all of February
        self._entry('2026-02-10', 1000.0)
        discount = self._discount(
            computation_mode='period', period_from='2026-02-01', period_to='2026-02-28')
        self.assertEqual(discount.outstanding_total, 0.0)
        self.assertEqual(discount.open_invoice_count, 0)

    def test_a_payment_after_the_periods_end_does_not_count(self):
        """period_to is also the countback's own cutoff - a receipt that had not
        happened yet as at that date cannot be what closed the item."""
        self._entry('2026-02-10', 1000.0)
        self._entry('2026-03-01', -1000.0)
        discount = self._discount(
            computation_mode='period', period_from='2026-02-01', period_to='2026-02-28')
        self.assertEqual(discount.outstanding_total, 1000.0)

    def test_period_mode_needs_both_dates(self):
        with self.assertRaises(ValidationError):
            self._discount(computation_mode='period', period_from='2026-02-01')

    def test_period_start_cannot_be_after_its_end(self):
        with self.assertRaises(ValidationError):
            self._discount(computation_mode='period',
                           period_from='2026-03-01', period_to='2026-02-01')

    def test_posting_in_period_mode_reconciles_only_the_windowed_items(self):
        self._entry('2026-01-10', 1000.0)
        self._entry('2026-02-10', 1000.0)
        discount = self._discount(
            computation_mode='period', period_from='2026-02-01', period_to='2026-02-28',
            mode='amount', amount_input=1000.0)
        discount.action_post()
        self.assertEqual(discount.state, 'posted')
        # January's item is untouched - it was never inside the window.
        january = self.env['account.move.line'].search([
            ('partner_id', '=', self.clinic.id), ('account_id', '=', self.receivable.id),
            ('date', '=', '2026-01-10')])
        self.assertFalse(january.reconciled)

    # -------------------------------------------------------------- partner default
    def test_posting_survives_an_item_that_is_already_reconciled(self):
        """The countback and Odoo's reconciliation do not agree, and must not have to.

        `_open_items` reads the running balance rather than residuals, so an entry
        it still calls open can be fully reconciled in Odoo's own terms. Feeding
        such a line to `reconcile()` raises "already reconciled" and the whole
        posting dies on it - which is what happened to a live discount for
        AESTHETIC DENTAL LAB, on one 180.00 line out of 54.
        """
        old = self._entry('2026-01-10', 180.0)
        new = self._entry('2026-02-10', 1000.0)

        # Odoo's reconciliation closes the NEWER item in full. The countback pays
        # the oldest first, so by its arithmetic the 1,000 receipt closed the 180
        # and left 180 of the newer item open - a line Odoo calls fully
        # reconciled. (The fixture used to pay the older item, which both
        # measures close alike, so it never produced the disagreement it was
        # written to test. 2026-09-10)
        pay = self._entry('2026-01-11', -1000.0)
        new_line = new.line_ids.filtered(lambda l: l.account_id == self.receivable)
        pay_line = pay.line_ids.filtered(lambda l: l.account_id == self.receivable)
        (new_line + pay_line).reconcile()
        self.assertTrue(new_line.reconciled, 'the newer item is reconciled in Odoo')

        discount = self._discount(date='2026-03-01', mode='amount', amount_input=100.0)
        items = discount._open_items()
        self.assertTrue(
            any(line.reconciled for line, _amount in items),
            'the countback should still be offering the reconciled line')

        discount.action_post()                      # used to raise UserError
        self.assertEqual(discount.state, 'posted')
        self.assertTrue(discount.move_id, 'the discount entry was created')

        credit = discount.move_id.line_ids.filtered(
            lambda l: l.account_id == self.receivable)
        self.assertFalse(credit.matched_debit_ids,
                         'nothing offered had residual to take the credit, so it is '
                         'posted and left open rather than failing the whole discount')
        old_line = old.line_ids.filtered(lambda l: l.account_id == self.receivable)
        self.assertFalse(old_line.reconciled, 'the older item is untouched')

    def test_a_partners_standard_discount_defaults_the_percentage(self):
        self.clinic.outstanding_discount_percent = 8.0
        with Form(self.Discount) as form:
            form.partner_id = self.clinic
            self.assertEqual(form.mode, 'percent')
            self.assertEqual(form.percent, 8.0)

    def test_a_partner_with_no_standard_discount_leaves_the_default_alone(self):
        bare = self.env['res.partner'].create({'name': 'No Discount Clinic'})
        with Form(self.Discount) as form:
            form.partner_id = bare
            self.assertEqual(form.percent, 5.0, "the model's own default, untouched")


@tagged('post_install', '-at_install')
class TestCancelTakesTheEntryWithIt(TransactionCase):
    """A cancelled discount is a mistake being taken back, not a transaction:
    its journal entry is cancelled, not reversed, so nothing of it is left on
    the clinic's statement or in the outstanding."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.Discount = cls.env['epg.outstanding.discount']
        cls.clinic = cls.env['res.partner'].create({'name': 'Cancel Test Clinic'})
        cls.receivable = cls.env['account.account'].search([
            ('account_type', '=', 'asset_receivable'),
            ('company_ids', 'in', [cls.company.id])], limit=1)
        cls.other = cls.env['account.account'].search([
            ('account_type', '=', 'income'),
            ('company_ids', 'in', [cls.company.id])], limit=1)
        cls.journal = cls.env['account.journal'].search(
            [('type', '=', 'general'), ('company_id', '=', cls.company.id)], limit=1)

    def _invoice_like(self, date, amount):
        move = self.env['account.move'].create({
            'move_type': 'entry', 'date': date, 'journal_id': self.journal.id,
            'line_ids': [
                (0, 0, {'account_id': self.receivable.id, 'partner_id': self.clinic.id,
                        'debit': amount, 'credit': 0.0}),
                (0, 0, {'account_id': self.other.id, 'partner_id': self.clinic.id,
                        'debit': 0.0, 'credit': amount}),
            ],
        })
        move.action_post()
        return move.line_ids.filtered(lambda l: l.account_id == self.receivable)

    def _posted_discount(self, amount=100.0):
        discount = self.Discount.create({
            'partner_id': self.clinic.id, 'account_id': self.other.id,
            'journal_id': self.journal.id, 'date': '2026-03-01',
            'mode': 'amount', 'amount_input': amount})
        discount.action_post()
        return discount

    def test_cancelling_the_discount_cancels_its_entry_and_reopens_the_invoice(self):
        invoice_line = self._invoice_like('2026-02-10', 1000.0)
        discount = self._posted_discount(100.0)
        entry = discount.move_id
        self.assertEqual(invoice_line.amount_residual, 900.0, "the discount was set against it")

        discount.action_cancel()

        self.assertEqual(discount.state, 'cancel')
        self.assertEqual(entry.state, 'cancel', "cancelled, not left posted")
        self.assertTrue(all(l.parent_state == 'cancel' for l in entry.line_ids))
        self.assertFalse(self.env['account.move'].search([('reversed_entry_id', '=', entry.id)]),
                         "no reversal: nothing of it remains on the books")
        self.assertEqual(invoice_line.amount_residual, 1000.0,
                         "the invoice is open again for the full amount")
        self.assertEqual(discount.move_id, entry, "the cancelled entry stays reachable")

    def test_a_cancelled_discount_is_gone_from_the_statement(self):
        if 'epg.partner.statement' not in self.env:
            self.skipTest("the statement module is not installed")
        self._invoice_like('2026-02-10', 1000.0)
        discount = self._posted_discount(100.0)
        entry = discount.move_id
        Engine = self.env['epg.partner.statement']
        options = {'statement_type': 'receivable', 'company': self.company,
                   'date_from': fields.Date.to_date('2026-01-01'),
                   'date_to': fields.Date.to_date('2026-12-31'),
                   'open_items_only': False, 'show_ageing': False,
                   'created_from': None, 'created_to': None}

        def names():
            data = Engine.compute(self.clinic, options)[self.clinic.id]
            return [l['move'] for b in data['blocks'] for l in b['lines']]

        self.assertIn(entry.name, names(), "a posted discount is on the statement")
        discount.action_cancel()
        self.assertNotIn(entry.name, names(), "a cancelled one is not")
        self.assertFalse(any(n.startswith('R') and entry.name in n for n in names()))

    def test_an_entry_the_books_will_not_release_is_reversed_instead(self):
        """A locked period cannot be changed; then, and only then, the old way."""
        self._invoice_like('2026-02-10', 1000.0)
        discount = self._posted_discount(100.0)
        entry = discount.move_id
        self.company.fiscalyear_lock_date = fields.Date.to_date('2026-06-30')
        try:
            discount.action_cancel()
        finally:
            self.company.fiscalyear_lock_date = False
        self.assertEqual(discount.state, 'cancel')
        self.assertEqual(entry.state, 'posted', "the books would not let it go")
        reversal = self.env['account.move'].search([('reversed_entry_id', '=', entry.id)])
        self.assertTrue(reversal, "so it was reversed, the old way")
        self.assertIn('reversed', discount.message_ids[0].body)
