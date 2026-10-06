# -*- coding: utf-8 -*-
"""Regressions found in the Odoo 20 review (2026-10-04): each test failed before its fix."""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestRegressions(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.write({'fiscalyear_lock_date': False, 'tax_lock_date': False, 'sale_lock_date': False,
                           'purchase_lock_date': False, 'ebshel_followup_min_amount': 0.0})
        cls.today = fields.Date.context_today(cls.env.user)
        Account = cls.env['account.account']
        cls.income = Account.search([('code', '=', 'ZZ7010')], limit=1) or Account.create(
            {'code': 'ZZ7010', 'name': 'Test income', 'account_type': 'income'})
        cls.bank = cls.env['account.journal'].create({'name': 'EAR Test Bank', 'type': 'bank', 'code': 'EARB'})
        Level = cls.env['ebshel.followup.level']
        Level.search([('company_id', '=', cls.company.id)]).write({'active': False})
        cls.l1 = Level.create({'name': 'R1 friendly', 'days': 7, 'action': 'email',
                               'attach_statement': False, 'attach_invoices': False})
        cls.l3 = Level.create({'name': 'R3 final', 'days': 45, 'action': 'letter', 'on_hold': True, 'repeat_days': 15})
        cls.customer = cls.env['res.partner'].create({'name': 'Regression Clinic', 'email': 'regression@example.com',
                                                      'property_payment_term_id': False})

    def _invoice(self, partner, amount, date, due=None):
        move = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': partner.id, 'invoice_date': date,
            'invoice_payment_term_id': False, 'invoice_date_due': due or date,
            'invoice_line_ids': [(0, 0, {'name': 'Service', 'quantity': 1, 'price_unit': amount,
                                         'account_id': self.income.id, 'tax_ids': [(6, 0, [])]})]})
        move.action_post()
        return move

    def _user(self, login, groups, companies=None):
        companies = companies or self.company
        return self.env['res.users'].create({
            'name': login, 'login': login, 'email': '%s@example.com' % login,
            'company_id': companies[:1].id, 'company_ids': [(6, 0, companies.ids)],
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id] + [self.env.ref(g).id for g in groups])]})

    # ------------------------------------------------------------------ the partner form
    def test_a_partner_being_created_does_not_reach_the_ledger(self):
        """The New Customer form computes the collection figures on a virtual record:
        its NewId went into the SQL and psycopg2 refused it (can't adapt type 'NewId')."""
        fresh = self.env['res.partner'].new({'name': 'Brand New Clinic'})
        fresh._compute_ebshel_followup()
        fresh._compute_ebshel_pay_habits()
        self.assertEqual(fresh.ebshel_due_total, 0.0)
        self.assertEqual(fresh.ebshel_followup_state, 'none')
        self.assertEqual(fresh.ebshel_avg_days_to_pay, 0.0)
        # ... and asking the ledger about a virtual id alone returns nothing, not everybody
        self.assertEqual(self.env['res.partner']._ebshel_followup_rows(self.company, self.today, [fresh.id]), [])
        self.assertEqual(self.env['res.partner']._ebshel_pay_habits([fresh.id]), {})

    def test_a_partner_being_edited_still_reads_its_own_ledger(self):
        self._invoice(self.customer, 400.0, self.today - timedelta(days=20), due=self.today - timedelta(days=10))
        onform = self.env['res.partner'].new({'name': self.customer.name}, origin=self.customer)
        onform._compute_ebshel_followup()
        self.assertAlmostEqual(onform.ebshel_overdue_total, 400.0, 2)
        self.assertEqual(onform.ebshel_suggested_level_id, self.l1)

    # ------------------------------------------------------------------ reminders
    def test_nothing_overdue_is_not_reminded(self):
        """A customer whose invoices are all still within their terms got a "past their due
        date" email with an empty table and a total of zero, and was moved to the level."""
        self._invoice(self.customer, 900.0, self.today, due=self.today + timedelta(days=30))
        result = self.customer.action_ebshel_send_reminder(level_id=self.l1.id)
        self.assertEqual(result['sent'], [])
        self.assertEqual([s['id'] for s in result['skipped']], [self.customer.id])
        self.assertFalse(self.customer.ebshel_followup_action_ids)
        self.assertFalse(self.customer.ebshel_followup_level_id)
        self.assertFalse(self.env['mail.mail'].search([('model', '=', 'res.partner'), ('res_id', '=', self.customer.id)]))

    def test_a_template_reminder_is_sent_once_to_the_address_chosen(self):
        """The shipped template adds its default recipients: the customer landed in
        recipient_ids AND email_to, and mail.mail sends those apart - every reminder twice.
        A customer without an address of its own (its accounts contact has one) got a
        recipient with no email, a failed send."""
        template = self.env.ref('ebshel_account_advanced.mail_template_reminder')
        self.l1.write({'template_id': template.id})
        self._invoice(self.customer, 600.0, self.today - timedelta(days=20), due=self.today - timedelta(days=10))
        self.customer.action_ebshel_send_reminder(level_id=self.l1.id)
        mail = self.customer.ebshel_followup_action_ids.mail_id
        self.assertEqual(mail.email_to, 'regression@example.com')
        self.assertFalse(mail.recipient_ids, "not a second copy to the same customer")
        company = self.env['res.partner'].create({'name': 'No Address Co', 'property_payment_term_id': False})
        self.env['res.partner'].create({'name': 'Accounts', 'parent_id': company.id, 'email': 'accounts@example.com'})
        self._invoice(company, 600.0, self.today - timedelta(days=20), due=self.today - timedelta(days=10))
        company.action_ebshel_send_reminder(level_id=self.l1.id)
        mail = company.ebshel_followup_action_ids.mail_id
        self.assertEqual(mail.email_to, 'accounts@example.com')
        self.assertFalse(mail.recipient_ids, "no recipient without an address")

    def test_a_contact_of_a_contact_is_part_of_the_family(self):
        """The ledger figures group by commercial partner (any depth); the open items, the
        reminder table and the desk read only the direct children. Invoices carry the
        commercial partner on their receivable line, so it shows on entries booked by hand."""
        person = self.env['res.partner'].create({'name': 'Accounts person', 'parent_id': self.customer.id})
        billing = self.env['res.partner'].create({'name': 'Billing address', 'parent_id': person.id, 'type': 'invoice'})
        self.assertEqual(billing.commercial_partner_id, self.customer)
        receivable = self.customer.property_account_receivable_id
        general = self.env['account.journal'].search([('type', '=', 'general'), ('company_id', '=', self.company.id)], limit=1)
        entry = self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': general.id, 'date': self.today - timedelta(days=20),
            'line_ids': [(0, 0, {'account_id': receivable.id, 'partner_id': billing.id, 'debit': 700.0, 'name': 'Opening balance',
                                 'date_maturity': self.today - timedelta(days=10)}),
                         (0, 0, {'account_id': self.income.id, 'credit': 700.0, 'name': 'Opening balance'})]})
        entry.action_post()
        line = entry.line_ids.filtered(lambda l: l.account_id == receivable)
        self.assertEqual(line.partner_id, billing)
        self.customer.invalidate_recordset()
        self.assertAlmostEqual(self.customer.ebshel_overdue_total, 700.0, 2, "the ledger figures count it")
        self.assertIn(line, self.customer._ebshel_open_items())
        detail = self.env['ebshel.followup.desk'].partner_detail(self.customer.id)
        self.assertIn(line.id, [i['id'] for i in detail['items']])
        result = self.customer.action_ebshel_send_reminder(level_id=self.l1.id)
        self.assertEqual(result['sent'], [self.customer.id], "the overdue invoice is found, so the reminder goes")

    def test_the_letters_of_a_sent_reminder_are_logged_once_at_the_level_sent(self):
        """The desk printed the letters of a reminder it had just sent through the letter
        action, which logged a second letter and moved the customer back to the level
        reached by days - undoing the level chosen in the dialog."""
        Level = self.env['ebshel.followup.level']
        while True:                                        # an id of two digits: browse('12') reads 1 and 2
            filler = Level.create({'name': 'filler', 'days': 400, 'active': False})
            if filler.id >= 10:
                break
        final = Level.create({'name': 'R4 final letter', 'days': 60, 'action': 'letter', 'repeat_days': 15})
        self._invoice(self.customer, 500.0, self.today - timedelta(days=20), due=self.today - timedelta(days=10))
        Desk = self.env['ebshel.followup.desk'].with_context(discard_logo_check=True)
        result = Desk.send(self.customer.ids, str(final.id))       # as the <select> hands it over
        self.assertEqual(result['letters'], [self.customer.id])
        self.assertEqual(self.customer.ebshel_followup_level_id, final)
        action = Desk.letter(result['letters'], final.id, False)
        self.assertEqual(action['type'], 'ir.actions.report')
        letters = self.customer.ebshel_followup_action_ids.filtered(lambda a: a.kind == 'letter')
        self.assertEqual(len(letters), 1, "the send logged it; printing it does not log it again")
        self.assertEqual(self.customer.ebshel_followup_level_id, final, "and does not move the customer back to R1")
        self.assertEqual(self.customer.ebshel_followup_next_date, self.today + timedelta(days=15))
        # the plain "print letters" button still logs
        Desk.letter(self.customer.ids)
        self.assertEqual(len(self.customer.ebshel_followup_action_ids.filtered(lambda a: a.kind == 'letter')), 2)

    def test_the_desk_reads_only_the_latest_action_of_each_customer(self):
        self._invoice(self.customer, 300.0, self.today - timedelta(days=20), due=self.today - timedelta(days=10))
        self.customer.action_ebshel_log_call('first call')
        self.customer.action_ebshel_log_call('second call')
        Action = self.env['ebshel.followup.action']
        older = Action.search([('partner_id', '=', self.customer.id), ('note', '=', 'first call')])
        older.date = fields.Datetime.now() + timedelta(hours=1)     # dated later, created earlier
        row = next(r for r in self.env['ebshel.followup.desk'].get_data({})['rows'] if r['id'] == self.customer.id)
        self.assertEqual(row['last_action']['note'], 'first call')

    # ------------------------------------------------------------------ the close and the scanner
    def test_a_payment_matched_at_the_bank_but_not_to_an_invoice_is_unreconciled(self):
        """Odoo 20 has no `in_process`/`posted` payment state: a posted payment is `paid`, and
        `reconciled` once its bank line is matched - while it may still settle no invoice."""
        pay = self.env['account.payment'].create({
            'payment_type': 'inbound', 'partner_type': 'customer', 'partner_id': self.customer.id, 'amount': 120.0,
            'journal_id': self.bank.id, 'date': self.today - timedelta(days=70)})
        pay.action_post()
        outstanding = pay.move_id.line_ids.filtered(lambda l: l.account_id == pay.outstanding_account_id)
        st = self.env['account.bank.statement.line'].create({'journal_id': self.bank.id, 'date': self.today - timedelta(days=69),
                                                             'payment_ref': 'deposit', 'amount': 120.0})
        self.env['ebshel.bank.desk'].reconcile(st.id, [{'kind': 'open', 'aml_id': outstanding.id}])
        pay.invalidate_recordset()
        self.assertEqual((pay.state, pay.is_reconciled), ('reconciled', False))
        period = self.env['ebshel.close.period'].create({'name': 'Payments', 'date_from': self.today - timedelta(days=90),
                                                         'date_to': self.today})
        _model, found, _amount = self.env['ebshel.close.checks']._check_payments_unmatched(period)
        self.assertIn(pay, found)
        self.env['ebshel.ledger.scanner'].scan(keys=['payments_unmatched'])
        finding = self.env['ebshel.ledger.finding'].search([('key', '=', 'payments_unmatched'), ('state', '=', 'open'),
                                                            ('company_id', '=', self.company.id)])
        self.assertIn(pay.id, finding.res_ids)

    def test_a_reader_opens_the_close_cockpit_on_an_open_period(self):
        """Opening the cockpit re-ran the checks, which write their counts on the tasks:
        an auditor (read-only) got an access error instead of the checklist."""
        period = self.env['ebshel.close.period'].create({'name': 'Open month', 'date_from': '2031-02-01', 'date_to': '2031-02-28'})
        reader = self._user('ear_reader', ['account.group_account_readonly'])
        self.env.invalidate_all()
        data = self.env['ebshel.close.cockpit'].with_user(reader).get_data(period.id)
        self.assertEqual(data['period']['id'], period.id)
        self.assertTrue(data['tasks'])
        self.assertFalse(data['can_write'])

    def test_actions_opened_from_the_boards_carry_their_views(self):
        """orm.call does not expand view_mode into views (a form button does): the web
        client's doAction dies on `action.views.map` without them."""
        period = self.env['ebshel.close.period'].create({'name': 'Views', 'date_from': '2031-03-01', 'date_to': '2031-03-31'})
        task = period.task_ids.filtered(lambda t: t.model)[:1]
        self.assertTrue(task)
        actions = [self.env['ebshel.close.cockpit'].open_task(task.id)]
        finding = self.env['ebshel.ledger.finding'].create({'key': 'old_drafts', 'name': 'x', 'model': 'account.move', 'res_ids': []})
        actions.append(self.env['ebshel.ledger.scanner'].open_finding(finding.id))
        budget = self.env['ebshel.budget'].create({'name': 'Views', 'date_from': '2031-01-01', 'date_to': '2031-12-31',
                                                   'line_ids': [(0, 0, {'name': 'L', 'account_ids': [(6, 0, self.income.ids)]})]})
        actions.append(budget.line_ids.action_view_actuals())
        for action in actions:
            self.assertTrue(action.get('views'), action)
            self.assertEqual([v[1] for v in action['views']], action['view_mode'].split(','))

    # ------------------------------------------------------------------ bank, deferrals, companies
    def test_two_equal_rows_in_one_paste_are_two_bank_lines(self):
        """Two equal transfers on one day are two rows of the statement: the second was taken
        for a duplicate of the first and dropped. A second paste is still all duplicates."""
        day = fields.Date.to_string(self.today)
        rows = [{'date': day, 'label': 'UPI CR SAME', 'amount': 250.0}, {'date': day, 'label': 'UPI CR SAME', 'amount': 250.0}]
        Desk = self.env['ebshel.bank.desk']
        first = Desk.import_rows(self.bank.id, rows, tick=False)
        self.assertEqual((first['created'], first['duplicates']), (2, 0))
        again = Desk.import_rows(self.bank.id, rows + [rows[0]], tick=False)
        self.assertEqual((again['created'], again['duplicates']), (1, 2), "only the third copy is new")

    def test_a_running_deferral_cannot_be_deleted(self):
        deferred = self.env['account.account'].create({'code': 'ZZ2411', 'name': 'Deferred', 'account_type': 'liability_current'})
        general = self.env['account.journal'].search([('type', '=', 'general'), ('company_id', '=', self.company.id)], limit=1)
        deferral = self.env['ebshel.deferral'].create({
            'name': 'Contract', 'kind': 'revenue', 'date_from': self.today, 'date_to': self.today + timedelta(days=89),
            'total': 900.0, 'pl_account_id': self.income.id, 'deferral_account_id': deferred.id, 'journal_id': general.id})
        deferral.action_run()
        self.assertEqual(deferral.state, 'running')
        self.assertGreaterEqual(deferral.move_count, 1)
        with self.assertRaises(UserError):
            deferral.unlink()
        draft = deferral.copy({'name': 'Draft one'})
        draft.unlink()
        self.assertFalse(draft.exists(), "a draft deferral has no entries and can go")

    def test_another_companys_records_stay_hidden(self):
        """Follow-up log, budget lines, close tasks and deferral months had no company rule."""
        other = self.env['res.company'].create({'name': 'EAR Other Company'})
        user = self._user('ear_accountant', ['account.group_account_user'])
        theirs = self.env['ebshel.followup.action'].create({'partner_id': self.customer.id, 'kind': 'call',
                                                            'note': 'their call', 'company_id': other.id})
        budget = self.env['ebshel.budget'].create({'name': 'Theirs', 'company_id': other.id, 'date_from': '2031-01-01',
                                                   'date_to': '2031-12-31', 'line_ids': [(0, 0, {'name': 'L', 'planned': 1.0})]})
        period = self.env['ebshel.close.period'].create({'name': 'Theirs', 'company_id': other.id,
                                                         'date_from': '2031-01-01', 'date_to': '2031-01-31'})
        their_income = self.env['account.account'].with_company(other).create(
            {'code': 'ZZ7019', 'name': 'Their income', 'account_type': 'income', 'company_ids': [(6, 0, other.ids)]})
        deferral = self.env['ebshel.deferral'].with_company(other).create({
            'name': 'Theirs', 'company_id': other.id, 'date_from': '2031-01-01', 'date_to': '2031-03-31', 'total': 300.0,
            'pl_account_id': their_income.id})
        deferral.build_schedule()
        self.assertTrue(deferral.line_ids)
        self.env.invalidate_all()
        as_user = self.env(user=user)
        self.assertFalse(as_user['ebshel.followup.action'].search([('id', '=', theirs.id)]))
        self.assertFalse(as_user['ebshel.budget.line'].search([('id', 'in', budget.line_ids.ids)]))
        self.assertFalse(as_user['ebshel.close.task'].search([('id', 'in', period.task_ids.ids)]))
        self.assertFalse(as_user['ebshel.deferral.line'].search([('id', 'in', deferral.line_ids.ids)]))
        with self.assertRaises(AccessError):
            as_user['ebshel.followup.action'].browse(theirs.id).read(['note'])
