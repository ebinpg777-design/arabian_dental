# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
from datetime import datetime, timedelta

from odoo import Command, fields
from odoo.tests import TransactionCase


class AdvancedStockReportsCase(TransactionCase):
    """Scenario data: three cost methods, receipts, deliveries, transfers,
    returns, scraps, counts, a manufacturing order and a backdated picking."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        cls.company.write({'asr_report_tz': 'UTC'})
        cls.warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.company.id)], limit=1)
        cls.stock_location = cls.warehouse.lot_stock_id
        # Arabian Dental's sale_custom ships the whole demand on one click
        # (``lab_done_from_demand`` on the operation type): a partial delivery then
        # never asks for a backorder and the scenarios below lose their open lines.
        # The option is switched off for the test transaction only.
        PickingType = cls.env['stock.picking.type']
        if 'lab_done_from_demand' in PickingType._fields:
            PickingType.search([('warehouse_id', '=', cls.warehouse.id)]).write({'lab_done_from_demand': False})
        cls.shelf = cls.env['stock.location'].create({
            'name': 'ASR Shelf', 'usage': 'internal', 'location_id': cls.stock_location.id,
        })
        cls.supplier_location = cls.env.ref('stock.stock_location_suppliers')
        cls.customer_location = cls.env.ref('stock.stock_location_customers')
        cls.scrap_location = cls.env['stock.location'].search(
            [('scrap_location', '=', True), ('company_id', 'in', [cls.company.id, False])], limit=1) \
            if 'scrap_location' in cls.env['stock.location']._fields else cls.env['stock.location'].search(
            [('usage', '=', 'inventory'), ('company_id', 'in', [cls.company.id, False])], limit=1)
        cls.uom_unit = cls.env.ref('uom.product_uom_unit')
        cls.partner = cls.env['res.partner'].create({'name': 'ASR Partner'})

        def categ(name, method):
            return cls.env['product.category'].create({
                'name': name, 'property_cost_method': method, 'property_valuation': 'periodic',
            })
        cls.categ_std = categ('ASR Standard', 'standard')
        cls.categ_avco = categ('ASR AVCO', 'average')
        cls.categ_fifo = categ('ASR FIFO', 'fifo')

        def product(name, category, price):
            # no taxes: a company's default purchase tax reaches the stock move's price
            # unit when a tax has no account (Odoo's ``total_void``) - Arabian Dental's
            # "5% GST P" turned 12.00 into 12.30 and failed every expectation below
            return cls.env['product.product'].create({
                'name': name, 'is_storable': True, 'type': 'consu', 'categ_id': category.id,
                'standard_price': price, 'uom_id': cls.uom_unit.id,
                'taxes_id': [Command.clear()], 'supplier_taxes_id': [Command.clear()],
            })
        cls.p_std = product('ASR Std', cls.categ_std, 10.0)
        cls.p_avco = product('ASR Avco', cls.categ_avco, 0.0)
        cls.p_fifo = product('ASR Fifo', cls.categ_fifo, 0.0)
        cls.products = cls.p_std | cls.p_avco | cls.p_fifo
        cls.Dirty = cls.env['asr.stock.dirty']

    # ------------------------------------------------------------------
    # Move helpers
    # ------------------------------------------------------------------
    def _make_move(self, product, qty, src, dst, price_unit=None, date=None, **extra):
        vals = {
            'product_id': product.id, 'product_uom': product.uom_id.id,
            'product_uom_qty': qty, 'location_id': src.id, 'location_dest_id': dst.id,
            'company_id': self.company.id, 'picked': True,
            'move_line_ids': [Command.create({
                'product_id': product.id, 'product_uom_id': product.uom_id.id, 'quantity': qty,
                'location_id': src.id, 'location_dest_id': dst.id, 'company_id': self.company.id,
            })],
        }
        if price_unit is not None:
            vals['price_unit'] = price_unit
        vals.update(extra)
        move = self.env['stock.move'].create(vals)
        move._action_confirm()
        move.picked = True
        move._action_done()
        if date:
            move.write({'date': date})
            move.move_line_ids.write({'date': date})
        return move

    def _in(self, product, qty, price, date=None, location=None):
        """Receipt valued at ``price`` per unit (through a manual value, like Adjust Valuation)."""
        move = self._make_move(product, qty, self.supplier_location, location or self.stock_location, date=date)
        self.env['product.value'].create({'move_id': move.id, 'value': price * qty, 'product_id': product.id,
                                          'company_id': self.company.id, 'date': move.date})
        return move

    def _out(self, product, qty, date=None, location=None):
        return self._make_move(product, qty, location or self.stock_location, self.customer_location, date=date)

    def _transfer(self, product, qty, src, dst, date=None):
        return self._make_move(product, qty, src, dst, date=date)

    def _process(self):
        self.Dirty._process_queue()

    def _odoo_value(self, product, instant):
        product = product.with_company(self.company).with_context(allowed_company_ids=self.company.ids, to_date=instant)
        product.invalidate_recordset()
        return product._with_valuation_context().qty_available, product.total_value

    def _closing(self, product, day):
        rows = self.env['asr.stock.value.daily'].sudo()._closing_at(self.company, [product.id], day)
        return rows.get(product.id, (0.0, 0.0, 0.0))

    def _assert_reconciled(self, product, day=None, msg=''):
        day = day or fields.Date.today()
        instant = self.company._asr_day_end_utc(day)
        if instant > datetime.now():
            instant = datetime.now()
        qty, value = self._odoo_value(product, instant)
        c_qty, c_value, _c = self._closing(product, day)
        self.assertAlmostEqual(c_qty, qty, places=4, msg=f"quantity {msg} {product.display_name}")
        self.assertAlmostEqual(c_value, value, places=2, msg=f"value {msg} {product.display_name}")

    @staticmethod
    def _days_ago(n):
        return datetime.now().replace(microsecond=0) - timedelta(days=n)
