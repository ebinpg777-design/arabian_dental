# -*- coding: utf-8 -*-
"""An asset from purchase to disposal, and the arithmetic in between."""
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tools import date_utils
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAssets(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        # the dates below are calendar years; a yearly asset follows the company's fiscal year
        cls.company.write({'fiscalyear_last_day': 31, 'fiscalyear_last_month': '12'})
        Account = cls.env['account.account']

        def account(code, name, atype):
            acc = Account.search([('account_type', '=', atype), ('code', '=', code)], limit=1)
            return acc or Account.create({'code': code, 'name': name, 'account_type': atype})
        cls.gross = account('ZZ9010', 'Test equipment', 'asset_fixed')
        cls.accum = account('ZZ9011', 'Test accumulated depreciation', 'asset_fixed')
        cls.expense = account('ZZ9012', 'Test depreciation expense', 'expense_depreciation')
        cls.gain = account('ZZ9013', 'Test gain on disposal', 'income_other')
        cls.loss = account('ZZ9014', 'Test loss on disposal', 'expense')
        cls.clearing = account('ZZ9015', 'Test disposal clearing', 'asset_current')
        cls.reserve = account('ZZ9016', 'Test revaluation reserve', 'equity')
        cls.sales = account('ZZ9017', 'Test other income', 'income')
        cls.payable = Account.search([('account_type', '=', 'liability_payable')], limit=1)
        cls.journal = cls.env['account.journal'].search([('type', '=', 'general'), ('company_id', '=', cls.company.id)], limit=1)
        cls.supplier = cls.env['res.partner'].create({'name': 'Equipment Co'})
        cls.category = cls.env['ebshel.asset.category'].create({
            'name': 'Test equipment', 'asset_account_id': cls.gross.id, 'depreciation_account_id': cls.accum.id,
            'expense_account_id': cls.expense.id, 'gain_account_id': cls.gain.id, 'loss_account_id': cls.loss.id,
            'journal_id': cls.journal.id, 'method': 'linear', 'period': 'year', 'duration': 5, 'prorata': 'days',
            'bill_trigger': 'draft', 'trigger_account_ids': [(6, 0, [cls.gross.id])], 'auto_post': True})

    def _asset(self, value=120000.0, **vals):
        base = {'name': 'Chair', 'category_id': self.category.id, 'purchase_date': date(2025, 4, 1),
                'start_date': date(2025, 4, 1), 'purchase_value': value, 'partner_id': self.supplier.id}
        base.update(vals)
        return self.env['ebshel.asset'].create(base)

    # ------------------------------------------------------------ schedules
    def test_a_straight_line_schedule_sums_to_the_depreciable_value(self):
        asset = self._asset(120000.0, salvage_value=20000.0)
        asset.action_confirm()
        lines = asset.line_ids
        self.assertEqual(asset.state, 'running')
        self.assertEqual(len(lines), 6, "five years plus the prorated first one")
        self.assertAlmostEqual(sum(lines.mapped('amount')), 100000.0, 2)
        # April to the year end is 275 of 365 days: the first line is that share of a year's 20,000
        self.assertAlmostEqual(lines[0].amount, round(20000.0 * 275 / 365, 2), 2)
        self.assertEqual(lines[0].date, date(2025, 12, 31))
        self.assertAlmostEqual(lines[-1].remaining, 20000.0, 2)

    def test_a_full_first_period_schedule_is_even(self):
        asset = self._asset(50000.0, prorata='none')
        asset.action_confirm()
        self.assertEqual(len(asset.line_ids), 5)
        self.assertTrue(all(abs(l.amount - 10000.0) < 0.01 for l in asset.line_ids))

    def test_declining_balance_takes_a_share_of_what_is_left(self):
        asset = self._asset(100000.0, method='declining', declining_rate=40.0, prorata='none', duration=4)
        asset.action_confirm()
        amounts = asset.line_ids.mapped('amount')
        self.assertEqual(len(amounts), 4)
        self.assertAlmostEqual(amounts[0], 40000.0, 2)
        self.assertAlmostEqual(amounts[1], 24000.0, 2)
        self.assertAlmostEqual(sum(amounts), 100000.0, 2, "the last line takes the rest")
        mixed = self._asset(100000.0, method='declining_linear', declining_rate=40.0, prorata='none', duration=4)
        mixed.action_confirm()
        m = mixed.line_ids.mapped('amount')
        self.assertAlmostEqual(m[0], 40000.0, 2)
        self.assertGreaterEqual(m[2], m[3] - 0.01, "straight line takes over when it is larger")
        self.assertAlmostEqual(sum(m), 100000.0, 2)

    def test_a_monthly_schedule_has_a_line_a_month(self):
        asset = self._asset(12000.0, period='month', duration=12, prorata='none')
        asset.action_confirm()
        self.assertEqual(len(asset.line_ids), 12)
        self.assertEqual(asset.line_ids[0].date, date(2025, 4, 30))
        self.assertEqual(asset.line_ids[-1].date, date(2026, 3, 31))
        self.assertAlmostEqual(asset.line_ids[3].amount, 1000.0, 2)

    def test_an_imported_asset_starts_after_what_was_already_depreciated(self):
        asset = self._asset(100000.0, already_depreciated=40000.0, prorata='none', duration=3)
        asset.action_confirm()
        self.assertAlmostEqual(sum(asset.line_ids.mapped('amount')), 60000.0, 2)
        self.assertAlmostEqual(asset.book_value, 60000.0, 2)

    # ------------------------------------------------------------ posting
    def test_posting_a_line_makes_a_balanced_entry_on_the_right_accounts(self):
        asset = self._asset(50000.0, prorata='none')
        asset.action_confirm()
        line = asset.line_ids[0]
        line.action_post()
        self.assertEqual(line.state, 'posted')
        move = line.move_id
        self.assertEqual(move.state, 'posted')
        self.assertEqual(move.ebshel_asset_id, asset)
        debit = move.line_ids.filtered(lambda l: l.debit)
        credit = move.line_ids.filtered(lambda l: l.credit)
        self.assertEqual(debit.account_id, self.expense)
        self.assertEqual(credit.account_id, self.accum)
        self.assertAlmostEqual(debit.debit, 10000.0, 2)
        self.assertAlmostEqual(asset.book_value, 40000.0, 2)
        self.assertIn('posted', asset.event_ids.mapped('kind'))
        with self.assertRaises(Exception):
            line.unlink()

    def test_the_nightly_job_posts_only_what_is_due(self):
        asset = self._asset(50000.0, prorata='none', purchase_date=date(2020, 1, 1), start_date=date(2020, 1, 1))
        asset.action_confirm()
        self.env['ebshel.asset']._cron_post_due()
        today = fields.Date.context_today(self.env.user)
        posted = asset.line_ids.filtered(lambda l: l.state == 'posted')
        self.assertTrue(posted)
        self.assertTrue(all(l.date <= today for l in posted))
        self.assertTrue(all(l.date > today for l in asset.line_ids - posted))
        if not (asset.line_ids - posted):
            self.assertEqual(asset.state, 'closed')

    def test_a_fully_posted_asset_closes_itself(self):
        asset = self._asset(3000.0, prorata='none', duration=3, purchase_date=date(2020, 1, 1), start_date=date(2020, 1, 1))
        asset.action_confirm()
        asset.line_ids.action_post()
        self.assertEqual(asset.state, 'closed')
        self.assertAlmostEqual(asset.book_value, 0.0, 2)

    # ------------------------------------------------------------ pause, modify, dispose
    def test_pausing_moves_the_schedule_on(self):
        asset = self._asset(50000.0, prorata='none')
        asset.action_confirm()
        first = asset.line_ids[0].date
        asset.action_pause()
        self.assertEqual(asset.state, 'paused')
        asset.action_resume()
        self.assertEqual(asset.state, 'running')
        self.assertGreaterEqual(asset.line_ids[0].date, first)

    def test_a_longer_life_rebuilds_the_future_only(self):
        asset = self._asset(50000.0, prorata='none')
        asset.action_confirm()
        asset.line_ids[0].action_post()
        wizard = self.env['ebshel.asset.change'].create({'asset_id': asset.id, 'kind': 'life', 'date': date(2026, 6, 30),
                                                         'duration': 8, 'salvage_value': 0.0, 'note': 'kept longer'})
        wizard.action_apply()
        drafts = asset.line_ids.filtered(lambda l: l.state == 'draft')
        self.assertEqual(len(drafts), 8)
        self.assertAlmostEqual(sum(drafts.mapped('amount')), 40000.0, 2)
        self.assertEqual(asset.line_ids.filtered(lambda l: l.state == 'posted').mapped('amount'), [10000.0])
        self.assertIn('modified', asset.event_ids.mapped('kind'))

    def test_an_increase_is_a_child_asset(self):
        asset = self._asset(50000.0, prorata='none')
        asset.action_confirm()
        self.env['ebshel.asset.change'].create({'asset_id': asset.id, 'kind': 'increase', 'date': date(2025, 9, 1),
                                                'amount': 5000.0, 'account_id': self.reserve.id,
                                                'note': 'new motor'}).action_apply()
        child = asset.child_ids
        self.assertEqual(len(child), 1)
        self.assertEqual(child.state, 'running')
        self.assertAlmostEqual(child.purchase_value, 5000.0, 2)
        self.assertAlmostEqual(sum(child.line_ids.mapped('amount')), 5000.0, 2)

    def test_an_impairment_posts_and_rebuilds(self):
        asset = self._asset(50000.0, prorata='none')
        asset.action_confirm()
        self.env['ebshel.asset.change'].create({'asset_id': asset.id, 'kind': 'decrease', 'date': date(2025, 9, 1),
                                                'amount': 8000.0, 'note': 'damaged'}).action_apply()
        impairment = asset.line_ids.filtered(lambda l: l.kind == 'impairment')
        self.assertEqual(impairment.state, 'posted')
        self.assertEqual(impairment.move_id.line_ids.filtered(lambda l: l.debit).account_id, self.loss)
        self.assertAlmostEqual(asset.book_value, 42000.0, 2)
        self.assertAlmostEqual(sum(asset.line_ids.filtered(lambda l: l.state == 'draft').mapped('amount')), 42000.0, 2)

    def test_a_disposal_writes_the_book_value_off(self):
        asset = self._asset(50000.0, prorata='none')
        asset.action_confirm()
        asset.line_ids[0].action_post()                       # 40,000 left
        wizard = self.env['ebshel.asset.dispose'].create({'asset_id': asset.id, 'kind': 'sale', 'date': date(2026, 6, 30),
                                                          'proceeds': 30000.0, 'proceeds_account_id': self.clearing.id,
                                                          'note': 'sold to a colleague'})
        wizard.action_apply()
        self.assertEqual(asset.state, 'disposed')
        # half of 2026 was depreciated first (5,000), then 35,000 written off
        posted = asset.line_ids.filtered(lambda l: l.state == 'posted' and l.kind == 'depreciation')
        self.assertEqual(len(posted), 2)
        self.assertAlmostEqual(posted[-1].amount, round(10000.0 * 181 / 365, 2), 2)
        move = asset.disposal_move_id
        self.assertEqual(move.state, 'posted')
        self.assertAlmostEqual(sum(move.line_ids.mapped('debit')), sum(move.line_ids.mapped('credit')), 2)
        gross_line = move.line_ids.filtered(lambda l: l.account_id == self.gross)
        self.assertAlmostEqual(gross_line.credit, 50000.0, 2)
        self.assertAlmostEqual(move.line_ids.filtered(lambda l: l.account_id == self.accum).debit, asset.depreciated_value, 2)
        book_before = 50000.0 - asset.depreciated_value
        # the proceeds come off the clearing account; only the difference is a loss
        self.assertAlmostEqual(move.line_ids.filtered(lambda l: l.account_id == self.clearing).debit, 30000.0, 2)
        self.assertAlmostEqual(move.line_ids.filtered(lambda l: l.account_id == self.loss).debit, book_before - 30000.0, 2)
        self.assertAlmostEqual(asset.gain_loss, 30000.0 - book_before, 2)
        self.assertTrue(all(l.state == 'skipped' for l in asset.line_ids if l.kind == 'depreciation' and l not in posted))

    # ------------------------------------------------------------ bills
    def _bill(self, qty=1, price=45000.0):
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.supplier.id, 'invoice_date': date(2025, 5, 10),
            'invoice_line_ids': [(0, 0, {'name': 'Dental chair', 'quantity': qty, 'price_unit': price,
                                         'account_id': self.gross.id, 'tax_ids': [(6, 0, [])]})]})
        bill.action_post()
        return bill

    def test_a_vendor_bill_creates_a_draft_asset(self):
        bill = self._bill()
        asset = bill.ebshel_asset_ids
        self.assertEqual(len(asset), 1)
        self.assertEqual(asset.state, 'draft')
        self.assertAlmostEqual(asset.purchase_value, 45000.0, 2)
        self.assertEqual(asset.purchase_date, date(2025, 5, 10))
        self.assertEqual(asset.partner_id, self.supplier)
        self.assertEqual(asset.bill_line_id.move_id, bill)
        self.assertEqual(bill.ebshel_asset_count, 1)

    def test_a_category_can_start_them_running_and_split_by_unit(self):
        self.category.write({'bill_trigger': 'running', 'one_per_unit': True})
        bill = self._bill(qty=3, price=10000.0)
        assets = bill.ebshel_asset_ids
        self.assertEqual(len(assets), 3)
        self.assertTrue(all(a.state == 'running' for a in assets))
        self.assertAlmostEqual(sum(assets.mapped('purchase_value')), 30000.0, 2)

    def test_a_bill_on_another_account_makes_nothing(self):
        other = self.env['account.account'].search([('account_type', '=', 'expense'), ('id', '!=', self.loss.id)], limit=1)
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.supplier.id, 'invoice_date': date(2025, 5, 10),
            'invoice_line_ids': [(0, 0, {'name': 'Gloves', 'quantity': 1, 'price_unit': 500.0,
                                         'account_id': other.id, 'tax_ids': [(6, 0, [])]})]})
        bill.action_post()
        self.assertFalse(bill.ebshel_asset_ids)

    def test_an_invoicing_user_posts_vendor_bills(self):
        """The Invoicing role alone posts bills. Posting looks for the asset categories, which
        that role could not read: every vendor bill failed with an access error (seen on 20.0
        with a real, non-superuser login)."""
        clerk = self.env['res.users'].create({
            'name': 'Bill Clerk', 'login': 'eaa_bill_clerk',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id, self.env.ref('account.group_account_invoice').id])]})
        Move = self.env['account.move'].with_user(clerk)
        other = self.env['account.account'].search([('account_type', '=', 'expense'), ('id', '!=', self.loss.id)], limit=1)
        plain = Move.create({
            'move_type': 'in_invoice', 'partner_id': self.supplier.id, 'invoice_date': date(2025, 5, 10),
            'invoice_line_ids': [(0, 0, {'name': 'Gloves', 'quantity': 1, 'price_unit': 500.0,
                                         'account_id': other.id, 'tax_ids': [(6, 0, [])]})]})
        plain.action_post()
        self.assertEqual(plain.state, 'posted')
        on_asset = Move.create({
            'move_type': 'in_invoice', 'partner_id': self.supplier.id, 'invoice_date': date(2025, 5, 10),
            'invoice_line_ids': [(0, 0, {'name': 'Lathe', 'quantity': 1, 'price_unit': 9000.0,
                                         'account_id': self.gross.id, 'tax_ids': [(6, 0, [])]})]})
        on_asset.action_post()
        self.assertEqual(len(on_asset.sudo().ebshel_asset_ids), 1, "the bill still makes its asset")

    # ------------------------------------------------------------ reports, lifecycle, simulator, dashboard
    def test_the_schedule_report_foots_to_the_assets(self):
        asset = self._asset(50000.0, prorata='none')
        asset.action_confirm()
        asset.line_ids[0].action_post()
        report = self.env['ebshel.fin.report'].by_key('asset_schedule')
        data = report.get_report_data({'date': {'preset': 'custom', 'from': '2025-01-01', 'to': '2025-12-31'}, 'unfold_all': True})
        row = next(l for l in data['lines'] if l.get('res_id') == asset.id)
        gross, before, period, todate, book = [c['value'] for c in row['columns']]
        self.assertAlmostEqual(gross, 50000.0, 2)
        self.assertAlmostEqual(period, 10000.0, 2)
        self.assertAlmostEqual(book, 40000.0, 2)
        total = next(l for l in data['lines'] if l['kind'] == 'total')
        self.assertGreaterEqual(total['columns'][0]['value'], 50000.0)

    def test_the_lifecycle_carries_events_and_the_curve(self):
        asset = self._asset(50000.0, prorata='none')
        asset.action_confirm()
        asset.line_ids[0].action_post()
        data = asset.lifecycle_json
        self.assertEqual([e['kind'] for e in data['events']][:3], ['created', 'running', 'posted'])
        self.assertEqual(len(data['curve']), 6)
        self.assertAlmostEqual(data['curve'][1]['book'], 40000.0, 2)
        self.assertTrue(data['curve'][1]['posted'] and not data['curve'][2]['posted'])

    def test_the_simulator_compares_the_methods(self):
        sim = self.env['ebshel.asset.simulate'].create({'purchase_value': 100000.0, 'salvage_value': 10000.0,
                                                        'start_date': date(2025, 1, 1), 'period': 'year',
                                                        'duration': 5, 'declining_rate': 30.0, 'prorata': 'none'})
        sim.action_run()
        self.assertIn('Straight line', sim.result_html)
        self.assertIn('Declining', sim.result_html)

    def test_the_dashboard_adds_up(self):
        asset = self._asset(50000.0, prorata='none')
        asset.action_confirm()
        data = self.env['ebshel.asset.dashboard'].get_data()
        kpis = {k['key']: k for k in data['kpis']}
        self.assertGreaterEqual(kpis['gross']['value'], 50000.0)
        self.assertGreaterEqual(kpis['book']['value'], 50000.0)
        self.assertTrue(any(c['name'] == 'Test equipment' for c in data['categories']))

    def test_the_label_and_the_register_render(self):
        asset = self._asset(50000.0)
        for ref in ('ebshel_account_assets.action_report_asset_label', 'ebshel_account_assets.action_report_asset_register'):
            pdf, kind = self.env['ir.actions.report'].with_context(force_report_rendering=True)._render_qweb_pdf(ref, res_ids=[asset.id])
            self.assertEqual(kind, 'pdf')
            self.assertTrue(pdf.startswith(b'%PDF'))

    # ------------------------------------------------------------ defects found and fixed
    def test_monthly_declining_takes_the_yearly_rate_of_the_years_opening_value(self):
        """The rate is applied to the book value at the start of each year, a twelfth a month.
        It was applied to what was left each month, compounding: 30% took 26.2% a year."""
        asset = self._asset(120000.0, method='declining', declining_rate=30.0, period='month', duration=60,
                            prorata='none', purchase_date=date(2025, 1, 1), start_date=date(2025, 1, 1))
        asset.action_confirm()
        lines = asset.line_ids.sorted('date')
        first_year = lines.filtered(lambda l: l.date.year == 2025)
        self.assertEqual(len(first_year), 12)
        self.assertTrue(all(abs(l.amount - 3000.0) < 0.01 for l in first_year), first_year.mapped('amount'))
        second_year = lines.filtered(lambda l: l.date.year == 2026)
        self.assertAlmostEqual(sum(second_year.mapped('amount')), 84000.0 * 0.30, 1)
        self.assertAlmostEqual(sum(lines.mapped('amount')), 120000.0, 2)

    def test_a_change_keeps_the_depreciation_of_the_period_it_falls_in(self):
        """Modified or written down in June, the year's depreciation was deleted and never made
        again: that year had none, the later years carried it."""
        asset = self._asset(50000.0, prorata='none')
        asset.action_confirm()
        asset.line_ids[0].action_post()                       # 2025
        self.env['ebshel.asset.change'].create({'asset_id': asset.id, 'kind': 'decrease', 'date': date(2026, 6, 30),
                                                'amount': 4000.0, 'note': 'damaged'}).action_apply()
        drafts = asset.line_ids.filtered(lambda l: l.state == 'draft').sorted('date')
        self.assertEqual(drafts[0].date, date(2026, 12, 31), "2026 is still depreciated")
        self.assertAlmostEqual(drafts[0].amount, 10000.0, 2)
        self.assertEqual(drafts[-1].date, date(2029, 12, 31), "the life stays what it was")
        self.assertAlmostEqual(sum(drafts.mapped('amount')), 36000.0, 2)
        self.assertAlmostEqual(asset.book_value, 36000.0, 2)

    def test_a_write_down_of_a_prorated_asset_keeps_its_last_period(self):
        asset = self._asset(50000.0)                          # from April: six lines, 2025 to 2030
        asset.action_confirm()
        self.assertEqual(asset.line_ids.sorted('date')[-1].date, date(2030, 12, 31))
        asset.line_ids.sorted('date')[:2].action_post()       # 2025 and 2026
        self.env['ebshel.asset.change'].create({'asset_id': asset.id, 'kind': 'decrease', 'date': date(2027, 3, 31),
                                                'amount': 1000.0, 'note': 'scratched'}).action_apply()
        drafts = asset.line_ids.filtered(lambda l: l.state == 'draft').sorted('date')
        self.assertEqual(drafts.mapped('date'), [date(2027, 12, 31), date(2028, 12, 31), date(2029, 12, 31), date(2030, 12, 31)])
        self.assertAlmostEqual(asset.depreciated_value + sum(drafts.mapped('amount')), 50000.0, 2)

    def test_disposing_in_the_first_prorated_period_counts_the_days_it_was_held(self):
        """The first line already covers only July to December; it was prorated again over the
        whole year, depreciating January to June that the asset never saw."""
        asset = self._asset(36500.0, purchase_date=date(2025, 7, 2), start_date=date(2025, 7, 2))
        asset.action_confirm()
        first = asset.line_ids.sorted('date')[0].amount       # the disposal rewrites that line
        self.assertAlmostEqual(first, round(7300.0 * 183 / 365, 2), 2)
        self.env['ebshel.asset.dispose'].create({'asset_id': asset.id, 'kind': 'scrap', 'date': date(2025, 9, 30),
                                                 'note': 'broken'}).action_apply()
        posted = asset.line_ids.filtered(lambda l: l.state == 'posted' and l.kind == 'depreciation')
        self.assertEqual(len(posted), 1)
        self.assertAlmostEqual(posted.amount, round(first * 91 / 183, 2), 2)

    def test_a_bill_in_another_currency_makes_an_asset_of_its_cost_here(self):
        other = self.env.ref('base.EUR') if self.company.currency_id != self.env.ref('base.EUR') else self.env.ref('base.GBP')
        other.active = True
        self.env['res.currency.rate'].create({'currency_id': other.id, 'name': '2025-01-01', 'rate': 0.3,
                                              'company_id': self.company.id})
        self.category.write({'one_per_unit': True})
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.supplier.id, 'invoice_date': date(2025, 5, 10),
            'currency_id': other.id,
            'invoice_line_ids': [
                (0, 0, {'name': 'Stool', 'quantity': 3, 'price_unit': 10.0, 'account_id': self.gross.id, 'tax_ids': [(6, 0, [])]}),
                (0, 0, {'name': 'Discount', 'quantity': 1, 'price_unit': -3.0, 'account_id': self.gross.id, 'tax_ids': [(6, 0, [])]}),
            ]})
        bill.action_post()                                    # the credit line made a negative asset: the bill refused
        assets = bill.ebshel_asset_ids
        stool = bill.invoice_line_ids.filtered(lambda l: l.name == 'Stool')
        self.assertEqual(len(assets), 3)
        self.assertAlmostEqual(stool.balance, 100.0, 2)
        self.assertEqual(sorted(assets.mapped('purchase_value')), [33.33, 33.33, 33.34])

    def test_the_schedule_report_leaves_out_what_left_before_the_period(self):
        asset = self._asset(50000.0, prorata='none')
        asset.action_confirm()
        asset.line_ids.sorted('date')[0].action_post()
        self.env['ebshel.asset.dispose'].create({'asset_id': asset.id, 'kind': 'sale', 'date': date(2026, 6, 30),
                                                 'proceeds': 30000.0, 'proceeds_account_id': self.clearing.id,
                                                 'note': 'sold'}).action_apply()
        report = self.env['ebshel.fin.report'].by_key('asset_schedule')
        later = report.get_report_data({'date': {'preset': 'custom', 'from': '2027-01-01', 'to': '2027-12-31'}, 'unfold_all': True})
        self.assertFalse([l for l in later['lines'] if l.get('res_id') == asset.id and l.get('kind') == 'asset'])
        year = {'date': {'preset': 'custom', 'from': '2026-01-01', 'to': '2026-12-31'}, 'unfold_all': True}
        data = report.get_report_data(year)
        row = next(l for l in data['lines'] if l.get('res_id') == asset.id and l.get('kind') == 'asset')
        period = row['columns'][2]['value']
        self.assertAlmostEqual(period, round(10000.0 * 181 / 365, 2), 2)
        _engine, _options, domain, _title, _other = report._items_domain(year, row['id'], 'period')
        self.assertAlmostEqual(sum(self.env['account.move.line'].search(domain).mapped('credit')), period, 2)

    def test_a_companys_depreciation_lines_stay_in_that_company(self):
        elsewhere = self.env['res.company'].create({'name': 'EAA elsewhere'})
        Account = self.env['account.account'].with_company(elsewhere)
        accounts = {key: Account.create({'code': 'EAAX%d' % i, 'name': key, 'account_type': atype,
                                         'company_ids': [(6, 0, [elsewhere.id])]})
                    for i, (key, atype) in enumerate((('gross', 'asset_fixed'), ('accum', 'asset_fixed'),
                                                       ('expense', 'expense_depreciation')))}
        journal = self.env['account.journal'].create({'name': 'EAA misc', 'code': 'EAAX', 'type': 'general',
                                                      'company_id': elsewhere.id})
        Category = self.env['ebshel.asset.category'].with_company(elsewhere)
        category = Category.create({
            'name': 'Elsewhere', 'company_id': elsewhere.id, 'asset_account_id': accounts['gross'].id,
            'depreciation_account_id': accounts['accum'].id, 'expense_account_id': accounts['expense'].id,
            'journal_id': journal.id, 'period': 'year', 'duration': 3, 'prorata': 'none'})
        asset = self.env['ebshel.asset'].with_company(elsewhere).create({
            'name': 'Far away', 'category_id': category.id, 'company_id': elsewhere.id, 'purchase_value': 3000.0,
            'purchase_date': date(2025, 1, 1), 'start_date': date(2025, 1, 1)})
        asset.action_confirm()
        self.assertTrue(asset.line_ids)
        clerk = self.env['res.users'].create({
            'name': 'EAA clerk', 'login': 'eaa_clerk_here',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id, self.env.ref('account.group_account_invoice').id])]})
        self.assertFalse(self.env['ebshel.asset.line'].with_user(clerk).search([('asset_id', '=', asset.id)]))
        self.assertFalse(self.env['ebshel.asset.event'].with_user(clerk).search([('asset_id', '=', asset.id)]))

    # ------------------------------------------------------------ review fixes (2026-10-13)
    def test_the_cover_filter_finds_the_state_the_asset_shows(self):
        """Odoo 20 hands the search `in` with a set: every search fell to 'covered'."""
        today = fields.Date.context_today(self.env.user)
        assets = self.env['ebshel.asset']
        for warranty, insurance in ((None, None), (-1, 400), (10, None), (10, 400), (100, 200), (None, 100)):
            assets |= self._asset(1000.0, warranty_end=warranty is not None and today + timedelta(days=warranty),
                                  insurance_end=insurance is not None and today + timedelta(days=insurance))
        Asset = self.env['ebshel.asset']
        for state in ('none', 'expired', 'soon', 'ok'):
            expected = assets.filtered(lambda a: a.cover_state == state)
            self.assertTrue(expected, state)
            self.assertEqual(Asset.search([('id', 'in', assets.ids), ('cover_state', '=', state)]), expected, state)
            self.assertEqual(Asset.search([('id', 'in', assets.ids), ('cover_state', '!=', state)]), assets - expected, state)
        expiring = Asset.search([('id', 'in', assets.ids), ('cover_state', 'in', ('soon', 'expired'))])
        self.assertEqual(expiring, assets.filtered(lambda a: a.cover_state in ('soon', 'expired')))
        self.assertEqual(len(expiring), 3, "one expired, and two with a date inside the 60 days")

    def test_a_short_pause_keeps_the_schedule_and_a_long_one_moves_it_by_whole_periods(self):
        """A one-day pause pushed a yearly schedule a year; monthly lines landed two in a month."""
        today = fields.Date.context_today(self.env.user)
        yearly = self._asset(50000.0, prorata='none')
        yearly.action_confirm()
        before = yearly.line_ids.sorted('date').mapped('date')
        yearly.action_pause()
        yearly.paused_on = today - timedelta(days=1)
        yearly.action_resume()
        self.assertEqual(yearly.line_ids.sorted('date').mapped('date'), before)
        monthly = self._asset(12000.0, period='month', duration=12, prorata='none')
        monthly.action_confirm()
        before = monthly.line_ids.sorted('date').mapped('date')
        monthly.action_pause()
        monthly.paused_on = today - relativedelta(months=2, days=3)
        monthly.action_resume()
        after = monthly.line_ids.sorted('date').mapped('date')
        self.assertEqual(after, [date_utils.end_of(d + relativedelta(months=2), 'month') for d in before])
        self.assertEqual(len({(d.year, d.month) for d in after}), 12, "still one line a month")

    def test_an_increase_is_posted_and_leaves_with_its_asset(self):
        """The increase depreciated a value no entry had debited; disposing of the parent left it running."""
        asset = self._asset(50000.0, prorata='none')
        asset.action_confirm()
        Change = self.env['ebshel.asset.change']
        with self.assertRaises(UserError):
            Change.create({'asset_id': asset.id, 'kind': 'increase', 'date': date(2025, 9, 1),
                           'amount': 5000.0, 'note': 'no account'}).action_apply()
        Change.create({'asset_id': asset.id, 'kind': 'increase', 'date': date(2025, 9, 1), 'amount': 5000.0,
                       'account_id': self.reserve.id, 'note': 'new motor'}).action_apply()
        child = asset.child_ids
        increase = child.move_ids.line_ids.filtered(lambda l: l.account_id == self.gross)
        self.assertAlmostEqual(increase.debit, 5000.0, 2)
        self.assertAlmostEqual(child.move_ids.line_ids.filtered(lambda l: l.account_id == self.reserve).credit, 5000.0, 2)
        self.env['ebshel.asset.dispose'].create({'asset_id': asset.id, 'kind': 'scrap', 'date': date(2025, 12, 31),
                                                 'note': 'worn out'}).action_apply()
        self.assertEqual(child.state, 'disposed')
        gross = child.move_ids.line_ids.filtered(lambda l: l.account_id == self.gross and l.parent_state == 'posted')
        self.assertAlmostEqual(sum(gross.mapped('balance')), 0.0, 2, "the increase left the asset account too")

    def test_a_yearly_asset_follows_the_companys_fiscal_year(self):
        self.company.write({'fiscalyear_last_day': 31, 'fiscalyear_last_month': '3'})
        asset = self._asset(36500.0, purchase_date=date(2025, 7, 1), start_date=date(2025, 7, 1))
        asset.action_confirm()
        lines = asset.line_ids.sorted('date')
        self.assertEqual(lines[0].date, date(2026, 3, 31))
        # July to March is 274 of the 365 days of April 2025 - March 2026
        self.assertAlmostEqual(lines[0].amount, round(7300.0 * 274 / 365, 2), 2)
        self.assertEqual(lines[1].date, date(2027, 3, 31))
        self.assertEqual(len(lines), 6)
        self.assertAlmostEqual(sum(lines.mapped('amount')), 36500.0, 2)

    def test_a_bill_and_its_assets_stay_in_step(self):
        bill = self._bill()
        draft = bill.ebshel_asset_ids
        bill.button_draft()
        self.assertEqual(draft.state, 'cancelled', "reset, the bill's unbooked asset is cancelled")
        bill.action_post()
        live = bill.ebshel_asset_ids.filtered(lambda a: a.state != 'cancelled')
        self.assertEqual(len(live), 1)
        self.assertNotEqual(live, draft)
        live.active = False
        self.assertFalse(bill._ebshel_create_assets(auto=False), "an archived asset is not made a second time")
        live.active = True
        live.action_confirm()
        line = live.line_ids.sorted('date')[0]
        line.action_post()
        with self.assertRaises(UserError):
            bill.button_draft()
        with self.assertRaises(UserError):
            bill.button_cancel()
        with self.assertRaises(UserError):
            line.move_id.button_draft()
        self.assertEqual(line.move_id.state, 'posted')

    def test_a_change_or_disposal_before_posted_depreciation_is_refused(self):
        asset = self._asset(50000.0, prorata='none')
        asset.action_confirm()
        asset.line_ids.sorted('date')[:2].action_post()      # 2025 and 2026
        with self.assertRaises(UserError):
            self.env['ebshel.asset.change'].create({'asset_id': asset.id, 'kind': 'life', 'date': date(2026, 6, 30),
                                                    'duration': 5, 'salvage_value': 0.0, 'note': 'late'}).action_apply()
        with self.assertRaises(UserError):
            self.env['ebshel.asset.dispose'].create({'asset_id': asset.id, 'kind': 'scrap', 'date': date(2026, 6, 30),
                                                     'note': 'late'}).action_apply()
        self.assertEqual(len(asset.line_ids.filtered(lambda l: l.state == 'draft')), 3)

    def test_a_sale_invoice_in_another_currency_books_the_gain_here(self):
        other = self.env.ref('base.EUR') if self.company.currency_id != self.env.ref('base.EUR') else self.env.ref('base.GBP')
        other.active = True
        self.env['res.currency.rate'].create({'currency_id': other.id, 'name': '2025-01-01', 'rate': 0.3,
                                              'company_id': self.company.id})
        asset = self._asset(50000.0, prorata='none')
        asset.action_confirm()
        asset.line_ids.sorted('date')[0].action_post()       # 40,000 left
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.supplier.id, 'invoice_date': date(2025, 12, 31),
            'currency_id': other.id,
            'invoice_line_ids': [(0, 0, {'name': 'Used chair', 'quantity': 1, 'price_unit': 15000.0,
                                         'account_id': self.sales.id, 'tax_ids': [(6, 0, [])]})]})
        invoice.action_post()
        self.env['ebshel.asset.dispose'].create({'asset_id': asset.id, 'kind': 'sale', 'date': date(2025, 12, 31),
                                                 'invoice_id': invoice.id, 'note': 'sold'}).action_apply()
        self.assertAlmostEqual(asset.disposal_value, 50000.0, 2, "15,000 at 0.3 is 50,000 here")
        self.assertAlmostEqual(asset.gain_loss, 10000.0, 2)
        move = asset.disposal_move_id
        self.assertAlmostEqual(move.line_ids.filtered(lambda l: l.account_id == self.gain).credit, 10000.0, 2)
        self.assertFalse(move.line_ids.filtered(lambda l: l.account_id == self.loss))
        income = (invoice | move).line_ids.filtered(lambda l: l.account_id == self.sales)
        self.assertAlmostEqual(sum(income.mapped('balance')), 0.0, 2, "the sale lands on the gain account only")

    def test_proceeds_without_an_invoice_need_an_account(self):
        asset = self._asset(50000.0, prorata='none')
        asset.action_confirm()
        with self.assertRaises(UserError):
            self.env['ebshel.asset.dispose'].create({'asset_id': asset.id, 'kind': 'sale', 'date': date(2025, 12, 31),
                                                     'proceeds': 1000.0, 'note': 'sold'}).action_apply()

    def test_an_asset_disposed_of_in_the_period_closes_at_nothing(self):
        asset = self._asset(50000.0, prorata='none')
        asset.action_confirm()
        asset.line_ids.sorted('date')[0].action_post()
        self.env['ebshel.asset.dispose'].create({'asset_id': asset.id, 'kind': 'scrap', 'date': date(2026, 6, 30),
                                                 'note': 'broken'}).action_apply()
        report = self.env['ebshel.fin.report'].by_key('asset_schedule')
        data = report.get_report_data({'date': {'preset': 'custom', 'from': '2026-01-01', 'to': '2026-12-31'}, 'unfold_all': True})
        row = next(l for l in data['lines'] if l.get('res_id') == asset.id and l.get('kind') == 'asset')
        gross, before, period, todate, book = [c['value'] for c in row['columns']]
        self.assertAlmostEqual(gross, 0.0, 2)
        self.assertAlmostEqual(book, 0.0, 2)
        self.assertAlmostEqual(before, 10000.0, 2)
        self.assertAlmostEqual(period, round(10000.0 * 181 / 365, 2), 2)

    def test_a_locked_period_moves_the_line_with_its_entry(self):
        asset = self._asset(3000.0, prorata='none', duration=3, purchase_date=date(2020, 1, 1), start_date=date(2020, 1, 1))
        asset.action_confirm()
        self.company.fiscalyear_lock_date = date(2020, 12, 31)
        line = asset.line_ids.sorted('date')[0]
        line.action_post()
        self.assertGreater(line.move_id.date, date(2020, 12, 31))
        self.assertEqual(line.date, line.move_id.date)

    def test_the_label_is_an_article(self):
        """Without div.article the PDF lost its UTF-8: the '·' printed as 'Â·'."""
        asset = self._asset(1000.0)
        html = self.env['ir.actions.report']._render_qweb_html('ebshel_account_assets.action_report_asset_label', [asset.id])[0]
        self.assertIn(b'class="article"', html)

    def test_archived_assets_keep_depreciating_and_rpc_gets_the_category_salvage(self):
        self.category.salvage_pct = 10.0
        asset = self._asset(50000.0, prorata='none', purchase_date=date(2020, 1, 1), start_date=date(2020, 1, 1))
        self.assertAlmostEqual(asset.salvage_value, 5000.0, 2)
        asset.action_confirm()
        asset.active = False
        self.env['ebshel.asset']._cron_post_due()
        self.assertTrue(asset.line_ids.filtered(lambda l: l.state == 'posted'))

    # ---------------------------------------------- the bill wizard, skipping, the accounts
    def _wizard(self, bill):
        Wizard = self.env['ebshel.asset.from.bill'].with_context(default_move_id=bill.id)
        return Wizard.create(Wizard.default_get(list(Wizard._fields)))

    def test_the_bill_wizard_offers_each_asset_line_with_its_category_guessed(self):
        """Client, 2026-10-25: assets from bills, set up by hand before they are made."""
        self.category.bill_trigger = 'no'                     # nothing made by itself on posting
        bill = self._bill(qty=2, price=20000.0)
        self.assertEqual(bill.ebshel_asset_candidate_count, 1)
        self.assertIn('Dental chair', bill.ebshel_asset_candidate_note)
        wizard = self._wizard(bill)
        [row] = wizard.line_ids
        self.assertEqual((row.category_id, row.units, row.name), (self.category, 1, 'Dental chair'))
        self.assertAlmostEqual(row.value, 40000.0, 2)
        row.write({'units': 2, 'custodian_id': self.supplier.id, 'location': 'Surgery 2', 'serial': 'SN-1'})
        row.invalidate_recordset()
        self.assertAlmostEqual(row.unit_value, 20000.0, 2)
        self.assertAlmostEqual(row.charge, 20000.0 / (5 * 12), 2, msg="what each will cost a month")
        wizard.start = 'running'
        action = wizard.action_create()
        assets = bill.ebshel_asset_ids
        self.assertEqual(len(assets), 2)
        self.assertEqual(set(assets.mapped('state')), {'running'})
        self.assertEqual(sorted(assets.mapped('purchase_value')), [20000.0, 20000.0])
        self.assertEqual(set(assets.mapped('location')), {'Surgery 2'})
        self.assertEqual(assets[0].custodian_id, self.supplier)
        self.assertEqual(bill.ebshel_asset_candidate_count, 0, "nothing left waiting")
        self.assertEqual(action['res_model'], 'ebshel.asset')
        self.assertIn('2 asset(s) created', bill.message_ids[0].body)

    def test_the_guess_prefers_the_category_named_in_the_line(self):
        chairs = self.category.copy({'name': 'Dental chairs', 'trigger_account_ids': [(6, 0, self.gross.ids)]})
        self.category.trigger_account_ids = self.gross
        self.category.bill_trigger = chairs.bill_trigger = 'no'
        bill = self._bill()
        [(line, guessed)] = bill._ebshel_candidates()
        self.assertEqual(guessed, chairs, "'Dental chair' on the line: the chairs category")
        chairs.name = 'Furniture'
        self.category.name = 'Equipment'
        self.env['ebshel.asset'].create({'name': 'x', 'category_id': self.category.id, 'purchase_value': 1.0})
        [(line, guessed)] = bill._ebshel_candidates()
        self.assertEqual(guessed, self.category, "no name says: the busiest category")

    def test_a_skipped_period_is_spread_over_the_rest(self):
        asset = self.env['ebshel.asset'].create({
            'name': 'Scanner', 'category_id': self.category.id, 'purchase_value': 12000.0,
            'purchase_date': date(2025, 1, 1), 'start_date': date(2025, 1, 1), 'period': 'month', 'duration': 12,
            'prorata': 'none'})
        asset.action_confirm()
        lines = asset.line_ids.sorted('date')
        self.assertEqual(len(lines), 12)
        last_day = lines[-1].date
        lines[0].action_skip()
        asset.invalidate_recordset()
        skipped = asset.line_ids.filtered(lambda l: l.state == 'skipped')
        drafts = asset.line_ids.filtered(lambda l: l.state == 'draft').sorted('date')
        self.assertEqual((len(skipped), len(drafts)), (1, 11))
        self.assertAlmostEqual(sum(drafts.mapped('amount')), 12000.0, 2, msg="the skipped month spread over the rest")
        self.assertEqual(drafts[-1].date, last_day, "the life does not change")
        self.assertEqual(asset.event_ids[-1].kind, 'skipped')
        with self.assertRaises(UserError):
            skipped.action_skip()

    def test_the_dashboard_skips_what_is_overdue(self):
        start = date_utils.start_of(date.today(), 'month') - relativedelta(months=3)
        asset = self.env['ebshel.asset'].create({
            'name': 'Old press', 'category_id': self.category.id, 'purchase_value': 24000.0,
            'purchase_date': start, 'start_date': start, 'period': 'month', 'duration': 24, 'prorata': 'none'})
        asset.action_confirm()
        overdue = asset.line_ids.filtered(lambda l: l.date <= date.today())
        self.assertGreaterEqual(len(overdue), 3)
        self.assertIn(asset.id, self.env['ebshel.asset.dashboard'].get_data()['overdue_ids'])
        self.env['ebshel.asset.dashboard'].skip_overdue()
        asset.invalidate_recordset()
        self.assertTrue(all(l.state == 'skipped' for l in overdue))
        self.assertAlmostEqual(sum(asset.line_ids.filtered(lambda l: l.state == 'draft').mapped('amount')), 24000.0, 2)
        self.assertNotIn(asset.id, self.env['ebshel.asset.dashboard'].get_data()['overdue_ids'])

    def test_a_category_or_an_asset_refuses_the_same_account_twice(self):
        with self.assertRaises(ValidationError):
            self.category.copy({'name': 'Wrong', 'depreciation_account_id': self.gross.id})
        with self.assertRaises(ValidationError):
            self.category.copy({'name': 'Wrong', 'expense_account_id': self.accum.id})
        asset = self.env['ebshel.asset'].create({'name': 'x', 'category_id': self.category.id, 'purchase_value': 1.0})
        with self.assertRaises(ValidationError):
            asset.depreciation_account_id = self.gross
        self.assertAlmostEqual(asset.progress, 0.0, 1)

    def test_an_asset_knows_how_far_along_it_is(self):
        asset = self.env['ebshel.asset'].create({
            'name': 'Lathe', 'category_id': self.category.id, 'purchase_value': 10000.0, 'salvage_value': 2000.0,
            'purchase_date': date(2024, 1, 1), 'start_date': date(2024, 1, 1), 'period': 'year', 'duration': 4, 'prorata': 'none'})
        asset.action_confirm()
        asset.line_ids.sorted('date')[0].action_post()
        self.assertAlmostEqual(asset.progress, 25.0, 1, msg="2000 of the 8000 there is to depreciate")
        self.assertEqual(asset.action_open_bill()['res_model'], 'account.move')

