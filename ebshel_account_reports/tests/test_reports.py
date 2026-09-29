# -*- coding: utf-8 -*-
"""The reports against the ledger they run on. Written as identities that must
hold on ANY ledger - a balance sheet balances, a trial balance foots, a
partner ledger agrees with the general ledger - and as deltas where a figure
is created by the test itself."""
import base64
from datetime import date, timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestFinReports(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Report = cls.env['ebshel.fin.report']
        cls.engine = cls.env['ebshel.fin.engine']
        cls.company = cls.env.company
        cls.today = fields.Date.context_today(cls.env.user)
        fy = cls.company.compute_fiscalyear_dates(cls.today)
        cls.fy_from, cls.fy_to = fy['date_from'], fy['date_to']
        cls.year = {'date': {'preset': 'custom', 'from': fields.Date.to_string(cls.fy_from),
                             'to': fields.Date.to_string(cls.today)}}
        cls.asof = {'date': {'preset': 'custom', 'from': fields.Date.to_string(cls.fy_from),
                             'to': fields.Date.to_string(cls.today)}}

    def _data(self, key, options=None):
        return self.Report.by_key(key).get_report_data(options or {})

    def _line(self, data, code=None, name=None):
        for line in data['lines']:
            if (code and line.get('code') == code) or (name and line['name'] == name):
                return line
        self.fail("no line %s" % (code or name))

    def _val(self, data, code=None, name=None, col=0):
        return self._line(data, code, name)['columns'][col]['value'] or 0.0

    # ------------------------------------------------------------ statements
    def test_the_balance_sheet_balances(self):
        data = self._data('balance_sheet', self.asof)
        check = self._line(data, code='CHECK')
        self.assertEqual(check['columns'][0]['class'], 'ok', check['columns'][0]['text'])
        assets = self._val(data, 'ASSETS')
        self.assertAlmostEqual(assets, self._val(data, 'LIAB') + self._val(data, 'EQUITY'), 2)
        # the same identity read on the last day of the previous fiscal year
        last = self.company.compute_fiscalyear_dates(self.fy_from - timedelta(days=1))
        past = self._data('balance_sheet', {'date': {'preset': 'custom', 'from': fields.Date.to_string(last['date_from']),
                                                     'to': fields.Date.to_string(last['date_to'])}})
        self.assertEqual(self._line(past, code='CHECK')['columns'][0]['class'], 'ok')

    def test_current_year_earnings_are_the_profit_and_loss_of_the_year(self):
        bs = self._data('balance_sheet', self.asof)
        pl = self._data('profit_loss', self.year)
        self.assertAlmostEqual(self._val(bs, 'EARN_CUR'), self._val(pl, 'NET'), 2)

    def test_cash_flow_reconciles_to_the_cash_movement(self):
        data = self._data('cash_flow', self.year)
        self.assertEqual(self._line(data, code='CHECK')['columns'][0]['class'], 'ok',
                         self._line(data, code='CHECK')['columns'][0]['text'])
        self.assertAlmostEqual(self._val(data, 'CASH_CLOSE') - self._val(data, 'CASH_OPEN'), self._val(data, 'CHANGE'), 2)

    def test_comparison_adds_columns_and_growth(self):
        data = self._data('profit_loss', dict(self.year, comparison={'mode': 'previous', 'periods': 2}, growth=True))
        kinds = [c['type'] for c in data['columns']]
        self.assertEqual(kinds, ['amount', 'amount', 'amount', 'growth'])
        rev = self._line(data, code='REV')
        a, b = rev['columns'][0]['value'], rev['columns'][1]['value']
        growth = rev['columns'][3]['value']
        if b:
            self.assertAlmostEqual(growth, round((a - b) / abs(b) * 100, 1), 1)
        else:
            self.assertIsNone(growth)

    def test_a_statement_line_unfolds_to_the_accounts_that_make_it(self):
        data = self._data('profit_loss', self.year)
        rev = self._line(data, code='REV')
        self.assertTrue(rev['unfoldable'])
        children = self.Report.by_key('profit_loss').expand_line(self.year, rev['id'])['lines']
        self.assertTrue(children, "revenue has accounts behind it")
        self.assertAlmostEqual(sum(c['columns'][0]['value'] for c in children), rev['columns'][0]['value'], 2)
        for child in children:
            self.assertEqual(child['parent_id'], rev['id'])

    def test_drilling_a_figure_opens_exactly_the_items_behind_it(self):
        data = self._data('profit_loss', self.year)
        rev = self._line(data, code='REV')
        action = self.Report.by_key('profit_loss').get_drill_action(self.year, rev['id'], 'p0')
        lines = self.env['account.move.line'].search(action['domain'])
        self.assertAlmostEqual(-sum(lines.mapped('balance')), rev['columns'][0]['value'], 2)

    def test_explaining_a_figure_adds_up(self):
        data = self._data('profit_loss', self.year)
        rev = self._line(data, code='REV')
        out = self.Report.by_key('profit_loss').explain_cell(self.year, rev['id'], 'p0')
        self.assertTrue(out['accounts'] and out['months'])
        # every month inside the fiscal year, added up, is the figure
        self.assertAlmostEqual(sum(m['value'] for m in out['months']), rev['columns'][0]['value'], 2)

    def test_trends_are_twelve_months_of_the_line(self):
        data = self._data('profit_loss', self.year)
        rev = self._line(data, code='REV')
        trends = self.Report.by_key('profit_loss').get_trends(self.year, [rev['id']])
        self.assertEqual(len(trends[rev['id']]), 12)

    # ------------------------------------------------------------ ledgers
    def test_the_trial_balance_foots(self):
        data = self._data('trial_balance', self.year)
        total = self._line(data, name='Total')['columns']
        ini_d, ini_c, d, c, end_d, end_c = [x['value'] or 0 for x in total]
        self.assertAlmostEqual(ini_d, ini_c, 1)
        self.assertAlmostEqual(d, c, 1)
        self.assertAlmostEqual(end_d, end_c, 1)

    def test_the_general_ledger_agrees_with_the_trial_balance(self):
        gl = self._data('general_ledger', self.year)
        tb = self._data('trial_balance', self.year)
        gl_total = self._line(gl, name='Total')['columns']
        tb_total = self._line(tb, name='Total')['columns']
        self.assertAlmostEqual(gl_total[0]['value'], tb_total[2]['value'], 2)
        self.assertAlmostEqual(gl_total[1]['value'], tb_total[3]['value'], 2)
        self.assertAlmostEqual(gl_total[2]['value'], (tb_total[4]['value'] or 0) - (tb_total[5]['value'] or 0), 2)

    def test_an_account_unfolds_to_its_items_with_a_running_balance(self):
        gl = self._data('general_ledger', self.year)
        account = next(l for l in gl['lines'] if l['kind'] == 'account' and (l['columns'][0]['value'] or l['columns'][1]['value']))
        res = self.Report.by_key('general_ledger').expand_line(self.year, account['id'])
        rows = [r for r in res['lines'] if r['kind'] == 'move_line']
        self.assertTrue(rows)
        self.assertEqual(res['lines'][0]['kind'], 'initial')
        running = res['lines'][0]['columns'][2]['value'] or 0.0
        for r in rows:
            running += (r['columns'][0]['value'] or 0) - (r['columns'][1]['value'] or 0)
            self.assertAlmostEqual(r['columns'][2]['value'], running, 2)
        if not res['has_more']:
            self.assertAlmostEqual(running, account['columns'][2]['value'], 2)

    def test_the_partner_ledger_agrees_with_the_general_ledger(self):
        pl = self._data('partner_ledger', self.year)
        total = self._line(pl, name='Total')['columns']
        accounts = self.engine.accounts(self.engine.normalize(self.Report.by_key('partner_ledger'), self.year))
        rec_pay = [a for a, m in accounts.items() if m['type'] in ('asset_receivable', 'liability_payable')]
        gl = self._data('general_ledger', self.year)
        gl_sum = sum(l['columns'][2]['value'] or 0 for l in gl['lines'] if l['kind'] == 'account' and l.get('account_id') in rec_pay)
        self.assertAlmostEqual(total[2]['value'], gl_sum, 2)

    def test_the_day_book_agrees_with_the_general_ledger(self):
        db = self._data('day_book', self.year)
        gl = self._data('general_ledger', self.year)
        self.assertAlmostEqual(self._line(db, name='Total')['columns'][0]['value'],
                               self._line(gl, name='Total')['columns'][0]['value'], 2)

    def test_the_cash_book_reads_the_liquidity_accounts(self):
        cb = self._data('cash_book', self.year)
        options = self.engine.normalize(self.Report.by_key('cash_book'), self.year)
        accounts = self.engine.accounts(options)
        for line in cb['lines']:
            if line['kind'] != 'account':
                continue
            cumulative = self.engine.sums_by_account(options, 'cumulative', self.fy_from, self.today,
                                                     account_ids=[line['account_id']])
            self.assertAlmostEqual(line['columns'][2]['value'], cumulative.get(line['account_id'], {}).get('balance', 0.0), 2)
            self.assertIn(accounts[line['account_id']]['type'], ('asset_cash', 'liability_credit_card'))

    def test_aged_receivable_is_what_is_open_today(self):
        """Residuals AS OF the date: the balance less the partials settled by then -
        computed here a second way, through the ORM, to check the SQL."""
        data = self._data('aged_receivable', self.asof)
        total = self._line(data, name='Total')['columns'][-1]['value'] or 0.0
        Line = self.env['account.move.line']
        lines = Line.search([('account_id.account_type', '=', 'asset_receivable'), ('parent_state', '=', 'posted'),
                             ('date', '<=', self.today), ('company_id', 'in', self.env.companies.ids)])
        # the report reads every company selected in the switcher, as Odoo's own reports do
        Partial = self.env['account.partial.reconcile']
        settled_debit = {r['debit_move_id'][0]: r['amount'] for r in Partial._read_group(
            [('max_date', '<=', self.today), ('debit_move_id', 'in', lines.ids)], ['debit_move_id'], ['amount:sum'])} \
            if False else {}
        for debit_line, amount in Partial._read_group([('max_date', '<=', self.today), ('debit_move_id', 'in', lines.ids)],
                                                      ['debit_move_id'], ['amount:sum']):
            settled_debit[debit_line.id] = amount
        settled_credit = {}
        for credit_line, amount in Partial._read_group([('max_date', '<=', self.today), ('credit_move_id', 'in', lines.ids)],
                                                       ['credit_move_id'], ['amount:sum']):
            settled_credit[credit_line.id] = amount
        expected = sum(l.balance - settled_debit.get(l.id, 0.0) + settled_credit.get(l.id, 0.0) for l in lines)
        self.assertAlmostEqual(total, expected, 1)
        for line in data['lines']:
            if line['kind'] == 'partner':
                self.assertAlmostEqual(sum(c['value'] or 0 for c in line['columns'][:-1]), line['columns'][-1]['value'], 2)

    def test_aged_buckets_are_configurable(self):
        data = self._data('aged_payable', dict(self.asof, aged_interval=15, aged_buckets=3))
        labels = [c['label'] for c in data['columns']]
        self.assertEqual(labels[1:4], ['1–15', '16–30', '31–45'])
        self.assertEqual(len(data['columns']), 3 + 3)

    def test_the_tax_report_reads_tax_lines(self):
        data = self._data('tax', self.year)
        options = self.engine.normalize(self.Report.by_key('tax'), self.year)
        for line in data['lines']:
            if line['kind'] != 'tax':
                continue
            tax = self.env['account.tax'].browse(line['tax_id'])
            amls = self.env['account.move.line'].search([('tax_line_id', '=', tax.id), ('parent_state', '=', 'posted'),
                                                         ('date', '>=', self.fy_from), ('date', '<=', self.today),
                                                         ('company_id', 'in', options['companies'])])
            sign = -1 if tax.type_tax_use == 'sale' else 1
            self.assertAlmostEqual(line['columns'][1]['value'], sign * sum(amls.mapped('balance')), 2)
            break

    # ------------------------------------------------------------ analytic weighting
    def test_an_analytic_filter_weights_the_amount_by_its_share(self):
        plan = self.env['account.analytic.plan'].search([], limit=1) or self.env['account.analytic.plan'].create({'name': 'Test plan'})
        aa = self.env['account.analytic.account'].create({'name': 'Half share', 'plan_id': plan.id})
        expense = self.env['account.account'].search([('account_type', '=', 'expense')], limit=1)
        cash = self.env['account.account'].search([('account_type', '=', 'asset_cash')], limit=1)
        journal = self.env['account.journal'].search([('type', '=', 'general')], limit=1)
        move = self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': journal.id, 'date': self.today,
            'line_ids': [(0, 0, {'account_id': expense.id, 'debit': 1000.0, 'analytic_distribution': {str(aa.id): 50.0}}),
                         (0, 0, {'account_id': cash.id, 'credit': 1000.0})]})
        move.action_post()
        data = self._data('profit_loss', dict(self.year, analytic=[aa.id]))
        self.assertAlmostEqual(self._val(data, 'OPEX'), 500.0, 2)

    # ------------------------------------------------------------ the designer
    def test_a_designed_report_computes_its_formulas(self):
        report = self.Report.create({
            'name': 'Twice the revenue', 'key': 'test_twice', 'kind': 'statement',
            'line_ids': [(0, 0, {'name': 'Revenue', 'code': 'R', 'kind': 'sum', 'sign': '-1', 'account_types': 'income'}),
                         (0, 0, {'name': 'Twice', 'code': 'T', 'kind': 'formula', 'formula': 'R * 2'}),
                         (0, 0, {'name': 'Share', 'code': 'S', 'kind': 'formula', 'formula': 'R / T * 100', 'display': 'percent'})]})
        data = report.get_report_data(self.year)
        r, t, s = (self._val(data, c) for c in ('R', 'T', 'S'))
        self.assertAlmostEqual(t, 2 * r, 2)
        if r:
            self.assertAlmostEqual(s, 50.0, 1)
        self.assertIn('%', self._line(data, code='S')['columns'][0]['text'])

    def test_the_designer_refuses_a_broken_formula(self):
        report = self.Report.create({'name': 'Broken', 'key': 'test_broken', 'kind': 'statement'})
        with self.assertRaises(ValidationError):
            self.env['ebshel.fin.report.line'].create({'report_id': report.id, 'name': 'X', 'code': 'X',
                                                       'kind': 'formula', 'formula': 'NOPE + 1'})
        with self.assertRaises(ValidationError):
            self.env['ebshel.fin.report.line'].create({'report_id': report.id, 'name': 'Y', 'code': 'Y',
                                                       'kind': 'formula', 'formula': 'Y + 1'})
        with self.assertRaises(ValidationError):
            self.env['ebshel.fin.report.line'].create({'report_id': report.id, 'name': 'Z', 'code': 'Z',
                                                       'kind': 'formula', 'formula': '__import__("os")'})

    def test_a_designed_report_gets_a_menu(self):
        report = self.Report.create({'name': 'Mine', 'key': 'test_menu', 'kind': 'statement'})
        report.action_create_menu()
        self.assertTrue(report.menu_id and report.action_id)
        self.assertEqual(report.menu_id.parent_id, self.env.ref('ebshel_account_reports.menu_custom_reports'))
        report.unlink()

    # ------------------------------------------------------------ exports, notes, views, schedules
    def test_the_workbook_and_the_pdf_render(self):
        report = self.Report.by_key('profit_loss')
        xlsx = report.export_xlsx(self.year)
        self.assertTrue(base64.b64decode(xlsx['content']).startswith(b'PK'))
        pdf, kind = self.env['ir.actions.report'].with_context(force_report_rendering=True)._render_qweb_pdf(
            'ebshel_account_reports.action_fin_report_pdf', res_ids=[report.id],
            data={'options': self.year, 'report_id': report.id})
        self.assertEqual(kind, 'pdf')
        self.assertTrue(pdf.startswith(b'%PDF'))

    def test_notes_stay_with_the_line(self):
        report = self.Report.by_key('profit_loss')
        data = report.get_report_data(self.year)
        rev = self._line(data, code='REV')
        self.env['ebshel.fin.report.annotation'].add_note(report.id, rev['id'], 'Seasonal peak', line_name=rev['name'])
        again = report.get_report_data(self.year)
        self.assertEqual(again['annotations'][rev['id']][0]['text'], 'Seasonal peak')

    def test_a_saved_view_reopens_with_its_filters(self):
        report = self.Report.by_key('profit_loss')
        vid = self.env['ebshel.fin.report.view'].save_view(report.id, 'Year so far', self.year, shared=True)
        views = report.get_report_data({})['saved_views']
        self.assertIn(vid, [v['id'] for v in views])
        self.assertEqual(next(v for v in views if v['id'] == vid)['options']['date']['from'], self.year['date']['from'])

    def test_a_schedule_sends_the_report_and_moves_on(self):
        partner = self.env['res.partner'].create({'name': 'Reader', 'email': 'reader@example.com'})
        schedule = self.env['ebshel.fin.report.schedule'].create({
            'name': 'Monthly P&L', 'report_id': self.Report.by_key('profit_loss').id, 'interval': 'monthly',
            'run_day': 1, 'next_run': self.today, 'partner_ids': [(6, 0, [partner.id])], 'format': 'both'})
        before = self.env['mail.mail'].search_count([])
        self.env['ebshel.fin.report.schedule']._cron_run()
        mail = self.env['mail.mail'].search([], order='id desc', limit=1)
        self.assertEqual(self.env['mail.mail'].search_count([]), before + 1)
        self.assertEqual(len(mail.attachment_ids), 2)
        self.assertGreater(schedule.next_run, self.today)
        self.assertEqual(schedule.run_count, 1)

    def test_the_reader_role_is_required(self):
        nobody = self.env['res.users'].create({'name': 'Nobody', 'login': 'fin_nobody',
                                               'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(Exception):
            self.Report.by_key('profit_loss').with_user(nobody).get_report_data({})

    def test_an_accounting_administrator_can_read_every_report(self):
        """Odoo 19's accounting Administrator implies billing only, not read-only:
        a check keyed on read-only alone locked the lab's own accountants out."""
        admin = self.env['res.users'].create({'name': 'Rep Admin', 'login': 'efr_admin',
                                              'group_ids': [(6, 0, [self.env.ref('base.group_user').id, self.env.ref('account.group_account_manager').id])]})
        for key in ('profit_loss', 'general_ledger', 'aged_receivable'):
            data = self.Report.with_user(admin).by_key(key).get_report_data(self.year if key != 'aged_receivable' else self.asof)
            self.assertTrue(data['lines'])
        self.Report.with_user(admin).search([])          # the ORM's own access hook is intact
