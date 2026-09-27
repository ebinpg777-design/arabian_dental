# -*- coding: utf-8 -*-
"""What the collection percentage is measured against, and the book it opens with.

Written as DELTAS wherever the figure is company-wide: these run against the
lab's own ledger, so "the opening is 500" is never true, but "the opening rose
by 500 when a 500 invoice was dated before the cutoff" always is.
"""
from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged

from odoo.addons.lab_collections.models.collection_performance import TREND_MONTHS


@tagged('post_install', '-at_install')
class TestCollectionBasis(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.perf = cls.env['lab.collection.performance'].sudo()
        cls.receivable = cls.env['account.account'].search([
            ('account_type', '=', 'asset_receivable'),
            ('company_ids', 'in', cls.company.id)], limit=1)
        cls.income = cls.env['account.account'].search([
            ('account_type', '=', 'income'),
            ('company_ids', 'in', cls.company.id)], limit=1)
        cls.misc = cls.env['account.journal'].search([
            ('type', '=', 'general'), ('company_id', '=', cls.company.id)], limit=1)
        cls.clinic = cls.env['res.partner'].create({'name': 'Basis Clinic'})
        cls.product = cls.env['product.product'].create(
            {'name': 'Basis Crown', 'lst_price': 1000.0})
        # January invoiced, February received: a window whose opening day is
        # 31 January, whatever today is.
        cls.options = {'sales_from': '2026-01-01', 'sales_to': '2026-01-31',
                       'pay_from': '2026-02-01', 'pay_to': '2026-02-28'}

    def _invoice(self, amount, day):
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.clinic.id,
            'invoice_date': day, 'date': day, 'company_id': self.company.id,
            'invoice_line_ids': [(0, 0, {
                'product_id': self.product.id, 'quantity': 1, 'price_unit': amount,
                'tax_ids': [(6, 0, [])]})],
        })
        invoice.action_post()
        return invoice

    def _journal_receipt(self, amount, day):
        """A receipt the way this ledger records one: a plain journal entry."""
        move = self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': self.misc.id, 'date': day,
            'line_ids': [
                (0, 0, {'account_id': self.income.id, 'debit': amount}),
                (0, 0, {'account_id': self.receivable.id, 'credit': amount,
                        'partner_id': self.clinic.id}),
            ]})
        move.action_post()
        return move

    def _open_for_clinic(self, cutoff):
        return round(sum(d['open'] for d in self.perf._open_debits(
            self.company, partner_ids=[self.clinic.id], cutoff=cutoff)), 2)

    # --------------------------------------------------------- the countback closes
    def test_the_position_as_of_a_day_ignores_what_came_after(self):
        """A statement date, not today's debt aged backwards."""
        self._invoice(700.0, '2026-01-10')
        self._journal_receipt(700.0, '2026-01-25')
        self.assertEqual(self._open_for_clinic(date(2026, 1, 5)), 0.0,
                         "nothing was owed before the invoice")
        self.assertAlmostEqual(self._open_for_clinic(date(2026, 1, 20)), 700.0, 2,
                               "the invoice was open before the receipt")
        self.assertEqual(self._open_for_clinic(date(2026, 1, 31)), 0.0,
                         "and paid after it")
        self.assertEqual(self._open_for_clinic(None), 0.0,
                         "today's countback agrees with the last reading")

    def test_the_age_is_measured_from_the_cutoff(self):
        self._invoice(300.0, '2026-01-10')
        [debit] = self.perf._open_debits(
            self.company, partner_ids=[self.clinic.id], cutoff=date(2026, 1, 20))
        self.assertEqual(debit['days'], 10)

    # ------------------------------------------------------------- the basis
    def test_the_company_defaults_to_last_periods_invoicing(self):
        """The lab's own framing, unchanged for anyone who never touches the setting."""
        self.assertEqual(self.company.lab_collection_basis, 'sales')
        self._invoice(120.0, '2026-01-12')
        data = self.perf.dashboard_data(self.options)
        self.assertEqual(data['basis'], 'sales')
        self.assertEqual(data['basis_default'], 'sales')
        totals = data['totals']
        self.assertEqual(totals['base'], totals['sales'])
        self.assertGreater(totals['sales'], 0.0)
        self.assertAlmostEqual(
            totals['percent'], round(totals['collected'] / totals['sales'] * 100, 1), 1)
        self.assertEqual(totals['pending'], round(totals['sales'] - totals['collected'], 2))

    def test_the_company_can_read_against_the_opening_receivable(self):
        self._invoice(500.0, '2026-01-15')
        self.company.lab_collection_basis = 'opening'
        data = self.perf.dashboard_data(self.options)
        self.assertEqual(data['basis'], 'opening')
        self.assertEqual(data['basis_default'], 'opening')
        self.assertEqual(data['opening_date'], '31/01/2026')
        totals = data['totals']
        self.assertEqual(totals['base'], totals['opening'])
        self.assertGreater(totals['opening'], 0.0)
        self.assertAlmostEqual(
            totals['percent'], round(totals['collected'] / totals['opening'] * 100, 1), 1)
        self.assertEqual(totals['pending'], round(totals['opening'] - totals['collected'], 2))

    def test_the_screen_can_flip_the_reading_without_touching_the_company(self):
        data = self.perf.dashboard_data(dict(self.options, basis='opening'))
        self.assertEqual(data['basis'], 'opening')
        self.assertEqual(data['basis_default'], 'sales')
        self.assertEqual(self.company.lab_collection_basis, 'sales')
        # A stale bookmark falls back; it does not take the screen down.
        self.assertEqual(self.perf.dashboard_data(dict(self.options, basis='nonsense'))['basis'],
                         'sales')

    def test_a_row_is_read_against_the_same_figure_as_the_headline(self):
        self._invoice(320.0, '2026-01-12')
        for row in self.perf.report_rows('team_id', self.options, _basis='opening'):
            self.assertEqual(row['base'], row['opening'])
            self.assertEqual(row['pending'], round(row['opening'] - row['collected'], 2))
        for row in self.perf.report_rows('team_id', self.options, _basis='sales'):
            self.assertEqual(row['base'], row['sales'])
            self.assertEqual(row['pending'], round(row['sales'] - row['collected'], 2))

    # ------------------------------------------------------- the opening figure
    def test_the_opening_figure_is_the_book_the_day_before_receipts_begin(self):
        before = self.perf.dashboard_data(self.options)['totals']
        self._invoice(500.0, '2026-01-15')                  # owed on 31 January
        after_invoice = self.perf.dashboard_data(self.options)['totals']
        self.assertAlmostEqual(after_invoice['opening'] - before['opening'], 500.0, 2)
        self.assertAlmostEqual(after_invoice['sales'] - before['sales'], 500.0, 2)
        self._journal_receipt(500.0, '2026-02-10')           # paid inside the receipts period
        after_receipt = self.perf.dashboard_data(self.options)['totals']
        self.assertAlmostEqual(after_receipt['opening'], after_invoice['opening'], 2,
                               "money received in February does not change what "
                               "was owed on 31 January")
        self.assertAlmostEqual(after_receipt['collected'] - after_invoice['collected'],
                               500.0, 2)
        self._invoice(200.0, '2026-02-05')                  # dated inside the receipts period
        after_february = self.perf.dashboard_data(self.options)['totals']
        self.assertAlmostEqual(after_february['opening'], after_receipt['opening'], 2,
                               "an invoice dated after the cutoff is not in the opening book")

    def test_the_rows_foot_to_the_headline(self):
        self._invoice(250.0, '2026-01-12')
        data = self.perf.dashboard_data(self.options)
        self.assertAlmostEqual(sum(r['opening'] for r in data['by_route']),
                               data['totals']['opening'], 2)
        self.assertAlmostEqual(sum(r['opening'] for r in data['by_user']),
                               data['totals']['opening'], 2)

    # ------------------------------------------------------------- movement
    def test_the_movement_ties_to_the_closing_book(self):
        before = self.perf.movement_data(self.options)
        self.assertAlmostEqual(
            before['opening'] + before['billed'] - before['received'] + before['other'],
            before['closing'], 2)
        self.assertAlmostEqual(before['closing'], round(sum(
            d['open'] for d in self.perf._open_debits(
                self.company, cutoff=date(2026, 2, 28))), 2), 2)
        self.assertEqual(before['opening_date'], '31/01/2026')
        self.assertEqual(before['closing_date'], '28/02/2026')
        self.assertFalse(before['closing_is_today'])
        # An invoice inside the period raises what was billed and what is open
        # at the end by the same amount, and nothing else.
        self._invoice(400.0, '2026-02-10')
        after = self.perf.movement_data(self.options)
        self.assertAlmostEqual(after['billed'] - before['billed'], 400.0, 2)
        self.assertAlmostEqual(after['closing'] - before['closing'], 400.0, 2)
        self.assertAlmostEqual(after['opening'], before['opening'], 2)
        self.assertAlmostEqual(after['other'], before['other'], 2)

    def test_a_period_still_running_closes_today(self):
        today = fields.Date.context_today(self.env.user)
        month_start = today.replace(day=1)
        options = {
            'sales_from': fields.Date.to_string(month_start - relativedelta(months=1)),
            'sales_to': fields.Date.to_string(month_start - relativedelta(days=1)),
            'pay_from': fields.Date.to_string(month_start),
            'pay_to': fields.Date.to_string(
                month_start + relativedelta(months=1) - relativedelta(days=1)),
        }
        movement = self.perf.movement_data(options)
        self.assertTrue(movement['closing_is_today'])
        self.assertEqual(movement['closing_date'], today.strftime('%d/%m/%Y'))

    # ------------------------------------------------------------ the paper
    def test_the_pdf_reads_the_same_basis_as_the_screen(self):
        self._invoice(150.0, '2026-01-12')
        self.company.lab_collection_basis = 'opening'
        values = self.env['report.lab_collections.report_collection'].sudo() \
            ._get_report_values([], {'options': self.options, 'tab': 'route'})
        self.assertEqual(values['basis'], 'opening')
        totals = values['totals']
        self.assertEqual(totals['base'], totals['opening'])
        self.assertGreater(totals['opening'], 0.0)
        self.assertAlmostEqual(
            totals['percent'], round(totals['collected'] / totals['opening'] * 100, 1), 1)
        self.assertIn('31/01/2026', values['basis_note'])
        self.assertEqual(values['opening_date'], '31/01/2026')

    # ------------------------------------------------------------ the setting
    def test_the_setting_reaches_the_company(self):
        settings = self.env['res.config.settings'].create(
            {'lab_collection_basis': 'opening'})
        settings.execute()
        self.assertEqual(self.company.lab_collection_basis, 'opening')

    # ------------------------------------------------------------ the trend
    def test_the_trend_carries_the_lab_as_a_whole(self):
        trend = self.perf.trend_data(self.options)
        self.assertIn('total', trend)
        self.assertEqual(len(trend['total']), TREND_MONTHS)
        for point in trend['total']:
            self.assertGreaterEqual(point['percent'], 0.0)

    # ------------------------------------------------------------ one pass
    def _countbacks_during(self, fn):
        """The cutoff of every FIFO countback `fn` runs (None = the book today)."""
        Model = self.env.registry['lab.collection.performance']
        original = Model._open_debits
        calls = []

        def counting(model, *args, **kwargs):
            calls.append(kwargs.get('cutoff'))
            return original(model, *args, **kwargs)

        self.patch(Model, '_open_debits', counting)
        fn()
        return calls

    def _running_period(self):
        today = fields.Date.context_today(self.env.user)
        month_start = today.replace(day=1)
        return {
            'sales_from': fields.Date.to_string(month_start - relativedelta(months=1)),
            'sales_to': fields.Date.to_string(month_start - relativedelta(days=1)),
            'pay_from': fields.Date.to_string(month_start),
            'pay_to': fields.Date.to_string(
                month_start + relativedelta(months=1) - relativedelta(days=1)),
        }

    def test_a_screen_load_runs_the_countback_twice(self):
        """Today and the opening day - the movement strip is cut from the same
        two, not run again. A period already closed needs its closing book as
        well, and nothing more."""
        today = fields.Date.context_today(self.env.user)
        running = self._countbacks_during(
            lambda: self.perf.dashboard_data(self._running_period(), with_extras=True))
        self.assertEqual(sorted(running, key=str),
                         sorted([None, today.replace(day=1) - relativedelta(days=1)], key=str))
        closed = self._countbacks_during(
            lambda: self.perf.dashboard_data(self.options, with_extras=True))
        self.assertEqual(sorted(closed, key=str),
                         sorted([None, date(2026, 1, 31), date(2026, 2, 28)], key=str))

    def test_the_paper_runs_the_countback_twice_whichever_tab(self):
        Render = self.env['report.lab_collections.report_collection'].sudo()
        for tab in ('route', 'user'):
            calls = self._countbacks_during(lambda: Render._get_report_values(
                [], {'options': self._running_period(), 'tab': tab}))
            self.assertEqual(len(calls), 2, (tab, calls))

    def test_the_strip_on_screen_is_the_strip_on_its_own(self):
        self._invoice(90.0, '2026-02-12')
        data = self.perf.dashboard_data(self.options)
        self.assertEqual(data['movement'], self.perf.movement_data(self.options))

    def test_open_at_end_of_a_running_period_is_the_open_receivable_card(self):
        data = self.perf.dashboard_data(self._running_period())
        self.assertTrue(data['movement']['closing_is_today'])
        self.assertAlmostEqual(data['movement']['closing'], data['totals']['open'], 2)
