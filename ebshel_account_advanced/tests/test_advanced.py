# -*- coding: utf-8 -*-
"""Every desk against a small, known ledger: collections, budgets, deferrals,
matching, the close, the cash forecast and the scanner."""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAdvanced(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.write({'fiscalyear_lock_date': False, 'tax_lock_date': False, 'sale_lock_date': False, 'purchase_lock_date': False})
        cls.today = fields.Date.context_today(cls.env.user)
        Account = cls.env['account.account']

        def account(code, name, atype):
            acc = Account.search([('code', '=', code)], limit=1)
            return acc or Account.create({'code': code, 'name': name, 'account_type': atype})
        cls.income = account('ZZ7010', 'Test income', 'income')
        cls.expense = account('ZZ6010', 'Test expense', 'expense')
        cls.deferred = account('ZZ2410', 'Test deferred revenue', 'liability_current')
        cls.prepaid = account('ZZ1410', 'Test prepaid expense', 'asset_current')
        cls.receivable = Account.search([('account_type', '=', 'asset_receivable')], limit=1)
        Journal = cls.env['account.journal']
        cls.general = Journal.search([('type', '=', 'general'), ('company_id', '=', cls.company.id)], limit=1)
        cls.bank = Journal.search([('type', '=', 'bank'), ('company_id', '=', cls.company.id)], limit=1) or Journal.create(
            {'name': 'Test Bank', 'type': 'bank', 'code': 'TBNK'})
        cls.company.write({'ebshel_deferred_revenue_account_id': cls.deferred.id, 'ebshel_deferred_expense_account_id': cls.prepaid.id,
                           'ebshel_deferral_journal_id': cls.general.id, 'ebshel_deferral_method': 'days', 'ebshel_deferral_auto': True,
                           'ebshel_followup_auto': True, 'ebshel_followup_min_amount': 0.0, 'ebshel_cash_collection_rate': 100,
                           'ebshel_close_lock': True})
        Level = cls.env['ebshel.followup.level']
        Level.search([('company_id', '=', cls.company.id)]).write({'active': False})
        cls.l1 = Level.create({'name': 'T1 friendly', 'days': 7, 'action': 'email', 'attach_statement': True, 'attach_invoices': True})
        cls.l2 = Level.create({'name': 'T2 second', 'days': 21, 'action': 'email_call', 'auto': True})
        cls.l3 = Level.create({'name': 'T3 final', 'days': 45, 'action': 'letter', 'on_hold': True, 'repeat_days': 15})
        cls.customer = cls.env['res.partner'].create({'name': 'Test Clinic', 'email': 'clinic@example.com', 'is_company': True})
        cls.vendor = cls.env['res.partner'].create({'name': 'Test Vendor', 'is_company': True})

    # ------------------------------------------------------------------ helpers
    def _invoice(self, partner, amount, date, due=None, kind='out_invoice', account=None, extra=None, post=True):
        account = account or (self.income if kind.startswith('out') else self.expense)
        line = {'name': 'Service', 'quantity': 1, 'price_unit': amount, 'account_id': account.id, 'tax_ids': [(6, 0, [])]}
        line.update(extra or {})
        move = self.env['account.move'].create({'move_type': kind, 'partner_id': partner.id, 'invoice_date': date,
                                                'invoice_date_due': due or date, 'invoice_line_ids': [(0, 0, line)]})
        if post:
            move.action_post()
        return move

    def _ar(self, move):
        return move.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable')

    # ------------------------------------------------------------------ collections
    def test_an_overdue_customer_reaches_the_level_of_its_oldest_invoice(self):
        self._invoice(self.customer, 1000.0, self.today - timedelta(days=40), due=self.today - timedelta(days=30))
        self.customer.invalidate_recordset()
        self.assertEqual(self.customer.ebshel_followup_state, 'action')
        self.assertEqual(self.customer.ebshel_suggested_level_id, self.l2)
        self.assertAlmostEqual(self.customer.ebshel_overdue_total, 1000.0, 2)
        self.assertEqual(self.customer.ebshel_overdue_days, 30)
        found = self.env['res.partner'].search([('ebshel_followup_state', '=', 'action')])
        self.assertIn(self.customer, found)
        data = self.env['ebshel.followup.desk'].get_data({'state': ['action'], 'search': 'test clinic'})
        row = next(r for r in data['rows'] if r['id'] == self.customer.id)
        self.assertEqual(row['suggested']['id'], self.l2.id)
        self.assertGreaterEqual(data['kpis']['overdue'], 1000.0)
        self.assertTrue(any(b['value'] >= 1000.0 for b in data['buckets']))

    def test_sending_the_reminder_mails_the_statement_and_moves_the_customer_on(self):
        self._invoice(self.customer, 1000.0, self.today - timedelta(days=40), due=self.today - timedelta(days=30))
        result = self.customer.action_ebshel_send_reminder(note='Please call us')
        self.assertEqual(result['sent'], [self.customer.id], result)
        action = self.env['ebshel.followup.action'].search([('partner_id', '=', self.customer.id)], limit=1)
        self.assertEqual(action.kind, 'email')
        self.assertEqual(action.level_id, self.l2)
        mail = action.mail_id
        self.assertTrue(mail and mail.email_to == 'clinic@example.com')
        self.assertIn('1,000', mail.body_html.replace('\xa0', ' '))
        self.assertGreaterEqual(len(mail.attachment_ids), 1, "the statement (and invoices) ride along")
        self.assertEqual(self.customer.ebshel_followup_level_id, self.l2)
        self.assertEqual(self.customer.ebshel_followup_next_date, self.today + timedelta(days=24))
        self.customer.invalidate_recordset()
        self.assertEqual(self.customer.ebshel_followup_state, 'reminded')
        self.assertTrue(self.customer.activity_ids, "email_call schedules the call")
        # a promise to pay parks the customer until the day after
        self.customer.action_ebshel_promise(self.today + timedelta(days=5), amount=1000.0, note='cheque coming')
        self.customer.invalidate_recordset()
        self.assertEqual(self.customer.ebshel_followup_state, 'promised')
        self.customer.write({'ebshel_promise_date': self.today - timedelta(days=1), 'ebshel_followup_next_date': self.today})
        self.customer.invalidate_recordset()
        self.assertEqual(self.customer.ebshel_followup_state, 'action', "a broken promise brings the customer back")

    def test_the_final_level_prints_a_letter_and_puts_the_customer_on_hold(self):
        self._invoice(self.customer, 500.0, self.today - timedelta(days=90), due=self.today - timedelta(days=60))
        result = self.customer.action_ebshel_send_reminder()
        self.assertEqual(result['letters'], [self.customer.id])
        self.assertTrue(self.customer.ebshel_on_hold)
        self.assertEqual(self.customer.ebshel_followup_next_date, self.today + timedelta(days=15))
        pdf, kind = self.env['ir.actions.report'].with_context(force_report_rendering=True)._render_qweb_pdf(
            'ebshel_account_advanced.action_report_followup_letter', res_ids=[self.customer.id])
        self.assertEqual(kind, 'pdf')
        self.assertTrue(pdf.startswith(b'%PDF'))

    def test_the_nightly_job_sends_only_automatic_levels(self):
        other = self.env['res.partner'].create({'name': 'Auto Clinic', 'email': 'auto@example.com', 'is_company': True})
        self._invoice(other, 800.0, self.today - timedelta(days=40), due=self.today - timedelta(days=30))       # level 2, automatic
        self._invoice(self.customer, 700.0, self.today - timedelta(days=20), due=self.today - timedelta(days=10))  # level 1, manual
        self.env['ebshel.followup.desk']._cron_followups()
        self.assertTrue(other.ebshel_followup_action_ids.filtered(lambda a: a.kind == 'email'))
        self.assertFalse(self.customer.ebshel_followup_action_ids)

    def test_excluded_customers_and_small_amounts_are_left_alone(self):
        self._invoice(self.customer, 1000.0, self.today - timedelta(days=40), due=self.today - timedelta(days=30))
        self.customer.action_ebshel_set_exclusion(True, note='disputed')
        self.customer.invalidate_recordset()
        self.assertEqual(self.customer.ebshel_followup_state, 'excluded')
        self.customer.action_ebshel_set_exclusion(False)
        self.company.ebshel_followup_min_amount = 5000.0
        self.customer.invalidate_recordset()
        self.assertEqual(self.customer.ebshel_followup_state, 'overdue')

    def test_days_to_pay_come_from_the_reconciled_invoices(self):
        inv = self._invoice(self.customer, 300.0, self.today - timedelta(days=40), due=self.today - timedelta(days=10))
        refund = self._invoice(self.customer, 300.0, self.today - timedelta(days=5), kind='out_refund')
        (self._ar(inv) + self._ar(refund)).reconcile()
        habits = self.env['res.partner']._ebshel_pay_habits([self.customer.id])
        self.assertAlmostEqual(habits[self.customer.id]['to_pay'], 35.0, 1)
        self.assertAlmostEqual(habits[self.customer.id]['late'], 5.0, 1)
        detail = self.env['ebshel.followup.desk'].partner_detail(self.customer.id)
        self.assertEqual(detail['habit']['to_pay'], 35.0)

    def test_the_wizard_sends_from_the_customer_list(self):
        self._invoice(self.customer, 1000.0, self.today - timedelta(days=40), due=self.today - timedelta(days=30))
        wizard = self.env['ebshel.followup.remind'].with_context(active_model='res.partner', active_ids=self.customer.ids).create({})
        self.assertEqual(wizard.partner_ids, self.customer)
        action = wizard.action_send()
        self.assertEqual(action['tag'], 'display_notification')
        self.assertEqual(self.customer.ebshel_followup_level_id, self.l2)

    # ------------------------------------------------------------------ budgets
    def _budget(self, planned=12000.0):
        fy = self.company.compute_fiscalyear_dates(self.today)
        return self.env['ebshel.budget'].create({
            'name': 'Test budget', 'date_from': fy['date_from'], 'date_to': fy['date_to'], 'kind': 'expense',
            'line_ids': [(0, 0, {'name': 'Consumables', 'kind': 'expense', 'account_ids': [(6, 0, [self.expense.id])], 'planned': planned})]})

    def test_a_budget_line_reads_its_actual_and_committed_from_the_ledger(self):
        budget = self._budget()
        budget.action_confirm()
        line = budget.line_ids
        self.assertEqual(len(line.phase_ids), 12)
        self.assertAlmostEqual(sum(line.phase_ids.mapped('planned')), 12000.0, 2)
        self._invoice(self.vendor, 2500.0, self.today, kind='in_invoice')
        self._invoice(self.vendor, 500.0, self.today, kind='in_invoice', post=False)
        line.invalidate_recordset()
        self.assertAlmostEqual(line.actual, 2500.0, 2)
        self.assertAlmostEqual(line.committed, 500.0, 2)
        self.assertAlmostEqual(line.available, 9000.0, 2)
        self.assertGreater(line.theoretical, 0.0)
        self.assertLessEqual(line.theoretical, 12000.0)
        self.assertAlmostEqual(budget.actual_total, 2500.0, 2)
        board = self.env['ebshel.budget.board'].get_data(budget.id)
        self.assertAlmostEqual(board['kpis']['actual'], 2500.0, 2)
        self.assertEqual(len(board['months']), 12)
        self.assertAlmostEqual(sum(m['actual'] for m in board['months']), 2500.0, 2)
        report = self.env['ebshel.fin.report'].by_key('budget_vs_actual')
        data = report.get_report_data({'date': {'preset': 'custom', 'from': str(budget.date_from), 'to': str(budget.date_to)}, 'unfold_all': True})
        row = next(l for l in data['lines'] if l.get('res_id') == line.id)
        self.assertAlmostEqual(row['columns'][1]['value'], 2500.0, 2)
        domain, _name = report._handler().drill(report, data['options'], data['columns'], row['id'], 'actual')
        self.assertEqual(self.env['account.move.line'].search_count(domain), 1)

    def test_a_revenue_line_counts_credits_as_positive(self):
        budget = self._budget()
        budget.line_ids.write({'kind': 'revenue', 'account_ids': [(6, 0, [self.income.id])], 'planned': 10000.0})
        self._invoice(self.customer, 4000.0, self.today)
        budget.line_ids.invalidate_recordset()
        self.assertAlmostEqual(budget.line_ids.actual, 4000.0, 2)

    def test_a_budget_over_its_planned_amount_raises_the_alert(self):
        budget = self._budget(planned=1000.0)
        budget.action_confirm()
        self._invoice(self.vendor, 1500.0, self.today, kind='in_invoice')
        budget.line_ids.invalidate_recordset()
        self.assertEqual(budget.line_ids.alert, 'over')
        self.assertEqual(budget.alert, 'over')
        self.assertTrue(self.env['ebshel.budget.board'].get_data(budget.id)['alerts'])

    # ------------------------------------------------------------------ deferrals
    def test_an_invoice_line_with_dates_starts_a_deferral(self):
        start = self.today.replace(day=1) - timedelta(days=1)
        start = start.replace(day=1) - timedelta(days=1)
        start = start.replace(day=1)                                   # first day, two months back
        end = start + timedelta(days=364)
        inv = self._invoice(self.customer, 1200.0, self.today, extra={'ebshel_deferral_start': start, 'ebshel_deferral_end': end})
        deferral = inv.ebshel_deferral_ids
        self.assertEqual(len(deferral), 1)
        self.assertEqual(deferral.state, 'running')
        self.assertEqual(deferral.kind, 'revenue')
        self.assertAlmostEqual(deferral.total, 1200.0, 2)
        self.assertEqual(len(deferral.line_ids), 12)
        self.assertAlmostEqual(sum(deferral.line_ids.mapped('amount')), 1200.0, 2)
        reclass = deferral.reclass_move_id
        self.assertEqual(reclass.state, 'posted')
        self.assertEqual(reclass.date, inv.date)
        self.assertAlmostEqual(reclass.line_ids.filtered(lambda l: l.account_id == self.income).debit, 1200.0, 2)
        self.assertAlmostEqual(reclass.line_ids.filtered(lambda l: l.account_id == self.deferred).credit, 1200.0, 2)
        due = deferral.line_ids.filtered(lambda l: l.date <= self.today)
        self.assertTrue(due)
        self.assertTrue(all(l.state == 'posted' for l in due), "what is already due is recognised at once")
        self.assertTrue(all(l.state == 'draft' for l in deferral.line_ids - due))
        self.assertAlmostEqual(deferral.recognised, sum(due.mapped('amount')), 2)
        first = due[0].move_id
        self.assertAlmostEqual(first.line_ids.filtered(lambda l: l.account_id == self.deferred).debit, due[0].amount, 2)
        self.assertAlmostEqual(first.line_ids.filtered(lambda l: l.account_id == self.income).credit, due[0].amount, 2)
        # the balance sheet holds exactly what is not yet recognised
        self.env.flush_all()
        balance = sum(self.env['account.move.line'].search([('account_id', '=', self.deferred.id), ('move_id.ebshel_deferral_id', '=', deferral.id),
                                                            ('parent_state', '=', 'posted')]).mapped('balance'))
        self.assertAlmostEqual(-balance, deferral.remaining, 2)
        with self.assertRaises(UserError):
            inv.button_draft()
        report = self.env['ebshel.fin.report'].by_key('deferral_schedule')
        data = report.get_report_data({'date': {'preset': 'custom', 'from': str(start), 'to': str(end)}, 'unfold_all': True, 'posted_only': False})
        row = next(l for l in data['lines'] if l.get('res_id') == deferral.id)
        self.assertAlmostEqual(row['columns'][0]['value'], 1200.0, 2)
        self.assertAlmostEqual(row['columns'][2]['value'], 1200.0, 2, "the whole schedule falls in the period")
        deferral.action_cancel()
        self.assertEqual(deferral.state, 'cancelled')
        self.env.flush_all()
        balance = sum(self.env['account.move.line'].search([('account_id', '=', self.deferred.id), ('parent_state', '=', 'posted'),
                                                            ('move_id.ebshel_deferral_id', '=', deferral.id)]).mapped('balance'))
        self.assertAlmostEqual(balance, 0.0, 2, "reversed to nothing")

    def test_a_vendor_bill_defers_an_expense_forward(self):
        start = self.today + timedelta(days=30)
        bill = self._invoice(self.vendor, 600.0, self.today, kind='in_invoice',
                             extra={'ebshel_deferral_start': start, 'ebshel_deferral_end': start + timedelta(days=180)})
        deferral = bill.ebshel_deferral_ids
        self.assertEqual(deferral.kind, 'expense')
        self.assertEqual(deferral.state, 'running')
        self.assertFalse(deferral.line_ids.filtered(lambda l: l.state == 'posted'), "nothing is due yet")
        reclass = deferral.reclass_move_id
        self.assertAlmostEqual(reclass.line_ids.filtered(lambda l: l.account_id == self.prepaid).debit, 600.0, 2)
        self.assertAlmostEqual(reclass.line_ids.filtered(lambda l: l.account_id == self.expense).credit, 600.0, 2)
        deferral.line_ids[0].action_post()
        self.assertEqual(deferral.line_ids[0].state, 'posted')
        self.env['ebshel.deferral']._cron_post_due()
        self.assertEqual(len(deferral.line_ids.filtered(lambda l: l.state == 'posted')), 1, "the cron posts only what is due")

    def test_equal_months_split_evenly(self):
        self.company.ebshel_deferral_method = 'months'
        d = self.env['ebshel.deferral'].create({'name': 'Manual', 'kind': 'revenue', 'date_from': '2027-01-01', 'date_to': '2027-12-31',
                                                'total': 1200.0, 'pl_account_id': self.income.id, 'method': 'months'})
        d.build_schedule()
        self.assertEqual(len(d.line_ids), 12)
        self.assertTrue(all(abs(l.amount - 100.0) < 0.01 for l in d.line_ids))

    # ------------------------------------------------------------------ matching
    def test_the_desk_pairs_a_refund_quoting_the_invoice(self):
        inv = self._invoice(self.customer, 1000.0, self.today - timedelta(days=20), due=self.today - timedelta(days=5))
        refund = self._invoice(self.customer, 1000.0, self.today, kind='out_refund')
        refund.ref = 'Credit against %s' % inv.name
        data = self.env['ebshel.match.desk'].get_data({'kind': 'receivable', 'partner_id': self.customer.id})
        self.assertEqual(len(data['pairs']), 1)
        pair = data['pairs'][0]
        self.assertEqual(pair['confidence'], 98)
        self.assertEqual(set(pair['aml_ids']), set((self._ar(inv) + self._ar(refund)).ids))
        result = self.env['ebshel.match.desk'].apply(data['pairs'])
        self.assertEqual(result['done'], 1)
        self.assertEqual(result['full'], 1)
        self.assertTrue(self._ar(inv).reconciled and self._ar(refund).reconciled)

    def test_several_invoices_that_add_up_are_found(self):
        a = self._invoice(self.customer, 400.0, self.today - timedelta(days=30), due=self.today - timedelta(days=20))
        b = self._invoice(self.customer, 600.0, self.today - timedelta(days=25), due=self.today - timedelta(days=15))
        refund = self._invoice(self.customer, 1000.0, self.today, kind='out_refund')
        data = self.env['ebshel.match.desk'].get_data({'kind': 'receivable', 'partner_id': self.customer.id})
        pair = data['pairs'][0]
        self.assertEqual(pair['confidence'], 80)
        self.assertEqual(set(pair['aml_ids']), set((self._ar(a) + self._ar(b) + self._ar(refund)).ids))
        self.env['ebshel.match.desk'].auto_apply('receivable', 80)
        self.assertTrue(self._ar(a).reconciled and self._ar(b).reconciled)

    def test_a_bank_line_is_matched_to_the_invoice_it_paid(self):
        inv = self._invoice(self.customer, 750.0, self.today - timedelta(days=20), due=self.today - timedelta(days=5))
        st = self.env['account.bank.statement.line'].create({'journal_id': self.bank.id, 'date': self.today, 'payment_ref': 'transfer',
                                                             'amount': 750.0, 'partner_id': self.customer.id})
        data = self.env['ebshel.match.desk'].get_data({'kind': 'bank', 'journal_id': self.bank.id})
        row = next(r for r in data['rows'] if r['id'] == st.id)
        self.assertEqual(row['best']['id'], self._ar(inv).id)
        self.assertGreaterEqual(row['best']['confidence'], 90)
        result = self.env['ebshel.match.desk'].match_bank(st.id, [self._ar(inv).id])
        self.assertTrue(result['reconciled'])
        self.assertTrue(self._ar(inv).reconciled)
        self.assertTrue(st.is_reconciled)
        self.assertIn(inv.payment_state, ('paid', 'in_payment'))
        liquidity, suspense, other = st._seek_for_lines()
        self.assertFalse(suspense)
        self.assertEqual(other.account_id, self.receivable)

    def test_a_partial_bank_line_leaves_the_rest_on_suspense(self):
        inv = self._invoice(self.customer, 1000.0, self.today - timedelta(days=20), due=self.today - timedelta(days=5))
        st = self.env['account.bank.statement.line'].create({'journal_id': self.bank.id, 'date': self.today, 'payment_ref': 'part',
                                                             'amount': 1300.0, 'partner_id': self.customer.id})
        result = self.env['ebshel.match.desk'].match_bank(st.id, [self._ar(inv).id])
        self.assertFalse(result['reconciled'])
        self.assertAlmostEqual(result['left'], 300.0, 2)
        self.assertTrue(self._ar(inv).reconciled)
        liquidity, suspense, other = st._seek_for_lines()
        self.assertAlmostEqual(-sum(suspense.mapped('balance')), 300.0, 2)

    # ------------------------------------------------------------------ close
    def test_the_close_checklist_counts_and_locks(self):
        period = self.env['ebshel.close.period'].create({'name': 'Far future', 'date_from': '2031-01-01', 'date_to': '2031-01-31'})
        self.assertTrue(period.task_ids, "the templates make the checklist")
        draft = self.env['account.move'].create({'move_type': 'entry', 'journal_id': self.general.id, 'date': '2031-01-15',
                                                 'line_ids': [(0, 0, {'account_id': self.expense.id, 'debit': 10.0}),
                                                              (0, 0, {'account_id': self.income.id, 'credit': 10.0})]})
        period.action_refresh()
        task = period.task_ids.filtered(lambda t: t.check_key == 'draft_entries')
        self.assertEqual(task.count, 1)
        self.assertEqual(task.state, 'todo')
        self.assertIn(draft.id, task.res_ids)
        self.assertEqual(task.action_open()['domain'], [('id', 'in', [draft.id])])
        with self.assertRaises(UserError):
            period.action_close()
        draft.action_post()
        period.action_refresh()
        self.assertEqual(task.count, 0)
        self.assertEqual(task.state, 'done', "a check ticks itself once it finds nothing")
        for t in period.task_ids.filtered(lambda t: t.state == 'todo'):
            t.action_skip()
        self.assertAlmostEqual(period.progress_pct, 100.0, 1)
        period.action_close()
        self.assertEqual(period.state, 'closed')
        self.assertEqual(self.company.fiscalyear_lock_date, fields.Date.to_date('2031-01-31'))
        cockpit = self.env['ebshel.close.cockpit'].get_data(period.id)
        self.assertEqual(cockpit['period']['state'], 'closed')
        self.assertEqual(cockpit['locks']['fiscal'], '2031-01-31')
        period.action_reopen()
        self.assertEqual(period.state, 'open')

    # ------------------------------------------------------------------ cash
    def test_the_forecast_puts_an_invoice_in_the_week_it_is_due(self):
        Forecast = self.env['ebshel.cash.forecast']
        before = Forecast.get_data({'collection_rate': 100, 'delay_days': 0})
        due = self.today + timedelta(days=10)
        self._invoice(self.customer, 5000.0, self.today, due=due)
        item = self.env['ebshel.cash.item'].create({'name': 'Payroll', 'kind': 'out', 'amount': 700.0, 'date': self.today, 'frequency': 'monthly'})
        after = Forecast.get_data({'collection_rate': 100, 'delay_days': 0})
        monday = self.today - timedelta(days=self.today.weekday())
        idx = (due - monday).days // 7
        self.assertAlmostEqual(after['weeks'][idx]['in_ar'] - before['weeks'][idx]['in_ar'], 5000.0, 2)
        self.assertAlmostEqual(sum(w['out_items'] for w in after['weeks']) - sum(w['out_items'] for w in before['weeks']), 2100.0, 2)
        running = after['kpis']['opening']
        for w in after['weeks']:
            running += w['net']
            self.assertAlmostEqual(w['closing'], running, 2)
        self.assertAlmostEqual(after['kpis']['closing'], running, 2)
        half = Forecast.get_data({'collection_rate': 50, 'delay_days': 0})
        self.assertAlmostEqual(half['weeks'][idx]['in_ar'], after['weeks'][idx]['in_ar'] / 2, 2)
        self.assertEqual(len(item._occurrences(self.today, self.today + timedelta(days=90))), 3)
        Forecast.delete_item(item.id)
        self.assertFalse(item.exists())

    # ------------------------------------------------------------------ scanner
    def test_the_scanner_finds_duplicate_bills_and_remembers_what_was_resolved(self):
        for _i in range(2):
            self._invoice(self.vendor, 999.0, self.today, kind='in_invoice', extra={}).write({'ref': 'INV-DUP-1'})
        bills = self.env['account.move'].search([('ref', '=', 'INV-DUP-1'), ('partner_id', '=', self.vendor.id)])
        self.assertEqual(len(bills), 2)
        self.env.flush_all()
        Scanner = self.env['ebshel.ledger.scanner']
        Scanner.scan(keys=['duplicate_bills'])
        finding = self.env['ebshel.ledger.finding'].search([('key', '=', 'duplicate_bills'), ('state', '=', 'open')]).filtered(
            lambda f: set(f.res_ids) == set(bills.ids))
        self.assertEqual(len(finding), 1)
        self.assertEqual(finding.severity, 'error')
        self.assertEqual(finding.count, 2)
        data = Scanner.get_data()
        self.assertLess(data['score'], 100)
        self.assertTrue(any(f['id'] == finding.id for f in data['findings']))
        finding.action_resolve()
        Scanner.scan(keys=['duplicate_bills'])
        self.assertEqual(finding.state, 'open', "the evidence is still there, so it comes back as the same finding")
        finding.action_ignore()
        Scanner.scan(keys=['duplicate_bills'])
        self.assertEqual(finding.state, 'ignored')
        bills[1].button_draft()
        self.env.flush_all()
        Scanner.scan(keys=['duplicate_bills'])
        self.assertEqual(finding.state, 'ignored', "ignored stays ignored")
        # a finding whose evidence vanished resolves itself
        finding.action_reopen()
        Scanner.scan(keys=['duplicate_bills'])
        self.assertEqual(finding.state, 'resolved')

    def test_the_scanner_flags_drafts_before_the_lock_date(self):
        draft = self.env['account.move'].create({'move_type': 'entry', 'journal_id': self.general.id, 'date': '2026-01-15',
                                                 'line_ids': [(0, 0, {'account_id': self.expense.id, 'debit': 10.0}),
                                                              (0, 0, {'account_id': self.income.id, 'credit': 10.0})]})
        self.company.fiscalyear_lock_date = '2026-01-31'
        self.env.flush_all()
        self.env['ebshel.ledger.scanner'].scan(keys=['locked_drafts'])
        finding = self.env['ebshel.ledger.finding'].search([('key', '=', 'locked_drafts'), ('state', '=', 'open')], limit=1)
        self.assertTrue(finding)
        self.assertIn(draft.id, finding.res_ids)

    def test_readers_can_look_but_not_act(self):
        reader = self.env['res.users'].create({'name': 'Reader', 'login': 'eaa_reader', 'email': 'reader@example.com',
                                               'group_ids': [(6, 0, [self.env.ref('base.group_user').id, self.env.ref('account.group_account_readonly').id])]})
        for model in ('ebshel.followup.desk', 'ebshel.match.desk', 'ebshel.close.cockpit', 'ebshel.cash.forecast'):
            self.env[model].with_user(reader).get_data()
        self.env['ebshel.ledger.scanner'].with_user(reader).get_data()
        self.env['ebshel.budget.board'].with_user(reader).get_data()
        from odoo.exceptions import AccessError
        with self.assertRaises(AccessError):
            self.env['ebshel.match.desk'].with_user(reader).apply([])
        # an accounting Administrator holds only the billing group by implication in
        # Odoo 19 (not read-only, not bookkeeper) - every desk must still open and act
        admin = self.env['res.users'].create({'name': 'Acc Admin', 'login': 'eaa_admin', 'email': 'admin@example.com',
                                              'group_ids': [(6, 0, [self.env.ref('base.group_user').id, self.env.ref('account.group_account_manager').id])]})
        for model in ('ebshel.followup.desk', 'ebshel.match.desk', 'ebshel.close.cockpit', 'ebshel.cash.forecast',
                      'ebshel.ledger.scanner', 'ebshel.budget.board'):
            self.env[model].with_user(admin).get_data()
        self.env['ebshel.match.desk'].with_user(admin).apply([])
        self.env['ebshel.budget'].with_user(admin).search([])
        self.env['ebshel.deferral'].with_user(admin).search([])
        with self.assertRaises(AccessError):
            self.env['ebshel.ledger.scanner'].with_user(reader).scan()
