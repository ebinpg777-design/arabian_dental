# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from .common import AdvancedStockReportsCase


@tagged('post_install', '-at_install', 'asr')
class TestDirtyQueue(AdvancedStockReportsCase):
    """Every write path of DESIGN.md §1.4, then the cron, then the identity again."""

    def _queued(self, product):
        return self.Dirty.search([('product_id', '=', product.id), ('company_id', '=', self.company.id)])

    def test_done_move_marks_dirty(self):
        move = self._in(self.p_fifo, 5, 4.0)
        self.assertTrue(self._queued(self.p_fifo))
        self._process()
        self.assertFalse(self._queued(self.p_fifo))
        self.assertEqual(self._queued(self.p_fifo).ids, [])
        self.assertTrue(move.is_in)

    def test_adjust_valuation(self):
        move = self._in(self.p_fifo, 5, 4.0, date=self._days_ago(2))
        self._process()
        self.env['product.value'].create({'move_id': move.id, 'value': 50.0, 'product_id': self.p_fifo.id,
                                          'company_id': self.company.id})
        key = self._queued(self.p_fifo)
        self.assertTrue(key)
        self.assertEqual(key.day_from, self.company._asr_local_day(move.date))
        self._process()
        self._assert_reconciled(self.p_fifo)
        rows = self.env['asr.stock.move.daily'].sudo().search([('product_id', '=', self.p_fifo.id)])
        self.assertAlmostEqual(sum(rows.mapped('value_in')), 50.0)

    def test_move_line_edit(self):
        move = self._in(self.p_avco, 5, 4.0, date=self._days_ago(2))
        self._process()
        move.move_line_ids.write({'quantity': 6})
        self.assertTrue(self._queued(self.p_avco))
        self._process()
        self._assert_reconciled(self.p_avco)
        rows = self.env['asr.stock.move.daily'].sudo().search([('product_id', '=', self.p_avco.id)])
        self.assertAlmostEqual(sum(rows.mapped('qty_in')), 6.0)

    def test_backdating(self):
        move = self._in(self.p_std, 5, 10.0)
        self._process()
        old_day = self.company._asr_local_day(move.date)
        new_date = self._days_ago(10)
        move.write({'date': new_date})
        keys = self._queued(self.p_std)
        self.assertEqual(set(keys.mapped('day_from')), {old_day, new_date.date()})
        self._process()
        rows = self.env['asr.stock.move.daily'].sudo().search([('product_id', '=', self.p_std.id)])
        self.assertEqual(rows.mapped('day'), [new_date.date()])
        self._assert_reconciled(self.p_std, day=fields.Date.today() - timedelta(days=5), msg='after backdating')

    def test_standard_price_change(self):
        self._in(self.p_std, 5, 10.0, date=self._days_ago(1))
        self._process()
        self.p_std.standard_price = 11.0
        self.assertTrue(self._queued(self.p_std))
        self._process()
        self._assert_reconciled(self.p_std)

    def test_category_change_rebuilds(self):
        self._in(self.p_std, 5, 10.0, date=self._days_ago(1))
        self._process()
        self.p_std.product_tmpl_id.categ_id = self.categ_avco
        self.assertTrue(self._queued(self.p_std))
        self._process()
        rows = self.env['asr.stock.value.daily'].sudo().search([('product_id', '=', self.p_std.id)])
        self.assertEqual(set(rows.mapped('cost_method')), {'average'})
        self._assert_reconciled(self.p_std)

    def test_timezone_change_full_rebuild(self):
        self._in(self.p_std, 5, 10.0)
        self._process()
        self.company.asr_report_tz = 'Pacific/Auckland'
        self.assertEqual(self.company.asr_engine_state, 'rebuilding')
        self.assertTrue(self._queued(self.p_std))
        self._process()
        self.assertEqual(self.company.asr_engine_state, 'ready')

    def test_return_valued_from_origin(self):
        self._in(self.p_fifo, 4, 25.0, date=self._days_ago(3))
        out = self._out(self.p_fifo, 4, date=self._days_ago(2))
        self._process()
        ret = self._make_move(self.p_fifo, 1, self.customer_location, self.stock_location,
                              origin_returned_move_id=out.id)
        self.assertAlmostEqual(ret.value, 25.0, places=2)
        self._process()
        self._assert_reconciled(self.p_fifo)
        rows = self.env['asr.stock.move.daily'].sudo().search([('product_id', '=', self.p_fifo.id)])
        types = set(rows.mapped('move_type'))
        self.assertIn('customer_return', types)

    def test_ensure_fresh_processes_small_queue(self):
        self._in(self.p_fifo, 1, 1.0)
        self.assertTrue(self._queued(self.p_fifo))
        self.assertTrue(self.Dirty._ensure_fresh(self.company, self.p_fifo))
        self.assertFalse(self._queued(self.p_fifo))
