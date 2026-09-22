# -*- coding: utf-8 -*-
from datetime import date, datetime

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestStockMovementReport(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.tz = False
        cls.env.company.partner_id.tz = False
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.env.company.id)], limit=1)
        cls.stock = cls.warehouse.lot_stock_id
        cls.supplier = cls.env.ref('stock.stock_location_suppliers')
        cls.categ = cls.env['product.category'].create({'name': 'Movement Test Brand'})
        cls.product = cls.env['product.product'].create({
            'name': 'Movement Wire', 'is_storable': True, 'categ_id': cls.categ.id})

    def _receive(self, qty, when):
        move = self.env['stock.move'].create({
            'product_id': self.product.id, 'product_uom_qty': qty,
            'product_uom': self.product.uom_id.id,
            'location_id': self.supplier.id, 'location_dest_id': self.stock.id})
        move._action_confirm()
        move.quantity = qty
        move.picked = True
        move._action_done()
        move.date = when
        return move

    def _row(self, date_from, date_to):
        wizard = self.env['stock.movement.report.wizard'].create({
            'warehouse_id': self.warehouse.id, 'categ_ids': [(6, 0, self.categ.ids)],
            'date_from': date_from, 'date_to': date_to})
        lines = wizard._get_lines(self.categ, wizard._internal_locations())
        return lines[0] if lines else None

    def test_periods_follow_the_local_day(self):
        # 1 Mar 20:00 UTC is 2 Mar 01:30 in Kolkata: it belongs to 2 March.
        self._receive(5, datetime(2026, 3, 1, 20, 0))
        row = self._row(date(2026, 3, 2), date(2026, 3, 2))
        self.assertEqual((row['opening_bal'], row['purchase'], row['closing_bal']), (0, 5, 5))
        self.assertIsNone(self._row(date(2026, 3, 1), date(2026, 3, 1)),
                          "the move is not on 1 March locally")

    def test_the_period_ends_at_local_midnight(self):
        # 18:29 UTC on 31 Mar is 23:59 in Kolkata: March.
        # 19:00 UTC on 31 Mar is 00:30 on 1 April in Kolkata: not March.
        self._receive(3, datetime(2026, 3, 31, 18, 29))
        self._receive(2, datetime(2026, 3, 31, 19, 0))
        row = self._row(date(2026, 3, 1), date(2026, 3, 31))
        self.assertEqual((row['purchase'], row['closing_bal']), (3, 3))

    def test_archived_products_are_reported(self):
        self._receive(4, datetime(2026, 3, 10, 6, 0))
        self.product.action_archive()
        row = self._row(date(2026, 3, 1), date(2026, 3, 31))
        self.assertTrue(row, "an archived product still moved stock")
        self.assertEqual(row['closing_bal'], 4)
