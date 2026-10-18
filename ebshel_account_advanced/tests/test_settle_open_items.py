# -*- coding: utf-8 -*-
"""Open items are settled against the partner's receipts by the scheduler.

Receipts on the lab's ledger, as on Ortho's (e1869906), are plain journal entries
that never point at an invoice, so without this nothing ever marks an invoice paid. The office
settles the way the countback counts: a receipt that quotes an invoice goes on
it, the rest goes oldest-first. Each test works on its own partner so it says
the same thing on an empty database and on a copy of production.
"""
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSettleOpenItems(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.write({'fiscalyear_lock_date': False, 'sale_lock_date': False, 'purchase_lock_date': False,
                           'tax_lock_date': False})
        cls.today = fields.Date.context_today(cls.env.user)
        cls.desk = cls.env['ebshel.match.desk']
        cls.receivable = cls.env['account.account'].search(
            [('account_type', '=', 'asset_receivable'), ('company_ids', 'in', cls.company.ids)], limit=1)
        cls.payable = cls.env['account.account'].search(
            [('account_type', '=', 'liability_payable'), ('company_ids', 'in', cls.company.ids)], limit=1)
        cls.income = cls.env['account.account'].search(
            [('account_type', '=', 'income'), ('company_ids', 'in', cls.company.ids)], limit=1)
        cls.expense = cls.env['account.account'].search(
            [('account_type', '=', 'expense'), ('company_ids', 'in', cls.company.ids)], limit=1)
        cls.journal = cls.env['account.journal'].search(
            [('type', '=', 'general'), ('company_id', '=', cls.company.id)], limit=1)

    def setUp(self):
        super().setUp()
        self.customer = self.env['res.partner'].create({'name': 'Settle Clinic', 'is_company': True,
                                                        'property_payment_term_id': False})
        # the accounts its invoices and bills go to: a receipt on another receivable
        # account is another group, and is rightly never settled against them
        self.receivable = self.customer.property_account_receivable_id or self.receivable
        self.payable = self.customer.property_account_payable_id or self.payable

    # ------------------------------------------------------------------ helpers
    def _invoice(self, amount, days_ago=0, partner=None, kind='out_invoice'):
        day = self.today - timedelta(days=days_ago)
        account = self.expense if kind == 'in_invoice' else self.income
        move = self.env['account.move'].create({
            'move_type': kind, 'partner_id': (partner or self.customer).id, 'invoice_date': day, 'date': day,
            'invoice_payment_term_id': False, 'invoice_date_due': day,
            'invoice_line_ids': [(0, 0, {'name': 'x', 'quantity': 1, 'price_unit': amount,
                                         'account_id': account.id, 'tax_ids': [(6, 0, [])]})]})
        move.action_post()
        return move

    def _receipt(self, amount, partner=None, label='Customer Payment', account=None):
        """A receipt the way this ledger posts them: a plain entry, no account.payment."""
        account = account or self.receivable
        side = {'credit': amount, 'debit': 0} if account == self.receivable else {'debit': amount, 'credit': 0}
        other = {'debit': amount, 'credit': 0} if account == self.receivable else {'credit': amount, 'debit': 0}
        move = self.env['account.move'].create({
            'move_type': 'entry', 'date': self.today, 'journal_id': self.journal.id,
            'line_ids': [
                (0, 0, dict(name=label, partner_id=(partner or self.customer).id, account_id=account.id, **side)),
                (0, 0, dict(name=label, account_id=self.income.id, **other)),
            ]})
        move.action_post()
        return move.line_ids.filtered(lambda l: l.account_id == account)

    def _settle(self, *partners):
        return self.desk._cron_settle(partner_ids=[p.id for p in (partners or (self.customer,))])

    def _open(self, move):
        return move.line_ids.filtered(lambda l: l.account_id.account_type in ('asset_receivable', 'liability_payable'))

    # -------------------------------------------------------------------- tests
    def test_a_receipt_clears_the_invoice_it_pays(self):
        inv = self._invoice(500)
        rcpt = self._receipt(500)
        stats = self._settle()
        self.assertEqual(stats['groups'], 1)
        self.assertEqual(inv.payment_state, 'paid')
        self.assertTrue(rcpt.reconciled)
        self.assertEqual(stats['failed'], 0)

    def test_an_on_account_receipt_pays_the_oldest_invoice_first(self):
        old = self._invoice(300, days_ago=40)
        new = self._invoice(700, days_ago=2)
        self._receipt(300)
        self._settle()
        self.assertEqual(old.payment_state, 'paid', "oldest first, like the countback")
        self.assertEqual(new.amount_residual, 700)

    def test_a_receipt_quoting_an_invoice_goes_on_that_invoice(self):
        old = self._invoice(700, days_ago=40)
        new = self._invoice(700, days_ago=2)
        self._receipt(700, label='Payment for %s' % new.name)
        self._settle()
        self.assertEqual(new.payment_state, 'paid', "the quoted invoice wins over age")
        self.assertEqual(old.amount_residual, 700)

    def test_a_short_receipt_leaves_the_rest_open(self):
        inv = self._invoice(1000)
        self._receipt(400)
        self._settle()
        self.assertEqual(inv.payment_state, 'partial')
        self.assertEqual(inv.amount_residual, 600)

    def test_partners_never_cross(self):
        other = self.env['res.partner'].create({'name': 'Other Clinic', 'is_company': True})
        mine = self._invoice(500)
        self._receipt(500, partner=other)
        self._settle(self.customer, other)
        self.assertEqual(mine.payment_state, 'not_paid', "another clinic's money is not this clinic's")

    def test_a_partner_with_only_invoices_is_left_alone(self):
        inv = self._invoice(500)
        stats = self._settle()
        self.assertEqual(stats['groups'], 0)
        self.assertEqual(inv.payment_state, 'not_paid')

    def test_running_twice_finds_nothing_the_second_time(self):
        self._invoice(500)
        self._receipt(500)
        self._settle()
        again = self._settle()
        self.assertEqual((again['groups'], again['partials'], again['failed']), (0, 0, 0))

    def test_a_vendor_bill_is_settled_with_its_payment(self):
        bill = self._invoice(800, kind='in_invoice')
        self._receipt(800, label='Vendor Payment', account=self.payable)
        stats = self._settle()
        self.assertEqual(bill.payment_state, 'paid')
        self.assertEqual(stats['groups'], 1)

    def test_the_scheduler_exists_switched_off_and_calls_the_settle(self):
        """The lab ships it OFF: its first run reconciles the whole book, and that is the
        accountant's to switch on. (Ortho ships it on.)"""
        cron = self.env.ref('ebshel_account_advanced.cron_settle').with_context(active_test=False)
        self.assertFalse(cron.active)
        self.assertEqual(cron.model_id.model, 'ebshel.match.desk')
        self.assertIn('_cron_settle', cron.code)
