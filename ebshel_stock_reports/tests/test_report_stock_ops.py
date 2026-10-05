# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
"""Reports #13 (valued count variance), #14 (scrap and returns by reason) and #15 (registers)."""
import io
import zipfile
from datetime import timedelta

from odoo import Command, fields
from odoo.tests import tagged

from .common import AdvancedStockReportsCase


@tagged('post_install', '-at_install', 'asr')
class TestReportStockOps(AdvancedStockReportsCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.reason_damaged = cls.env.ref('ebshel_stock_reports.reason_damaged')
        cls.reason_quality = cls.env.ref('ebshel_stock_reports.reason_quality')
        cls.reason_lost = cls.env.ref('ebshel_stock_reports.reason_lost')
        cls.inventory_location = cls.p_std.with_company(cls.company).property_stock_inventory

    def setUp(self):
        super().setUp()
        # 20 units at 10 (standard cost of p_std): value 200
        self.receipt = self._in(self.p_std, 20, 10.0, date=self._days_ago(4))

        # Scrap 2 with a reason
        self.scrap = self.env['stock.scrap'].create({
            'product_id': self.p_std.id, 'product_uom_id': self.uom_unit.id, 'scrap_qty': 2,
            'location_id': self.stock_location.id, 'company_id': self.company.id,
            'scrap_reason_tag_ids': [Command.set(self.reason_damaged.ids)],
        })
        self.scrap.do_scrap()

        # Deliver 5 through the demo delivery type, then return 2 through the return wizard
        self.delivery = self.env['stock.picking'].create({
            'picking_type_id': self.warehouse.out_type_id.id,
            'location_id': self.stock_location.id,
            'location_dest_id': self.customer_location.id,
            'partner_id': self.partner.id,
            'origin': 'ASR-SO-1',
            'move_ids': [Command.create({
                'product_id': self.p_std.id, 'product_uom': self.uom_unit.id, 'product_uom_qty': 5,
                'location_id': self.stock_location.id, 'location_dest_id': self.customer_location.id,
            })],
        })
        self.delivery.action_confirm()
        self.delivery.action_assign()
        self.delivery.move_ids.write({'quantity': 5, 'picked': True})
        self.delivery.button_validate()
        self.assertEqual(self.delivery.state, 'done')
        wizard = self.env['stock.return.picking'].with_context(
            active_id=self.delivery.id, active_ids=self.delivery.ids, active_model='stock.picking',
        ).create({'asr_reason_id': self.reason_quality.id})
        wizard.product_return_moves.write({'quantity': 2})
        result = wizard.action_create_returns()
        self.return_picking = self.env['stock.picking'].browse(result['res_id'])
        self.return_picking.move_ids.write({'quantity': 2, 'picked': True})
        self.return_picking.button_validate()
        self.assertEqual(self.return_picking.state, 'done')

        # Stock location now holds 15: count 12 (loss of 3) through the adjustment wizard with a reason
        quant = self.env['stock.quant'].with_context(inventory_mode=True).create({
            'product_id': self.p_std.id, 'location_id': self.stock_location.id, 'inventory_quantity': 12,
        })
        # Shelf holds 0: count 4 (gain of 4)
        quant_shelf = self.env['stock.quant'].with_context(inventory_mode=True).create({
            'product_id': self.p_std.id, 'location_id': self.shelf.id, 'inventory_quantity': 4,
        })
        self.env['stock.inventory.adjustment.name'].create({
            'quant_ids': [Command.set((quant | quant_shelf).ids)],
            'inventory_adjustment_name': 'ASR Count',
            'asr_reason_id': self.reason_lost.id,
        }).action_apply()

        # A count applied before the module existed: no before/counted quantities
        self.old_count = self._make_move(self.p_std, 1, self.inventory_location, self.stock_location,
                                         is_inventory=True)
        # An internal transfer without a picking type
        self._transfer(self.p_std, 1, self.stock_location, self.shelf)
        self._process()

    def _wizard(self, model, **vals):
        values = {
            'company_id': self.company.id, 'date_from': fields.Date.today() - timedelta(days=10),
            'date_to': fields.Date.today(), 'product_ids': [Command.set(self.p_std.ids)],
        }
        values.update(vals)
        return self.env[model].create(values)

    def _check_screen_xlsx_pdf(self, wizard, expected_rows, header, pdf_rows=None):
        """Screen lines are the data rows; the PDF document also counts the ``_group`` headers."""
        action = wizard.action_view()
        lines = self.env[wizard._asr_line_model].search(action['domain'])
        self.assertEqual(len(lines), expected_rows)
        content = wizard._asr_xlsx()
        self.assertTrue(content.startswith(b'PK'))
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            self.assertIn('xl/worksheets/sheet1.xml', archive.namelist())
        pdf_action = wizard.action_pdf()
        self.assertEqual(pdf_action['report_name'], 'ebshel_stock_reports.report_document')
        document = self.env['asr.report.print'].browse(pdf_action['context']['active_ids'])
        self.assertEqual(document.row_count, pdf_rows if pdf_rows is not None else expected_rows)
        html = self.env['ir.actions.report']._render_qweb_html(
            'ebshel_stock_reports.action_report_document', document.ids)[0]
        self.assertIn(header, html)

    # ------------------------------------------------------------------
    # Scenario checks on the new fields
    # ------------------------------------------------------------------
    def test_scenario_fields(self):
        self.assertEqual(self.scrap.move_ids.asr_reason_id, self.reason_damaged)
        return_move = self.return_picking.move_ids
        self.assertEqual(len(return_move), 1)
        self.assertEqual(return_move.origin_returned_move_id, self.delivery.move_ids)
        self.assertEqual(return_move.asr_reason_id, self.reason_quality)
        self.assertEqual(return_move.location_id.usage, 'customer')
        counts = self.env['stock.move'].search([
            ('product_id', '=', self.p_std.id), ('is_inventory', '=', True), ('reference', '=', 'ASR Count')])
        self.assertEqual(len(counts), 2)
        loss = counts.filtered(lambda m: m.location_id == self.stock_location)
        gain = counts.filtered(lambda m: m.location_dest_id == self.shelf)
        self.assertEqual(loss.asr_qty_before, 15.0)
        self.assertEqual(loss.asr_qty_counted, 12.0)
        self.assertEqual(loss.asr_reason_id, self.reason_lost)
        self.assertEqual(gain.asr_qty_before, 0.0)
        self.assertEqual(gain.asr_qty_counted, 4.0)
        self.assertEqual(gain.asr_reason_id, self.reason_lost)
        self.assertEqual(self.old_count.asr_qty_before, 0.0)
        self.assertEqual(self.old_count.asr_qty_counted, 0.0)

    # ------------------------------------------------------------------
    # #13 Valued count variance
    # ------------------------------------------------------------------
    def test_count_variance(self):
        wizard = self._wizard('asr.report.count.variance')
        rows = wizard._asr_rows()
        self.assertEqual(len(rows), 3)
        by_location = {(r['location_id'], r['difference_only']): r for r in rows}
        loss = by_location[(self.stock_location, False)]
        gain = by_location[(self.shelf, False)]
        old = by_location[(self.stock_location, True)]
        self.assertEqual(loss['reference'], 'ASR Count')
        self.assertEqual(loss['reason_id'], self.reason_lost)
        self.assertAlmostEqual(loss['qty_before'], 15.0)
        self.assertAlmostEqual(loss['qty_counted'], 12.0)
        self.assertAlmostEqual(loss['qty_diff'], -3.0)
        self.assertAlmostEqual(loss['value_diff'], -30.0, places=2)
        self.assertAlmostEqual(loss['value_loss'], -30.0, places=2)
        self.assertAlmostEqual(loss['value_gain'], 0.0)
        self.assertAlmostEqual(loss['unit_cost'], 10.0, places=2)
        self.assertAlmostEqual(gain['qty_before'], 0.0)
        self.assertAlmostEqual(gain['qty_counted'], 4.0)
        self.assertAlmostEqual(gain['qty_diff'], 4.0)
        self.assertAlmostEqual(gain['value_diff'], 40.0, places=2)
        self.assertAlmostEqual(gain['value_gain'], 40.0, places=2)
        self.assertIsNone(old['qty_before'])
        self.assertIsNone(old['qty_counted'])
        self.assertAlmostEqual(old['qty_diff'], 1.0)
        self.assertAlmostEqual(old['value_diff'], 10.0, places=2)
        totals = wizard._asr_totals(rows, wizard._asr_columns())
        self.assertAlmostEqual(totals['qty_diff'], 2.0)
        self.assertAlmostEqual(totals['value_gain'], 50.0, places=2)
        self.assertAlmostEqual(totals['value_loss'], -30.0, places=2)
        self.assertAlmostEqual(totals['value_diff'], 20.0, places=2)
        # location filter applies to the counted (internal) side of the line
        shelf_only = self._wizard('asr.report.count.variance', location_ids=[Command.set(self.shelf.ids)])
        rows = shelf_only._asr_rows()
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]['qty_diff'], 4.0)
        # group by location: one header per location, rows in location order
        grouped = self._wizard('asr.report.count.variance', group_by_location=True)
        rows = grouped._asr_rows()
        headers = [r['_group'] for r in rows if r.get('_group')]
        self.assertEqual(len(headers), 2)
        self.assertEqual(len([r for r in rows if not r.get('_group')]), 3)
        self.assertEqual(grouped._asr_view_context(), {'search_default_group_location': 1})
        self._check_screen_xlsx_pdf(wizard, 3, b'Net Value')
        lines = self.env['asr.report.count.variance.line'].search([('wizard_id', '=', wizard.id)])
        self.assertEqual(len(lines.filtered('difference_only')), 1)

    # ------------------------------------------------------------------
    # #14 Scrap and returns by reason
    # ------------------------------------------------------------------
    def test_scrap_return(self):
        wizard = self._wizard('asr.report.scrap.return')
        self.assertEqual(wizard.kind, 'both')
        self.assertEqual(wizard.group_by, 'reason')
        all_rows = wizard._asr_rows()
        headers = [r['_group'] for r in all_rows if r.get('_group')]
        rows = [r for r in all_rows if not r.get('_group')]
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(headers), 2)
        # group order follows the reason sequence: Damaged (10) before Quality Issue (80)
        self.assertTrue(headers[0].startswith('Damaged'))
        self.assertTrue(headers[1].startswith('Quality Issue'))
        self.assertIn('quantity 2.00', headers[0])
        self.assertEqual(all_rows[0]['_group'], headers[0])
        self.assertEqual(all_rows[1]['kind_code'], 'scrap')
        scrap, ret = rows
        self.assertEqual(scrap['kind'], 'Scrap')
        self.assertEqual(scrap['reference'], self.scrap.name)
        self.assertEqual(scrap['reason_id'], self.reason_damaged)
        self.assertEqual(scrap['gst_category'], 'destroyed')
        self.assertEqual(scrap['product_id'], self.p_std)
        self.assertAlmostEqual(scrap['quantity'], 2.0)
        self.assertAlmostEqual(scrap['value'], 20.0, places=2)
        self.assertEqual(scrap['location_id'], self.stock_location)
        self.assertEqual(scrap['location_dest_id'], self.scrap.scrap_location_id)
        self.assertEqual(ret['kind_code'], 'customer_return')
        self.assertEqual(ret['reference'], self.return_picking.name)
        self.assertEqual(ret['reason_id'], self.reason_quality)
        self.assertEqual(ret['gst_category'], 'other')
        self.assertEqual(ret['partner_id'], self.partner)
        self.assertAlmostEqual(ret['quantity'], 2.0)
        self.assertAlmostEqual(ret['value'], 20.0, places=2)
        self.assertEqual(ret['location_id'], self.customer_location)
        totals = wizard._asr_totals(all_rows, wizard._asr_columns())
        self.assertAlmostEqual(totals['quantity'], 4.0)
        self.assertAlmostEqual(totals['value'], 40.0, places=2)
        # kind filter
        scraps = self._wizard('asr.report.scrap.return', kind='scrap')
        self.assertEqual([r['kind_code'] for r in scraps._asr_rows() if not r.get('_group')], ['scrap'])
        returns = self._wizard('asr.report.scrap.return', kind='return')
        self.assertEqual([r['kind_code'] for r in returns._asr_rows() if not r.get('_group')], ['customer_return'])
        # other groupings
        by_partner = self._wizard('asr.report.scrap.return', group_by='partner')
        rows = by_partner._asr_rows()
        headers = [r['_group'] for r in rows if r.get('_group')]
        self.assertEqual(len(headers), 2)
        self.assertTrue(headers[0].startswith(self.partner.display_name))
        self.assertEqual(by_partner._asr_view_context(), {'search_default_group_partner': 1})
        by_month = self._wizard('asr.report.scrap.return', group_by='month')
        rows = by_month._asr_rows()
        headers = [r['_group'] for r in rows if r.get('_group')]
        self.assertEqual(len(headers), 1)
        self.assertTrue(headers[0].startswith(fields.Date.today().strftime('%Y-%m')))
        by_product = self._wizard('asr.report.scrap.return', group_by='product')
        self.assertEqual(len([r for r in by_product._asr_rows() if r.get('_group')]), 1)
        # scrap location filter: the scrap leaves the stock location, the return enters it
        stock_only = self._wizard('asr.report.scrap.return', location_ids=[Command.set(self.stock_location.ids)])
        self.assertEqual(len([r for r in stock_only._asr_rows() if not r.get('_group')]), 2)
        shelf_only = self._wizard('asr.report.scrap.return', location_ids=[Command.set(self.shelf.ids)])
        self.assertEqual(shelf_only._asr_rows(), [])
        self._check_screen_xlsx_pdf(wizard, 2, b'GST Category', pdf_rows=4)  # two group headers + two lines
        lines = self.env['asr.report.scrap.return.line'].search([('wizard_id', '=', wizard.id)])
        self.assertEqual(set(lines.mapped('kind_code')), {'scrap', 'customer_return'})

    # ------------------------------------------------------------------
    # #15 Registers
    # ------------------------------------------------------------------
    def test_registers(self):
        inward = self._wizard('asr.report.register', register='inward')
        rows = inward._asr_rows()
        self.assertEqual(len(rows), 2)  # receipt (no picking type) and the customer return (Receipts type)
        self.assertEqual([r['serial'] for r in rows], [1, 2])
        receipt, ret = rows
        self.assertEqual(receipt['product_id'], self.p_std)
        self.assertFalse(receipt['picking_type_id'])
        self.assertAlmostEqual(receipt['quantity'], 20.0)
        self.assertAlmostEqual(receipt['value'], 200.0, places=2)
        self.assertAlmostEqual(receipt['unit_value'], 10.0, places=2)
        self.assertEqual(receipt['uom_id'], self.uom_unit)
        self.assertEqual(ret['reference'], self.return_picking.name)
        self.assertEqual(ret['picking_type_id'], self.return_picking.picking_type_id)
        self.assertEqual(ret['picking_type_id'].code, 'incoming')
        self.assertEqual(ret['partner_id'], self.partner)
        self.assertEqual(ret['origin'], self.return_picking.origin)
        self.assertEqual(ret['location_id'], self.customer_location)
        self.assertAlmostEqual(ret['quantity'], 2.0)
        self.assertAlmostEqual(ret['value'], 20.0, places=2)
        self.assertEqual(ret['purchase_order'], '')
        self.assertIn('purchase_order', [c['key'] for c in inward._asr_columns()])
        self.assertNotIn('sale_order', [c['key'] for c in inward._asr_columns()])
        totals = inward._asr_totals(rows, inward._asr_columns())
        self.assertAlmostEqual(totals['quantity'], 22.0)
        self.assertAlmostEqual(totals['value'], 220.0, places=2)

        outward = self._wizard('asr.report.register', register='outward')
        rows = outward._asr_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['reference'], self.delivery.name)
        self.assertEqual(rows[0]['picking_type_id'], self.warehouse.out_type_id)
        self.assertEqual(rows[0]['origin'], 'ASR-SO-1')
        self.assertEqual(rows[0]['partner_id'], self.partner)
        self.assertAlmostEqual(rows[0]['quantity'], 5.0)
        self.assertAlmostEqual(rows[0]['value'], 50.0, places=2)
        self.assertIn('sale_order', [c['key'] for c in outward._asr_columns()])

        internal = self._wizard('asr.report.register', register='internal')
        rows = internal._asr_rows()
        self.assertEqual(len(rows), 1)  # the transfer stock -> shelf (no picking type)
        self.assertEqual(rows[0]['location_dest_id'], self.shelf)
        self.assertAlmostEqual(rows[0]['quantity'], 1.0)
        self.assertAlmostEqual(rows[0]['value'], 0.0)  # transfers are not valued

        dropship = self._wizard('asr.report.register', register='dropship')
        self.assertEqual(dropship._asr_rows(), [])

        # warehouse filter through the picking type (and the locations for moves without one)
        by_warehouse = self._wizard('asr.report.register', register='inward',
                                    warehouse_ids=[Command.set(self.warehouse.ids)])
        self.assertEqual(len(by_warehouse._asr_rows()), 2)
        other_warehouse = self.env['stock.warehouse'].create({'name': 'ASR Other', 'code': 'ASRO'})
        other = self._wizard('asr.report.register', register='inward',
                             warehouse_ids=[Command.set(other_warehouse.ids)])
        self.assertEqual(other._asr_rows(), [])
        # location filter on the line's locations
        shelf_internal = self._wizard('asr.report.register', register='internal',
                                      location_ids=[Command.set(self.shelf.ids)])
        self.assertEqual(len(shelf_internal._asr_rows()), 1)
        self._check_screen_xlsx_pdf(inward, 2, b'Operation Type')
        lines = self.env['asr.report.register.line'].search([('wizard_id', '=', inward.id)])
        self.assertEqual(lines.mapped('serial'), [1, 2])
