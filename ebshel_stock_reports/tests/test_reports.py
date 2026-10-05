# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
import io
from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from .common import AdvancedStockReportsCase


@tagged('post_install', '-at_install', 'asr')
class TestStockLedger(AdvancedStockReportsCase):

    def setUp(self):
        super().setUp()
        self._in(self.p_fifo, 10, 10.0, date=self._days_ago(5))
        self._in(self.p_fifo, 5, 14.0, date=self._days_ago(4))
        self._transfer(self.p_fifo, 3, self.stock_location, self.shelf, date=self._days_ago(3))
        self._out(self.p_fifo, 6, date=self._days_ago(2))
        self._process()

    def _wizard(self, **vals):
        values = {
            'company_id': self.company.id, 'date_from': fields.Date.today() - timedelta(days=3),
            'date_to': fields.Date.today(), 'product_ids': [(6, 0, self.p_fifo.ids)],
        }
        values.update(vals)
        return self.env['asr.report.stock.ledger'].create(values)

    def test_ledger_foots_to_odoo(self):
        wizard = self._wizard()
        rows = wizard._asr_rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertAlmostEqual(row['qty_opening'], 15.0)
        self.assertAlmostEqual(row['qty_transfer_in'], 3.0)
        self.assertAlmostEqual(row['qty_transfer_out'], 3.0)
        self.assertAlmostEqual(row['qty_delivery'], 6.0)
        self.assertAlmostEqual(row['qty_closing'], 9.0)
        qty, value = self._odoo_value(self.p_fifo, fields.Datetime.now())
        self.assertAlmostEqual(row['qty_closing'], qty)
        self.assertAlmostEqual(row['value_closing'], value, places=2)
        # closing = opening + in - out + adjustment
        self.assertAlmostEqual(
            row['value_closing'],
            row['value_opening'] + row['value_in'] - row['value_out'] + row['value_adjustment'], places=4)
        # FIFO: the 6 delivered came from the 10 @ 10: value out 60, closing 4 @ 10 + 5 @ 14 = 110
        self.assertAlmostEqual(row['value_out'], 60.0, places=2)
        self.assertAlmostEqual(row['value_closing'], 110.0, places=2)

    def test_location_level(self):
        wizard = self._wizard(location_ids=[(6, 0, self.shelf.ids)])
        rows = wizard._asr_rows()
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]['qty_opening'], 0.0)
        self.assertAlmostEqual(rows[0]['qty_transfer_in'], 3.0)
        self.assertAlmostEqual(rows[0]['qty_closing'], 3.0)
        # ratio method: 3/9 of the company value
        self.assertAlmostEqual(rows[0]['value_closing'], 110.0 * 3 / 9, places=2)

    def test_card_and_moves_modes(self):
        card = self._wizard(mode='card')
        rows = [r for r in card._asr_rows() if not r.get('_group')]
        self.assertEqual(len(rows), 2)  # transfer day and delivery day
        self.assertAlmostEqual(rows[-1]['qty_closing'], 9.0)
        moves = self._wizard(mode='moves', date_from=fields.Date.today() - timedelta(days=10))
        rows = [r for r in moves._asr_rows() if not r.get('_group')]
        self.assertEqual(len(rows), 3)  # two receipts and one delivery; the internal transfer stays inside the level
        self.assertAlmostEqual(rows[-1]['qty_closing'], 9.0)
        self.assertAlmostEqual(rows[-1]['value_out'], 60.0, places=2)

    def test_screen_xlsx_pdf(self):
        wizard = self._wizard()
        action = wizard.action_view()
        lines = self.env['asr.report.stock.ledger.line'].search(action['domain'])
        self.assertEqual(len(lines), 1)
        self.assertAlmostEqual(lines.value_closing, 110.0, places=2)
        content = wizard._asr_xlsx()
        self.assertTrue(content.startswith(b'PK'))
        import zipfile
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            self.assertIn('xl/worksheets/sheet1.xml', archive.namelist())
        pdf_action = wizard.action_pdf()
        self.assertEqual(pdf_action['report_name'], 'ebshel_stock_reports.report_document')
        document = self.env['asr.report.print'].browse(pdf_action['context']['active_ids'])
        self.assertEqual(document.row_count, 1)
        html = self.env['ir.actions.report']._render_qweb_html(
            'ebshel_stock_reports.action_report_document', document.ids)[0]
        self.assertIn(b'Closing Value', html)

    def test_pdf_row_cap(self):
        self.company.asr_pdf_row_cap = 1
        for product in (self.p_std, self.p_avco):
            self._in(product, 1, 1.0)
        self._process()
        wizard = self._wizard(product_ids=[(6, 0, self.products.ids)])
        action = wizard.action_pdf()
        document = self.env['asr.report.print'].browse(action['context']['active_ids'])
        self.assertTrue(document.truncated)
        self.assertEqual(document.row_count, 1)
