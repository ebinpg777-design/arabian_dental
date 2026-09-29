# -*- coding: utf-8 -*-
"""The bank, the cockpit and the entry studio against a small known ledger."""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestBankCockpit(TransactionCase):

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
        cls.charges = account('ZZ6020', 'Test bank charges', 'expense')
        cls.receivable = Account.search([('account_type', '=', 'asset_receivable')], limit=1)
        cls.general = cls.env['account.journal'].search([('type', '=', 'general'), ('company_id', '=', cls.company.id)], limit=1)
        cls.bank = cls.env['account.journal'].create({'name': 'EAB Test Bank', 'type': 'bank', 'code': 'EABK'})
        cls.customer = cls.env['res.partner'].create({'name': 'Bank Test Clinic', 'is_company': True, 'property_payment_term_id': False})
        cls.Desk = cls.env['ebshel.bank.desk']

    def _invoice(self, amount, date=None, due=None, partner=None):
        move = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': (partner or self.customer).id, 'invoice_date': date or self.today,
            'invoice_payment_term_id': False, 'invoice_date_due': due or date or self.today,
            'invoice_line_ids': [(0, 0, {'name': 'Service', 'quantity': 1, 'price_unit': amount, 'account_id': self.income.id, 'tax_ids': [(6, 0, [])]})]})
        move.action_post()
        return move

    def _ar(self, move):
        return move.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable')

    def _receipt(self, amount, date=None, label='Receipt'):
        """Money booked straight to the bank account, the way a lab without statements does it."""
        move = self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': self.bank.id, 'date': date or self.today, 'ref': label,
            'line_ids': [(0, 0, {'account_id': self.bank.default_account_id.id, 'debit': amount, 'name': label}),
                         (0, 0, {'account_id': self.receivable.id, 'partner_id': self.customer.id, 'credit': amount, 'name': label})]})
        move.action_post()
        return move.line_ids.filtered(lambda l: l.account_id == self.bank.default_account_id)

    def _st(self, amount, label='transfer', date=None, partner=None):
        return self.env['account.bank.statement.line'].create({
            'journal_id': self.bank.id, 'date': date or self.today, 'payment_ref': label, 'amount': amount,
            'partner_id': partner.id if partner else False})

    # ------------------------------------------------------------ statement lines
    def test_a_bank_line_matched_to_an_invoice_pays_it(self):
        inv = self._invoice(900.0, date=self.today - timedelta(days=10))
        st = self._st(900.0, 'NEFT ' + inv.name, partner=self.customer)
        line = self.Desk.get_line(st.id)
        self.assertTrue(line['candidates'])
        best = line['candidates'][0]
        self.assertEqual((best['kind'], best['id']), ('open', self._ar(inv).id))
        res = self.Desk.reconcile(st.id, [{'kind': 'open', 'aml_id': self._ar(inv).id}])
        self.assertTrue(res['reconciled'])
        self.assertTrue(self._ar(inv).reconciled)
        self.assertIn(inv.payment_state, ('paid', 'in_payment'))
        rows = self.Desk.get_lines(self.bank.id, state='done')['rows']
        self.assertEqual(rows[0]['id'], st.id)
        self.assertIn(inv.name, rows[0]['matched'][0]['with'])
        self.Desk.undo(st.id)
        self.assertFalse(self._ar(inv).reconciled)
        self.assertFalse(st.is_reconciled)

    def test_a_charge_is_written_off_to_an_account_and_taught_as_a_rule(self):
        st = self._st(-35.0, 'SMS ALERT CHARGES SEP')
        res = self.Desk.reconcile(st.id, [{'kind': 'account', 'account_id': self.charges.id, 'amount': 35.0, 'label': 'SMS charges'}])
        self.assertTrue(res['reconciled'])
        other = st.move_id.line_ids.filtered(lambda l: l.account_id == self.charges)
        self.assertAlmostEqual(other.debit, 35.0, 2)
        words = self.Desk.suggest_keywords(st.id)
        self.assertIn('charges', words)
        rule = self.Desk.create_rule(st.id, self.charges.id, 'sms alert', label='SMS charges')
        st2 = self._st(-40.0, 'SMS ALERT CHARGES OCT')
        done = self.Desk.auto_reconcile(self.bank.id)
        self.assertEqual(done['rules'], 1)
        self.assertTrue(st2.is_reconciled)
        self.assertEqual(st2.ebshel_rule_id.id, rule['id'])
        self.assertEqual(st2.move_id.line_ids.filtered(lambda l: l.account_id == self.charges).name, 'SMS charges')
        self.assertEqual(self.env['ebshel.bank.rule'].browse(rule['id']).hits, 1)

    def test_a_partial_match_leaves_the_rest_on_suspense(self):
        inv = self._invoice(600.0)
        st = self._st(1000.0, 'part', partner=self.customer)
        res = self.Desk.reconcile(st.id, [{'kind': 'open', 'aml_id': self._ar(inv).id}])
        self.assertFalse(res['reconciled'])
        self.assertAlmostEqual(res['left'], 400.0, 2)
        self.assertTrue(self._ar(inv).reconciled)
        _l, suspense, _o = st._seek_for_lines()
        self.assertAlmostEqual(-sum(suspense.mapped('balance')), 400.0, 2)

    def test_money_already_in_the_books_is_ticked_not_booked_twice(self):
        booked = self._receipt(1250.0, date=self.today - timedelta(days=2), label='Cheque 4471')
        st = self._st(1250.0, 'CLG CHEQUE 4471', date=self.today)
        line = self.Desk.get_line(st.id)
        book = [c for c in line['candidates'] if c['kind'] == 'book']
        self.assertEqual(book[0]['id'], booked.id)
        self.Desk.already_booked(st.id, booked.id)
        self.assertFalse(st.exists())
        self.assertEqual(booked.ebshel_cleared_date, self.today)
        self.assertEqual(booked.ebshel_bank_ref, 'CLG CHEQUE 4471')

    def test_pasted_rows_tick_what_exists_and_create_the_rest(self):
        booked = self._receipt(500.0, date=self.today - timedelta(days=1), label='UPI 1')
        rows = [{'date': fields.Date.to_string(self.today), 'label': 'UPI 1', 'amount': 500.0},
                {'date': fields.Date.to_string(self.today), 'label': 'NEW VENDOR PAYMENT', 'amount': -220.0},
                {'date': 'not a date', 'label': 'junk', 'amount': 1.0}]
        res = self.Desk.import_rows(self.bank.id, rows)
        self.assertEqual((res['ticked'], res['created'], len(res['errors'])), (1, 1, 1))
        self.assertEqual(booked.ebshel_cleared_date, self.today)
        again = self.Desk.import_rows(self.bank.id, rows[:2])
        self.assertEqual((again['ticked'], again['created'], again['duplicates']), (0, 0, 2))

    # ------------------------------------------------------------ ticking and the statement
    def test_the_reconciliation_statement_explains_the_bank_balance(self):
        cleared = self._receipt(1000.0, date=self.today - timedelta(days=20), label='old')
        self.Desk.set_cleared([cleared.id], date=fields.Date.to_string(self.today - timedelta(days=19)))
        self._receipt(300.0, date=self.today - timedelta(days=1), label='deposit in transit')
        payment = self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': self.bank.id, 'date': self.today, 'ref': 'cheque out',
            'line_ids': [(0, 0, {'account_id': self.expense.id, 'debit': 120.0}),
                         (0, 0, {'account_id': self.bank.default_account_id.id, 'credit': 120.0})]})
        payment.action_post()
        brs = self.Desk.get_brs(self.bank.id, fields.Date.to_string(self.today))
        n = brs['numbers']
        self.assertAlmostEqual(n['book'], 1180.0, 2)
        self.assertAlmostEqual(n['deposits'], 300.0, 2)
        self.assertAlmostEqual(n['payments'], 120.0, 2)
        self.assertAlmostEqual(n['bank_expected'], 1000.0, 2)
        self.assertEqual(n['uncleared'], 2)
        self.assertEqual(len(brs['items']), 2)
        self.Desk.save_checkpoint(self.bank.id, fields.Date.to_string(self.today), 1000.0)
        brs = self.Desk.get_brs(self.bank.id, fields.Date.to_string(self.today))
        self.assertAlmostEqual(brs['checkpoint']['difference'], 0.0, 2)
        journals = {j['id']: j for j in self.Desk.get_journals()['journals']}
        self.assertAlmostEqual(journals[self.bank.id]['checkpoint']['difference'], 0.0, 2)
        pdf, kind = self.env['ir.actions.report'].with_context(force_report_rendering=True)._render_qweb_pdf(
            'ebshel_account_advanced.action_report_brs', res_ids=[self.bank.id],
            data={'date': fields.Date.to_string(self.today), 'journal_id': self.bank.id})
        self.assertEqual(kind, 'pdf')
        self.assertTrue(pdf.startswith(b'%PDF'))

    def test_a_baseline_marks_history_as_seen(self):
        old = self._receipt(700.0, date=self.today - timedelta(days=40), label='old 1')
        new = self._receipt(80.0, date=self.today, label='new')
        n = self.Desk.baseline(self.bank.id, fields.Date.to_string(self.today - timedelta(days=30)))
        self.assertEqual(n, 1)
        old.invalidate_recordset()
        self.assertEqual(old.ebshel_cleared_date, old.date)
        self.assertFalse(new.ebshel_cleared_date)
        self.Desk.set_cleared([old.id], date=False)
        self.assertFalse(old.ebshel_cleared_date)

    def test_readers_look_but_do_not_reconcile(self):
        reader = self.env['res.users'].create({'name': 'Bank Reader', 'login': 'eab_reader',
                                               'group_ids': [(6, 0, [self.env.ref('base.group_user').id, self.env.ref('account.group_account_readonly').id])]})
        self.Desk.with_user(reader).get_journals()
        with self.assertRaises(AccessError):
            self.Desk.with_user(reader).auto_reconcile(self.bank.id)
        admin = self.env['res.users'].create({'name': 'Bank Admin', 'login': 'eab_admin',
                                              'group_ids': [(6, 0, [self.env.ref('base.group_user').id, self.env.ref('account.group_account_manager').id])]})
        self.Desk.with_user(admin).get_journals()
        self.env['ebshel.finance.cockpit'].with_user(admin).get_data()
        self.env['ebshel.entry.studio'].with_user(admin).get_setup()

    # ------------------------------------------------------------ cockpit
    def test_the_cockpit_adds_up_and_balances(self):
        self._invoice(4000.0)
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.customer.id, 'invoice_date': self.today, 'invoice_payment_term_id': False,
            'invoice_line_ids': [(0, 0, {'name': 'Gloves', 'quantity': 1, 'price_unit': 1500.0, 'account_id': self.expense.id, 'tax_ids': [(6, 0, [])]})]})
        bill.action_post()
        data = self.env['ebshel.finance.cockpit'].get_data('month')
        kpis = {k['key']: k for k in data['kpis']}
        self.assertGreaterEqual(kpis['REV']['value'], 4000.0)
        wf = {w['key']: w['value'] for w in data['waterfall']}
        self.assertAlmostEqual(wf['REV'] + wf['COGS'] + wf['OPEX'] + wf['DEP'] + wf['OINC'] + wf['OEXP'], wf['NET'], 1)
        self.assertTrue(data['balanced'])
        self.assertEqual(len(data['months']), 12)
        self.assertTrue(any(r['key'] == 'gm' for r in data['ratios']))
        self.assertTrue(data['top_expenses'])
        values = [e['value'] for e in data['top_expenses']]
        self.assertEqual(values, sorted(values, reverse=True), "largest first")
        self._st(50.0, 'unmatched')
        todo = {t['key']: t for t in self.env['ebshel.finance.cockpit'].get_data('month')['todo']}
        self.assertGreaterEqual(todo['bank']['count'], 1)

    # ------------------------------------------------------------ entry studio
    def test_the_studio_checks_balances_posts_and_learns_templates(self):
        Studio = self.env['ebshel.entry.studio']
        setup = Studio.get_setup()
        self.assertTrue(setup['journals'])
        bad = Studio.check({'journal_id': self.general.id, 'date': fields.Date.to_string(self.today),
                            'lines': [{'account_id': self.expense.id, 'debit': 100.0}, {'account_id': self.income.id, 'credit': 90.0}]})
        self.assertTrue(any('differ' in p for p in bad['problems']))
        with self.assertRaises(UserError):
            Studio.create_entry({'journal_id': self.general.id, 'lines': [{'account_id': self.expense.id, 'debit': 100.0}]})
        vals = {'journal_id': self.general.id, 'date': fields.Date.to_string(self.today), 'ref': 'Studio test',
                'lines': [{'account_id': self.expense.id, 'debit': 100.0, 'label': 'rent'},
                          {'account_id': self.income.id, 'credit': 100.0, 'label': 'rent'}]}
        res = Studio.create_entry(vals, post=True)
        move = self.env['account.move'].browse(res['id'])
        self.assertEqual(move.state, 'posted')
        self.assertEqual(move.ref, 'Studio test')
        tpl = Studio.save_template('Rent split', vals)
        filled = Studio.load_template(tpl['id'], amount=2500.0)
        self.assertAlmostEqual(sum(l['debit'] for l in filled['lines']), 2500.0, 2)
        self.assertAlmostEqual(sum(l['credit'] for l in filled['lines']), 2500.0, 2)
        found = Studio.resolve_accounts([self.expense.code, 'no such account'])
        self.assertEqual(found[self.expense.code]['id'], self.expense.id)
        self.assertNotIn('no such account', found)
        hits = Studio.find('account.account', self.expense.code)
        self.assertIn(self.expense.id, [h['id'] for h in hits])

    def test_analytic_redistribution_rewrites_posted_items(self):
        plan = self.env['account.analytic.plan'].search([], limit=1) or self.env['account.analytic.plan'].create({'name': 'Test plan'})
        aa = self.env['account.analytic.account'].create({'name': 'Branch A', 'plan_id': plan.id})
        move = self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': self.general.id, 'date': self.today,
            'line_ids': [(0, 0, {'account_id': self.expense.id, 'debit': 300.0}), (0, 0, {'account_id': self.income.id, 'credit': 300.0})]})
        move.action_post()
        line = move.line_ids.filtered(lambda l: l.account_id == self.expense)
        wizard = self.env['ebshel.analytic.redistribute'].with_context(active_model='account.move.line', active_ids=line.ids).create({
            'mode': 'replace', 'analytic_distribution': {str(aa.id): 100.0}})
        self.assertEqual(wizard.count, 1)
        wizard.action_apply()
        self.assertEqual(line.analytic_distribution, {str(aa.id): 100.0})
        self.assertAlmostEqual(sum(line.analytic_line_ids.mapped('amount')), -300.0, 2)
        self.assertEqual(line.analytic_line_ids.mapped('account_id'), aa)
