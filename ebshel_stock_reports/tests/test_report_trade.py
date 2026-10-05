# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
"""Reports #8 Customer OTIF, #9 Vendor OTIF, #10 Purchase Price Variance, #12 Open Order Shortage."""
import io
import zipfile
from datetime import timedelta

from odoo import Command, fields
from odoo.tests import Form, tagged

from .common import AdvancedStockReportsCase


@tagged('post_install', '-at_install', 'asr')
class TestReportTrade(AdvancedStockReportsCase):

    def setUp(self):
        super().setUp()
        self.vendor = self.env['res.partner'].create({'name': 'ASR Vendor'})
        self.customer = self.env['res.partner'].create({'name': 'ASR Customer'})
        self.today = fields.Date.context_today(self.env['res.company'])

        # --- Vendor side: a PO received late (planned 3 days ago, received today), then billed at another price
        self.po = self.env['purchase.order'].create({
            'partner_id': self.vendor.id,
            'picking_type_id': self.warehouse.in_type_id.id,
            'order_line': [
                Command.create({'product_id': self.p_std.id, 'product_qty': 10, 'price_unit': 12.0,
                                'date_planned': self._days_ago(3)}),
                Command.create({'product_id': self.p_avco.id, 'product_qty': 5, 'price_unit': 8.0,
                                'date_planned': self._days_ago(3)}),
            ],
        })
        self.po.button_confirm()
        receipt = self.po.picking_ids
        self.assertEqual(len(receipt), 1)
        receipt.move_ids.picked = True
        receipt.button_validate()
        self.assertEqual(receipt.state, 'done')
        self.receipt = receipt
        self.std_move = receipt.move_ids.filtered(lambda m: m.product_id == self.p_std)
        self.assertAlmostEqual(self.std_move.value, 120.0, places=2)

        # vendor bill for the standard product at 15 instead of 12: the receipt move is revalued
        self.po.action_create_invoice()
        bill = self.po.invoice_ids
        self.assertEqual(len(bill), 1)
        bill.invoice_date = self.today
        bill.invoice_line_ids.filtered(lambda aml: aml.product_id == self.p_std).price_unit = 15.0
        bill.action_post()
        self.assertAlmostEqual(self.std_move.value, 150.0, places=2)

        # --- Customer side: SO1 promised 2 days ago, delivered today, one line in full and one partial
        self.so1 = self._sale_order(self._days_ago(2), [(self.p_std, 8), (self.p_avco, 10)])
        pick = self.so1.picking_ids
        self.assertEqual(len(pick), 1)
        for move in pick.move_ids:
            move.quantity = 8 if move.product_id == self.p_std else 3
            move.picked = True
        Form.from_action(self.env, pick.button_validate()).save().process()
        self.assertEqual(pick.state, 'done')
        self.backorder = self.so1.picking_ids.filtered(lambda p: p.backorder_id)
        self.assertEqual(len(self.backorder), 1)
        # SO2 promised 5 days ago and not shipped at all; SO3 promised yesterday, same product
        self.so2 = self._sale_order(self._days_ago(5), [(self.p_fifo, 4)])
        self.so3 = self._sale_order(self._days_ago(1), [(self.p_fifo, 2)])
        # 3 units of the FIFO product arrive without being reserved, 1 more is on order
        self._in(self.p_fifo, 3, 20.0)
        self.po_open = self.env['purchase.order'].create({
            'partner_id': self.vendor.id,
            'picking_type_id': self.warehouse.in_type_id.id,
            'order_line': [Command.create({'product_id': self.p_fifo.id, 'product_qty': 1, 'price_unit': 20.0,
                                           'date_planned': fields.Datetime.now() + timedelta(days=2)})],
        })
        self.po_open.button_confirm()
        self._process()

    def _sale_order(self, commitment, lines):
        order = self.env['sale.order'].create({
            'partner_id': self.customer.id,
            'warehouse_id': self.warehouse.id,
            'commitment_date': commitment,
            'order_line': [Command.create({'product_id': product.id, 'product_uom_qty': qty})
                           for product, qty in lines],
        })
        order.action_confirm()
        return order

    def _wizard(self, model, **vals):
        values = {
            'company_id': self.company.id, 'date_from': self.today - timedelta(days=10), 'date_to': self.today,
        }
        values.update(vals)
        return self.env[model].create(values)

    def _check_outputs(self, wizard, line_model, expected_lines, label):
        action = wizard.action_view()
        lines = self.env[line_model].search(action['domain'])
        self.assertEqual(len(lines), expected_lines)
        self.assertIn('pivot', action['view_mode'])
        content = wizard._asr_xlsx()
        self.assertTrue(content.startswith(b'PK'))
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            self.assertIn('xl/worksheets/sheet1.xml', archive.namelist())
        pdf_action = wizard.action_pdf()
        document = self.env['asr.report.print'].browse(pdf_action['context']['active_ids'])
        self.assertEqual(document.row_count, expected_lines)
        html = self.env['ir.actions.report']._render_qweb_html(
            'ebshel_stock_reports.action_report_document', document.ids)[0]
        self.assertIn(label.encode(), html)
        return lines

    # ------------------------------------------------------------------
    # #8 Customer OTIF
    # ------------------------------------------------------------------
    def test_customer_otif_lines(self):
        wizard = self._wizard('asr.report.customer.otif', partner_ids=[(6, 0, self.customer.ids)])
        rows = wizard._asr_rows()
        by_key = {(r['order_id'].id, r['product_id'].id): r for r in rows}
        self.assertEqual(len(rows), 4)
        std = by_key[(self.so1.id, self.p_std.id)]
        self.assertEqual(std['date_promised'], self.today - timedelta(days=2))
        self.assertEqual(std['date_delivered'], self.today)
        self.assertEqual(std['days_late'], 2)
        self.assertAlmostEqual(std['qty_ordered'], 8.0)
        self.assertAlmostEqual(std['qty_delivered'], 8.0)
        self.assertFalse(std['on_time'])
        self.assertTrue(std['in_full'])
        self.assertFalse(std['otif'])
        avco = by_key[(self.so1.id, self.p_avco.id)]
        self.assertAlmostEqual(avco['qty_delivered'], 3.0)
        self.assertFalse(avco['in_full'])
        self.assertEqual(avco['otif_count'], 0)
        # late open lines: promised in the period, nothing delivered
        open_line = by_key[(self.so2.id, self.p_fifo.id)]
        self.assertFalse(open_line['date_delivered'])
        self.assertEqual(open_line['days_late'], 5)
        self.assertFalse(open_line['on_time'])
        self.assertIn((self.so3.id, self.p_fifo.id), by_key)
        # the date filter excludes the open lines when the promised day is outside the period
        wizard.date_from = self.today - timedelta(days=1)
        self.assertEqual(len(wizard._asr_rows()), 3)

    def test_customer_otif_on_time_and_tolerance(self):
        # promised tomorrow: on time; 20 % tolerance makes 8/10 in full
        so = self._sale_order(fields.Datetime.now() + timedelta(days=1), [(self.p_std, 2)])
        so.picking_ids.move_ids.picked = True
        so.picking_ids.button_validate()
        self.company.asr_otif_tolerance = 70.0
        wizard = self._wizard('asr.report.customer.otif', partner_ids=[(6, 0, self.customer.ids)])
        rows = {(r['order_id'].id, r['product_id'].id): r for r in wizard._asr_rows()}
        self.assertTrue(rows[(so.id, self.p_std.id)]['otif'])
        self.assertEqual(rows[(so.id, self.p_std.id)]['days_late'], -1)
        self.assertTrue(rows[(self.so1.id, self.p_avco.id)]['in_full'])  # 3 >= 10 * 0.3

    def test_customer_otif_customer_mode_and_outputs(self):
        wizard = self._wizard('asr.report.customer.otif', group_by='customer',
                              partner_ids=[(6, 0, self.customer.ids)])
        rows = wizard._asr_rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['partner_id'], self.customer)
        self.assertEqual(row['line_count'], 4)
        self.assertEqual(row['on_time_count'], 0)
        self.assertEqual(row['in_full_count'], 1)
        self.assertEqual(row['otif_count'], 0)
        self.assertAlmostEqual(row['in_full_rate'], 25.0)
        self.assertAlmostEqual(row['otif_rate'], 0.0)
        lines = self._check_outputs(wizard, 'asr.report.customer.otif.line', 1, 'OTIF %')
        self.assertEqual(lines.otif_count, 0)
        line_wizard = self._wizard('asr.report.customer.otif', partner_ids=[(6, 0, self.customer.ids)])
        lines = self._check_outputs(line_wizard, 'asr.report.customer.otif.line', 4, 'Days Late')
        self.assertEqual(sum(lines.mapped('in_full_count')), 1)

    # ------------------------------------------------------------------
    # #9 Vendor OTIF
    # ------------------------------------------------------------------
    def test_vendor_otif(self):
        wizard = self._wizard('asr.report.vendor.otif', partner_ids=[(6, 0, self.vendor.ids)])
        rows = wizard._asr_rows()
        self.assertEqual(len(rows), 2)  # the open PO has no receipt yet
        by_product = {r['product_id'].id: r for r in rows}
        std = by_product[self.p_std.id]
        self.assertEqual(std['order_id'], self.po)
        self.assertEqual(std['date_promised'], self.today - timedelta(days=3))
        self.assertEqual(std['date_received'], self.today)
        self.assertEqual(std['days_late'], 3)
        self.assertAlmostEqual(std['qty_ordered'], 10.0)
        self.assertAlmostEqual(std['qty_received'], 10.0)
        self.assertFalse(std['on_time'])
        self.assertTrue(std['in_full'])
        self.assertFalse(std['otif'])
        # Odoo's own figures beside the vendor: nothing on time, received the day it was ordered
        self.assertAlmostEqual(std['odoo_on_time_rate'], 0.0)
        self.assertIsNotNone(std['odoo_days_to_arrival'])
        self.assertLess(abs(std['odoo_days_to_arrival']), 1.0)
        # Odoo's view also counts the confirmed, not yet received move of the open PO (1 unit, not on time)
        odoo_rows = self.env['vendor.delay.report'].sudo()._read_group(
            [('partner_id', '=', self.vendor.id)], [], ['qty_on_time:sum', 'qty_total:sum'])
        self.assertAlmostEqual(odoo_rows[0][0], 0.0)
        self.assertAlmostEqual(odoo_rows[0][1], 16.0)

        vendor_mode = self._wizard('asr.report.vendor.otif', group_by='vendor', partner_ids=[(6, 0, self.vendor.ids)])
        rows = vendor_mode._asr_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['line_count'], 2)
        self.assertAlmostEqual(rows[0]['in_full_rate'], 100.0)
        self.assertAlmostEqual(rows[0]['otif_rate'], 0.0)
        self.assertAlmostEqual(rows[0]['odoo_on_time_rate'], 0.0)
        lines = self._check_outputs(vendor_mode, 'asr.report.vendor.otif.line', 1, 'Odoo On-Time Rate')
        self.assertEqual(lines.in_full_count, 2)
        self._check_outputs(wizard, 'asr.report.vendor.otif.line', 2, 'Days Late')

    def test_vendor_otif_on_time(self):
        po = self.env['purchase.order'].create({
            'partner_id': self.vendor.id,
            'picking_type_id': self.warehouse.in_type_id.id,
            'order_line': [Command.create({'product_id': self.p_fifo.id, 'product_qty': 4, 'price_unit': 1.0,
                                           'date_planned': fields.Datetime.now() + timedelta(days=1)})],
        })
        po.button_confirm()
        po.picking_ids.move_ids.quantity = 3
        po.picking_ids.move_ids.picked = True
        Form.from_action(self.env, po.picking_ids.button_validate()).save().process_cancel_backorder()
        wizard = self._wizard('asr.report.vendor.otif', partner_ids=[(6, 0, self.vendor.ids)],
                              product_ids=[(6, 0, self.p_fifo.ids)])
        rows = wizard._asr_rows()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['on_time'])
        self.assertEqual(rows[0]['days_late'], -1)
        self.assertAlmostEqual(rows[0]['qty_received'], 3.0)
        self.assertFalse(rows[0]['in_full'])
        self.assertFalse(rows[0]['otif'])
        self.assertAlmostEqual(rows[0]['odoo_on_time_rate'], 100.0 * 3 / 19, places=2)  # 3 on time out of 10 + 5 + 4

    # ------------------------------------------------------------------
    # #10 Purchase price variance
    # ------------------------------------------------------------------
    def test_price_variance(self):
        wizard = self._wizard('asr.report.price.variance', partner_ids=[(6, 0, self.vendor.ids)])
        rows = wizard._asr_rows()
        self.assertEqual(len(rows), 2)
        by_product = {r['product_id'].id: r for r in rows}
        std = by_product[self.p_std.id]
        self.assertEqual(std['move_id'], self.std_move)
        self.assertEqual(std['reference'], self.receipt.name)
        self.assertEqual(std['partner_id'], self.vendor)
        self.assertEqual(std['order_id'], self.po)
        self.assertAlmostEqual(std['quantity'], 10.0)
        self.assertAlmostEqual(std['price_po'], 12.0)
        self.assertAlmostEqual(std['price_valued'], 15.0)  # the posted bill revalued the receipt
        self.assertAlmostEqual(std['price_standard'], 10.0)
        self.assertAlmostEqual(std['var_po_std'], 20.0)
        self.assertAlmostEqual(std['var_inv_po'], 30.0)
        self.assertAlmostEqual(std['var_total'], 50.0)
        self.assertAlmostEqual(std['var_po_std_pct'], 20.0)
        self.assertAlmostEqual(std['var_inv_po_pct'], 25.0)
        self.assertAlmostEqual(std['var_total_pct'], 50.0)
        avco = by_product[self.p_avco.id]
        self.assertAlmostEqual(avco['quantity'], 5.0)
        self.assertAlmostEqual(avco['price_po'], 8.0)
        self.assertAlmostEqual(avco['price_valued'], 8.0)
        self.assertAlmostEqual(avco['var_inv_po'], 0.0)
        # AVCO: the current cost, which is the receipt price here
        self.assertAlmostEqual(avco['price_standard'], self.p_avco.with_company(self.company).standard_price)

    def test_price_variance_standard_history_and_product_mode(self):
        # a standard price change after the receipt does not change the cost at the receipt date
        self.p_std.with_company(self.company).standard_price = 11.0
        wizard = self._wizard('asr.report.price.variance', product_ids=[(6, 0, self.p_std.ids)])
        rows = wizard._asr_rows()
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]['price_standard'], 10.0)
        # a receipt after the change is costed at the new price
        po = self.env['purchase.order'].create({
            'partner_id': self.vendor.id,
            'picking_type_id': self.warehouse.in_type_id.id,
            'order_line': [Command.create({'product_id': self.p_std.id, 'product_qty': 2, 'price_unit': 13.0})],
        })
        po.button_confirm()
        po.picking_ids.move_ids.picked = True
        po.picking_ids.button_validate()
        rows = wizard._asr_rows()
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(rows[1]['price_standard'], 11.0)
        self.assertAlmostEqual(rows[1]['var_po_std'], 4.0)
        product_mode = self._wizard('asr.report.price.variance', group_by='product',
                                    product_ids=[(6, 0, self.p_std.ids)])
        rows = product_mode._asr_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['move_count'], 2)
        self.assertAlmostEqual(rows[0]['quantity'], 12.0)
        self.assertAlmostEqual(rows[0]['price_po'], (120.0 + 26.0) / 12)
        self.assertAlmostEqual(rows[0]['price_valued'], (150.0 + 26.0) / 12)
        self.assertAlmostEqual(rows[0]['var_po_std'], 24.0)
        self.assertAlmostEqual(rows[0]['var_inv_po'], 30.0)
        self.assertAlmostEqual(rows[0]['var_total'], 54.0)
        lines = self._check_outputs(product_mode, 'asr.report.price.variance.line', 1, 'Total Variance')
        self.assertAlmostEqual(lines.var_total, 54.0, places=2)
        self._check_outputs(wizard, 'asr.report.price.variance.line', 2, 'Valued Price')

    def test_price_variance_hidden_values(self):
        user = self.env['res.users'].create({
            'name': 'ASR Trade User', 'login': 'asr_trade_user',
            'group_ids': [(6, 0, [self.env.ref('ebshel_stock_reports.group_user').id,
                                  self.env.ref('stock.group_stock_user').id])],
        })
        wizard = self._wizard('asr.report.price.variance').with_user(user)
        self.assertFalse(wizard.show_values)
        keys = [c['key'] for c in wizard._asr_visible_columns()]
        self.assertIn('quantity', keys)
        self.assertNotIn('price_po', keys)
        self.assertNotIn('var_total', keys)

    # ------------------------------------------------------------------
    # #12 Open order shortage
    # ------------------------------------------------------------------
    def test_order_shortage(self):
        wizard = self._wizard('asr.report.order.shortage', partner_ids=[(6, 0, self.customer.ids)])
        rows = wizard._asr_rows()
        # SO2 (promised 5 days ago), SO1 backorder line (2 days ago), SO3 (yesterday); the SO1 std line is delivered
        self.assertEqual([r['order_id'] for r in rows], [self.so2, self.so1, self.so3])
        so2, so1, so3 = rows
        # FIFO: 3 free units, nothing reserved; SO2 takes 3 of its 4, SO3 gets none; 1 unit incoming covers SO2
        self.assertEqual(so2['product_id'], self.p_fifo)
        self.assertEqual(so2['date_promised'], self.today - timedelta(days=5))
        self.assertEqual(so2['warehouse_id'], self.warehouse)
        self.assertAlmostEqual(so2['qty_to_deliver'], 4.0)
        self.assertAlmostEqual(so2['qty_reserved'], 0.0)
        self.assertAlmostEqual(so2['qty_free'], 3.0)
        self.assertAlmostEqual(so2['qty_allocated'], 3.0)
        self.assertAlmostEqual(so2['qty_shortage'], 1.0)
        self.assertAlmostEqual(so2['qty_incoming'], 1.0)
        self.assertAlmostEqual(so2['qty_shortage_after'], 0.0)
        self.assertAlmostEqual(so3['qty_to_deliver'], 2.0)
        self.assertAlmostEqual(so3['qty_free'], 0.0)
        self.assertAlmostEqual(so3['qty_allocated'], 0.0)
        self.assertAlmostEqual(so3['qty_shortage'], 2.0)
        self.assertAlmostEqual(so3['qty_incoming'], 0.0)
        self.assertAlmostEqual(so3['qty_shortage_after'], 2.0)
        # AVCO backorder: 7 to deliver, the 2 units left in stock are reserved, nothing free or incoming
        self.assertEqual(so1['product_id'], self.p_avco)
        self.assertAlmostEqual(so1['qty_to_deliver'], 7.0)
        self.assertAlmostEqual(so1['qty_reserved'], 2.0)
        self.assertAlmostEqual(so1['qty_free'], 0.0)
        self.assertAlmostEqual(so1['qty_shortage'], 5.0)
        self.assertAlmostEqual(so1['qty_shortage_after'], 5.0)
        # a line that is fully reserved is not short and only shows with show_all
        self._in(self.p_avco, 5, 8.0)
        self.backorder.action_assign()
        self.assertAlmostEqual(self.backorder.move_ids.quantity, 7.0)
        rows = wizard._asr_rows()
        self.assertEqual([r['order_id'] for r in rows], [self.so2, self.so3])
        wizard.show_all = True
        rows = wizard._asr_rows()
        self.assertEqual([r['order_id'] for r in rows], [self.so2, self.so1, self.so3])
        self.assertAlmostEqual(rows[1]['qty_reserved'], 7.0)
        self.assertAlmostEqual(rows[1]['qty_shortage'], 0.0)
        lines = self._check_outputs(wizard, 'asr.report.order.shortage.line', 3, 'Shortage After Incoming')
        self.assertAlmostEqual(sum(lines.mapped('qty_shortage')), 3.0)
        # warehouse filter (the demo database has open orders of other customers in this warehouse)
        other = self._wizard('asr.report.order.shortage', warehouse_ids=[(6, 0, self.warehouse.ids)],
                             partner_ids=[(6, 0, self.customer.ids)])
        self.assertEqual(len(other._asr_rows()), 2)
        empty_wh = self.env['stock.warehouse'].create({
            'name': 'ASR Other', 'code': 'ASRO', 'company_id': self.company.id})
        filtered = self._wizard('asr.report.order.shortage', warehouse_ids=[(6, 0, empty_wh.ids)])
        self.assertEqual(filtered._asr_rows(), [])
