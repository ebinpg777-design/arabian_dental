# -*- coding: utf-8 -*-
"""What was made faster must still say the same thing, and must stay fast.

Each shortcut is held against the long way round it replaced - the one-pass sums
against a query per window, the writer of numbers against formatLang, the joined
open items against a sub-query per item - and the number of times a reading goes
through the ledger is counted, so a change that quietly brings the scans back
fails here rather than on somebody's screen."""
import re
from datetime import date
from unittest.mock import patch

from odoo import fields
from odoo.sql_db import Cursor
from odoo.tests import TransactionCase, tagged
from odoo.tools import SQL
from odoo.tools.misc import formatLang

from ..models.engine import Memo, group_digits


class Statements:
    """Counts the statements a block runs, by what they read."""

    def __init__(self):
        self.seen = []

    def __enter__(self):
        real, seen = Cursor.execute, self.seen

        def counted(cursor, query, params=None, log_exceptions=True):
            text = query if isinstance(query, str) else getattr(query, 'code', str(query))
            seen.append(re.sub(r'\s+', ' ', str(text)))
            return real(cursor, query, params, log_exceptions)
        self.patch = patch.object(Cursor, 'execute', counted)
        self.patch.start()
        return self

    def __exit__(self, *exc):
        self.patch.stop()

    def scans(self):
        """Passes over the journal items that sum them."""
        return [q for q in self.seen if 'FROM account_move_line l' in q and 'SUM(l.balance' in q]


@tagged('post_install', '-at_install')
class TestFinReportSpeed(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Report = cls.env['ebshel.fin.report']
        cls.engine = cls.env['ebshel.fin.engine']
        cls.company = cls.env.company
        cls.company.write({'ebshel_fin_totals_last': False, 'ebshel_fin_negative': 'minus'})
        cls.today = fields.Date.context_today(cls.env.user)
        fy = cls.company.compute_fiscalyear_dates(cls.today)
        cls.year = {'date': {'preset': 'custom', 'from': fields.Date.to_string(fy['date_from']),
                             'to': fields.Date.to_string(cls.today)}}
        cls.pl = cls.Report.by_key('profit_loss')
        cls.bs = cls.Report.by_key('balance_sheet')
        cls.ledger = cls.Report.by_key('general_ledger')
        cls.partner = cls.env['res.partner'].create({'name': 'EFR Speed Clinic'})
        cls.other = cls.env['res.partner'].create({'name': 'EFR Speed Supplier'})
        like = cls.env['account.account'].search([('account_type', '=', 'income')], limit=1)
        cls.income = like.copy({'name': 'EFR speed income', 'code': 'EFS%s' % like.id})
        cls.invoice = cls._invoice(1500.0)
        cls.second = cls._invoice(700.0)

    @classmethod
    def _invoice(cls, amount, partner=None):
        move = cls.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': (partner or cls.partner).id, 'invoice_date': cls.today,
            'invoice_payment_term_id': False,
            'invoice_line_ids': [(0, 0, {'name': 'EFR speed crown', 'quantity': 1, 'price_unit': amount,
                                         'account_id': cls.income.id, 'tax_ids': [(5, 0, 0)]})]})
        move.action_post()
        return move

    def _prepared(self, report, options=None):
        return report._prepare(options or self.year)

    # ------------------------------------------------------------ the ledger is gone through once
    def test_a_statement_goes_through_the_ledger_once(self):
        for report, options in ((self.pl, self.year), (self.bs, self.year),
                                (self.pl, dict(self.year, comparison={'mode': 'previous', 'periods': 3}))):
            with self.subTest(report=report.key, columns=options.get('comparison')):
                self.env.flush_all()
                with Statements() as seen:
                    report.get_report_data(options)
                self.assertEqual(len(seen.scans()), 1, "every window of every column, side by side in one pass")

    def test_unfolding_everything_does_not_go_through_it_again(self):
        with Statements() as seen:
            data = self.pl.get_report_data(dict(self.year, unfold_all=True))
        self.assertEqual(len(seen.scans()), 1)
        self.assertTrue(any(l.get('kind') == 'account' for l in data['lines']))

    def test_entries_read_together_are_the_entries_read_one_heading_at_a_time(self):
        for key, prefix in (('general_ledger', 'ac:'), ('partner_ledger', 'pa:'), ('customer_statement', 'pa:')):
            with self.subTest(report=key):
                report = self.Report.by_key(key)
                together = report.get_report_data(dict(self.year, unfold_all=True))['lines']
                heads = [l for l in together if l.get('unfoldable')][:6]
                self.assertGreater(len(heads), 3, "enough open headings for them to be read together")
                for head in heads:
                    alone = report.expand_line(self.year, head['id'])['lines']
                    block = [l for l in together if l.get('parent_id') == head['id'] and l.get('kind') != 'more']
                    self.assertEqual([l['id'] for l in block], [l['id'] for l in alone if l.get('kind') != 'more'])
                    self.assertEqual([[c['value'] for c in l['columns']] for l in block],
                                     [[c['value'] for c in l['columns']] for l in alone if l.get('kind') != 'more'])

    def test_a_ledger_reads_its_opening_and_its_period_in_one_pass(self):
        for key in ('general_ledger', 'trial_balance'):
            with self.subTest(report=key):
                with Statements() as seen:
                    self.Report.by_key(key).get_report_data(self.year)
                self.assertEqual(len(seen.scans()), 1)

    def test_the_one_pass_says_what_a_query_per_window_said(self):
        engine, handler, options, columns = self._prepared(self.bs)
        d_from, d_to = fields.Date.to_date(options['date']['from']), fields.Date.to_date(options['date']['to'])
        windows = [(mode, d_from, d_to) for mode in ('flow', 'cumulative', 'opening', 'initial',
                                                     'earnings_current', 'earnings_previous')]
        engine.preload_sums(options, windows)
        plain = self.engine                       # no memo: the long way round
        for mode, a, b in windows:
            with self.subTest(mode=mode):
                long_way = plain.sums_by_account(options, mode, a, b)
                self.assertTrue(long_way or mode in ('opening', 'initial', 'earnings_previous'))
                short = engine.sums_by_account(options, mode, a, b)
                self.assertEqual(set(short), set(long_way))
                for account, values in long_way.items():
                    for key in ('balance', 'debit', 'credit'):
                        self.assertAlmostEqual(short[account][key], values[key], 2)
                    self.assertEqual(short[account]['count'], values['count'])
                some = sorted(long_way)[:3]
                self.assertEqual(set(engine.sums_by_account(options, mode, a, b, account_ids=some)), set(some))

    def test_a_memo_is_never_shared_between_readings(self):
        first = self._prepared(self.pl)[0]
        second = self._prepared(self.pl)[0]
        self.assertIsNot(first.env.context['fin_memo'], second.env.context['fin_memo'])
        self.assertNotEqual(Memo(), Memo(), "two empty memos are still two readings")
        # so a figure posted between two readings is in the second
        before = self.pl.get_report_data(self.year)
        self._invoice(250.0)
        after = self.pl.get_report_data(self.year)
        rev = lambda data: next(l for l in data['lines'] if l.get('code') == 'REV')['columns'][0]['value']
        self.assertAlmostEqual(rev(after) - rev(before), 250.0, 2)

    def test_the_engine_without_a_reading_keeps_nothing(self):
        self.assertIsNone(self.engine._memo())
        self.engine.preload_sums(self.engine.normalize(self.pl, self.year), [('flow', date(2026, 1, 1), date(2026, 1, 31))])
        self.assertIsNone(self.engine.with_context(fin_memo={'not': 'a memo'})._memo())

    # ------------------------------------------------------------ trends
    def test_trends_say_what_a_query_per_line_said_in_two_queries(self):
        data = self.pl.get_report_data(dict(self.year, unfold_all=True))
        ids = [l['id'] for l in data['lines']]
        engine, handler, options, columns = self._prepared(self.pl)
        with Statements() as seen:
            trends = self.pl.get_trends(self.year, ids)
        self.assertLessEqual(len([q for q in seen.seen if 'FROM account_move_line l' in q]), 2)
        self.assertTrue(trends)
        checked = 0
        for lid, series in trends.items():
            line, accounts = handler._target(self.pl, options, lid)
            long_way = self.engine.monthly(options, handler.MODE_OF_TREND.get(line.balance_mode, 'flow'), sorted(accounts))
            self.assertEqual(len(series), 12)
            for point, (month, value) in zip(series, long_way):
                self.assertAlmostEqual(point['value'], round(float(line.sign) * value, 2), 2)
                checked += 1
        self.assertGreater(checked, 12)

    def test_the_trend_of_a_stock_is_where_it_stood(self):
        data = self.bs.get_report_data(self.year)
        ids = [l['id'] for l in data['lines']]
        engine, handler, options, columns = self._prepared(self.bs)
        for lid, series in self.bs.get_trends(self.year, ids).items():
            line, accounts = handler._target(self.bs, options, lid)
            mode = handler.MODE_OF_TREND.get(line.balance_mode, 'flow')
            long_way = self.engine.monthly(options, mode, sorted(accounts))
            for point, (month, value) in zip(series, long_way):
                self.assertAlmostEqual(point['value'], round(float(line.sign) * value, 2), 2)

    def test_the_ledger_trends_are_not_a_query_per_account(self):
        data = self.ledger.get_report_data(self.year)
        ids = [l['id'] for l in data['lines']]
        with Statements() as seen:
            trends = self.ledger.get_trends(self.year, ids)
        self.assertLessEqual(len([q for q in seen.seen if 'FROM account_move_line l' in q]), 2)
        mine = trends['ac:%d' % self.income.id]
        self.assertAlmostEqual(mine[-1]['value'], -2200.0, 2)

    # ------------------------------------------------------------ the writer of numbers
    def test_digits_are_grouped_the_way_a_language_groups(self):
        self.assertEqual(group_digits('1234567', [3, 0], ','), '1,234,567')
        self.assertEqual(group_digits('12345678', [3, 2, 0], ','), '1,23,45,678')
        self.assertEqual(group_digits('12345678', [3, 2, -1], ','), '123,45,678')
        self.assertEqual(group_digits('1234567', [3, 0], '.'), '1.234.567')
        self.assertEqual(group_digits('1234567', [], ','), '1234567')
        self.assertEqual(group_digits('1234567', [3, 0], ''), '1234567')
        self.assertEqual(group_digits('12', [3, 0], ','), '12')
        self.assertEqual(group_digits('123', [3, 0], ','), '123')

    def test_the_writer_writes_what_formatlang_writes(self):
        currency = self.company.currency_id
        lang = self.env['res.lang']._activate_lang(self.env.lang or 'en_US')
        values = (0.0, 1.0, -1.0, 12.5, 999.994, 999.995, 1000.0, -1234.5, 1234567.891, -98765432.1, 0.004, 100000.0)
        for grouping, comma, point in (('[3,0]', ',', '.'), ('[3,2,0]', ',', '.'), ('[3,0]', '.', ','),
                                       ('[3,0]', ' ', ','), ('[3,2,0]', '', '.')):
            for position in ('before', 'after'):
                with self.subTest(grouping=grouping, comma=comma, position=position):
                    lang.write({'grouping': grouping, 'thousands_sep': comma, 'decimal_point': point})
                    currency.position = position
                    self.env.registry.clear_cache()
                    engine = self.engine.with_context(fin_memo=Memo())
                    money, plain = engine._writer(currency.decimal_places, currency), engine._writer(1)
                    self.assertEqual(money.__name__, 'write', "the short way round, not formatLang again")
                    for value in values:
                        self.assertEqual(money(value), formatLang(self.env, value, currency_obj=currency), value)
                        self.assertEqual(plain(value), formatLang(self.env, value, digits=1), value)

    def test_the_writer_is_made_once_a_reading(self):
        engine = self._prepared(self.pl)[0]
        currency = self.company.currency_id
        self.assertIs(engine._writer(2, currency), engine._writer(2, currency))
        for display in ('amount', 'percent', 'ratio', 'days', 'count'):
            engine.fmt(1.0, currency, display)          # each writer is checked against formatLang once, as it is made
        with patch('odoo.addons.ebshel_account_reports.models.engine.formatLang') as slow:
            slow.side_effect = AssertionError("formatLang was asked for a figure")
            for display in ('amount', 'percent', 'ratio', 'days', 'count'):
                self.assertTrue(engine.fmt(1234.5, currency, display))

    # ------------------------------------------------------------ below zero, the company's way
    def test_a_negative_amount_is_written_the_way_the_company_chose(self):
        currency = self.company.currency_id
        usual = self._prepared(self.pl)[0].fmt(1250.5, currency)
        for style, shape in (('minus', '-%s'), ('brackets', '(%s)'), ('trailing', '%s-')):
            with self.subTest(style=style):
                self.company.ebshel_fin_negative = style
                engine = self._prepared(self.pl)[0]
                written = engine.fmt(-1250.5, currency)
                if style == 'minus':
                    self.assertEqual(written, formatLang(self.env, -1250.5, currency_obj=currency))
                else:
                    self.assertEqual(written, shape % usual)
                self.assertEqual(engine.fmt(1250.5, currency), usual, "above zero nothing changes")
                self.assertNotIn('(', engine.fmt(-0.001, currency))
                self.assertNotIn('-', engine.fmt(-0.001, currency))
                self.assertEqual(self.pl.get_report_data(self.year)['report']['negative'], style)

    def test_the_workbook_writes_below_zero_the_same_way(self):
        import base64, io, openpyxl
        self.company.ebshel_fin_negative = 'brackets'
        file = self.pl.export_xlsx(self.year, {'scope': 'summary'})
        sheet = openpyxl.load_workbook(io.BytesIO(base64.b64decode(file['content']))).worksheets[0]
        formats = {c.number_format for row in sheet.iter_rows() for c in row if isinstance(c.value, (int, float))}
        self.assertTrue(any('(#,##0.00)' in f for f in formats), formats)

    # ------------------------------------------------------------ totals under their sections
    def test_a_section_is_closed_by_its_total(self):
        plain = self.bs.get_report_data(self.year)['lines']
        self.company.ebshel_fin_totals_last = True
        data = self.bs.get_report_data(self.year)
        self.assertTrue(data['report']['totals_last'])
        lines = data['lines']
        by_id = {l['id']: l for l in plain}
        totals = [l for l in lines if l.get('total_of')]
        self.assertTrue(totals, "the balance sheet has sections")
        for total in totals:
            heading = next(l for l in lines if l['id'] == total['total_of'])
            self.assertTrue(heading['total_last'])
            self.assertTrue(all(c['value'] is None and not c['text'] for c in heading['columns']), "the heading stands alone")
            self.assertEqual(total['name'], 'Total %s' % heading['name'])
            self.assertEqual([c['value'] for c in total['columns']], [c['value'] for c in by_id[heading['id']]['columns']])
            at, top = lines.index(heading), lines.index(total)
            self.assertGreater(top, at + 1)
            self.assertTrue(all((l.get('level') or 0) > (heading.get('level') or 0) or l.get('total_of')
                                for l in lines[at + 1:top]), "everything between them belongs to the section")
            self.assertTrue(top + 1 == len(lines) or (lines[top + 1].get('level') or 0) <= (heading.get('level') or 0)
                            or lines[top + 1].get('total_of'))
        self.assertEqual(len(lines), len(plain) + len(totals))

    def test_a_folded_section_carries_its_own_total(self):
        self.company.ebshel_fin_totals_last = True
        data = self.pl.get_report_data(self.year)
        rev = next(l for l in data['lines'] if l.get('code') == 'REV')
        self.assertFalse(rev.get('total_last'))
        self.assertIsNotNone(rev['columns'][0]['value'])
        res = self.pl.expand_line(self.year, rev['id'])
        self.assertTrue(res['totals_last'], "unfolded on the screen, its figures move under its rows")
        open_data = self.pl.get_report_data(dict(self.year, expanded=[rev['id']]))
        total = next(l for l in open_data['lines'] if l.get('total_of') == rev['id'])
        self.assertAlmostEqual(total['columns'][0]['value'], rev['columns'][0]['value'], 2)
        self.company.ebshel_fin_totals_last = False
        self.assertFalse(self.pl.expand_line(self.year, rev['id'])['totals_last'])

    def test_the_exports_close_their_sections_too(self):
        self.company.ebshel_fin_totals_last = True
        rows = self.bs.export_rows(self.year, {'scope': 'summary'})['rows']
        names = [r[0] for r in rows]
        totals = [n for n in names if str(n).startswith('Total ')]
        self.assertTrue(totals)
        for name in totals:
            heading = rows[names.index(name[len('Total '):])]
            self.assertTrue(all(v in ('', None) for v in heading[1:]))

    # ------------------------------------------------------------ open items
    def test_the_joined_open_items_are_the_open_items(self):
        """Against the sub-query per item it replaced, on a ledger where something IS matched."""
        journal = self.env['account.journal'].create({'name': 'EFR speed bank', 'code': 'EFSB', 'type': 'bank'})
        payment = self.env['account.payment'].create({
            'payment_type': 'inbound', 'partner_type': 'customer', 'partner_id': self.partner.id,
            'amount': 600.0, 'journal_id': journal.id, 'date': self.today})
        payment.action_post()
        receivable = self.invoice.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable')
        paid = payment.move_id.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable')
        if paid:
            (receivable | paid).reconcile()
        self.env.flush_all()
        report = self.Report.by_key('aged_receivable')
        engine, handler, options, columns = self._prepared(report, dict(self.year, partners=[self.partner.id]))
        d_to = fields.Date.to_date(options['date']['to'])
        where = SQL(" AND ").join([engine.base_where(options), SQL("a.account_type = %s", 'asset_receivable'),
                                   SQL("l.date <= %s", d_to)])
        long_way = self.env.execute_query(SQL("""
            SELECT s.id, s.open_amount FROM (
                SELECT l.id, l.balance
                       - COALESCE((SELECT SUM(p.amount) FROM account_partial_reconcile p
                                    WHERE p.debit_move_id = l.id AND p.max_date <= %s), 0)
                       + COALESCE((SELECT SUM(p.amount) FROM account_partial_reconcile p
                                    WHERE p.credit_move_id = l.id AND p.max_date <= %s), 0) AS open_amount
                  FROM account_move_line l JOIN account_account a ON a.id = l.account_id
                 WHERE %s) s
             WHERE ROUND(s.open_amount::numeric, 2) <> 0""", d_to, d_to, where))
        short = handler._open_items(options, 'asset_receivable')
        self.assertEqual({r[0]: round(float(r[1]), 2) for r in long_way}, {r[0]: round(float(r[6]), 2) for r in short})
        self.assertTrue(short)
        if paid:
            mine = next(r for r in short if r[0] == receivable.id)
            self.assertAlmostEqual(float(mine[6]), 900.0, 2, "1,500 invoiced, 600 of it matched")

    # ------------------------------------------------------------ the statement of account
    def test_a_statement_of_account_opens_folded_and_whole(self):
        report = self.Report.by_key('customer_statement')
        with Statements() as seen:
            data = report.get_report_data(self.year)
        partners = [l for l in data['lines'] if l.get('kind') == 'partner']
        self.assertLessEqual(len([q for q in seen.seen if 'FROM account_move_line' in q]), 3,
                             "two grouped sums, whatever the number of partners - it was a query for each")
        with Statements() as seen:
            everything = report.get_report_data(dict(self.year, unfold_all=True))
        self.assertLessEqual(len([q for q in seen.seen if 'FROM account_move_line' in q]), 4,
                             "and unfolding every partner reads their entries together")
        self.assertTrue([l for l in everything['lines'] if l.get('parts')])
        self.assertTrue(all(l['unfoldable'] and not l['unfolded'] for l in partners))
        self.assertFalse([l for l in data['lines'] if l.get('parts')], "no entry is sent before its partner is opened")
        mine = next(l for l in partners if l.get('partner_id') == self.partner.id)
        self.assertAlmostEqual(mine['columns'][0]['value'], 2200.0, 2)
        ledger = self.Report.by_key('partner_ledger').get_report_data(dict(self.year, account_types=['asset_receivable']))
        self.assertEqual(len(partners), len([l for l in ledger['lines'] if l.get('kind') == 'partner']),
                         "every partner of the receivable ledger, not the first two hundred")

    def test_an_opened_statement_runs_from_its_opening_to_its_closing(self):
        report = self.Report.by_key('customer_statement')
        lid = 'pa:%d' % self.partner.id
        kids = report.expand_line(self.year, lid)['lines']
        self.assertEqual(kids[0]['kind'], 'initial')
        self.assertEqual(kids[-1]['name'], 'Closing balance')
        entries = [l for l in kids if l.get('parts')]
        self.assertEqual({l['parts']['move'] for l in entries}, {self.invoice.name, self.second.name})
        self.assertAlmostEqual(kids[-1]['columns'][0]['value'], 2200.0, 2)
        self.assertAlmostEqual(kids[-1]['columns'][2]['value'], entries[-1]['columns'][2]['value'], 2)
        data = report.get_report_data(dict(self.year, expanded=[lid]))['lines']
        at = next(i for i, l in enumerate(data) if l['id'] == lid)
        block = [l for l in data[at + 1:] if l.get('parent_id') == lid]
        self.assertEqual(block[-1]['id'], lid + ':close')
        self.assertEqual(len(block), len(kids))

    def test_one_partners_statement_on_paper_is_whole_and_closes_on_its_balance(self):
        report = self.Report.by_key('customer_statement')
        options = dict(self.year, partners=[self.partner.id])
        values = self.env['report.ebshel_account_reports.fin_report_pdf']._get_report_values(
            [report.id], {'options': options, 'report_id': report.id})
        lines = [l for s in values['slices'] for l in s]
        self.assertEqual([l['name'] for l in lines if not l.get('parts')],
                         ['EFR Speed Clinic', 'Initial balance', 'Closing balance'])
        self.assertEqual(len([l for l in lines if l.get('parts')]), 2)
        self.assertTrue(lines[0].get('page_break'))

    # ------------------------------------------------------------ what is sent to the screen
    def test_a_reload_is_not_sent_what_the_screen_already_has(self):
        first = self.pl.get_report_data(self.year)
        again = self.pl.get_report_data(self.year, lean=True)
        for key in ('choices', 'reports', 'saved_views'):
            self.assertIn(key, first)
            self.assertNotIn(key, again)
        self.assertEqual(again['lines'], first['lines'])
        self.assertEqual(again['report'], first['report'])

    def test_the_wider_filters_are_found_when_they_are_asked_for(self):
        seller = self.env['res.users'].create({'name': 'EFR Speed Seller', 'login': 'efr_speed_seller',
                                               'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        self.invoice.invoice_user_id = seller
        with Statements() as seen:
            data = self.pl.get_report_data(self.year)
        self.assertFalse([q for q in seen.seen if 'DISTINCT invoice_user_id' in q], "not on every load")
        self.assertFalse(data['choices']['wide_loaded'])
        self.assertEqual(data['choices']['salespeople'], [])
        chosen = self.pl.get_report_data(dict(self.year, salespeople=[seller.id]))['choices']
        self.assertEqual(chosen['salespeople'], [{'id': seller.id, 'name': 'EFR Speed Seller'}], "a chip still has its name")
        wide = self.pl.get_wide_choices(self.year)
        self.assertTrue(wide['wide_loaded'])
        self.assertIn(seller.id, [u['id'] for u in wide['salespeople']])
        self.assertTrue(wide['product_categories'])

    def test_a_cell_is_sent_without_what_says_nothing(self):
        data = self.pl.get_report_data(self.year)
        cells = [c for l in data['lines'] for c in l['columns']]
        self.assertTrue(cells)
        self.assertFalse([c for c in cells if 'class' in c and not c['class']])
        self.assertFalse([c for c in cells if 'drill' in c and not c['drill']])
        self.assertTrue([c for c in cells if c.get('drill')], "a figure that opens still says so")
        self.assertTrue(all({'value', 'text', 'display'} <= set(c) for c in cells))

    # ------------------------------------------------------------ what changed, on a ledger
    def test_a_ledger_can_be_asked_what_changed(self):
        for key in ('general_ledger', 'trial_balance', 'partner_ledger', 'aged_receivable', 'customer_statement'):
            with self.subTest(report=key):
                out = self.Report.by_key(key).get_movers(self.year)
                self.assertIn('rows', out)
                self.assertTrue(out['now'] and out['before'])
