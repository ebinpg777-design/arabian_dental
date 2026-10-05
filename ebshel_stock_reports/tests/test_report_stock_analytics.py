# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
"""Reports #4 Inventory Aging, #5 Slow / Non-moving, #6 ABC / XYZ, #7 Turnover / Days of cover."""
from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from .common import AdvancedStockReportsCase


def _by_product(rows):
    return {r['product_id'].id: r for r in rows if not r.get('_group') and r.get('product_id')}


@tagged('post_install', '-at_install', 'asr')
class TestReportAging(AdvancedStockReportsCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company.asr_aging_buckets = '5,10'  # buckets 0-5, 6-10, >10

    def _wizard(self, **vals):
        return self.env['asr.report.aging'].create(dict(
            {'company_id': self.company.id, 'product_ids': [(6, 0, self.products.ids)], 'mode': 'valuation'}, **vals))

    def _scenario(self):
        # FIFO: stack today = 4 @ 20 (2 days) + 5 of the 10 @ 12 (7 days)
        self._in(self.p_fifo, 10, 10.0, date=self._days_ago(12))
        self._in(self.p_fifo, 10, 12.0, date=self._days_ago(7))
        self._out(self.p_fifo, 15, date=self._days_ago(3))
        self._in(self.p_fifo, 4, 20.0, date=self._days_ago(2))
        # AVCO: stack today = 10 (7 days) + 5 of the 10 (12 days), valued at the standard price
        self._in(self.p_avco, 10, 10.0, date=self._days_ago(12))
        self._in(self.p_avco, 10, 20.0, date=self._days_ago(7))
        self._out(self.p_avco, 5, date=self._days_ago(3))
        self._process()

    def _odoo_remaining(self, product):
        remaining = product.with_company(self.company)._get_remaining_moves().get(product, {})
        return sum(move.remaining_value for move in remaining), sum(remaining.values())

    def test_valuation_today_foots_to_remaining_stack(self):
        self._scenario()
        rows = _by_product(self._wizard()._asr_rows())
        fifo = rows[self.p_fifo.id]
        self.assertAlmostEqual(fifo['qty_b1'], 4.0)
        self.assertAlmostEqual(fifo['qty_b2'], 5.0)
        self.assertAlmostEqual(fifo['qty_b3'], 0.0)
        self.assertAlmostEqual(fifo['qty_total'], 9.0)
        self.assertAlmostEqual(fifo['value_b1'], 80.0, places=2)
        self.assertAlmostEqual(fifo['value_b2'], 60.0, places=2)
        self.assertAlmostEqual(fifo['value_total'], 140.0, places=2)
        self.assertEqual(fifo['oldest_date'], fields.Date.today() - timedelta(days=7))
        avco = rows[self.p_avco.id]
        self.assertAlmostEqual(avco['qty_b2'], 10.0)
        self.assertAlmostEqual(avco['qty_b3'], 5.0)
        self.assertAlmostEqual(avco['qty_total'], 15.0)
        std = self.p_avco.with_company(self.company).standard_price
        self.assertAlmostEqual(avco['value_b2'], 10 * std, places=2)
        self.assertAlmostEqual(avco['value_b3'], 5 * std, places=2)
        self.assertEqual(avco['oldest_date'], fields.Date.today() - timedelta(days=12))
        # identity: sum of the slices == Odoo's remaining values == the Stock report total
        for product in (self.p_fifo, self.p_avco):
            odoo_value, odoo_qty = self._odoo_remaining(product)
            row = rows[product.id]
            self.assertAlmostEqual(row['value_total'], odoo_value, places=2, msg=product.display_name)
            self.assertAlmostEqual(row['qty_total'], odoo_qty, places=4, msg=product.display_name)
            _qty, total_value = self._odoo_value(product, fields.Datetime.now())
            self.assertAlmostEqual(row['value_total'], total_value, places=2, msg=product.display_name)
        self.assertNotIn(self.p_std.id, rows)  # no stock, no row

    def test_valuation_past_date(self):
        self._scenario()
        day = fields.Date.today() - timedelta(days=4)
        rows = _by_product(self._wizard(date_to=day)._asr_rows())
        fifo = rows[self.p_fifo.id]
        self.assertAlmostEqual(fifo['qty_b1'], 10.0)   # 7 days ago -> age 3 at the report date
        self.assertAlmostEqual(fifo['qty_b2'], 10.0)   # 12 days ago -> age 8
        self.assertAlmostEqual(fifo['value_b1'], 120.0, places=2)
        self.assertAlmostEqual(fifo['value_b2'], 100.0, places=2)
        _q, closing_value, _c = self._closing(self.p_fifo, day)
        self.assertAlmostEqual(fifo['value_total'], closing_value, places=2)

    def test_physical_mode_and_outputs(self):
        self._scenario()
        quants = self.env['stock.quant'].sudo().search([('product_id', '=', self.p_avco.id),
                                                       ('location_id', '=', self.stock_location.id)])
        quants.write({'in_date': self._days_ago(8)})
        wizard = self._wizard(mode='physical', location_ids=[(6, 0, self.stock_location.ids)])
        rows = _by_product(wizard._asr_rows())
        avco = rows[self.p_avco.id]
        self.assertAlmostEqual(avco['qty_b2'], 15.0)
        self.assertAlmostEqual(avco['qty_total'], 15.0)
        _q, _v, avg_cost = self._closing(self.p_avco, fields.Date.today())
        self.assertAlmostEqual(avco['value_total'], 15 * avg_cost, places=2)
        self.assertEqual(avco['oldest_date'], fields.Date.today() - timedelta(days=8))
        # screen, Excel and PDF paths
        action = wizard.action_view()
        lines = self.env['asr.report.aging.line'].search([('wizard_id', '=', wizard.id)])
        self.assertEqual(action['res_model'], 'asr.report.aging.line')
        self.assertEqual(len(lines), 2)
        self.assertTrue(wizard._asr_xlsx().startswith(b'PK'))
        self.assertEqual(wizard.action_pdf()['type'], 'ir.actions.report')
        # the bucket labels follow the company setting
        labels = [c['label'] for c in wizard._asr_columns() if c['key'].startswith('qty_b')]
        self.assertEqual(labels, ['Qty 0-5 days', 'Qty 6-10 days', 'Qty >10 days'])


@tagged('post_install', '-at_install', 'asr')
class TestReportSlowMoving(AdvancedStockReportsCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company.asr_slow_days = 10

    def _wizard(self, **vals):
        return self.env['asr.report.slow.moving'].create(dict(
            {'company_id': self.company.id, 'product_ids': [(6, 0, self.products.ids)], 'status': 'all'}, **vals))

    def _scenario(self):
        self._in(self.p_std, 10, 10.0, date=self._days_ago(30))
        self._out(self.p_std, 2, date=self._days_ago(25))       # 25 days > 2 x 10 -> non-moving
        self._in(self.p_avco, 10, 10.0, date=self._days_ago(30))
        self._out(self.p_avco, 3, date=self._days_ago(15))      # 15 days > 10 -> slow
        self._in(self.p_fifo, 10, 10.0, date=self._days_ago(30))
        self._out(self.p_fifo, 1, date=self._days_ago(2))       # active
        self._process()

    def test_status_and_values(self):
        self._scenario()
        self.env['asr.product.class'].sudo().create({
            'company_id': self.company.id, 'product_id': self.p_std.id, 'abc_class': 'A', 'xyz_class': 'Z'})
        rows = _by_product(self._wizard()._asr_rows())
        today = fields.Date.today()
        std = rows[self.p_std.id]
        self.assertEqual(std['status'], 'non_moving')
        self.assertEqual(std['last_issue_day'], today - timedelta(days=25))
        self.assertEqual(std['last_receipt_day'], today - timedelta(days=30))
        self.assertEqual(std['days_since_issue'], 25)
        self.assertAlmostEqual(std['qty_on_hand'], 8.0)
        self.assertAlmostEqual(std['value_on_hand'], 80.0, places=2)
        self.assertEqual((std['abc_class'], std['xyz_class'], std['combined_class']), ('A', 'Z', 'AZ'))
        self.assertEqual(rows[self.p_avco.id]['status'], 'slow')
        self.assertEqual(rows[self.p_avco.id]['days_since_issue'], 15)
        self.assertAlmostEqual(rows[self.p_avco.id]['qty_on_hand'], 7.0)
        self.assertEqual(rows[self.p_fifo.id]['status'], 'active')
        self.assertFalse(rows[self.p_fifo.id]['abc_class'])
        # filters
        self.assertEqual(set(_by_product(self._wizard(status='slow')._asr_rows())), {self.p_avco.id})
        self.assertEqual(set(_by_product(self._wizard(status='non_moving')._asr_rows())), {self.p_std.id})
        self.assertEqual(set(_by_product(self._wizard(status='slow_non_moving')._asr_rows())),
                         {self.p_std.id, self.p_avco.id})

    def test_without_stock_and_location_level(self):
        self._scenario()
        self._out(self.p_std, 8, date=self._days_ago(1))  # stock gone, issued yesterday
        self._transfer(self.p_fifo, 4, self.stock_location, self.shelf, date=self._days_ago(1))
        self._process()
        rows = _by_product(self._wizard()._asr_rows())
        self.assertNotIn(self.p_std.id, rows)
        rows = _by_product(self._wizard(include_without_stock=True)._asr_rows())
        self.assertEqual(rows[self.p_std.id]['status'], 'active')
        self.assertAlmostEqual(rows[self.p_std.id]['qty_on_hand'], 0.0)
        # shelf only: 4 units of the FIFO product, never issued from there, value = ratio of the company value
        rows = _by_product(self._wizard(location_ids=[(6, 0, self.shelf.ids)])._asr_rows())
        self.assertEqual(set(rows), {self.p_fifo.id})
        fifo = rows[self.p_fifo.id]
        self.assertAlmostEqual(fifo['qty_on_hand'], 4.0)
        self.assertEqual(fifo['status'], 'non_moving')
        self.assertFalse(fifo['last_issue_day'])
        self.assertEqual(fifo['last_receipt_day'], fields.Date.today() - timedelta(days=1))
        _q, company_value, _c = self._closing(self.p_fifo, fields.Date.today())
        self.assertAlmostEqual(fifo['value_on_hand'], company_value * 4 / 9, places=2)

    def test_outputs(self):
        self._scenario()
        wizard = self._wizard()
        action = wizard.action_view()
        lines = self.env['asr.report.slow.moving.line'].search([('wizard_id', '=', wizard.id)])
        self.assertEqual(action['res_model'], 'asr.report.slow.moving.line')
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines.filtered(lambda ln: ln.product_id == self.p_std).status, 'non_moving')
        self.assertTrue(wizard._asr_xlsx().startswith(b'PK'))
        self.assertEqual(wizard.action_pdf()['type'], 'ir.actions.report')


@tagged('post_install', '-at_install', 'asr')
class TestReportAbcXyz(AdvancedStockReportsCase):

    def _wizard(self, **vals):
        return self.env['asr.report.abc.xyz'].create(dict(
            {'company_id': self.company.id, 'product_ids': [(6, 0, self.products.ids)],
             'months': 12, 'recompute': True}, **vals))

    def _scenario(self):
        # issue values at cost 8e9 / 1.5e9 / 5e8 -> cumulative 80 % (A), 95 % (B), 100 % (C).
        # Big prices so that whatever demo data the database holds is noise in the shares.
        self.p_std.standard_price = 1e8  # standard cost: issues are valued at the standard price
        self._in(self.p_std, 100, 1e8, date=self._days_ago(60))
        self._out(self.p_std, 40, date=self._days_ago(40))
        self._out(self.p_std, 40, date=self._days_ago(10))
        self._in(self.p_avco, 100, 5e7, date=self._days_ago(60))
        self._out(self.p_avco, 30, date=self._days_ago(20))
        self._in(self.p_fifo, 20, 5e7, date=self._days_ago(60))
        self._out(self.p_fifo, 10, date=self._days_ago(5))
        self._process()

    def test_classes_and_on_hand(self):
        self._scenario()
        self.assertFalse(self.env['asr.product.class'].sudo().search_count([('company_id', '=', self.company.id)]))
        wizard = self._wizard()
        all_rows = wizard._asr_rows()
        rows = _by_product(all_rows)
        std, avco, fifo = rows[self.p_std.id], rows[self.p_avco.id], rows[self.p_fifo.id]
        self.assertEqual((std['abc_class'], avco['abc_class'], fifo['abc_class']), ('A', 'B', 'C'))
        self.assertEqual(std['combined_class'], 'A' + std['xyz_class'])
        self.assertIn(std['xyz_class'], ('X', 'Y', 'Z'))
        self.assertAlmostEqual(std['issue_qty'], 80.0)
        self.assertAlmostEqual(std['issue_value'], 8e9, places=2)
        self.assertAlmostEqual(std['cumulative_share'], 80.0, delta=0.5)
        self.assertAlmostEqual(avco['cumulative_share'], 95.0, delta=0.5)
        self.assertAlmostEqual(fifo['cumulative_share'], 100.0, delta=0.5)
        self.assertAlmostEqual(std['qty_on_hand'], 20.0)
        self.assertAlmostEqual(std['value_on_hand'], 2e9, places=2)
        self.assertAlmostEqual(avco['qty_on_hand'], 70.0)
        self.assertAlmostEqual(avco['value_on_hand'], 3.5e9, places=2)
        self.assertAlmostEqual(fifo['qty_on_hand'], 10.0)
        self.assertAlmostEqual(fifo['value_on_hand'], 5e8, places=2)
        # ranked by issue value, then the summary block
        product_rows = [r for r in all_rows if not r.get('_group') and not r.get('is_summary')]
        self.assertEqual([r['product_id'] for r in product_rows], [self.p_std, self.p_avco, self.p_fifo])
        summary = [r for r in all_rows if r.get('is_summary')]
        self.assertEqual(len(summary), 3)
        self.assertEqual(sum(r['product_count'] for r in summary), 3)
        self.assertAlmostEqual(sum(r['issue_value'] for r in summary), 1e10, places=2)
        self.assertTrue(any(r.get('_group') for r in all_rows))
        # summary rows are not counted twice in the totals
        totals = wizard._asr_totals(all_rows, wizard._asr_columns())
        self.assertAlmostEqual(totals['issue_value'], 1e10, places=2)
        # the classes are persisted and reused without recompute
        self.assertEqual(self.env['asr.product.class'].sudo().search_count(
            [('company_id', '=', self.company.id), ('product_id', 'in', self.products.ids)]), 3)
        rows = _by_product(self._wizard(recompute=False, include_summary=False)._asr_rows())
        self.assertEqual(rows[self.p_std.id]['abc_class'], 'A')

    def test_outputs(self):
        self._scenario()
        wizard = self._wizard()
        action = wizard.action_view()
        lines = self.env['asr.report.abc.xyz.line'].search([('wizard_id', '=', wizard.id)])
        self.assertEqual(action['res_model'], 'asr.report.abc.xyz.line')
        self.assertEqual(len(lines.filtered(lambda ln: not ln.is_summary)), 3)
        self.assertEqual(len(lines.filtered('is_summary')), 3)
        self.assertTrue(wizard._asr_xlsx().startswith(b'PK'))
        self.assertEqual(wizard.action_pdf()['type'], 'ir.actions.report')


@tagged('post_install', '-at_install', 'asr')
class TestReportTurnover(AdvancedStockReportsCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company.asr_cover_days = 10

    def _wizard(self, **vals):
        today = fields.Date.today()
        return self.env['asr.report.turnover'].create(dict(
            {'company_id': self.company.id, 'product_ids': [(6, 0, self.products.ids)],
             'date_from': today - timedelta(days=50), 'date_to': today}, **vals))

    def _scenario(self):
        self._in(self.p_std, 100, 10.0, date=self._days_ago(70))
        self._out(self.p_std, 20, date=self._days_ago(40))
        self._out(self.p_std, 10, date=self._days_ago(5))
        self._in(self.p_fifo, 10, 5.0, date=self._days_ago(70))   # stock, no issue
        self._process()

    def test_turnover_and_cover(self):
        self._scenario()
        wizard = self._wizard()
        rows = _by_product(wizard._asr_rows())
        today = fields.Date.today()
        std = rows[self.p_std.id]
        self.assertAlmostEqual(std['qty_opening'], 100.0)
        self.assertAlmostEqual(std['qty_issue'], 30.0)
        self.assertAlmostEqual(std['value_issue'], 300.0, places=2)
        self.assertAlmostEqual(std['qty_on_hand'], 70.0)
        self.assertAlmostEqual(std['avg_daily_issue'], 1.0)      # 10 units over the 10-day window
        self.assertAlmostEqual(std['days_of_cover'], 70.0, places=2)
        # average inventory value = mean of the closing values at the value points
        points = wizard._value_points(today - timedelta(days=50), today)
        self.assertEqual(points[0], today - timedelta(days=51))
        self.assertEqual(points[-1], today)
        expected = sum(self._closing(self.p_std, p)[1] for p in points) / len(points)
        self.assertAlmostEqual(std['value_avg_inventory'], expected, places=2)
        self.assertGreaterEqual(std['value_avg_inventory'], 700.0)
        self.assertLessEqual(std['value_avg_inventory'], 1000.0)
        self.assertAlmostEqual(std['turnover'], 300.0 / expected, places=4)
        fifo = rows[self.p_fifo.id]
        self.assertAlmostEqual(fifo['qty_issue'], 0.0)
        self.assertAlmostEqual(fifo['qty_on_hand'], 10.0)
        self.assertIsNone(fifo['days_of_cover'])
        self.assertAlmostEqual(fifo['turnover'], 0.0)
        self.assertNotIn(self.p_avco.id, rows)
        rows = _by_product(self._wizard(hide_empty=False)._asr_rows())
        self.assertNotIn(self.p_avco.id, rows)  # never moved: no summary row at all

    def test_value_points_month_ends(self):
        wizard = self._wizard()
        points = wizard._value_points(fields.Date.to_date('2026-01-15'), fields.Date.to_date('2026-04-30'))
        self.assertEqual([str(p) for p in points],
                         ['2026-01-14', '2026-01-31', '2026-02-28', '2026-03-31', '2026-04-30'])
        points = wizard._value_points(fields.Date.to_date('2026-03-01'), fields.Date.to_date('2026-03-10'))
        self.assertEqual([str(p) for p in points], ['2026-02-28', '2026-03-10'])

    def test_location_level_and_outputs(self):
        self._scenario()
        self._transfer(self.p_std, 30, self.stock_location, self.shelf, date=self._days_ago(3))
        self._process()
        wizard = self._wizard(location_ids=[(6, 0, self.shelf.ids)])
        rows = _by_product(wizard._asr_rows())
        std = rows[self.p_std.id]
        self.assertAlmostEqual(std['qty_opening'], 0.0)
        self.assertAlmostEqual(std['qty_issue'], 0.0)       # the issues left the main stock location
        self.assertAlmostEqual(std['qty_on_hand'], 30.0)
        self.assertIsNone(std['days_of_cover'])
        # value of the shelf at the end = ratio of the company value (70 units, 700)
        self.assertGreater(std['value_avg_inventory'], 0.0)
        wizard = self._wizard()
        action = wizard.action_view()
        lines = self.env['asr.report.turnover.line'].search([('wizard_id', '=', wizard.id)])
        self.assertEqual(action['res_model'], 'asr.report.turnover.line')
        self.assertEqual(len(lines), 2)
        self.assertAlmostEqual(lines.filtered(lambda ln: ln.product_id == self.p_std).days_of_cover, 70.0, places=1)
        self.assertTrue(wizard._asr_xlsx().startswith(b'PK'))
        self.assertEqual(wizard.action_pdf()['type'], 'ir.actions.report')
