# -*- coding: utf-8 -*-
from datetime import date

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestLabFinanceOps(TransactionCase):
    """Exercises the cheque lifecycle against real journal entries.

    The target database ships without a chart of accounts, so the fixture builds
    the minimum needed to post and reconcile — a receivable, an income account,
    an outstanding-receipts account and a sale + bank journal.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.params = cls.env['ir.config_parameter'].sudo()
        cls.today = date.today()
        company = cls.env.company

        Account = cls.env['account.account']

        def account(code, name, atype, reconcile=False):
            existing = Account.search(
                [('code', '=', code), ('company_ids', 'in', company.id)], limit=1)
            return existing or Account.create({
                'code': code, 'name': name, 'account_type': atype,
                'reconcile': reconcile, 'company_ids': [(4, company.id)]})

        cls.acc_recv = account('T11200', 'Test Receivable', 'asset_receivable', True)
        cls.acc_income = account('T40000', 'Test Income', 'income')
        cls.acc_outstanding = account(
            'T10140', 'Test Outstanding Receipts', 'asset_current', True)
        cls.acc_bank = account('T10141', 'Test Bank', 'asset_cash')

        Journal = cls.env['account.journal']
        cls.sale_journal = Journal.search(
            [('type', '=', 'sale'), ('company_id', '=', company.id)], limit=1
        ) or Journal.create({
            'name': 'Test Sales', 'code': 'TSAL', 'type': 'sale',
            'company_id': company.id})
        cls.bank_journal = Journal.search(
            [('type', '=', 'bank'), ('company_id', '=', company.id)], limit=1
        ) or Journal.create({
            'name': 'Test Bank', 'code': 'TBNK', 'type': 'bank',
            'company_id': company.id, 'default_account_id': cls.acc_bank.id})
        cls.bank_journal.inbound_payment_method_line_ids.filtered(
            lambda l: not l.payment_account_id
        ).payment_account_id = cls.acc_outstanding.id

        cls.clinic = cls.env['res.partner'].create({
            'name': 'Test Clinic', 'is_company': True, 'is_clinic': True,
            'property_account_receivable_id': cls.acc_recv.id})
        cls.product = cls.env['product.product'].create({
            # No tax: the amounts below are asserted net, whatever the company's
            # default sale tax happens to be on the database under test.
            'taxes_id': [(5, 0, 0)],
            'name': 'Test Appliance', 'type': 'consu', 'list_price': 2000.0,
            'property_account_income_id': cls.acc_income.id})

    # ------------------------------------------------------------------ helpers
    def _invoice(self, amount=2000.0):
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.clinic.id,
            'invoice_date': self.today,
            'invoice_line_ids': [(0, 0, {
                'product_id': self.product.id, 'quantity': 1, 'price_unit': amount})],
        })
        invoice.action_post()
        return invoice

    def _cheque(self, amount, **vals):
        return self.env['lab.cheque'].create(dict({
            'cheque_number': f'CHK-{amount:.0f}-{len(self.env["lab.cheque"].search([]))}',
            'partner_id': self.clinic.id,
            'amount': amount,
            'cheque_date': self.today,
            'journal_id': self.bank_journal.id,
        }, **vals))

    # ------------------------------------------------------------------ cheques
    def test_auto_allocation_consumes_oldest_invoices_first(self):
        first = self._invoice(1000.0)
        second = self._invoice(3000.0)
        cheque = self._cheque(2500.0)
        cheque.action_load_open_invoices()
        allocated = {a.move_id: a.amount for a in cheque.allocation_ids}
        self.assertEqual(allocated.get(first), 1000.0)
        self.assertEqual(allocated.get(second), 1500.0)
        self.assertEqual(cheque.unallocated_amount, 0.0)

    def test_allocation_cannot_exceed_the_cheque(self):
        invoice = self._invoice(5000.0)
        cheque = self._cheque(1000.0)
        with self.assertRaises(ValidationError):
            cheque.write({'allocation_ids': [
                (0, 0, {'move_id': invoice.id, 'amount': 5000.0})]})

    def test_clearing_posts_a_payment_and_reconciles_the_allocated_invoice(self):
        invoice = self._invoice(2000.0)
        cheque = self._cheque(2000.0, allocation_ids=[
            (0, 0, {'move_id': invoice.id, 'amount': 2000.0})])
        cheque.action_deposit()
        cheque.action_clear()
        invoice.invalidate_recordset()
        self.assertEqual(cheque.state, 'cleared')
        self.assertIn(cheque.payment_id.state, ('paid', 'in_process'))
        self.assertEqual(cheque.payment_id.cheque_id, cheque)
        self.assertIn(invoice.payment_state, ('paid', 'in_payment'))
        self.assertEqual(invoice.amount_residual, 0.0)

    def test_cheque_must_be_deposited_before_it_can_clear(self):
        with self.assertRaises(UserError):
            self._cheque(1000.0).action_clear()

    def test_deposit_requires_a_bank_journal(self):
        cheque = self._cheque(1000.0)
        cheque.journal_id = False
        with self.assertRaises(UserError):
            cheque.action_deposit()

    def test_unallocated_remainder_stays_an_open_credit(self):
        """Money must never be applied to a invoice nobody pointed it at."""
        invoice = self._invoice(1000.0)
        cheque = self._cheque(2500.0, allocation_ids=[
            (0, 0, {'move_id': invoice.id, 'amount': 1000.0})])
        self.assertEqual(cheque.unallocated_amount, 1500.0)
        cheque.action_deposit()
        cheque.action_clear()
        invoice.invalidate_recordset()
        self.assertEqual(invoice.amount_residual, 0.0)
        # The 1 500 nobody allocated must stay unapplied, not drift onto a invoice.
        self.assertFalse(cheque.payment_id.is_reconciled)

    def test_bounce_needs_a_reason_and_reopens_the_invoice(self):
        invoice = self._invoice(2000.0)
        cheque = self._cheque(2000.0, allocation_ids=[
            (0, 0, {'move_id': invoice.id, 'amount': 2000.0})])
        cheque.action_deposit()
        cheque.action_clear()
        with self.assertRaises(UserError):
            cheque.action_bounce()
        cheque.bounce_reason = 'Insufficient funds'
        cheque.action_bounce()
        invoice.invalidate_recordset()
        self.assertEqual(cheque.state, 'bounced')
        self.assertEqual(invoice.payment_state, 'not_paid')

    def test_post_dated_cheque_is_flagged(self):
        from datetime import timedelta
        cheque = self._cheque(1000.0, cheque_date=self.today + timedelta(days=30))
        self.assertTrue(cheque.is_post_dated)

    # ------------------------------------------------------------------ approval
    def _require_approval(self, threshold='1000'):
        self.params.set_param('lab_finance_ops.require_payment_approval', 'True')
        self.params.set_param('lab_finance_ops.payment_approval_threshold', threshold)
        self.addCleanup(
            self.params.set_param,
            'lab_finance_ops.require_payment_approval', 'False')

    def _payment(self, amount):
        return self.env['account.payment'].create({
            'payment_type': 'inbound', 'partner_type': 'customer',
            'partner_id': self.clinic.id, 'amount': amount,
            'date': self.today, 'journal_id': self.bank_journal.id})

    def test_payment_below_threshold_needs_no_approval(self):
        self._require_approval('1000')
        payment = self._payment(500.0)
        self.assertEqual(payment.approval_state, 'not_required')
        payment.action_post()
        self.assertIn(payment.state, ('paid', 'in_process'))

    def test_payment_above_threshold_is_held_until_approved(self):
        self._require_approval('1000')
        payment = self._payment(5000.0)
        self.assertEqual(payment.approval_state, 'to_approve')
        with self.assertRaises(UserError):
            payment.action_post()
        payment.action_approve_payment()
        payment.action_post()
        self.assertIn(payment.state, ('paid', 'in_process'))

    def test_refusing_a_payment_requires_a_remark(self):
        self._require_approval('0')
        payment = self._payment(5000.0)
        with self.assertRaises(UserError):
            payment.action_refuse_payment()
        payment.approval_note = 'Duplicate receipt'
        payment.action_refuse_payment()
        self.assertEqual(payment.approval_state, 'refused')

    def test_cleared_cheque_bypasses_the_approval_gate(self):
        """The Received -> Deposited -> Cleared workflow *is* the control."""
        self._require_approval('0')
        invoice = self._invoice(2000.0)
        cheque = self._cheque(2000.0, allocation_ids=[
            (0, 0, {'move_id': invoice.id, 'amount': 2000.0})])
        cheque.action_deposit()
        cheque.action_clear()
        self.assertEqual(cheque.payment_id.approval_state, 'not_required')
        self.assertIn(cheque.payment_id.state, ('paid', 'in_process'))

    def test_editing_an_approved_payment_sends_it_back_for_approval(self):
        self._require_approval('1000')
        payment = self._payment(5000.0)
        payment.action_approve_payment()
        self.assertEqual(payment.approval_state, 'approved')
        payment.memo = 'Receipt for June'
        self.assertEqual(payment.approval_state, 'approved',
                         "a remark changes nothing that was approved")
        payment.amount = 50000.0
        self.assertEqual(payment.approval_state, 'to_approve')
        self.assertFalse(payment.approved_by_id)
        with self.assertRaises(UserError):
            payment.action_post()
        payment.action_approve_payment()
        payment.partner_id = self.env['res.partner'].create({'name': 'Other Clinic'})
        self.assertEqual(payment.approval_state, 'to_approve',
                         "a different payee is a different payment")

    # ------------------------------------------------------------------ reports
    def test_outstanding_report_ages_open_invoices(self):
        from datetime import timedelta
        invoice = self._invoice(3000.0)
        invoice.invoice_date_due = self.today - timedelta(days=100)
        invoice.line_ids.filtered(
            lambda l: l.account_id.account_type == 'asset_receivable'
        ).date_maturity = self.today - timedelta(days=100)
        rows = self.env['lab.outstanding.report'].search(
            [('move_id', '=', invoice.id)])
        self.assertTrue(rows)
        self.assertEqual(rows[0].age_bucket, 'b_90_plus')
        self.assertEqual(rows[0].amount_residual, 3000.0)

    def test_daybook_balances_are_consistent(self):
        invoice = self._invoice(2000.0)
        cheque = self._cheque(2000.0, allocation_ids=[
            (0, 0, {'move_id': invoice.id, 'amount': 2000.0})])
        cheque.action_deposit()
        cheque.action_clear()
        wizard = self.env['lab.daybook.wizard'].create({
            'date_from': self.today.replace(day=1),
            'date_to': self.today,
            'book_type': 'bank',
        })
        self.assertAlmostEqual(
            wizard.closing_balance,
            wizard.opening_balance + wizard.period_debit - wizard.period_credit, 2)
        action = wizard.action_view_daybook()
        self.assertEqual(action['res_model'], 'account.move.line')

    def test_daybook_rejects_a_reversed_date_range(self):
        from datetime import timedelta
        wizard = self.env['lab.daybook.wizard'].create({
            'date_from': self.today,
            'date_to': self.today - timedelta(days=5),
            'book_type': 'all',
        })
        with self.assertRaises(UserError):
            wizard.action_view_daybook()

    def test_eod_report_totals_and_renders(self):
        self._invoice(2000.0)
        self._cheque(1500.0)
        eod = self.env['lab.eod.report'].create({'date': self.today})
        self.assertGreaterEqual(eod.invoice_count, 1)
        self.assertGreaterEqual(eod.cheque_count, 1)
        self.assertGreaterEqual(eod.outstanding_total, 0.0)
        html, _report_type = self.env['ir.actions.report']._render_qweb_html(
            'lab_finance_ops.report_lab_eod', eod.ids)
        self.assertIn(b'End-of-Day Report', html)

    def test_daybook_balance_is_the_cash_account_including_archived_books(self):
        from datetime import timedelta
        Account = self.env['account.account']
        company = self.env.company
        till = Account.search(
            [('code', '=', 'T10150'), ('company_ids', 'in', company.id)], limit=1
        ) or Account.create({
            'code': 'T10150', 'name': 'Test Till', 'account_type': 'asset_cash',
            'company_ids': [(4, company.id)]})
        book = self.env['account.journal'].create({
            'name': 'Test Archived Till', 'code': 'TTIL', 'type': 'cash',
            'company_id': company.id, 'default_account_id': till.id})
        general = self.env['account.journal'].search(
            [('type', '=', 'general'), ('company_id', '=', company.id)], limit=1
        ) or self.env['account.journal'].create({
            'name': 'Test Misc', 'code': 'TMSC', 'type': 'general',
            'company_id': company.id})

        def entry(journal, day, amount):
            move = self.env['account.move'].create({
                'move_type': 'entry', 'journal_id': journal.id, 'date': day,
                'line_ids': [
                    (0, 0, {'account_id': till.id, 'debit': amount}),
                    (0, 0, {'account_id': self.acc_income.id, 'credit': amount}),
                ]})
            move.action_post()

        # The opening balance goes in through MISC, the way this ledger's did.
        entry(general, self.today - timedelta(days=2), 500.0)
        entry(book, self.today, 200.0)
        book.active = False

        wizard = self.env['lab.daybook.wizard'].create({
            'date_from': self.today - timedelta(days=1), 'date_to': self.today,
            'book_type': 'cash'})
        self.assertIn(book, wizard._resolve_journals(), "archived books still count")

        wizard = self.env['lab.daybook.wizard'].create({
            'date_from': self.today - timedelta(days=1), 'date_to': self.today,
            'book_type': 'cash', 'journal_ids': [(6, 0, book.ids)]})
        self.assertAlmostEqual(wizard.opening_balance, 500.0, 2)
        self.assertAlmostEqual(wizard.period_debit, 200.0, 2)
        self.assertAlmostEqual(wizard.period_credit, 0.0, 2)
        self.assertAlmostEqual(wizard.closing_balance, 700.0, 2)
        lines = self.env['account.move.line'].search(
            wizard.action_view_daybook()['domain'])
        self.assertEqual(set(lines.account_id.ids), {till.id},
                         "the book lists the lines its balance is made of")

    def test_eod_day_is_the_lab_day_not_the_utc_day(self):
        from datetime import datetime
        eod = self.env['lab.eod.report'].with_context(tz='Asia/Kolkata').create(
            {'date': date(2026, 9, 15)})
        self.assertEqual(eod._day_bounds(),
                         (datetime(2026, 9, 14, 18, 30), datetime(2026, 9, 15, 18, 30)))
