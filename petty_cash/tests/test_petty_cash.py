from odoo import fields
from odoo.tests.common import TransactionCase, tagged
from odoo.exceptions import UserError, ValidationError


@tagged('post_install', '-at_install')
class TestPettyCash(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        # The acting user must be a petty cash manager to allocate on behalf of holders.
        cls.env.user.group_ids = [(4, cls.env.ref('petty_cash.group_petty_cash_manager').id)]
        cls.Allocation = cls.env['petty.cash.allocation']
        cls.Transaction = cls.env['petty.cash.transaction']
        cls.Expense = cls.env['petty.cash.expense']

        # A petty cash journal (reuse a cash journal if present, else create one).
        journal = cls.env['account.journal'].search(
            [('type', '=', 'cash'), ('company_id', '=', cls.company.id)], limit=1)
        if not journal:
            journal = cls.env['account.journal'].create({
                'name': 'Test Petty Cash', 'type': 'cash', 'code': 'TPC',
                'company_id': cls.company.id,
            })
        journal.is_petty_cash = True
        # The journal's own account has to carry the flag too — that is what the
        # journal entries are recognised by (account_move._petty_cash_auto_generate,
        # _create_petty_cash_transaction), not the flag on the journal.
        journal.default_account_id.is_petty_cash = True
        cls.journal = journal

        # The cash account the float is funded from. It is the contra side of
        # the allocation entry (Dr petty cash / Cr this) and is mandatory before
        # an allocation or return can be posted.
        cls.funding_account = cls.env['account.account'].create({
            'name': 'Test Funding Cash',
            'code': 'TSTCSH',
            'account_type': 'asset_cash',
            'company_ids': [(6, 0, cls.company.ids)],
        })

        # Create an individual partner for the cash holder. Give them proper
        # payable/receivable accounts so that payment move generation succeeds
        # when the journal now has an outstanding account configured.
        payable = cls.env['account.account'].search([
            ('account_type', '=', 'liability_payable'), ('company_ids', 'in', [cls.company.id])], limit=1)
        receivable = cls.env['account.account'].search([
            ('account_type', '=', 'asset_receivable'), ('company_ids', 'in', [cls.company.id])], limit=1)
        cls.holder = cls.env['res.partner'].create({
            'name': 'Test Cash Holder',
            'property_account_payable_id': payable.id,
            'property_account_receivable_id': receivable.id,
        })
        # Reset configurable thresholds to known defaults for deterministic tests.
        cls.company.write({
            'petty_cash_warning_threshold': 40.0,
            'petty_cash_critical_threshold': 15.0,
            'petty_cash_alerts_enabled': True,
            'petty_cash_auto_replenish_default': False,
            'petty_cash_expense_attachment_required': False,
            'petty_cash_expense_attachment_min': 0.0,
            'petty_cash_expense_auto_approve_max': 0.0,
            'petty_cash_self_request_limit': 0.0,
        })

    # ── helpers ──────────────────────────────────────────────────────────
    def _make_allocation(self, amount=1000.0, partner=None):
        alloc = self.Allocation.create({
            'partner_id': (partner or self.holder).id,
            'journal_id': self.journal.id,
            'amount_limit': amount,
            'company_id': self.company.id,
        })
        alloc.action_submit_request()
        alloc.action_allocate()
        tx = alloc.transaction_ids.filtered(lambda t: t.type == 'allocation')[:1]
        tx.counter_account_id = self.funding_account
        tx.action_approve()
        tx.action_post()
        return alloc

    def _spend(self, alloc, amount, label='Spend'):
        exp = self.Expense.create({
            'allocation_id': alloc.id,
            'line_ids': [(0, 0, {'name': label, 'amount': amount})],
        })
        exp.action_submit()
        if exp.state == 'submitted':
            exp.action_approve()
        exp.action_register_cash_spend()
        return exp

    # ── lifecycle ────────────────────────────────────────────────────────
    def test_01_allocation_lifecycle(self):
        alloc = self.Allocation.create({
            'partner_id': self.holder.id, 'journal_id': self.journal.id,
            'amount_limit': 1000.0, 'company_id': self.company.id,
        })
        self.assertNotEqual(alloc.name, 'New', 'Sequence should assign a name')
        self.assertEqual(alloc.state, 'draft')
        alloc.action_submit_request()
        self.assertEqual(alloc.state, 'submitted')
        alloc.action_allocate()
        tx = alloc.transaction_ids.filtered(lambda t: t.type == 'allocation')[:1]
        self.assertTrue(tx, 'Allocation transaction created')
        tx.counter_account_id = self.funding_account
        tx.action_approve()
        tx.action_post()
        self.assertEqual(alloc.state, 'allocated')
        self.assertEqual(alloc.amount_allocated, 1000.0)
        self.assertEqual(alloc.amount_balance, 1000.0)
        self.assertEqual(alloc.float_amount, 1000.0, 'Float defaults to allocated amount')
        self.assertTrue(self.holder.is_petty_cash_holder)

    def test_02_expense_reduces_balance(self):
        alloc = self._make_allocation(1000.0)
        self._spend(alloc, 200.0)
        self.assertEqual(alloc.amount_utilized, 200.0)
        self.assertEqual(alloc.amount_balance, 800.0)

    def _do_close(self, alloc, amount=None, is_closing=True):
        """Helper: use the wizard to close or partially return."""
        if amount is None:
            amount = alloc.amount_balance
        # Opened through the context like the buttons do, so the wizard's
        # default_get resolves the account the float was funded from.
        wiz = self.env['petty.cash.close.wizard'].with_context(
            default_allocation_id=alloc.id).create({
            'allocation_id': alloc.id,
            'amount': amount,
            'is_closing': is_closing,
            'date': fields.Date.today(),
        })
        self.assertEqual(wiz.counter_account_id, self.funding_account,
                         'Return defaults back to the funding account')
        wiz.action_confirm()
        return wiz

    def test_03_close_sets_closed_date(self):
        alloc = self._make_allocation(1000.0)
        self._spend(alloc, 200.0)
        # Close with remaining balance 800
        self._do_close(alloc, amount=800.0, is_closing=True)
        self.assertEqual(alloc.state, 'returned', 'Allocation should be closed')
        self.assertTrue(alloc.return_date, 'Closed date must be set')
        self.assertAlmostEqual(alloc.amount_balance, 0.0, places=2)
        self.assertAlmostEqual(alloc.amount_returned, 800.0, places=2)

    def test_03b_partial_return_keeps_active(self):
        alloc = self._make_allocation(1000.0)
        self._do_close(alloc, amount=300.0, is_closing=False)
        self.assertEqual(alloc.state, 'allocated', 'Partial return must not close allocation')
        self.assertAlmostEqual(alloc.amount_returned, 300.0, places=2)
        self.assertAlmostEqual(alloc.amount_balance, 700.0, places=2)
        self.assertEqual(alloc.partial_return_count, 1)
        # Second partial return
        self._do_close(alloc, amount=200.0, is_closing=False)
        self.assertEqual(alloc.partial_return_count, 2)
        self.assertAlmostEqual(alloc.amount_balance, 500.0, places=2)
        # Now close
        self._do_close(alloc, amount=500.0, is_closing=True)
        self.assertEqual(alloc.state, 'returned')
        self.assertAlmostEqual(alloc.amount_balance, 0.0, places=2)

    # ── health thresholds ────────────────────────────────────────────────
    def test_04_health_thresholds(self):
        alloc = self._make_allocation(1000.0)
        self.assertEqual(alloc.health_status, 'healthy')
        self._spend(alloc, 650.0)   # balance 350 -> 35% -> warning
        self.assertEqual(alloc.health_status, 'warning')
        self._spend(alloc, 300.0)   # balance 50 -> 5% -> critical
        self.assertEqual(alloc.health_status, 'critical')

    # ── configurable features ────────────────────────────────────────────
    def test_05_expense_auto_approve(self):
        self.company.petty_cash_expense_auto_approve_max = 100.0
        alloc = self._make_allocation(1000.0)
        small = self.Expense.create({
            'allocation_id': alloc.id,
            'line_ids': [(0, 0, {'name': 'Tea', 'amount': 50.0})],
        })
        small.action_submit()
        self.assertEqual(small.state, 'approved', 'Small expense auto-approved')
        big = self.Expense.create({
            'allocation_id': alloc.id,
            'line_ids': [(0, 0, {'name': 'Printer', 'amount': 500.0})],
        })
        big.action_submit()
        self.assertEqual(big.state, 'submitted', 'Large expense needs manual approval')

    def test_06_receipt_required(self):
        self.company.write({
            'petty_cash_expense_attachment_required': True,
            'petty_cash_expense_attachment_min': 100.0,
        })
        alloc = self._make_allocation(1000.0)
        exp = self.Expense.create({
            'allocation_id': alloc.id,
            'line_ids': [(0, 0, {'name': 'Taxi', 'amount': 200.0})],
        })
        with self.assertRaises(UserError):
            exp.action_submit()
        attachment = self.env['ir.attachment'].create({
            'name': 'receipt.pdf', 'res_model': exp._name, 'res_id': exp.id,
        })
        exp.attachment_ids = [(4, attachment.id)]
        exp.action_submit()
        self.assertIn(exp.state, ('submitted', 'approved'))

    def test_07_self_request_limit(self):
        self.company.petty_cash_self_request_limit = 500.0
        user = self.env['res.users'].create({
            'name': 'PC User', 'login': 'pc_user_test',
            'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('petty_cash.group_petty_cash_user').id,
                self.env.ref('account.group_account_readonly').id,
            ])],
        })
        alloc = self.Allocation.create({
            'partner_id': user.partner_id.id, 'journal_id': self.journal.id,
            'amount_limit': 1000.0, 'company_id': self.company.id,
        })
        with self.assertRaises(UserError):
            alloc.with_user(user).action_submit_request()
        # Within the limit it submits fine (reuse the same draft record).
        alloc.amount_limit = 300.0
        alloc.with_user(user).action_submit_request()
        self.assertEqual(alloc.state, 'submitted')

    # ── constraints ──────────────────────────────────────────────────────
    def test_08_unique_active_allocation(self):
        self._make_allocation(1000.0)
        with self.assertRaises(ValidationError):
            self.Allocation.create({
                'partner_id': self.holder.id, 'journal_id': self.journal.id,
                'amount_limit': 500.0, 'company_id': self.company.id,
            })

    # ── replenishment ────────────────────────────────────────────────────
    def test_09_replenishment(self):
        alloc = self._make_allocation(1000.0)
        self._spend(alloc, 900.0)   # balance 100, float 1000
        tx = alloc._create_replenishment_request(automated=True)
        self.assertTrue(tx, 'A top-up transaction is created')
        self.assertEqual(tx.type, 'allocation')
        self.assertEqual(tx.amount, 900.0, 'Top-up restores the float')
        # A second call must not stack while one is pending.
        self.assertFalse(alloc._create_replenishment_request(automated=True))

    # ── dashboard / statement / crons ────────────────────────────────────
    def test_10_dashboard_data(self):
        self._make_allocation(1000.0)
        data = self.Allocation.get_dashboard_data(period='all')
        for key in ('kpis', 'domains', 'holders', 'top_spend', 'attention', 'pending'):
            self.assertIn(key, data)
        for key in ('allocated', 'utilized', 'balance', 'returned',
                    'avg_utilization', 'active_count', 'returned_count'):
            self.assertIn(key, data['kpis'])
        self.assertGreaterEqual(data['kpis']['allocated'], 1000.0)
        self.assertIn('returned', data['domains'])

    def test_11_statement_lines(self):
        alloc = self._make_allocation(1000.0)
        self._spend(alloc, 250.0)
        lines = alloc.get_statement_lines()
        self.assertTrue(lines)
        self.assertEqual(lines[-1]['balance'], alloc.amount_balance)

    def test_12_crons_run(self):
        alloc = self._make_allocation(1000.0)
        self._spend(alloc, 950.0)   # critical
        alloc.auto_replenish = True
        # Should execute without raising.
        self.Allocation._cron_check_low_balance()
        self.Allocation._cron_auto_replenish()
        self.assertTrue(
            alloc.transaction_ids.filtered(lambda t: t.type == 'allocation' and t.state == 'draft'),
            'Auto-replenish cron raised a draft top-up')

    def test_13_cancel_guard(self):
        alloc = self._make_allocation(1000.0)
        self._spend(alloc, 100.0)
        with self.assertRaises(UserError):
            alloc.action_cancel()   # has a remaining balance

    # ── contra account / entry direction ─────────────────────────────────
    def test_14_contra_account_required_before_posting(self):
        alloc = self.Allocation.create({
            'partner_id': self.holder.id, 'journal_id': self.journal.id,
            'amount_limit': 1000.0, 'company_id': self.company.id,
        })
        alloc.action_submit_request()
        alloc.action_allocate()
        tx = alloc.transaction_ids.filtered(lambda t: t.type == 'allocation')[:1]
        tx.counter_account_id = False
        tx.action_approve()
        with self.assertRaises(UserError) as err:
            tx.action_post()
        self.assertIn('Contra Account', str(err.exception))
        self.assertEqual(tx.state, 'approved', 'The transaction must not post')
        # …and it goes through once the account is set.
        tx.counter_account_id = self.funding_account
        tx.action_post()
        self.assertEqual(tx.state, 'posted')

    def test_15_allocation_debits_the_petty_cash_account(self):
        alloc = self._make_allocation(1000.0)
        tx = alloc.transaction_ids.filtered(lambda t: t.type == 'allocation')[:1]
        self.assertEqual(tx.payment_id.payment_type, 'inbound')
        self.assertEqual(tx.payment_id.partner_type, 'supplier',
                         'Holders are payees, never customers')
        self.assertEqual(tx.move_id.move_type, 'entry')
        pc_line = tx.move_id.line_ids.filtered(lambda l: l.account_id.is_petty_cash)
        contra = tx.move_id.line_ids - pc_line
        self.assertTrue(pc_line.debit and not pc_line.credit,
                        'An allocation debits the petty cash account')
        self.assertEqual(contra.account_id, self.funding_account)
        self.assertTrue(contra.credit and not contra.debit)

    def test_16_return_mirrors_the_funding_entry(self):
        alloc = self._make_allocation(1000.0)
        self._do_close(alloc, amount=400.0, is_closing=False)
        ret = alloc.transaction_ids.filtered(lambda t: t.type == 'return')[:1]
        self.assertEqual(ret.counter_account_id, self.funding_account)
        self.assertEqual(ret.payment_id.payment_type, 'outbound')
        pc_line = ret.move_id.line_ids.filtered(lambda l: l.account_id.is_petty_cash)
        contra = ret.move_id.line_ids - pc_line
        self.assertTrue(pc_line.credit and not pc_line.debit,
                        'A return credits the petty cash account')
        self.assertEqual(contra.account_id, self.funding_account)
        self.assertFalse(
            ret.move_id.line_ids.filtered(
                lambda l: l.account_id.account_type == 'liability_payable'),
            'Returns must not fall back to the holder payable')

    def test_17_top_up_inherits_the_funding_account(self):
        alloc = self._make_allocation(1000.0)
        self._spend(alloc, 900.0)
        tx = alloc._create_replenishment_request()
        self.assertEqual(tx.counter_account_id, self.funding_account,
                         'Only the first allocation needs a contra account by hand')

    def test_18_cash_transactions_carry_the_holder(self):
        alloc = self._make_allocation(1000.0)
        vendor = self.env['res.partner'].create({'name': 'Test Vendor', 'supplier_rank': 1})
        exp = self.Expense.create({
            'allocation_id': alloc.id,
            'partner_id': vendor.id,
            'line_ids': [(0, 0, {'name': 'Taxi', 'amount': 50.0})],
        })
        exp.action_submit()
        if exp.state == 'submitted':
            exp.action_approve()
        exp.action_register_cash_spend()
        self.assertEqual(
            exp.transaction_id.partner_id, alloc.partner_id,
            'A cash movement belongs to the holder, not the vendor')
        self.assertIn(vendor.name, exp.transaction_id.note or '',
                      'The vendor stays visible in the note')
        self.assertFalse(
            alloc.transaction_ids.filtered(lambda t: t.partner_id != alloc.partner_id),
            "The allocation's transaction list is all the holder's")

    # ── balance integrity ────────────────────────────────────────────────
    def _return_tx(self, alloc, amount, is_closing=False):
        return self.Transaction.create({
            'allocation_id': alloc.id, 'type': 'return', 'amount': amount,
            'counter_account_id': self.funding_account.id,
            'company_id': self.company.id, 'is_closing': is_closing,
        })

    def _plain_holder(self, login):
        """A cash holder with nothing but the petty cash User role."""
        user = self.env['res.users'].create({
            'name': 'PC Holder %s' % login, 'login': login,
            'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('petty_cash.group_petty_cash_user').id,
            ])],
        })
        user.partner_id.write({
            'property_account_payable_id': self.holder.property_account_payable_id.id,
            'property_account_receivable_id': self.holder.property_account_receivable_id.id,
        })
        return user

    def test_19_two_full_returns_cannot_both_be_approved(self):
        alloc = self._make_allocation(1000.0)
        first, second = self._return_tx(alloc, 1000.0), self._return_tx(alloc, 1000.0)
        (first | second).action_submit()
        with self.assertRaises(ValidationError):
            (first | second).action_approve()       # one batch reserves too
        first.action_approve()
        with self.assertRaises(ValidationError):
            second.action_approve()
        self.assertEqual(second.state, 'submitted')

    def test_20_posting_rechecks_the_balance_an_old_approval_saw(self):
        self.company.petty_cash_auto_close = False
        alloc = self._make_allocation(1000.0)
        first, second = self._return_tx(alloc, 1000.0), self._return_tx(alloc, 1000.0)
        first.action_approve()
        # An approval that predates the reservation: nothing held the cash for it.
        second.write({'state': 'approved'})
        with self.assertRaises(UserError):
            (first | second).action_post()
        first.action_post()
        self.assertAlmostEqual(alloc.amount_balance, 0.0, places=2)
        with self.assertRaises(UserError):
            second.action_post()
        self.assertEqual(second.state, 'approved')
        self.assertAlmostEqual(alloc.amount_balance, 0.0, places=2)

    def test_21_a_closed_float_takes_no_more_posts(self):
        alloc = self._make_allocation(1000.0)
        closing = self._return_tx(alloc, 400.0, is_closing=True)
        late = self._return_tx(alloc, 100.0)
        closing.action_approve()
        late.action_approve()
        closing.action_post()
        self.assertEqual(alloc.state, 'returned')
        with self.assertRaises(UserError):
            late.action_post()
        self.assertEqual(late.state, 'approved')

    # ── close wizard write-off ───────────────────────────────────────────
    def _write_off_setup(self):
        account = self.env['account.account'].search([
            ('account_type', '=', 'expense'), ('company_ids', 'in', [self.company.id])], limit=1)
        self.assertTrue(account, 'fixture: an expense account to write off to')
        self.company.write({
            'petty_cash_write_off_account_id': account.id,
            'petty_cash_write_off_threshold': 50.0,
        })

    def _close_with_write_off(self, alloc, amount, user=None):
        Wizard = self.env['petty.cash.close.wizard']
        if user:
            Wizard = Wizard.with_user(user)
        return Wizard.with_context(default_allocation_id=alloc.id).create({
            'allocation_id': alloc.id, 'amount': amount, 'is_closing': True,
            'use_write_off': True, 'date': fields.Date.today(),
        })

    def test_22_closing_with_a_write_off_writes_the_remainder_off(self):
        self._write_off_setup()
        alloc = self._make_allocation(1000.0)
        wiz = self._close_with_write_off(alloc, 990.0)
        self.assertAlmostEqual(wiz.write_off_amount, 10.0, places=2)
        wiz.action_confirm()
        write_offs = alloc.transaction_ids.filtered(lambda t: t.type == 'write_off')
        self.assertEqual(len(write_offs), 1, 'the remainder is written off, once')
        self.assertAlmostEqual(write_offs.amount, 10.0, places=2)
        self.assertEqual(write_offs.state, 'posted')
        self.assertEqual(alloc.state, 'returned')
        self.assertAlmostEqual(alloc.amount_balance, 0.0, places=2)
        self.assertAlmostEqual(alloc.amount_write_off, 10.0, places=2)

    def test_23_a_holder_cannot_write_off_their_own_remainder(self):
        self._write_off_setup()
        user = self._plain_holder('pc_writeoff_holder')
        alloc = self._make_allocation(1000.0, partner=user.partner_id)
        wiz = self._close_with_write_off(alloc, 990.0, user=user)
        with self.assertRaises(UserError):
            wiz.action_confirm()
        self.assertFalse(
            alloc.transaction_ids.filtered(lambda t: t.type in ('return', 'write_off')),
            'refused outright, not a return with the write-off quietly dropped')

    # ── who may approve / post ───────────────────────────────────────────
    def test_24_a_holder_cannot_approve_or_post_their_own_spend(self):
        user = self._plain_holder('pc_self_approver')
        alloc = self._make_allocation(1000.0, partner=user.partner_id)
        exp = self.Expense.create({
            'allocation_id': alloc.id,
            'line_ids': [(0, 0, {'name': 'Self approved', 'amount': 100.0})],
        })
        exp.action_submit()
        self.assertEqual(exp.state, 'submitted')
        with self.assertRaises(UserError):
            exp.with_user(user).action_approve()
        self.assertEqual(exp.state, 'submitted')
        exp.action_approve()                      # a manager signs it off
        with self.assertRaises(UserError):
            exp.with_user(user).action_register_cash_spend()
        self.assertFalse(exp.transaction_id)
        self.assertAlmostEqual(alloc.amount_balance, 1000.0, places=2)

        ret = self._return_tx(alloc, 100.0)
        ret.with_user(user).action_submit()
        with self.assertRaises(UserError):
            ret.with_user(user).action_approve()
        self.assertEqual(ret.state, 'submitted')

    def test_25_only_an_officer_cancels_and_only_a_cancelled_float_resets(self):
        user = self._plain_holder('pc_resetter')
        alloc = self.Allocation.create({
            'partner_id': user.partner_id.id, 'journal_id': self.journal.id,
            'amount_limit': 300.0, 'company_id': self.company.id,
        })
        alloc.action_submit_request()
        with self.assertRaises(UserError):
            alloc.action_reset_to_draft()          # submitted, not cancelled
        with self.assertRaises(UserError):
            alloc.with_user(user).action_cancel()
        alloc.action_cancel()
        self.assertEqual(alloc.state, 'cancelled')
        with self.assertRaises(UserError):
            alloc.with_user(user).action_reset_to_draft()
        alloc.action_reset_to_draft()
        self.assertEqual(alloc.state, 'draft')

