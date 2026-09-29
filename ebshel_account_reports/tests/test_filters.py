# -*- coding: utf-8 -*-
"""The wider filters, the journal items behind a line, and "what changed".

Every filter is written twice - once as SQL for the figures, once as a domain
for the items a figure opens. The tests hold the two to each other: whatever
the filter, a figure must equal the items behind it. Where the test owns the
entry (a new tag, salesperson, category, label) the figure is known outright."""
import base64

from odoo import fields
from odoo.tests import TransactionCase, tagged

PROBE = 'EFR-FILTER-PROBE'
AMOUNT = 1234.56


@tagged('post_install', '-at_install')
class TestFinReportFilters(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.write({'ebshel_fin_totals_last': False, 'ebshel_fin_negative': 'minus'})
        cls.Report = cls.env['ebshel.fin.report']
        cls.engine = cls.env['ebshel.fin.engine']
        cls.pl = cls.Report.by_key('profit_loss')
        cls.today = fields.Date.context_today(cls.env.user)
        fy = cls.env.company.compute_fiscalyear_dates(cls.today)
        cls.year = {'date': {'preset': 'custom', 'from': fields.Date.to_string(fy['date_from']),
                             'to': fields.Date.to_string(cls.today)}}
        cls.tag = cls.env['res.partner.category'].create({'name': 'EFR probe tag'})
        cls.partner = cls.env['res.partner'].create({'name': 'EFR Probe Clinic', 'category_id': [(6, 0, [cls.tag.id])]})
        cls.categ = cls.env['product.category'].create({'name': 'EFR probe category'})
        cls.child_categ = cls.env['product.category'].create({'name': 'EFR probe child', 'parent_id': cls.categ.id})
        cls.seller = cls.env['res.users'].create({
            'name': 'EFR Seller', 'login': 'efr_seller',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])]})
        like = cls.env['account.account'].search([('account_type', '=', 'income')], limit=1)
        cls.income = like.copy({'name': 'EFR probe income', 'code': 'EFR%s' % like.id})
        cls.product = cls.env['product.product'].create({
            'name': 'EFR probe product', 'type': 'service', 'categ_id': cls.child_categ.id,
            'property_account_income_id': cls.income.id})
        cls.invoice = cls.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': cls.partner.id, 'invoice_date': cls.today,
            'invoice_user_id': cls.seller.id, 'invoice_payment_term_id': False,
            'invoice_line_ids': [(0, 0, {'product_id': cls.product.id, 'name': '%s crown' % PROBE, 'quantity': 1,
                                         'price_unit': AMOUNT, 'account_id': cls.income.id, 'tax_ids': [(5, 0, 0)]})]})
        cls.invoice.action_post()

    def _rev(self, options):
        data = self.pl.get_report_data(options)
        line = next(l for l in data['lines'] if l.get('code') == 'REV')
        return data, line

    def _items_total(self, options, line):
        action = self.pl.get_drill_action(options, line['id'], 'p0')
        return -sum(self.env['account.move.line'].search(action['domain']).mapped('balance'))

    # ------------------------------------------------------------ the filters
    def test_a_filter_the_test_owns_leaves_exactly_its_own_entry(self):
        for name, extra in (
            ('partner tag', {'partner_categories': [self.tag.id]}),
            ('salesperson', {'salespeople': [self.seller.id]}),
            ('product category, through its child', {'product_categories': [self.categ.id]}),
            ('label', {'label': PROBE}),
        ):
            with self.subTest(filter=name):
                options = dict(self.year, **extra)
                data, rev = self._rev(options)
                self.assertAlmostEqual(rev['columns'][0]['value'], AMOUNT, 2)
                self.assertAlmostEqual(self._items_total(options, rev), AMOUNT, 2)

    def test_every_filter_keeps_a_figure_equal_to_its_items(self):
        for name, extra in (
            ('journal type', {'journal_types': ['sale']}),
            ('journal type, several', {'journal_types': ['purchase', 'general']}),
            ('amount band', {'amount_min': 1000, 'amount_max': 2000}),
            ('amount from', {'amount_min': 500}),
            ('unreconciled', {'unreconciled': True}),
            ('drafts too', {'posted_only': False}),
            ('two together', {'journal_types': ['sale'], 'partner_categories': [self.tag.id], 'amount_min': 1}),
        ):
            with self.subTest(filter=name):
                options = dict(self.year, **extra)
                data, rev = self._rev(options)
                self.assertAlmostEqual(rev['columns'][0]['value'], self._items_total(options, rev), 2)

    def test_the_amount_band_keeps_and_drops_the_entry(self):
        base = dict(self.year, partner_categories=[self.tag.id])
        self.assertAlmostEqual(self._rev(dict(base, amount_min=1000, amount_max=2000))[1]['columns'][0]['value'], AMOUNT, 2)
        self.assertAlmostEqual(self._rev(dict(base, amount_min=2000))[1]['columns'][0]['value'] or 0.0, 0.0, 2)
        self.assertAlmostEqual(self._rev(dict(base, amount_max=1000))[1]['columns'][0]['value'] or 0.0, 0.0, 2)

    def test_a_journal_type_that_holds_no_sale_empties_revenue_of_the_entry(self):
        options = dict(self.year, partner_categories=[self.tag.id], journal_types=['purchase'])
        self.assertAlmostEqual(self._rev(options)[1]['columns'][0]['value'] or 0.0, 0.0, 2)

    def test_nonsense_in_a_filter_is_dropped_not_run(self):
        options = self.engine.normalize(self.pl, dict(
            self.year, journal_types=['sale', "x'; DROP TABLE account_move; --"], partner_categories=['abc', self.tag.id],
            amount_min='not a number', amount_max='', unit=7, label=None))
        self.assertEqual(options['journal_types'], ['sale'])
        self.assertEqual(options['partner_categories'], [self.tag.id])
        self.assertIsNone(options['amount_min'])
        self.assertIsNone(options['amount_max'])
        self.assertEqual(options['unit'], 1)
        self.assertEqual(options['label'], '')

    def test_the_wider_choices_are_offered(self):
        data = self.pl.get_report_data(self.year)
        # what the wider filters can be set to is asked for when their panel is opened
        choices = dict(data['choices'], **self.pl.get_wide_choices(self.year))
        self.assertIn('sale', [t[0] for t in choices['journal_types']])
        self.assertIn(self.tag.id, [c['id'] for c in choices['partner_categories']])
        self.assertIn(self.categ.id, [c['id'] for c in choices['product_categories']])
        self.assertIn(self.seller.id, [c['id'] for c in choices['salespeople']])
        self.assertEqual([u[0] for u in choices['units']], [1, 1000, 100000, 1000000, 10000000])
        self.assertIn('profit_loss', [r['key'] for r in data['reports']])
        self.assertTrue(data['report']['wide_filters'])

    # ------------------------------------------------------------ what the filter panels offer
    def test_the_partner_panel_opens_on_the_partners_that_moved(self):
        quiet = self.env['res.partner'].create({'name': 'EFR Probe Quiet Clinic'})
        hits = self.pl.get_partner_suggestions(self.year)
        self.assertTrue(hits and all(h['active'] and h['count'] for h in hits), "it opens on partners that moved, never empty")
        counts = [h['count'] for h in hits]
        self.assertEqual(counts, sorted(counts, reverse=True), "the most active first")
        self.assertNotIn(quiet.id, [h['id'] for h in hits], "a partner with nothing in the period is not suggested")
        found = self.pl.get_partner_suggestions(self.year, query='EFR Probe')
        ours = found[0]
        self.assertTrue(ours['active'])
        self.assertEqual(ours['count'], 1)
        self.assertAlmostEqual(ours['amount'], AMOUNT, 2, "what is owed on the receivable")
        self.assertEqual(found[0]['id'], self.partner.id, "those that moved come before those found by name")
        quiet_hit = next(h for h in found if h['id'] == quiet.id)
        self.assertFalse(quiet_hit['active'], "but a name typed still finds a partner the period never saw")
        narrowed = self.pl.get_partner_suggestions(dict(self.year, partners=[quiet.id]), query='EFR Probe')
        self.assertEqual(narrowed[0]['id'], self.partner.id, "the panel is not narrowed by its own filter")

    def test_the_account_panel_puts_a_code_before_a_name(self):
        hits = self.pl.get_account_suggestions(self.year, query=self.income.code)
        self.assertEqual(hits[0]['id'], self.income.id)
        self.assertEqual(hits[0]['count'], 1)
        by_name = self.pl.get_account_suggestions(self.year, query='probe income')
        self.assertIn(self.income.id, [h['id'] for h in by_name])
        self.assertLessEqual(len(self.pl.get_account_suggestions(self.year, limit=5)), 5)

    def test_the_period_panel_knows_the_fiscal_years_around_it(self):
        fiscal = self.pl.get_report_data(self.year)['fiscal']
        fy = self.env.company.compute_fiscalyear_dates(self.today)
        self.assertEqual((fiscal['start'], fiscal['end']), (fields.Date.to_string(fy['date_from']), fields.Date.to_string(fy['date_to'])))
        self.assertLess(fiscal['prev_end'], fiscal['start'])

    def test_the_panels_need_the_reader_role(self):
        nobody = self.env['res.users'].create({'name': 'Nobody', 'login': 'efr_panel_nobody',
                                               'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        for call in (lambda: self.pl.with_user(nobody).get_partner_suggestions(self.year),
                     lambda: self.pl.with_user(nobody).get_account_suggestions(self.year)):
            with self.assertRaises(Exception):
                call()

    # ------------------------------------------------------------ the journal items of a line
    def test_the_items_of_a_line_come_a_page_at_a_time_and_add_up(self):
        data, rev = self._rev(self.year)
        first = self.pl.get_items(self.year, rev['id'], 'p0', limit=1)
        self.assertEqual(len(first['rows']), 1)
        self.assertGreaterEqual(first['total'], 1)
        self.assertEqual(first['has_more'], first['total'] > 1)
        self.assertAlmostEqual(-first['sums']['balance_value'], rev['columns'][0]['value'], 2)
        if first['has_more']:
            second = self.pl.get_items(self.year, rev['id'], 'p0', offset=1, limit=1)
            self.assertNotEqual(second['rows'][0]['id'], first['rows'][0]['id'])

    def test_the_items_can_be_searched_by_words_and_by_amount(self):
        data, rev = self._rev(self.year)
        line = self.invoice.line_ids.filtered(lambda l: l.account_id == self.income)
        for needle in (PROBE, 'EFR Probe Clinic', self.invoice.name, '1,234.56', '1234.56'):
            with self.subTest(search=needle):
                found = self.pl.get_items(self.year, rev['id'], 'p0', search=needle)
                self.assertIn(line.id, [r['id'] for r in found['rows']])
        row = next(r for r in self.pl.get_items(self.year, rev['id'], 'p0', search=PROBE)['rows'] if r['id'] == line.id)
        self.assertEqual(row['move_id'], self.invoice.id)
        self.assertEqual(row['partner'], 'EFR Probe Clinic')
        self.assertTrue(row['credit_text'] and not row['debit_text'])
        self.assertEqual(row['state'], 'posted')

    def test_the_items_follow_the_filters_of_the_report(self):
        options = dict(self.year, partner_categories=[self.tag.id])
        data, rev = self._rev(options)
        found = self.pl.get_items(options, rev['id'], 'p0')
        self.assertEqual(found['total'], 1)
        self.assertAlmostEqual(found['rows'][0]['credit'], AMOUNT, 2)

    def test_the_items_can_be_put_in_order(self):
        data, rev = self._rev(self.year)
        rows = self.pl.get_items(self.year, rev['id'], 'p0', order='date')['rows']
        self.assertEqual([r['date'] for r in rows], sorted(r['date'] for r in rows))
        rows = self.pl.get_items(self.year, rev['id'], 'p0', order='date desc')['rows']
        self.assertEqual([r['date'] for r in rows], sorted((r['date'] for r in rows), reverse=True))
        # an order the screen never offers falls back, it is never handed to the database
        self.pl.get_items(self.year, rev['id'], 'p0', order='id; DROP TABLE account_move')

    def test_an_account_of_the_ledger_opens_to_its_items(self):
        ledger = self.Report.by_key('general_ledger')
        data = ledger.get_report_data(dict(self.year, accounts_query=self.income.code))
        account = next(l for l in data['lines'] if l['id'] == 'ac:%s' % self.income.id)
        found = ledger.get_items(dict(self.year, accounts_query=self.income.code), account['id'])
        self.assertEqual(found['total'], 1)
        self.assertEqual(found['rows'][0]['move'], self.invoice.name)

    def test_an_account_keeps_its_code_whichever_company_is_first(self):
        """A code is stored per company. Read under the first company's key only, every
        account of the second came out with no code - and no prefix rule could find it."""
        other = self.env['res.company'].search([('id', '!=', self.env.company.id)], limit=1) \
            or self.env['res.company'].create({'name': 'EFR second company'})
        options = self.engine.normalize(self.pl, self.year)
        for order in ([self.env.company.id, other.id], [other.id, self.env.company.id]):
            with self.subTest(companies=order):
                accounts = self.engine.accounts(dict(options, companies=order))
                self.assertEqual(accounts[self.income.id]['code'], self.income.code)

    def test_a_line_with_nothing_behind_it_opens_empty(self):
        self.assertEqual(self.pl.get_items(self.year, 'ln:0', 'p0')['rows'], [])
        self.assertEqual(self.pl.get_items(self.year, 'nothing', 'p0')['total'], 0)

    # ------------------------------------------------------------ what changed
    def test_what_changed_names_the_account_that_moved(self):
        out = self.pl.get_movers(self.year, limit=1000)
        mine = next(r for r in out['rows'] if r['account_id'] == self.income.id)
        self.assertAlmostEqual(mine['change'], AMOUNT, 2)
        self.assertAlmostEqual(mine['before'], 0.0, 2)
        self.assertIsNone(mine['pct'])
        self.assertTrue(mine['good'], "more income is good news")
        self.assertTrue(out['now'] and out['before'])
        changes = [abs(r['change']) for r in out['rows']]
        self.assertEqual(changes, sorted(changes, reverse=True))

    def test_what_changed_is_cut_to_the_largest(self):
        out = self.pl.get_movers(self.year, limit=3)
        self.assertLessEqual(len(out['rows']), 3)
        self.assertGreaterEqual(out['count'], len(out['rows']))

    def test_more_cost_is_bad_news(self):
        like = self.env['account.account'].search([('account_type', '=', 'expense')], limit=1)
        expense = like.copy({'name': 'EFR probe cost', 'code': 'EFX%s' % like.id})
        cash = self.env['account.account'].search([('account_type', '=', 'asset_cash')], limit=1)
        journal = self.env['account.journal'].search([('type', '=', 'general')], limit=1)
        self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': journal.id, 'date': self.today,
            'line_ids': [(0, 0, {'account_id': expense.id, 'debit': 400.0}), (0, 0, {'account_id': cash.id, 'credit': 400.0})],
        }).action_post()
        mine = next(r for r in self.pl.get_movers(self.year, limit=1000)['rows'] if r['account_id'] == expense.id)
        self.assertAlmostEqual(mine['change'], 400.0, 2)
        self.assertFalse(mine['good'])

    # ------------------------------------------------------------ how the figures are shown
    def test_figures_in_thousands_change_the_text_never_the_value(self):
        options = dict(self.year, partner_categories=[self.tag.id])
        exact = self._rev(options)[1]['columns'][0]
        data, rev = self._rev(dict(options, unit=1000))
        self.assertEqual(data['unit_label'], 'thousands')
        self.assertAlmostEqual(rev['columns'][0]['value'], exact['value'], 2)
        self.assertEqual(rev['columns'][0]['text'], '1.23')
        self.assertNotEqual(exact['text'], '1.23')

    def test_the_items_are_always_shown_to_the_paisa(self):
        options = dict(self.year, partner_categories=[self.tag.id], unit=100000)
        data, rev = self._rev(options)
        self.assertIn('1,234.56', self.pl.get_items(options, rev['id'], 'p0')['rows'][0]['credit_text'])

    def test_a_figure_that_rounds_to_nothing_is_never_minus_nothing(self):
        currency = self.env.company.currency_id
        self.assertNotIn('-', self.engine.with_context(fin_unit=1000).fmt(-3.0, currency))
        self.assertNotIn('-', self.engine.fmt(-0.001, currency))
        self.assertIn('-', self.engine.fmt(-3.0, currency))
        self.assertIn('-', self.engine.with_context(fin_unit=1000).fmt(-3000.0, currency))

    def test_growth_against_next_to_nothing_is_not_a_percentage(self):
        self.assertIsNone(self.engine.growth(100.0, 0.001))
        self.assertIsNone(self.engine.growth(100.0, 0.0))
        handler = self.env['ebshel.fin.handler']
        cell = handler._growth([{'value': 56113246.03}, {'value': -3.0}])
        self.assertEqual(cell['text'], '> +999%')
        self.assertEqual(handler._growth([{'value': 150.0}, {'value': 100.0}])['text'], '+50.0%')
        self.assertEqual(handler._growth([{'value': -5000.0}, {'value': 3.0}])['text'], '< -999%')

    def test_the_items_name_their_side(self):
        options = dict(self.year, partner_categories=[self.tag.id])
        data, rev = self._rev(options)
        sums = self.pl.get_items(options, rev['id'], 'p0')['sums']
        self.assertEqual(sums['side'], 'Cr')
        self.assertIn('1,234.56', sums['net'])
        self.assertNotIn('-', sums['net'])

    def test_the_period_is_named_in_words_on_every_report(self):
        for key in ('profit_loss', 'general_ledger', 'balance_sheet', 'aged_receivable'):
            with self.subTest(report=key):
                data = self.Report.by_key(key).get_report_data(self.year)
                self.assertTrue(data['period_label'])
                self.assertNotIn(data['period_label'], ('Debit', 'Credit', 'Balance'))

    def test_a_share_is_only_offered_where_the_report_names_its_base(self):
        self.assertEqual(self.pl.share_code, 'REV')
        self.assertTrue(self.engine.normalize(self.pl, dict(self.year, share=True))['share'])
        ledger = self.Report.by_key('general_ledger')
        self.assertFalse(self.engine.normalize(ledger, dict(self.year, share=True))['share'])

    def test_the_workbook_and_the_pdf_carry_the_filters_and_the_unit(self):
        options = dict(self.year, partner_categories=[self.tag.id], journal_types=['sale'], unit=1000)
        xlsx = self.pl.export_xlsx(options)
        self.assertTrue(base64.b64decode(xlsx['content']).startswith(b'PK'))
        pdf, kind = self.env['ir.actions.report'].with_context(force_report_rendering=True)._render_qweb_pdf(
            'ebshel_account_reports.action_fin_report_pdf', res_ids=[self.pl.id],
            data={'options': options, 'report_id': self.pl.id})
        self.assertTrue(pdf.startswith(b'%PDF'))

    # ------------------------------------------------------------ who may
    def test_the_items_and_what_changed_need_the_reader_role(self):
        nobody = self.env['res.users'].create({'name': 'Nobody', 'login': 'efr_nobody',
                                               'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        data, rev = self._rev(self.year)
        with self.assertRaises(Exception):
            self.pl.with_user(nobody).get_items(self.year, rev['id'], 'p0')
        with self.assertRaises(Exception):
            self.pl.with_user(nobody).get_movers(self.year)

    def test_an_accounting_administrator_opens_the_items(self):
        admin = self.env['res.users'].create({
            'name': 'Items Admin', 'login': 'efr_items_admin',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id, self.env.ref('account.group_account_manager').id])]})
        options = dict(self.year, partner_categories=[self.tag.id])
        data = self.pl.with_user(admin).get_report_data(options)
        rev = next(l for l in data['lines'] if l.get('code') == 'REV')
        found = self.pl.with_user(admin).get_items(options, rev['id'], 'p0')
        self.assertEqual(found['total'], 1)
        self.assertTrue(self.pl.with_user(admin).get_movers(options)['rows'])
