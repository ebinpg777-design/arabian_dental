# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import AdvancedStockReportsCase


@tagged('post_install', '-at_install', 'asr')
class TestSecurity(AdvancedStockReportsCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_qty = cls.env['res.users'].create({
            'name': 'ASR Quantity User', 'login': 'asr_qty',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('ebshel_stock_reports.group_user').id])],
        })
        cls.user_values = cls.env['res.users'].create({
            'name': 'ASR Values User', 'login': 'asr_values',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('ebshel_stock_reports.group_see_values').id])],
        })
        cls.user_none = cls.env['res.users'].create({
            'name': 'ASR Nobody', 'login': 'asr_none',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })

    def setUp(self):
        super().setUp()
        self._in(self.p_fifo, 5, 10.0)
        self._process()

    def _ledger(self, user):
        return self.env['asr.report.stock.ledger'].with_user(user).create({
            'company_id': self.company.id, 'date_from': fields.Date.today(), 'date_to': fields.Date.today(),
            'product_ids': [(6, 0, self.p_fifo.ids)],
        })

    def test_value_columns_hidden_without_group(self):
        ledger = self._ledger(self.user_qty)
        keys = {c['key'] for c in ledger._asr_visible_columns()}
        self.assertNotIn('value_closing', keys)
        self.assertIn('qty_closing', keys)
        ledger_values = self._ledger(self.user_values)
        keys = {c['key'] for c in ledger_values._asr_visible_columns()}
        self.assertIn('value_closing', keys)

    def test_xlsx_and_pdf_without_values(self):
        ledger = self._ledger(self.user_qty)
        content = ledger._asr_xlsx()
        self.assertTrue(content.startswith(b'PK'))
        action = ledger.action_pdf()
        document = self.env['asr.report.print'].browse(action['context']['active_ids'])
        self.assertNotIn('value_closing', document.columns_json)

    def test_line_value_fields_inaccessible(self):
        ledger = self._ledger(self.user_qty)
        ledger.action_view()
        Line = self.env['asr.report.stock.ledger.line'].with_user(self.user_qty)
        line = Line.search([('wizard_id', '=', ledger.id)], limit=1)
        self.assertTrue(line)
        with self.assertRaises(AccessError):
            line.read(['value_closing'])
        line.read(['qty_closing'])

    def test_engine_tables_need_group(self):
        with self.assertRaises(AccessError):
            self.env['asr.stock.move.daily'].with_user(self.user_none).search([]).read(['qty_in'])
        self.env['asr.stock.move.daily'].with_user(self.user_qty).search([], limit=1).read(['qty_in'])
        with self.assertRaises(AccessError):
            self.env['asr.stock.value.daily'].with_user(self.user_qty).search([], limit=1).read(['closing_value'])

    def test_multi_company_rule(self):
        other = self.env['res.company'].create({'name': 'ASR Other Co'})
        self.env.cr.execute(
            "INSERT INTO asr_stock_value_daily (company_id, product_id, day, closing_qty, closing_value, avg_cost, "
            "revaluation_value, cost_method, replay_qty, replay_avg, exact_slow_path, computed_at) "
            "VALUES (%s, %s, %s, 1, 1, 1, 0, 'fifo', 0, 0, false, now())",
            (other.id, self.p_fifo.id, fields.Date.today()))
        ValueDaily = self.env['asr.stock.value.daily'].with_user(self.user_values)
        rows = ValueDaily.search([('product_id', '=', self.p_fifo.id)])
        self.assertEqual(set(rows.mapped('company_id').ids), {self.company.id})
