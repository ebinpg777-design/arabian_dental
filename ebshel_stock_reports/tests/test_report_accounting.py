# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
"""Reports #17 (inventory vs GL and accruals), #19 (GST stock register) and #20 (monthly production account)."""
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import AdvancedStockReportsCase

LOSS_KEYS = ('qty_lost', 'qty_stolen', 'qty_destroyed', 'qty_written_off', 'qty_gift', 'qty_free_sample',
             'qty_other_loss')


@tagged('post_install', '-at_install', 'asr')
class TestReportAccounting(AdvancedStockReportsCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vendor = cls.env['res.partner'].create({'name': 'ASR Vendor'})
        cls.customer = cls.env['res.partner'].create({'name': 'ASR Customer'})
        cls.reason_destroyed = cls.env.ref('ebshel_stock_reports.reason_damaged')
        cls.categ_std.asr_gst_stock_class = 'raw'
        cls.categ_avco.asr_gst_stock_class = 'finished'
        cls.categ_fifo.asr_gst_stock_class = False

    # ------------------------------------------------------------------
    # Scenario helpers
    # ------------------------------------------------------------------
    def _validate(self, picking, qty):
        picking.move_ids.write({'quantity': qty, 'picked': True})
        picking.with_context(skip_backorder=True, picking_ids_not_to_backorder=picking.ids).button_validate()
        self.assertEqual(picking.state, 'done')

    def _purchase(self, product, qty, price, receive=None):
        po = self.env['purchase.order'].create({
            'partner_id': self.vendor.id, 'company_id': self.company.id,
            'order_line': [Command.create({'product_id': product.id, 'product_qty': qty, 'price_unit': price})],
        })
        po.button_confirm()
        if receive:
            self._validate(po.picking_ids[0], receive)
        return po

    def _sale(self, product, qty, price, deliver=None):
        so = self.env['sale.order'].create({
            'partner_id': self.customer.id, 'company_id': self.company.id,
            'order_line': [Command.create({'product_id': product.id, 'product_uom_qty': qty, 'price_unit': price})],
        })
        so.action_confirm()
        if deliver:
            self._validate(so.picking_ids[0], deliver)
        return so

    def _bill(self, po_line, qty, price):
        return self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.vendor.id, 'company_id': self.company.id,
            'invoice_date': fields.Date.today(),
            'invoice_line_ids': [Command.create({
                'product_id': po_line.product_id.id, 'quantity': qty, 'price_unit': price,
                'purchase_line_id': po_line.id,
            })],
        })

    def _invoice(self, so_line, qty, price):
        return self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.customer.id, 'company_id': self.company.id,
            'invoice_date': fields.Date.today(),
            'invoice_line_ids': [Command.create({
                'product_id': so_line.product_id.id, 'quantity': qty, 'price_unit': price,
                'sale_line_ids': [Command.set(so_line.ids)],
            })],
        })

    def _manufacture(self, finished, component, qty_per_unit, qty):
        bom = self.env['mrp.bom'].create({
            'product_tmpl_id': finished.product_tmpl_id.id, 'product_id': finished.id,
            'product_qty': 1.0, 'type': 'normal', 'consumption': 'flexible',
            'bom_line_ids': [Command.create({'product_id': component.id, 'product_qty': qty_per_unit})],
        })
        mo = self.env['mrp.production'].create({
            'product_id': finished.id, 'product_qty': qty, 'bom_id': bom.id, 'company_id': self.company.id,
        })
        mo.action_confirm()
        mo.button_mark_done()
        self.assertEqual(mo.state, 'done')
        return mo

    def _scrap(self, product, qty, reason):
        scrap = self.env['stock.scrap'].create({
            'product_id': product.id, 'product_uom_id': product.uom_id.id, 'scrap_qty': qty,
            'location_id': self.stock_location.id, 'company_id': self.company.id,
            'scrap_reason_tag_ids': [Command.set(reason.ids)],
        })
        scrap.do_scrap()
        return scrap

    @staticmethod
    def _rows(wizard):
        return [r for r in wizard._asr_rows() if not r.get('_group')]

    def _assert_identity(self, row, msg):
        out = row['qty_issued'] + row['qty_consumed'] + row['qty_vendor_return'] + row['qty_transfer_out'] \
            + sum(row[k] for k in LOSS_KEYS)
        self.assertAlmostEqual(row['qty_opening'] + row['qty_received'] - out, row['qty_closing'], places=4, msg=msg)

    # ------------------------------------------------------------------
    # #17 Inventory vs GL and accruals
    # ------------------------------------------------------------------
    def test_accrual_listings(self):
        self._in(self.p_std, 20, 10.0, date=self._days_ago(3))
        po_received = self._purchase(self.p_std, 10, 12.0, receive=10)          # received, not billed
        po_billed = self._purchase(self.p_avco, 5, 8.0, receive=2)              # billed 5, received 2
        self._bill(po_billed.order_line, 5, 8.0)
        so_delivered = self._sale(self.p_std, 4, 30.0, deliver=4)              # delivered, not invoiced
        so_invoiced = self._sale(self.p_std, 3, 25.0)                          # invoiced, not delivered
        self._invoice(so_invoiced.order_line, 3, 25.0)
        self.assertEqual(po_billed.order_line.qty_invoiced, 5)
        self.assertEqual(so_invoiced.order_line.qty_invoiced, 3)
        self._process()

        def wizard(listing):
            return self.env['asr.report.accrual'].create({
                'company_id': self.company.id, 'listing': listing, 'date_to': fields.Date.today(),
                'product_ids': [Command.set(self.products.ids)],
            })

        rows = self._rows(wizard('received_not_billed'))
        self.assertEqual([r['order_ref'] for r in rows], [po_received.name])
        row = rows[0]
        self.assertEqual((row['qty_done'], row['qty_invoiced'], row['qty_open']), (10, 0, 10))
        self.assertAlmostEqual(row['price_unit'], 12.0)
        self.assertAlmostEqual(row['amount_price'], 120.0)
        self.assertGreater(row['unit_cost'], 0)
        self.assertAlmostEqual(row['amount_cost'], 10 * row['unit_cost'])
        self.assertEqual(row['partner_id'], self.vendor)
        self.assertTrue(row['invoice_status'])
        self.assertEqual(row['date_expected'], self.company._asr_local_day(po_received.order_line.date_planned))
        groups = [r for r in wizard('received_not_billed')._asr_rows() if r.get('_group')]
        self.assertEqual(len(groups), 1)
        self.assertIn(self.vendor.name, groups[0]['_group'])

        rows = self._rows(wizard('billed_not_received'))
        self.assertEqual([r['order_ref'] for r in rows], [po_billed.name])
        self.assertEqual((rows[0]['qty_done'], rows[0]['qty_invoiced'], rows[0]['qty_open']), (2, 5, 3))
        self.assertAlmostEqual(rows[0]['amount_price'], 24.0)

        rows = self._rows(wizard('delivered_not_invoiced'))
        self.assertEqual([r['order_ref'] for r in rows], [so_delivered.name])
        self.assertEqual((rows[0]['qty_done'], rows[0]['qty_invoiced'], rows[0]['qty_open']), (4, 0, 4))
        self.assertAlmostEqual(rows[0]['price_unit'], 30.0)
        self.assertAlmostEqual(rows[0]['amount_price'], 120.0)
        self.assertAlmostEqual(rows[0]['amount_cost'], 4 * 10.0)  # standard cost 10
        self.assertEqual(rows[0]['partner_id'], self.customer)

        rows = self._rows(wizard('invoiced_not_delivered'))
        self.assertEqual([r['order_ref'] for r in rows], [so_invoiced.name])
        self.assertEqual((rows[0]['qty_done'], rows[0]['qty_invoiced'], rows[0]['qty_open']), (0, 3, 3))
        self.assertAlmostEqual(rows[0]['amount_price'], 75.0)

        summary = wizard('summary')
        rows = self._rows(summary)
        by_key = {r['listing']: r for r in rows}
        for key in ('received_not_billed', 'billed_not_received', 'delivered_not_invoiced', 'invoiced_not_delivered'):
            self.assertEqual(by_key[key]['row_count'], 1, key)
        self.assertAlmostEqual(by_key['received_not_billed']['amount_price'], 120.0)
        self.assertAlmostEqual(by_key['delivered_not_invoiced']['amount_price'], 120.0)
        self.assertIn('stock_value', by_key)
        self.assertIn('gl_value', by_key)
        # the engine value of the summary is the sum of the products' closing values
        expected = sum(self._closing(p, fields.Date.today())[1] for p in self.products)
        self.assertAlmostEqual(by_key['stock_value']['amount_cost'], expected, places=2)
        if self.company.account_stock_valuation_id:
            self.assertIn('difference', by_key)
            self.assertAlmostEqual(by_key['difference']['amount_cost'],
                                   by_key['stock_value']['amount_cost'] - by_key['gl_value']['amount_cost'], places=2)

        # screen, Excel, PDF, link to Odoo's report
        action = summary.action_view()
        self.assertEqual(action['res_model'], 'asr.report.accrual.line')
        self.assertEqual(summary.line_count, len(rows))
        listing = wizard('received_not_billed')
        listing.action_view()
        self.assertEqual(listing.line_ids.mapped('qty_open'), [10.0])
        self.assertTrue(listing._asr_xlsx())
        self.assertEqual(listing.action_pdf()['type'], 'ir.actions.report')
        self.assertEqual(summary.action_open_odoo_valuation()['type'], 'ir.actions.client')

    # ------------------------------------------------------------------
    # #19 GST stock register
    # ------------------------------------------------------------------
    def test_gst_register(self):
        self._in(self.p_std, 10, 10.0, date=self._days_ago(5))
        self._out(self.p_std, 2, date=self._days_ago(3))
        self._in(self.p_fifo, 3, 2.0, date=self._days_ago(4))
        self._scrap(self.p_std, 1, self.reason_destroyed)
        mo = self._manufacture(self.p_avco, self.p_std, 2, 3)   # consumes 6 p_std, produces 3 p_avco
        self._process()
        wizard = self.env['asr.report.gst.register'].create({
            'company_id': self.company.id, 'date_from': fields.Date.today() - timedelta(days=10),
            'date_to': fields.Date.today(), 'product_ids': [Command.set(self.products.ids)],
        })
        all_rows = wizard._asr_rows()
        groups = [r['_group'] for r in all_rows if r.get('_group')]
        self.assertEqual(groups, ['Raw Material / Input', 'Finished Goods', 'Other'])
        rows = {r['product_id']: r for r in all_rows if not r.get('_group')}
        self.assertEqual(set(rows), set(self.products))

        raw = rows[self.p_std]
        self.assertEqual(raw['gst_class'], 'raw')
        self.assertEqual((raw['qty_opening'], raw['qty_received'], raw['qty_issued']), (0, 10, 2))
        self.assertEqual(raw['qty_consumed'], 6)
        self.assertEqual(raw['qty_destroyed'], 1)
        self.assertEqual(raw['qty_other_loss'], 0)
        self.assertEqual(raw['qty_closing'], 1)
        self.assertAlmostEqual(raw['value_closing'], 10.0, places=2)

        finished = rows[self.p_avco]
        self.assertEqual(finished['gst_class'], 'finished')
        self.assertEqual(finished['qty_received'], 3)
        self.assertEqual(finished['qty_closing'], 3)
        self.assertEqual(mo.qty_produced, 3)

        other = rows[self.p_fifo]
        self.assertEqual(other['gst_class'], 'other')
        self.assertIn('no GST stock class', other['note'])
        self.assertEqual((other['qty_received'], other['qty_closing']), (3, 3))
        for product, row in rows.items():
            self._assert_identity(row, product.display_name)
            self._assert_reconciled(product)

        # a scrap without any reason lands in "other loss"
        scrap = self.env['stock.scrap'].create({
            'product_id': self.p_fifo.id, 'product_uom_id': self.uom_unit.id, 'scrap_qty': 1,
            'location_id': self.stock_location.id, 'company_id': self.company.id,
        })
        scrap.do_scrap()
        self._process()
        row = {r['product_id']: r for r in self._rows(wizard)}[self.p_fifo]
        self.assertEqual((row['qty_other_loss'], row['qty_closing']), (1, 2))
        self._assert_identity(row, 'other loss')

        # period helper and the GST switch
        wizard.date_to = fields.Date.to_date('2026-08-20')
        wizard.period = 'quarter'
        wizard._onchange_period()
        self.assertEqual((wizard.date_from, wizard.date_to),
                         (fields.Date.to_date('2026-07-01'), fields.Date.to_date('2026-09-30')))
        wizard.period = 'year'
        wizard._onchange_period()
        self.assertEqual((wizard.date_from, wizard.date_to),
                         (fields.Date.to_date('2026-04-01'), fields.Date.to_date('2027-03-31')))
        wizard.write({'date_from': fields.Date.today() - timedelta(days=10), 'date_to': fields.Date.today()})

        action = wizard.action_view()
        self.assertEqual(action['res_model'], 'asr.report.gst.register.line')
        self.assertEqual(len(wizard.line_ids), 3)
        self.assertTrue(wizard._asr_xlsx())
        self.assertEqual(wizard.action_pdf()['type'], 'ir.actions.report')
        self.company.asr_india_gst = False
        with self.assertRaises(UserError):
            wizard.action_view()
        with self.assertRaises(UserError):
            wizard._asr_xlsx()
        with self.assertRaises(UserError):
            wizard.action_pdf()

    # ------------------------------------------------------------------
    # #20 Monthly production account
    # ------------------------------------------------------------------
    def test_production_account(self):
        self._in(self.p_std, 10, 10.0, date=self._days_ago(5))
        self._out(self.p_std, 1, date=self._days_ago(2))
        mo = self._manufacture(self.p_avco, self.p_std, 2, 3)   # consumes 6 p_std, produces 3 p_avco
        self._out(self.p_avco, 1)
        self._process()
        today = fields.Date.today()
        wizard = self.env['asr.report.production.account'].create({
            'company_id': self.company.id, 'date_from': today.replace(day=1), 'date_to': today,
            'product_ids': [Command.set(self.p_avco.ids)],
        })
        all_rows = wizard._asr_rows()
        month = today.strftime('%Y-%m')
        self.assertEqual([r['_group'] for r in all_rows if r.get('_group')],
                         [f"{month} - Finished goods", f"{month} - Materials consumed"])
        rows = [r for r in all_rows if not r.get('_group')]
        finished = [r for r in rows if r['section'] == 'finished']
        materials = [r for r in rows if r['section'] == 'material']
        self.assertEqual([r['product_id'] for r in finished], [self.p_avco])
        self.assertEqual([r['product_id'] for r in materials], [self.p_std])

        f = finished[0]
        self.assertEqual(f['month'], month)
        self.assertEqual((f['qty_produced'], f['qty_sold'], f['qty_closing'], f['mo_count']), (3, 1, 2, 1))
        self.assertFalse(f['is_byproduct'])
        self.assertAlmostEqual(f['value_production'], sum(mo.move_finished_ids.mapped('value')), places=2)
        self.assertAlmostEqual(f['value_production'], 60.0, places=2)   # 6 components at 10
        self.assertEqual(f['value_consumption'], 0.0)

        m = materials[0]
        self.assertEqual((m['qty_consumed'], m['qty_produced'], m['mo_count']), (6, 0, 1))
        self.assertAlmostEqual(m['value_consumption'], sum(mo.move_raw_ids.mapped('value')), places=2)
        self.assertAlmostEqual(m['value_consumption'], 60.0, places=2)
        self.assertEqual(m['qty_closing'], 3)
        for row in rows:
            self.assertAlmostEqual(
                row['qty_opening'] + row['qty_produced'] + row['qty_received'] - row['qty_sold'] - row['qty_consumed']
                - row['qty_wastage'] - row['qty_other_out'], row['qty_closing'], places=4, msg=row['product_id'].name)

        # month without production: no rows
        empty = self.env['asr.report.production.account'].create({
            'company_id': self.company.id, 'date_from': fields.Date.to_date('2020-01-01'),
            'date_to': fields.Date.to_date('2020-02-29'), 'product_ids': [Command.set(self.p_avco.ids)],
        })
        self.assertEqual(empty._asr_rows(), [])

        action = wizard.action_view()
        self.assertEqual(action['res_model'], 'asr.report.production.account.line')
        self.assertEqual(len(wizard.line_ids), 2)
        self.assertTrue(wizard._asr_xlsx())
        self.assertEqual(wizard.action_pdf()['type'], 'ir.actions.report')
        self.company.asr_india_gst = False
        with self.assertRaises(UserError):
            wizard.action_view()
