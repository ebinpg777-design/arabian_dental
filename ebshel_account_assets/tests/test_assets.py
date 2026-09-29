# -*- coding: utf-8 -*-
"""An asset from purchase to disposal, and the arithmetic in between."""
from datetime import date

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAssets(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        Account = cls.env['account.account']

        def account(code, name, atype):
            acc = Account.search([('account_type', '=', atype), ('code', '=', code)], limit=1)
            return acc or Account.create({'code': code, 'name': name, 'account_type': atype})
        cls.gross = account('ZZ9010', 'Test equipment', 'asset_fixed')
        cls.accum = account('ZZ9011', 'Test accumulated depreciation', 'asset_fixed')
        cls.expense = account('ZZ9012', 'Test depreciation expense', 'expense_depreciation')
        cls.gain = account('ZZ9013', 'Test gain on disposal', 'income_other')
        cls.loss = account('ZZ9014', 'Test loss on disposal', 'expense')
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
                                                'amount': 5000.0, 'note': 'new motor'}).action_apply()
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
                                                          'proceeds': 30000.0, 'note': 'sold to a colleague'})
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
        self.assertAlmostEqual(move.line_ids.filtered(lambda l: l.account_id == self.loss).debit, book_before, 2)
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
