# -*- coding: utf-8 -*-
"""Each test here holds a defect that was found and fixed, written as what must be
true instead - and each fails on the code before its fix."""
import base64
import io
import zipfile
from datetime import date, timedelta
from unittest.mock import patch


from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

from .test_speed import Statements
from ..models import handlers


@tagged('post_install', '-at_install')
class TestFinReportRegressions(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.write({'ebshel_fin_totals_last': False, 'ebshel_fin_negative': 'minus'})
        cls.Report = cls.env['ebshel.fin.report']
        cls.engine = cls.env['ebshel.fin.engine']
        cls.company = cls.env.company
        cls.today = fields.Date.context_today(cls.env.user)
        fy = cls.company.compute_fiscalyear_dates(cls.today)
        cls.fy_from = fy['date_from']
        cls.year = {'date': {'preset': 'custom', 'from': fields.Date.to_string(cls.fy_from),
                             'to': fields.Date.to_string(cls.today)}}
        cls.last_year_day = cls.fy_from - timedelta(days=40)
        Account = cls.env['account.account']
        mine = [('company_ids', 'in', cls.company.id)]
        cls.cash = Account.search([('account_type', '=', 'asset_cash')] + mine, limit=1)
        cls.earnings = Account.search([('account_type', '=', 'equity_unaffected')] + mine, limit=1)
        cls.expense = Account.create({'code': 'EFRR600', 'name': 'EFR review expense', 'account_type': 'expense'})
        cls.liability = Account.create({'code': 'EFRR200', 'name': 'EFR review liability', 'account_type': 'liability_current'})
        cls.journal = cls.env['account.journal'].search([('type', '=', 'general'), ('company_id', '=', cls.company.id)], limit=1)

    # ------------------------------------------------------------ helpers
    def _entry(self, day, debit_account, credit_account, amount, **line):
        move = self.env['account.move'].create({'move_type': 'entry', 'journal_id': self.journal.id, 'date': day, 'line_ids': [
            (0, 0, dict({'account_id': debit_account.id, 'debit': amount, 'name': 'review probe'}, **line)),
            (0, 0, {'account_id': credit_account.id, 'credit': amount, 'name': 'review probe'})]})
        move.action_post()
        return move

    def _data(self, key, options):
        return self.Report.by_key(key).get_report_data(options)

    def _row(self, data, lid):
        return next(l for l in data['lines'] if l['id'] == lid)

    def _cell(self, data, line, key):
        return line['columns'][[c['key'] for c in data['columns']].index(key)]['value'] or 0.0

    def _items(self, key, options, line_id, column_key, field='balance'):
        _engine, _options, domain, _title, _other = self.Report.by_key(key)._items_domain(options, line_id, column_key)
        return sum(self.env['account.move.line'].search(domain).mapped(field))

    def _user(self, login, *groups):
        return self.env['res.users'].create({
            'name': login, 'login': login,
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id] + [self.env.ref(g).id for g in groups])]})

    # ------------------------------------------------------------ the ledgers foot
    def test_earlier_years_on_the_earnings_account_are_undistributed(self):
        """The current year earnings account starts again every year, like the profit and loss
        accounts, but only those were carried to 'undistributed': an opening entry booked to
        it in an earlier year was on no line, and neither ledger footed."""
        self._entry(self.last_year_day, self.cash, self.earnings, 500.0)
        self._entry(self.last_year_day, self.expense, self.cash, 70.0)
        tb = self._data('trial_balance', self.year)
        ini_d, ini_c, d, c, end_d, end_c = [x['value'] or 0.0 for x in self._row(tb, 'total')['columns']]
        self.assertAlmostEqual(ini_d, ini_c, 2)
        self.assertAlmostEqual(end_d, end_c, 2)
        gl = self._data('general_ledger', self.year)
        total = self._row(gl, 'total')
        self.assertAlmostEqual(self._cell(gl, total, 'opening'), 0.0, 2)
        self.assertAlmostEqual(self._cell(gl, total, 'balance'), 0.0, 2)
        # the line and the items it opens are one set, with or without an account filter
        for options in (self.year, dict(self.year, accounts_query=self.earnings.code)):
            data = self._data('general_ledger', options)
            figure = self._cell(data, self._row(data, 'unalloc'), 'opening')
            self.assertAlmostEqual(self._items('general_ledger', options, 'unalloc', 'opening'), figure, 2,
                                   options.get('accounts_query'))
        narrowed = self._data('general_ledger', dict(self.year, accounts_query=self.earnings.code))
        self.assertAlmostEqual(self._cell(narrowed, self._row(narrowed, 'unalloc'), 'opening'), -500.0, 2)
        tb_narrow = dict(self.year, accounts_query=self.earnings.code)
        tb_data = self._data('trial_balance', tb_narrow)
        unalloc = self._row(tb_data, 'unalloc')['columns']
        self.assertAlmostEqual(self._items('trial_balance', tb_narrow, 'unalloc', 'ini_c'),
                               (unalloc[0]['value'] or 0.0) - (unalloc[1]['value'] or 0.0), 2)

    def test_the_cash_book_balance_opens_the_items_it_adds_up(self):
        """A cash account's balance is its opening and the period: it opened the period's items only."""
        self._entry(self.last_year_day, self.cash, self.liability, 250.0)
        self._entry(self.today, self.cash, self.liability, 40.0)
        data = self._data('cash_book', self.year)
        lid = 'ac:%d' % self.cash.id
        balance = self._cell(data, self._row(data, lid), 'balance')
        self.assertAlmostEqual(self._items('cash_book', self.year, lid, 'balance'), balance, 2)
        self.assertAlmostEqual(self._items('cash_book', self.year, lid, 'debit', 'debit'),
                               self._cell(data, self._row(data, lid), 'debit'), 2)

    def test_a_partners_balance_is_explained_from_before_the_period_too(self):
        """The balance of a partner opens every item up to the date; its breakdown read only the period's."""
        partner = self.env['res.partner'].create({'name': 'EFR review explained'})
        receivable = self.env['account.account'].search([('account_type', '=', 'asset_receivable'),
                                                         ('company_ids', 'in', self.company.id)], limit=1)
        self._entry(self.last_year_day, receivable, self.liability, 300.0, partner_id=partner.id)
        self._entry(self.today, receivable, self.liability, 45.0, partner_id=partner.id)
        report = self.Report.by_key('partner_ledger')
        data = report.get_report_data(self.year)
        lid = 'pa:%d' % partner.id
        balance = self._cell(data, self._row(data, lid), 'balance')
        self.assertAlmostEqual(balance, 345.0, 2)
        out = report.explain_cell(self.year, lid, 'balance')
        self.assertAlmostEqual(sum(a['value'] for a in out['accounts']), balance, 2)
        self.assertAlmostEqual(self._items('partner_ledger', self.year, lid, 'balance'), balance, 2)

    # ------------------------------------------------------------ statements
    def test_a_designed_line_can_choose_its_accounts_by_a_domain(self):
        """The account search box and the domain shared a name: the domain's query object was
        then asked .lower() - the whole report failed to open."""
        report = self.Report.create({
            'name': 'By domain', 'key': 'test_by_domain', 'kind': 'statement',
            'line_ids': [(0, 0, {'name': 'Income by domain', 'code': 'D', 'kind': 'sum', 'sign': '-1',
                                 'domain': "[('account_id.account_type', '=', 'income')]"}),
                         (0, 0, {'name': 'Income by type', 'code': 'T', 'kind': 'sum', 'sign': '-1',
                                 'account_types': 'income'})]})
        data = report.get_report_data(self.year)
        values = {l['code']: l['columns'][0]['value'] or 0.0 for l in data['lines']}
        self.assertTrue(values['T'], "the demo ledger has income")
        self.assertAlmostEqual(values['D'], values['T'], 2)

    def test_the_same_month_last_year_is_all_of_it(self):
        self.assertEqual(self.engine.shift_period(date(2025, 2, 1), date(2025, 2, 28), 'last_year', 1),
                         (date(2024, 2, 1), date(2024, 2, 29)))
        self.assertEqual(self.engine.shift_period(date(2024, 2, 1), date(2024, 2, 29), 'last_year', 1),
                         (date(2023, 2, 1), date(2023, 2, 28)))
        self.assertEqual(self.engine.shift_period(date(2025, 1, 1), date(2025, 3, 31), 'last_year', 1),
                         (date(2024, 1, 1), date(2024, 3, 31)))

    # ------------------------------------------------------------ analytic plans
    def _two_plans(self):
        Plan = self.env['account.analytic.plan']
        project_plan, other_plans = Plan._get_all_plans()
        other = other_plans[:1] or Plan.create({'name': 'EFR review departments'})
        Analytic = self.env['account.analytic.account']
        project = Analytic.create({'name': 'EFR review project', 'plan_id': project_plan.id})
        department = Analytic.create({'name': 'EFR review department', 'plan_id': other.id})
        # one share naming an account of each plan: a key "project,department"
        self._entry(self.today, self.expense, self.cash, 900.0,
                    analytic_distribution={'%d,%d' % (project.id, department.id): 100.0})
        return project, department

    def test_an_analytic_filter_finds_a_share_of_two_plans(self):
        project, department = self._two_plans()
        pl = self.Report.by_key('profit_loss')
        for account in (project, department):
            with self.subTest(account=account.name):
                options = dict(self.year, analytic=[account.id])
                data = pl.get_report_data(options)
                opex = next(l for l in data['lines'] if l.get('code') == 'OPEX')
                self.assertAlmostEqual(opex['columns'][0]['value'], 900.0, 2)
                action = pl.get_drill_action(options, opex['id'], 'p0')
                self.assertAlmostEqual(sum(self.env['account.move.line'].search(action['domain']).mapped('balance')), 900.0, 2)

    def test_the_analytic_report_reads_every_plan(self):
        """Odoo keeps an analytic line's account of each plan in a column of its own; only the
        project plan's was read, so a department never appeared."""
        project, department = self._two_plans()
        report = self.Report.by_key('analytic')
        data = report.get_report_data(self.year)
        for account in (project, department):
            with self.subTest(account=account.name):
                line = self._row(data, 'an:%d' % account.id)
                self.assertAlmostEqual(line['columns'][0]['value'], -900.0, 2)
                action = report.get_drill_action(self.year, line['id'], data['columns'][0]['key'])
                self.assertAlmostEqual(sum(self.env['account.analytic.line'].search(action['domain']).mapped('amount')),
                                       -900.0, 2)
                rows = report.expand_line(self.year, line['id'])['lines']
                self.assertEqual(len(rows), 1)

    def test_an_analytic_account_says_when_it_has_more_entries(self):
        project, _department = self._two_plans()
        self._entry(self.today, self.expense, self.cash, 100.0, analytic_distribution={str(project.id): 100.0})
        lid = 'an:%d' % project.id
        with patch.object(handlers, 'PAGE', 1):
            data = self._data('analytic', dict(self.year, expanded=[lid]))
        kids = [l for l in data['lines'] if l.get('parent_id') == lid]
        self.assertEqual([l['kind'] for l in kids], ['move_line', 'more'])
        self.assertEqual(kids[-1]['offset'], 1)

    # ------------------------------------------------------------ journal audit
    def test_the_journal_audit_goes_through_the_ledger_once(self):
        report = self.Report.by_key('journal')
        with Statements() as seen:
            data = report.get_report_data(self.year)
        self.assertEqual(len([q for q in seen.seen if 'FROM account_move_line l' in q]), 1)
        total = self._row(data, 'total')['columns']
        moves = self.env['account.move.line']._read_group(
            [('parent_state', '=', 'posted'), ('date', '>=', self.fy_from), ('date', '<=', self.today),
             ('company_id', 'in', self.env.companies.ids)], [], ['move_id:count_distinct', 'debit:sum'])[0]
        self.assertEqual(total[2]['value'], moves[0])
        self.assertAlmostEqual(total[0]['value'], moves[1], 2)

    def test_open_days_and_journals_are_read_together(self):
        """Unfolded, the day book read each day's entries in a query of its own (150 of them on a
        five-month ledger), the journal audit each journal's: now one query for all of them,
        saying what a query per heading said."""
        for key in ('day_book', 'journal'):
            with self.subTest(report=key):
                report = self.Report.by_key(key)
                with Statements() as seen:
                    together = report.get_report_data(dict(self.year, unfold_all=True))['lines']
                self.assertLessEqual(len([q for q in seen.seen if 'FROM account_move_line l' in q]), 2)
                heads = [l for l in together if l.get('unfoldable')]
                self.assertGreater(len(heads), 1, "enough open headings for them to be read together")
                for head in heads[:8]:
                    alone = [l for l in report.expand_line(self.year, head['id'])['lines'] if l.get('kind') != 'more']
                    block = [l for l in together if l.get('parent_id') == head['id'] and l.get('kind') != 'more']
                    self.assertTrue(block)
                    self.assertEqual([l['id'] for l in block], [l['id'] for l in alone])
                    self.assertEqual([[c['value'] for c in l['columns']] for l in block],
                                     [[c['value'] for c in l['columns']] for l in alone])

    def test_what_changed_is_read_in_one_pass(self):
        with Statements() as seen:
            self.Report.by_key('profit_loss').get_movers(self.year)
        self.assertEqual(len(seen.scans()), 1)

    # ------------------------------------------------------------ mail
    def test_a_statement_mail_writes_the_name_as_text(self):
        partner = self.env['res.partner'].create({'name': '<b>Bold</b> & Co', 'email': 'bold@example.com'})
        income = self.env['account.account'].search([('account_type', '=', 'income'), ('company_ids', 'in', self.company.id)], limit=1)
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': partner.id, 'invoice_date': self.today, 'invoice_payment_term_id': False,
            'invoice_line_ids': [(0, 0, {'name': 'Probe', 'quantity': 1, 'price_unit': 10.0, 'account_id': income.id,
                                         'tax_ids': [(6, 0, [])]})]})
        invoice.action_post()
        self.Report.by_key('customer_statement').action_send_statements(dict(self.year, partners=[partner.id]))
        mail = self.env['mail.mail'].search([('email_to', '=', 'bold@example.com')], order='id desc', limit=1)
        self.assertIn('&lt;b&gt;Bold&lt;/b&gt; &amp; Co', mail.body_html)
        self.assertNotIn('<b>Bold', mail.body_html)

    def test_an_accountant_can_email_the_statements(self):
        """Only Settings may create a mail.mail: the button failed for everyone but the administrator."""
        clerk = self._user('efr_review_sender', 'account.group_account_invoice')
        partner = self.env['res.partner'].create({'name': 'EFR review addressee', 'email': 'addressee@example.com'})
        income = self.env['account.account'].search([('account_type', '=', 'income'), ('company_ids', 'in', self.company.id)], limit=1)
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': partner.id, 'invoice_date': self.today, 'invoice_payment_term_id': False,
            'invoice_line_ids': [(0, 0, {'name': 'Probe', 'quantity': 1, 'price_unit': 12.0, 'account_id': income.id,
                                         'tax_ids': [(6, 0, [])]})]})
        invoice.action_post()
        result = self.Report.by_key('customer_statement').with_user(clerk).action_send_statements(
            dict(self.year, partners=[partner.id]))
        self.assertEqual(result, {'sent': 1, 'skipped': 0})
        mail = self.env['mail.mail'].search([('email_to', '=', 'addressee@example.com')], order='id desc', limit=1)
        self.assertEqual(len(mail.attachment_ids), 1)

    def _schedule(self, name, **vals):
        partner = self.env['res.partner'].create({'name': 'Reader %s' % name, 'email': '%s@example.com' % name})
        return self.env['ebshel.fin.report.schedule'].create(dict({
            'name': name, 'report_id': self.Report.by_key('profit_loss').id, 'interval': 'monthly', 'run_day': 1,
            'next_run': self.today, 'partner_ids': [(6, 0, [partner.id])], 'format': 'xlsx'}, **vals))

    def test_a_failing_schedule_does_not_hold_up_the_others(self):
        Schedule = self.env['ebshel.fin.report.schedule']
        bad, good = self._schedule('bad'), self._schedule('good')
        real = type(Schedule)._send

        def send(schedule):
            if schedule == bad:
                schedule.env.cr.execute("SELECT 1 / 0")
            return real(schedule)
        with patch.object(type(Schedule), '_send', send), \
                mute_logger('odoo.sql_db', 'odoo.addons.ebshel_account_reports.models.extras'):
            Schedule._cron_run()
        self.assertGreater(bad.next_run, self.today, "the failed one moves on too")
        self.assertEqual(bad.run_count, 0)
        self.assertEqual(good.run_count, 1)

    def test_braces_in_a_schedule_are_text(self):
        schedule = self._schedule('braces', subject='{report} {draft}', body='<p>For {period} {not a key}</p>')
        schedule.action_send_now()
        mail = self.env['mail.mail'].search([('email_to', '=', 'braces@example.com')], order='id desc', limit=1)
        self.assertEqual(mail.subject, 'Profit and Loss {draft}')
        self.assertIn('{not a key}', mail.body_html)
        self.assertNotIn('{period}', mail.body_html)

    # ------------------------------------------------------------ the workbook
    def test_an_ampersand_in_the_name_is_text_in_the_page_footer(self):
        """In a workbook's footer & starts a code: "R&D" printed the date in place of the D."""
        report = self.Report.by_key('profit_loss').copy({'name': 'R&D costs'})
        file = report.export_xlsx(self.year)
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(file['content']))) as archive:
            sheet = archive.read('xl/worksheets/sheet1.xml').decode()
        self.assertIn('R&amp;&amp;D costs', sheet)
        self.assertNotIn('R&amp;D costs', sheet.replace('R&amp;&amp;D', ''))

    # ------------------------------------------------------------ who may do what
    def test_an_invoicing_user_saves_a_view_of_their_own(self):
        """The viewer offers every reader 'Save this view'; the Invoicing role could only read views."""
        clerk = self._user('efr_review_clerk', 'account.group_account_invoice')
        report = self.Report.by_key('profit_loss')
        View = self.env['ebshel.fin.report.view'].with_user(clerk)
        vid = View.save_view(report.id, 'Mine', self.year, is_default=True)
        View.browse(vid).unlink()

    def test_a_shared_view_is_changed_only_by_its_owner(self):
        report = self.Report.by_key('profit_loss')
        owner = self._user('efr_review_owner', 'account.group_account_readonly')
        reader = self._user('efr_review_reader', 'account.group_account_readonly')
        clerk = self._user('efr_review_clerk2', 'account.group_account_invoice')
        vid = self.env['ebshel.fin.report.view'].with_user(owner).save_view(report.id, 'Shared', self.year, shared=True)
        for user in (reader, clerk):
            with self.subTest(user=user.login):
                view = self.env['ebshel.fin.report.view'].with_user(user).browse(vid)
                self.assertEqual(view.name, 'Shared', "a shared view is read by everyone")
                with self.assertRaises(AccessError):
                    view.write({'name': 'Taken over'})
                with self.assertRaises(AccessError):
                    view.unlink()
        self.env['ebshel.fin.report.view'].with_user(owner).browse(vid).write({'name': 'Still mine'})

    def test_a_note_is_removed_only_by_its_author(self):
        report = self.Report.by_key('profit_loss')
        author = self._user('efr_review_author', 'account.group_account_invoice')
        other = self._user('efr_review_other', 'account.group_account_invoice')
        Note = self.env['ebshel.fin.report.annotation']
        nid = Note.with_user(author).add_note(report.id, 'ln:1', 'mine')
        with self.assertRaises(AccessError):
            Note.with_user(other).browse(nid).unlink()
        with self.assertRaises(AccessError):
            Note.with_user(other).create({'report_id': report.id, 'line_key': 'ln:1', 'text': 'forged',
                                          'user_id': author.id})
        self.assertEqual(Note.with_user(other).browse(nid).text, 'mine', "still read by the team")
        self.assertEqual(report.with_user(other).get_annotations()['ln:1'][0]['text'], 'mine')
        Note.with_user(author).browse(nid).unlink()

    def test_the_partner_panel_offers_only_partners_the_reader_may_see(self):
        elsewhere = self.env['res.company'].create({'name': 'EFR review elsewhere'})
        hidden = self.env['res.partner'].create({'name': 'Zebrafinch Hidden', 'company_id': elsewhere.id})
        shown = self.env['res.partner'].create({'name': 'Zebrafinch Shown'})
        clerk = self._user('efr_review_clerk3', 'account.group_account_invoice')
        found = self.Report.by_key('partner_ledger').with_user(clerk).get_partner_suggestions(self.year, query='Zebrafinch')
        ids = {p['id'] for p in found}
        self.assertIn(shown.id, ids)
        self.assertNotIn(hidden.id, ids)
