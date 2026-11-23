# -*- coding: utf-8 -*-
import importlib.util
import os
from datetime import date, timedelta

from odoo.exceptions import UserError, ValidationError
from odoo.modules.module import get_module_path
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
        # the bank's Cheques in Hand (PDC) account: a cheque waits here from the day it is
        # received to the day it is deposited (2026-11-23)
        cls.acc_pdc = account('T10150', 'PDC - Test Bank', 'asset_current', True)
        cls.bank_journal.lab_pdc_account_id = cls.acc_pdc

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
        self.assertIn(cheque.payment_id.state, ('paid', 'reconciled'))
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
        # the bounce is in the books, and the cheque shows where (2026-11-23: "status of
        # payment and cheque register is updating but nothing happening in journal entry")
        reversals = cheque.move_ids.filtered('reversed_entry_id')
        self.assertEqual(reversals.reversed_entry_id, cheque.payment_id.move_id | cheque.deposit_move_id)
        self.assertEqual(set(reversals.mapped('date')), {self.today})
        self.assertEqual(cheque.move_count, 4, "receipt, bank entry and the two reversals")
        self.assertIn(reversals[0].name, cheque.message_ids[0].body)
        self.assertEqual(cheque.action_view_moves()['domain'], [('id', 'in', cheque.move_ids.ids)])

    def test_a_cheque_recorded_the_old_way_bounces_by_reversal(self):
        """Its receipt stays in the books with a reversal beside it - it used to be reset to
        draft and cancelled, which rewrote past books."""
        invoice = self._invoice(1500.0)
        old_receipt = self._payment(1500.0)
        old_receipt.action_post()
        receivable = old_receipt.move_id.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable')
        (receivable | invoice.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable')).reconcile()
        cheque = self._cheque(1500.0)
        cheque.write({'accounting': 'clearing', 'payment_id': old_receipt.id})
        cheque.action_deposit()
        cheque.action_clear()
        cheque.bounce_reason = 'Account closed'
        cheque.action_bounce()
        invoice.invalidate_recordset()
        self.assertEqual(old_receipt.move_id.state, 'posted', "the old receipt is not touched")
        self.assertEqual(old_receipt.state, 'rejected')
        reversal = old_receipt.move_id.reversal_move_ids
        self.assertEqual((len(reversal), reversal.state, reversal.date), (1, 'posted', self.today))
        self.assertEqual(invoice.payment_state, 'not_paid')
        self.assertIn(reversal, cheque.move_ids)

    def test_the_payment_opens_its_cheque(self):
        invoice, cheque = self._received(days_ago=2)
        cheque.action_confirm_receipt()
        payment = cheque.payment_id
        action = payment.action_view_lab_cheque()
        self.assertEqual((action['res_model'], action['res_id']), ('lab.cheque', cheque.id))
        self.assertEqual((payment.lab_cheque_number, payment.lab_cheque_state), (cheque.cheque_number, 'received'))
        arch = payment.get_view(self.env.ref('account.view_account_payment_form').id, 'form')['arch']
        self.assertIn('action_view_lab_cheque', arch)

    def test_the_upgrade_links_each_cheque_to_its_entries(self):
        path = os.path.join(get_module_path('lab_finance_ops'), 'migrations', '20.0.1.3.0', 'post-migrate.py')
        spec = importlib.util.spec_from_file_location('lab_finance_ops_moves_post_migrate', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _invoice, cheque = self._received(days_ago=3)
        cheque.action_deposit()
        cheque.action_clear()
        cheque.bounce_reason = 'Stopped'
        cheque.action_bounce()
        expected = cheque.move_ids
        self.assertEqual(len(expected), 4)
        self.env.flush_all()
        self.env.cr.execute("DELETE FROM lab_cheque_account_move_rel WHERE cheque_id = %s", [cheque.id])
        self.env.invalidate_all()
        self.assertFalse(cheque.move_ids)
        module.migrate(self.env.cr, '20.0.1.2.0')
        self.env.invalidate_all()
        self.assertEqual(cheque.move_ids, expected)

    def test_post_dated_cheque_is_flagged(self):
        from datetime import timedelta
        cheque = self._cheque(1000.0, cheque_date=self.today + timedelta(days=30))
        self.assertTrue(cheque.is_post_dated)

    # ------------------------------------------------- settled when received, banked when cleared
    # client, 2026-11-23: "the customer invoices should be matched with postdated cheques on
    # the date of cheque received", then: "Deposit entry will create only after the cheque
    # cleared, otherwise when it bounced the bank balance will be wrong"
    def _lines(self, account, move=None):
        domain = [('account_id', '=', account.id), ('parent_state', '=', 'posted')]
        if move:
            domain.append(('move_id', '=', move.id))
        return self.env['account.move.line'].search(domain)

    def _received(self, amount=2000.0, days_ago=10, **vals):
        invoice = self._invoice(amount)
        cheque = self._cheque(amount, received_date=self.today - timedelta(days=days_ago),
                              cheque_date=self.today + timedelta(days=20),
                              allocation_ids=[(0, 0, {'move_id': invoice.id, 'amount': amount})], **vals)
        return invoice, cheque

    def test_the_invoices_are_settled_on_the_day_the_cheque_is_received(self):
        invoice, cheque = self._received(days_ago=10)
        self.assertEqual(cheque.accounting, 'holding')
        cheque.action_confirm_receipt()
        payment = cheque.payment_id
        invoice.invalidate_recordset()
        self.assertEqual(payment.date, self.today - timedelta(days=10), "dated the received day")
        self.assertEqual(payment.outstanding_account_id, self.acc_pdc, "the money waits in the PDC account")
        self.assertEqual(invoice.amount_residual, 0.0)
        self.assertFalse(self._lines(self.bank_journal.default_account_id, payment.move_id), "nothing in the bank yet")
        self.assertTrue(cheque.receipt_booked)

    def test_a_deposited_cheque_is_not_in_the_bank_yet(self):
        invoice, cheque = self._received(days_ago=10)
        cheque.action_confirm_receipt()
        cheque.deposit_date = self.today - timedelta(days=3)
        cheque.action_deposit()
        self.assertEqual((cheque.state, cheque.deposit_date), ('deposited', self.today - timedelta(days=3)))
        self.assertFalse(cheque.deposit_move_id, "nothing booked on deposit")
        self.assertFalse(self._lines(self.bank_journal.default_account_id).filtered(
            lambda l: l.partner_id == cheque.partner_id.commercial_partner_id), "the bank has not moved")
        self.assertEqual(sum(self._lines(self.acc_pdc, cheque.payment_id.move_id).mapped('balance')), 2000.0,
                         "the cheque is still in hand, in the PDC account")

    def test_the_bank_shows_it_from_the_day_it_clears(self):
        invoice, cheque = self._received(days_ago=10)
        cheque.action_confirm_receipt()
        cheque.deposit_date = self.today - timedelta(days=3)
        cheque.action_deposit()
        cheque.clear_date = self.today - timedelta(days=1)
        cheque.action_clear()
        entry = cheque.deposit_move_id
        self.assertEqual(cheque.state, 'cleared')
        self.assertEqual(entry.date, self.today - timedelta(days=1), "dated the day the bank credited it")
        bank_line = self._lines(self.bank_journal.default_account_id, entry)
        self.assertEqual(bank_line.balance, 2000.0)
        pdc = self._lines(self.acc_pdc, entry) | self._lines(self.acc_pdc, cheque.payment_id.move_id)
        self.assertTrue(all(pdc.mapped('reconciled')), "the PDC account is matched out")
        self.assertEqual(sum(pdc.mapped('balance')), 0.0)
        self.assertEqual(len(self.env['account.payment'].search([('cheque_id', '=', cheque.id)])), 1,
                         "one receipt, never a second payment")

    def test_clearing_without_a_date_clears_today(self):
        _invoice, cheque = self._received(days_ago=4)
        cheque.action_deposit()
        cheque.action_clear()
        self.assertEqual((cheque.clear_date, cheque.deposit_move_id.date), (self.today, self.today))

    def test_a_clearing_date_must_be_between_deposit_and_today(self):
        _invoice, cheque = self._received(days_ago=6)
        cheque.deposit_date = self.today - timedelta(days=2)
        cheque.action_deposit()
        cheque.clear_date = self.today + timedelta(days=1)
        with self.assertRaises(UserError):
            cheque.action_clear()
        cheque.clear_date = self.today - timedelta(days=3)
        with self.assertRaises(UserError):
            cheque.action_clear()
        self.assertEqual(cheque.state, 'deposited')
        self.assertFalse(cheque.deposit_move_id)

    def test_depositing_a_cheque_not_yet_received_receives_it_first(self):
        invoice, cheque = self._received(days_ago=4)
        cheque.action_deposit()
        invoice.invalidate_recordset()
        self.assertEqual(cheque.payment_id.date, self.today - timedelta(days=4))
        self.assertFalse(cheque.deposit_move_id)
        self.assertEqual(invoice.amount_residual, 0.0)

    def test_a_deposit_date_must_be_between_receipt_and_today(self):
        _invoice, cheque = self._received(days_ago=4)
        cheque.deposit_date = self.today + timedelta(days=1)
        with self.assertRaises(UserError):
            cheque.action_deposit()
        cheque.deposit_date = self.today - timedelta(days=9)
        with self.assertRaises(UserError):
            cheque.action_deposit()

    def test_a_cheque_bounced_before_clearing_never_touches_the_bank(self):
        invoice, cheque = self._received(days_ago=10)
        cheque.action_deposit()
        cheque.bounce_reason = 'Insufficient funds'
        cheque.action_bounce()
        invoice.invalidate_recordset()
        self.assertEqual(invoice.payment_state, 'not_paid')
        self.assertEqual(cheque.payment_id.state, 'rejected')
        self.assertFalse(cheque.deposit_move_id)
        self.assertFalse(self._lines(self.bank_journal.default_account_id).filtered(
            lambda l: l.partner_id == cheque.partner_id.commercial_partner_id),
            "the bank account never showed money it did not receive")
        pdc = self._lines(self.acc_pdc).filtered(lambda l: l.partner_id == cheque.partner_id.commercial_partner_id)
        self.assertEqual(sum(pdc.mapped('balance')), 0.0)
        self.assertTrue(all(pdc.mapped('reconciled')))
        # presented again: back in hand today, settled again when received
        cheque.action_draft()
        self.assertEqual((cheque.state, cheque.payment_id, cheque.received_date), ('received', self.env['account.payment'], self.today))
        cheque.action_confirm_receipt()
        invoice.invalidate_recordset()
        self.assertEqual(invoice.amount_residual, 0.0)

    def test_a_cheque_bounced_after_clearing_reverses_the_bank(self):
        invoice, cheque = self._received(days_ago=10)
        cheque.action_deposit()
        cheque.action_clear()
        entry = cheque.deposit_move_id
        cheque.bounce_reason = 'Returned by the bank after credit'
        cheque.action_bounce()
        invoice.invalidate_recordset()
        self.assertEqual(invoice.payment_state, 'not_paid')
        self.assertEqual(sum(self._lines(self.bank_journal.default_account_id).filtered(
            lambda l: l.move_id in (entry | entry.reversal_move_ids)).mapped('balance')), 0.0)
        self.assertEqual(entry.reversal_move_ids.date, self.today, "reversed the day it bounced")

    def test_a_payment_approver_clears_a_cheque(self):
        approver = self.env['res.users'].create({
            'name': 'Cheque Approver', 'login': 'cheque_approver',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                   self.env.ref('account.group_account_invoice').id,
                                   self.env.ref('lab_finance_ops.group_lab_payment_approver').id])]})
        _invoice, cheque = self._received(days_ago=5)
        cheque.action_deposit()
        cheque.with_user(approver).action_clear()
        self.assertEqual(cheque.state, 'cleared')
        self.assertTrue(cheque.deposit_move_id, "booked although a cleared cheque is locked to its clearer")

    def test_a_received_cheque_cancelled_reopens_its_invoices_but_a_deposited_one_bounces(self):
        invoice, cheque = self._received()
        cheque.action_confirm_receipt()
        cheque.action_cancel()
        invoice.invalidate_recordset()
        self.assertEqual((cheque.state, invoice.payment_state), ('cancel', 'not_paid'))
        _invoice, banked = self._received()
        banked.action_deposit()
        with self.assertRaises(UserError):
            banked.action_cancel()

    def test_a_cheque_in_the_books_keeps_what_it_settled(self):
        invoice, cheque = self._received()
        cheque.action_confirm_receipt()
        with self.assertRaises(UserError):
            cheque.amount = 2500.0
        with self.assertRaises(UserError):
            cheque.allocation_ids.amount = 1000.0
        cheque.note = 'Handed over at the counter'
        cheque.amount = 2000.0  # the same value is not a change

    def test_a_bank_without_a_pdc_account_is_named(self):
        _invoice, cheque = self._received()
        cheque.journal_id.lab_pdc_account_id = False
        with self.assertRaisesRegex(UserError, 'Cheques in Hand'):
            cheque.action_confirm_receipt()

    def test_each_invoice_is_settled_for_what_was_allocated_to_it(self):
        first, second = self._invoice(2000.0), self._invoice(2000.0)
        cheque = self._cheque(3000.0, allocation_ids=[
            (0, 0, {'move_id': first.id, 'amount': 1000.0}),
            (0, 0, {'move_id': second.id, 'amount': 2000.0})])
        cheque.action_confirm_receipt()
        (first | second).invalidate_recordset()
        if hasattr(self.env['account.move.line'], '_reconcile_up_to'):
            self.assertEqual((first.amount_residual, second.amount_residual), (1000.0, 0.0))

    def test_a_cheque_recorded_the_old_way_is_not_paid_twice(self):
        """Every in-hand cheque brought over from Odoo 17 carries the receipt Odoo 17 posted."""
        old_receipt = self._payment(1500.0)
        old_receipt.action_post()
        cheque = self._cheque(1500.0)
        cheque.write({'accounting': 'clearing', 'payment_id': old_receipt.id})
        cheque.action_deposit()
        cheque.action_clear()
        self.assertEqual(cheque.payment_id, old_receipt)
        self.assertFalse(cheque.deposit_move_id)
        self.assertFalse(self.env['account.payment'].search([('cheque_id', '=', cheque.id)]))

    def test_the_add_cheque_card(self):
        Cheque = self.env['lab.cheque']
        first, second = self._invoice(1000.0), self._invoice(1500.0)
        empty = Cheque.lab_cheque_entry_data()
        self.assertIn(self.bank_journal.id, [j['id'] for j in empty['journals'] if j['pdc']])
        data = Cheque.lab_cheque_entry_data(self.clinic.id)
        offered = {i['id']: i['open'] for i in data['invoices']}
        self.assertEqual((offered.get(first.id), offered.get(second.id)), (1000.0, 1500.0))
        made = Cheque.lab_cheque_quick_create({
            'partner_id': self.clinic.id, 'cheque_number': '000164', 'amount': 2500.0,
            'cheque_date': str(self.today + timedelta(days=30)), 'received_date': str(self.today),
            'bank_name': 'Federal Bank', 'journal_id': self.bank_journal.id, 'state': 'cleared',
        }, [{'move_id': first.id, 'amount': 1000.0}, {'move_id': second.id, 'amount': 1500.0}])
        cheque = Cheque.browse(made['id'])
        (first | second).invalidate_recordset()
        self.assertEqual(cheque.state, 'received', "only what the card may set is set")
        self.assertTrue(made['payment'])
        self.assertEqual(first.amount_residual + second.amount_residual, 0.0)
        again = Cheque.lab_cheque_entry_data(self.clinic.id)
        self.assertEqual(again['last']['bank_name'], 'Federal Bank', "the next cheque remembers the bank")
        self.assertIn('000164', again['numbers'])
        summary = Cheque.lab_cheque_summary()
        self.assertGreaterEqual(summary['in_hand']['count'], 1)
        self.assertEqual(Cheque.search_count(summary['week']['domain'] + [('id', '=', cheque.id)]), 0,
                         "a month away is not due this week")

    def test_the_upgrade_keeps_recorded_cheques_and_finds_each_banks_pdc_account(self):
        path = os.path.join(get_module_path('lab_finance_ops'), 'migrations', '20.0.1.1.0', 'post-migrate.py')
        spec = importlib.util.spec_from_file_location('lab_finance_ops_pdc_post_migrate', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cheque = self._cheque(1000.0)
        bank_account = self.env['account.account'].create({
            'code': 'T10160', 'name': 'KGB OD Account', 'account_type': 'asset_cash'})
        pdc = self.env['account.account'].create({
            'code': 'T10161', 'name': 'PDC -KGB OD ACC', 'account_type': 'asset_current', 'reconcile': True})
        journal = self.env['account.journal'].create({
            'name': 'KGB TEST', 'code': 'TKGB', 'type': 'bank', 'default_account_id': bank_account.id})
        self.env.flush_all()
        module.migrate(self.env.cr, '20.0.1.0.2')
        self.env.invalidate_all()
        self.assertEqual(cheque.accounting, 'clearing')
        self.assertEqual(journal.lab_pdc_account_id, pdc)

    # ------------------------------------------------------------------ approval
    def _require_approval(self, threshold='1000'):
        self.params.set_str('lab_finance_ops.require_payment_approval', 'True')
        self.params.set_str('lab_finance_ops.payment_approval_threshold', threshold)
        self.addCleanup(
            self.params.set_str,
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
        self.assertIn(payment.state, ('paid', 'reconciled'))

    def test_payment_above_threshold_is_held_until_approved(self):
        self._require_approval('1000')
        payment = self._payment(5000.0)
        self.assertEqual(payment.approval_state, 'to_approve')
        with self.assertRaises(UserError):
            payment.action_post()
        payment.action_approve_payment()
        payment.action_post()
        self.assertIn(payment.state, ('paid', 'reconciled'))

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
        self.assertIn(cheque.payment_id.state, ('paid', 'reconciled'))

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
