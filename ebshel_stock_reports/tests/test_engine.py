# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from .common import AdvancedStockReportsCase


@tagged('post_install', '-at_install', 'asr')
class TestEngine(AdvancedStockReportsCase):

    def test_standard_closing_matches_odoo(self):
        self._in(self.p_std, 10, 10.0, date=self._days_ago(5))
        self._out(self.p_std, 4, date=self._days_ago(3))
        # price change without a move: product.value row, revaluation
        self.p_std.standard_price = 12.0
        self._process()
        self._assert_reconciled(self.p_std, msg='today')
        self._assert_reconciled(self.p_std, day=fields.Date.today() - timedelta(days=4), msg='between')
        rows = self.env['asr.stock.value.daily'].sudo().search(
            [('product_id', '=', self.p_std.id)], order='day')
        self.assertTrue(any(r.revaluation_value for r in rows), "the price change must show as a revaluation")
        self.assertAlmostEqual(sum(rows.mapped('revaluation_value')), 2.0 * 6, places=2)

    def test_avco_replay_matches_odoo(self):
        self._in(self.p_avco, 10, 10.0, date=self._days_ago(6))
        self._in(self.p_avco, 10, 20.0, date=self._days_ago(5))
        self._out(self.p_avco, 5, date=self._days_ago(4))
        self._in(self.p_avco, 5, 30.0, date=self._days_ago(2))
        self._out(self.p_avco, 15, date=self._days_ago(1))
        self._process()
        for back in (6, 5, 4, 2, 1, 0):
            self._assert_reconciled(self.p_avco, day=fields.Date.today() - timedelta(days=back), msg=f'{back} days ago')

    def test_avco_negative_then_positive(self):
        self._out(self.p_avco, 3, date=self._days_ago(4))
        self._in(self.p_avco, 10, 7.0, date=self._days_ago(3))
        self._out(self.p_avco, 2, date=self._days_ago(1))
        self._process()
        for back in (4, 3, 1, 0):
            self._assert_reconciled(self.p_avco, day=fields.Date.today() - timedelta(days=back), msg=f'{back} days ago')

    def test_avco_manual_price_change_resets_replay(self):
        self._in(self.p_avco, 10, 10.0, date=self._days_ago(5))
        self._out(self.p_avco, 2, date=self._days_ago(4))
        self.p_avco.standard_price = 15.0  # manual revaluation of an AVCO product
        self._out(self.p_avco, 3)
        self._process()
        self._assert_reconciled(self.p_avco)

    def test_fifo_stack_matches_odoo(self):
        self._in(self.p_fifo, 10, 10.0, date=self._days_ago(6))
        self._in(self.p_fifo, 10, 12.0, date=self._days_ago(5))
        self._out(self.p_fifo, 15, date=self._days_ago(3))
        self._in(self.p_fifo, 4, 20.0, date=self._days_ago(2))
        self._process()
        for back in (6, 5, 3, 2, 0):
            self._assert_reconciled(self.p_fifo, day=fields.Date.today() - timedelta(days=back), msg=f'{back} days ago')
        # aging valuation mode foots to the remaining stack
        remaining = self.p_fifo.with_company(self.company)._get_remaining_moves().get(self.p_fifo, {})
        stack_value = sum(move.remaining_value for move in remaining)
        _q, value, _c = self._closing(self.p_fifo, fields.Date.today())
        self.assertAlmostEqual(value, stack_value, places=2)

    def test_fifo_negative_uses_last_in_price(self):
        self._in(self.p_fifo, 2, 9.0, date=self._days_ago(3))
        self._out(self.p_fifo, 5, date=self._days_ago(2))
        self._process()
        self._assert_reconciled(self.p_fifo)

    def test_transfer_and_warehouse_ratio(self):
        self._in(self.p_avco, 10, 10.0, date=self._days_ago(3))
        self._transfer(self.p_avco, 4, self.stock_location, self.shelf, date=self._days_ago(2))
        self._process()
        rows = self.env['asr.stock.move.daily'].sudo().search([('product_id', '=', self.p_avco.id)])
        types = set(rows.mapped('move_type'))
        self.assertIn('transfer_in', types)
        self.assertIn('transfer_out', types)
        self.assertEqual(sum(rows.filtered(lambda r: r.move_type.startswith('transfer')).mapped('value_in')), 0.0)
        self._assert_reconciled(self.p_avco)
        # warehouse level value = company value x ratio
        ledger = self.env['asr.report.stock.ledger'].create({
            'company_id': self.company.id, 'date_from': fields.Date.today() - timedelta(days=10),
            'date_to': fields.Date.today(), 'product_ids': [(6, 0, self.p_avco.ids)],
            'location_ids': [(6, 0, self.shelf.ids)],
        })
        rows = [r for r in ledger._asr_rows() if not r.get('_group')]
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]['qty_closing'], 4.0)
        product = self.p_avco.with_company(self.company).with_context(warehouse_id=self.warehouse.id)
        expected = product.total_value * 4 / 10
        self.assertAlmostEqual(rows[0]['value_closing'], expected, places=2)

    def test_timezone_day_boundary(self):
        self.company.asr_report_tz = 'Asia/Kolkata'
        self._process()
        day = fields.Date.today()
        instant = self.company._asr_day_end_utc(day)
        self.assertEqual(instant.hour, 18)
        self.assertEqual(instant.minute, 29)

    def test_consignment_excluded(self):
        move = self._make_move(self.p_std, 5, self.supplier_location, self.stock_location, date=self._days_ago(1))
        move.move_line_ids.write({'owner_id': self.partner.id})
        self._process()
        rows = self.env['asr.stock.move.daily'].sudo().search([('product_id', '=', self.p_std.id)])
        self.assertTrue(rows)
        self.assertEqual(set(rows.mapped('move_type')), {'consignment'})
        self._assert_reconciled(self.p_std)

    def test_scrap_and_count_types(self):
        self._in(self.p_std, 10, 10.0, date=self._days_ago(2))
        scrap = self.env['stock.scrap'].create({
            'product_id': self.p_std.id, 'product_uom_id': self.uom_unit.id, 'scrap_qty': 1,
            'location_id': self.stock_location.id, 'company_id': self.company.id,
            'scrap_reason_tag_ids': [(6, 0, self.env.ref('ebshel_stock_reports.reason_damaged').ids)],
        })
        scrap.do_scrap()
        self.assertEqual(scrap.move_ids.asr_reason_id, self.env.ref('ebshel_stock_reports.reason_damaged'))
        quant = self.env['stock.quant'].with_context(inventory_mode=True).create({
            'product_id': self.p_std.id, 'location_id': self.stock_location.id, 'inventory_quantity': 7,
        })
        reason_lost = self.env.ref('ebshel_stock_reports.reason_lost')
        quant.with_context(asr_reason_id=reason_lost.id).action_apply_inventory()
        count_move = self.env['stock.move'].search(
            [('product_id', '=', self.p_std.id), ('is_inventory', '=', True)], limit=1)
        self.assertEqual(count_move.asr_qty_before, 9.0)
        self.assertEqual(count_move.asr_qty_counted, 7.0)
        self.assertEqual(count_move.asr_reason_id, self.env.ref('ebshel_stock_reports.reason_lost'))
        self._process()
        rows = self.env['asr.stock.move.daily'].sudo().search([('product_id', '=', self.p_std.id)])
        types = set(rows.mapped('move_type'))
        self.assertEqual(types, {'receipt', 'scrap', 'count_loss'})
        self._assert_reconciled(self.p_std)

    def test_health_check(self):
        self._in(self.p_avco, 3, 5.0)
        self._process()
        checked, mismatched = self.Dirty._health_check(self.company)
        self.assertGreaterEqual(checked, 1)
        self.assertFalse(mismatched)
