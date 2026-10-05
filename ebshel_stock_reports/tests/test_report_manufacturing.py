# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
"""Reports #2 (planned vs actual consumption), #3 (WIP), #11 (MO component shortage)
and #18 (MO cost / production analysis)."""
import io
import zipfile
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import Form, tagged

from .common import AdvancedStockReportsCase


@tagged('post_install', '-at_install', 'asr')
class TestManufacturingReports(AdvancedStockReportsCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.write({'group_ids': [Command.link(cls.env.ref('mrp.group_mrp_routings').id)]})
        cls.finished = cls.env['product.product'].create({
            'name': 'ASR Finished', 'is_storable': True, 'type': 'consu', 'categ_id': cls.categ_avco.id,
            'uom_id': cls.uom_unit.id,
        })
        cls.finished2 = cls.env['product.product'].create({
            'name': 'ASR Finished 2', 'is_storable': True, 'type': 'consu', 'categ_id': cls.categ_std.id,
            'uom_id': cls.uom_unit.id,
        })
        cls.workcenter = cls.env['mrp.workcenter'].create({
            'name': 'ASR Assembly', 'costs_hour': 60.0, 'time_efficiency': 100.0, 'time_start': 0.0, 'time_stop': 0.0,
        })
        cls.bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.finished.product_tmpl_id.id, 'product_qty': 1.0, 'type': 'normal',
            'consumption': 'flexible',
            'bom_line_ids': [Command.create({'product_id': cls.p_std.id, 'product_qty': 2.0})],
            'operation_ids': [Command.create({
                'name': 'Assemble', 'workcenter_id': cls.workcenter.id, 'time_mode': 'manual',
                'time_cycle_manual': 60.0, 'cost_mode': 'actual',
            })],
        })
        cls.bom2 = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.finished2.product_tmpl_id.id, 'product_qty': 1.0, 'type': 'normal',
            'consumption': 'flexible',
            'bom_line_ids': [Command.create({'product_id': cls.p_avco.id, 'product_qty': 3.0})],
        })

    def setUp(self):
        super().setUp()
        self._in(self.p_std, 100, 10.0, date=self._days_ago(3))
        self._process()

    # ------------------------------------------------------------------
    # Scenario helpers
    # ------------------------------------------------------------------
    def _create_mo(self, bom, qty, confirm=True):
        mo = self.env['mrp.production'].create({
            'product_id': bom.product_id.id or bom.product_tmpl_id.product_variant_id.id,
            'bom_id': bom.id, 'product_qty': qty, 'company_id': self.company.id,
        })
        if confirm:
            mo.action_confirm()
        return mo

    def _produce_with_backorder(self):
        """A 10-unit MO of which 4 are produced with 30 minutes of work; the rest goes to a backorder."""
        mo = self._create_mo(self.bom, 10)
        self.assertEqual(mo.state, 'confirmed')
        self.assertAlmostEqual(mo.move_raw_ids.asr_bom_planned_qty_unit, 2.0)
        self.assertFalse(mo.move_raw_ids.asr_planned_estimated)
        with Form(mo) as mo_form:
            mo_form.qty_producing = 4.0
        mo.workorder_ids.duration = 30.0
        action = mo.button_mark_done()
        self.assertEqual(action.get('res_model'), 'mrp.production.backorder')
        wizard = Form(self.env['mrp.production.backorder'].with_context(**action['context'])).save()
        wizard.action_backorder()
        self.assertEqual(mo.state, 'done')
        self.assertAlmostEqual(mo.qty_produced, 4.0)
        backorder = self.env['mrp.production'].search([
            ('product_id', '=', self.finished.id), ('state', '!=', 'done'), ('id', '!=', mo.id)])
        self.assertEqual(len(backorder), 1)
        self.assertAlmostEqual(backorder.product_qty, 6.0)
        # the backorder moves are copies and inherit the frozen plan per unit
        self.assertAlmostEqual(backorder.move_raw_ids.asr_bom_planned_qty_unit, 2.0)
        self._process()
        return mo, backorder

    def _check_outputs(self, wizard, line_model, expected_lines, header):
        action = wizard.action_view()
        lines = self.env[line_model].search(action['domain'])
        self.assertEqual(len(lines), expected_lines)
        all_rows = wizard._asr_rows()  # the PDF keeps the group headers
        content = wizard._asr_xlsx()
        self.assertTrue(content.startswith(b'PK'))
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            self.assertIn('xl/worksheets/sheet1.xml', archive.namelist())
        pdf_action = wizard.action_pdf()
        document = self.env['asr.report.print'].browse(pdf_action['context']['active_ids'])
        self.assertEqual(document.row_count, len(all_rows))
        self.assertEqual(len([r for r in all_rows if not r.get('_group')]), expected_lines)
        html = self.env['ir.actions.report']._render_qweb_html(
            'ebshel_stock_reports.action_report_document', document.ids)[0]
        self.assertIn(header.encode(), html)
        return lines

    # ------------------------------------------------------------------
    # Report #2
    # ------------------------------------------------------------------
    def test_consumption(self):
        mo, _backorder = self._produce_with_backorder()
        today = fields.Date.today()
        wizard = self.env['asr.report.consumption'].create({
            'company_id': self.company.id, 'date_from': today, 'date_to': today,
            'product_ids': [Command.set(self.finished.ids)],
        })
        rows = wizard._asr_rows()
        self.assertEqual(len(rows), 2)  # one group header, one component row
        self.assertEqual(rows[0]['_group'], f"{mo.name} - {self.finished.display_name}")
        row = rows[1]
        self.assertEqual(row['production_id'], mo)
        self.assertEqual(row['component_id'], self.p_std)
        self.assertAlmostEqual(row['qty_produced'], 4.0)
        self.assertAlmostEqual(row['qty_planned'], 8.0)
        self.assertFalse(row['estimated'])
        self.assertAlmostEqual(row['qty_actual'], 8.0)
        self.assertAlmostEqual(row['qty_scrap'], 0.0)
        self.assertAlmostEqual(row['qty_variance'], 0.0)
        self.assertAlmostEqual(row['value_actual'], 80.0, places=2)
        self.assertAlmostEqual(row['value_planned'], 80.0, places=2)
        self.assertAlmostEqual(row['value_variance'], 0.0, places=2)
        # the open backorder is not a done order: not listed
        self.assertNotIn(_backorder, [r.get('production_id') for r in rows if not r.get('_group')])
        # explicit order filter
        wizard_mo = self.env['asr.report.consumption'].create({
            'company_id': self.company.id, 'date_from': False, 'date_to': False,
            'production_ids': [Command.set(mo.ids)],
        })
        self.assertEqual(len([r for r in wizard_mo._asr_rows() if not r.get('_group')]), 1)
        lines = self._check_outputs(wizard, 'asr.report.consumption.line', 1, 'Planned Qty')
        self.assertAlmostEqual(lines.qty_planned, 8.0)
        self.assertAlmostEqual(lines.value_actual, 80.0, places=2)

    def test_consumption_estimated_fallback(self):
        """Raw moves without a frozen plan (orders confirmed before the install) fall back to the demand."""
        mo, _backorder = self._produce_with_backorder()
        mo.move_raw_ids.write({'asr_bom_planned_qty_unit': 0.0, 'asr_planned_estimated': False})
        today = fields.Date.today()
        wizard = self.env['asr.report.consumption'].create({
            'company_id': self.company.id, 'date_from': today, 'date_to': today,
            'production_ids': [Command.set(mo.ids)],
        })
        row = [r for r in wizard._asr_rows() if not r.get('_group')][0]
        self.assertTrue(row['estimated'])
        self.assertAlmostEqual(row['qty_planned'], 8.0)  # demand scaled to the 4 produced by the split

    # ------------------------------------------------------------------
    # Report #18
    # ------------------------------------------------------------------
    def test_mo_cost(self):
        mo, _backorder = self._produce_with_backorder()
        today = fields.Date.today()
        wizard = self.env['asr.report.mo.cost'].create({
            'company_id': self.company.id, 'date_from': today, 'date_to': today,
            'product_ids': [Command.set(self.finished.ids)],
        })
        rows = wizard._asr_rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['production_id'], mo)
        self.assertAlmostEqual(row['qty_produced'], 4.0)
        self.assertAlmostEqual(row['cost_component'], 80.0, places=2)   # 8 x 10
        self.assertAlmostEqual(row['cost_operation'], 30.0, places=2)   # 30 min at 60/h
        self.assertAlmostEqual(row['cost_extra'], 0.0, places=2)
        self.assertAlmostEqual(row['byproduct_share'], 0.0)
        self.assertAlmostEqual(row['cost_total'], 110.0, places=2)
        self.assertAlmostEqual(row['cost_finished'], 110.0, places=2)
        self.assertAlmostEqual(row['cost_unit'], 27.5, places=2)
        # what mrp_account put on the finished move
        finished_move = mo.move_finished_ids.filtered(lambda m: m.state == 'done')
        self.assertAlmostEqual(finished_move.value, 110.0, places=2)
        # BoM cost for the 4 produced: 2 x 10 x 4 = 80 components + 4 cycles x 60 min at 60/h = 240
        self.assertAlmostEqual(row['cost_bom'], 320.0, places=2)
        self.assertAlmostEqual(row['cost_bom_unit'], 80.0, places=2)
        self.assertAlmostEqual(row['variance'], -210.0, places=2)
        self.assertAlmostEqual(row['variance_pct'], -210.0 / 320.0 * 100.0, places=2)
        lines = self._check_outputs(wizard, 'asr.report.mo.cost.line', 1, 'Component Cost')
        self.assertAlmostEqual(lines.cost_finished, 110.0, places=2)
        # per product and month
        wizard.group_by = 'product_month'
        rows = wizard._asr_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['mo_count'], 1)
        self.assertFalse(rows[0]['production_id'])
        self.assertEqual(rows[0]['month'], today.replace(day=1))
        self.assertAlmostEqual(rows[0]['cost_unit'], 27.5, places=2)
        self.assertAlmostEqual(rows[0]['cost_finished'], 110.0, places=2)
        action = wizard.action_view()
        self.assertIn('graph', action['view_mode'])
        self.assertIn('pivot', action['view_mode'])

    def test_mo_cost_extra_cost(self):
        mo = self._create_mo(self.bom, 2)
        mo.extra_cost = 5.0
        with Form(mo) as mo_form:
            mo_form.qty_producing = 2.0
        mo.workorder_ids.duration = 60.0
        mo.button_mark_done()
        self.assertEqual(mo.state, 'done')
        today = fields.Date.today()
        wizard = self.env['asr.report.mo.cost'].create({
            'company_id': self.company.id, 'date_from': today, 'date_to': today,
            'product_ids': [Command.set(self.finished.ids)],
        })
        row = wizard._asr_rows()[0]
        self.assertAlmostEqual(row['cost_component'], 40.0, places=2)
        self.assertAlmostEqual(row['cost_operation'], 60.0, places=2)
        self.assertAlmostEqual(row['cost_extra'], 10.0, places=2)
        self.assertAlmostEqual(row['cost_finished'], 110.0, places=2)
        self.assertAlmostEqual(row['cost_unit'], 55.0, places=2)

    # ------------------------------------------------------------------
    # Report #3
    # ------------------------------------------------------------------
    def test_wip(self):
        _mo, backorder = self._produce_with_backorder()
        with Form(backorder) as bo_form:
            bo_form.qty_producing = 2.0
        self.assertTrue(backorder.move_raw_ids.picked)
        self.assertAlmostEqual(backorder.move_raw_ids.quantity, 4.0)
        backorder.workorder_ids.duration = 15.0
        today = self.company._asr_local_day(fields.Datetime.now())
        wizard = self.env['asr.report.wip'].create({
            'company_id': self.company.id, 'date': today, 'pricing': 'standard',
            'product_ids': [Command.set(self.finished.ids)],
        })
        self.assertTrue(wizard.is_live)
        self.assertFalse(wizard.snapshot_warning)
        rows = [r for r in wizard._asr_rows() if not r.get('_group')]
        self.assertEqual(len(rows), 2)
        component, overhead = rows
        self.assertEqual(component['production_id'], backorder)
        self.assertEqual(component['component_id'], self.p_std)
        self.assertAlmostEqual(component['quantity'], 4.0)
        self.assertAlmostEqual(component['value'], 40.0, places=2)
        self.assertEqual(overhead['workcenter_id'], self.workcenter)
        self.assertAlmostEqual(overhead['value'], 15.0, places=2)
        self.assertEqual(overhead['line_type'], 'Overhead')
        lines = self._check_outputs(wizard, 'asr.report.wip.line', 2, 'Work Center')
        self.assertAlmostEqual(sum(lines.mapped('value')), 55.0, places=2)
        # average cost pricing (engine): the component was received at 10 as well
        wizard.pricing = 'avg_cost'
        rows = [r for r in wizard._asr_rows() if not r.get('_group')]
        self.assertAlmostEqual(rows[0]['value'], 40.0, places=2)

    def test_wip_past_date_needs_snapshot(self):
        _mo, backorder = self._produce_with_backorder()
        with Form(backorder) as bo_form:
            bo_form.qty_producing = 1.0
        today = self.company._asr_local_day(fields.Datetime.now())
        yesterday = today - timedelta(days=1)
        # pretend the components were issued yesterday
        backorder.move_raw_ids.move_line_ids.write({'date': fields.Datetime.now() - timedelta(days=1)})
        wizard = self.env['asr.report.wip'].create({
            'company_id': self.company.id, 'date': yesterday, 'product_ids': [Command.set(self.finished.ids)],
        })
        self.assertFalse(wizard.is_live)
        self.assertFalse(wizard.snapshot_id)
        self.assertIn('snapshot', wizard.snapshot_warning)
        with self.assertRaises(UserError):
            wizard.action_view()
        # a snapshot taken for yesterday serves it
        snapshot = self.env['asr.wip.snapshot']._take(self.company, yesterday)
        wizard.invalidate_recordset()
        self.assertEqual(wizard.snapshot_id, snapshot)
        self.assertFalse(wizard.snapshot_warning)
        rows = [r for r in wizard._asr_rows() if not r.get('_group')]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['production_id'], backorder)
        self.assertAlmostEqual(rows[0]['quantity'], 2.0)
        self.assertAlmostEqual(rows[0]['value'], 20.0, places=2)
        lines = self._check_outputs(wizard, 'asr.report.wip.line', 1, 'Components Issued')
        self.assertAlmostEqual(lines.quantity, 2.0)
        # a day without a snapshot falls back to the closest earlier one and says so
        older = self.env['asr.wip.snapshot']._take(self.company, yesterday - timedelta(days=3))
        between = self.env['asr.report.wip'].create({
            'company_id': self.company.id, 'date': yesterday - timedelta(days=2),
        })
        self.assertEqual(between.snapshot_id, older)
        self.assertIn('closest earlier snapshot', between.snapshot_warning)
        nothing = self.env['asr.report.wip'].create({
            'company_id': self.company.id, 'date': yesterday - timedelta(days=4),
        })
        self.assertFalse(nothing.snapshot_id)
        # the button stores today's WIP and opens it
        action = wizard.action_take_snapshot()
        new_snapshot = self.env['asr.wip.snapshot'].browse(action['res_id'])
        self.assertEqual(new_snapshot.date, today)
        self.assertEqual(new_snapshot.kind, 'manual')
        self.assertEqual(action['res_model'], 'asr.wip.snapshot')

    # ------------------------------------------------------------------
    # Report #11
    # ------------------------------------------------------------------
    def test_mo_shortage(self):
        mo_short = self._create_mo(self.bom2, 5)  # 15 x p_avco, nothing in stock
        self.assertIn(mo_short.state, ('confirmed', 'progress'))
        wizard = self.env['asr.report.mo.shortage'].create({
            'company_id': self.company.id, 'product_ids': [Command.set(self.finished2.ids)],
        })
        rows = [r for r in wizard._asr_rows() if not r.get('_group')]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['production_id'], mo_short)
        self.assertEqual(row['component_id'], self.p_avco)
        self.assertEqual(row['warehouse_id'], self.warehouse)
        self.assertAlmostEqual(row['qty_required'], 15.0)
        self.assertAlmostEqual(row['qty_reserved'], 0.0)
        self.assertAlmostEqual(row['qty_to_consume'], 15.0)
        self.assertAlmostEqual(row['qty_on_hand'], 0.0)
        self.assertAlmostEqual(row['qty_free'], 0.0)
        self.assertAlmostEqual(row['qty_shortage'], 15.0)
        lines = self._check_outputs(wizard, 'asr.report.mo.shortage.line', 1, 'Shortage')
        self.assertAlmostEqual(lines.qty_shortage, 15.0)
        # beyond the horizon: not listed
        wizard.horizon_date = fields.Date.today() - timedelta(days=2)
        self.assertEqual(wizard._asr_rows(), [])

    def test_mo_shortage_cumulative(self):
        """Two orders share 20 units of a component: the first takes 15, the second (6) is short by 1."""
        self._in(self.p_avco, 20, 5.0)
        mo_a = self._create_mo(self.bom2, 5)
        mo_b = self._create_mo(self.bom2, 2)
        mo_b.date_start = mo_a.date_start + timedelta(hours=1)
        # the first order is unreserved: its 15 come out of the free quantity before the second order
        mo_a.do_unreserve()
        self.assertAlmostEqual(mo_a.move_raw_ids.quantity, 0.0)
        wizard = self.env['asr.report.mo.shortage'].create({
            'company_id': self.company.id, 'product_ids': [Command.set(self.finished2.ids)],
        })
        rows = [r for r in wizard._asr_rows() if not r.get('_group')]
        self.assertEqual([r['production_id'] for r in rows], [mo_b])
        self.assertAlmostEqual(rows[0]['qty_required'], 6.0)
        self.assertAlmostEqual(rows[0]['qty_shortage'], 1.0)
        self.assertAlmostEqual(rows[0]['qty_on_hand'], 20.0)
        wizard.show_all = True
        rows = [r for r in wizard._asr_rows() if not r.get('_group')]
        by_mo = {r['production_id']: r for r in rows}
        self.assertEqual(set(by_mo), {mo_a, mo_b})
        self.assertAlmostEqual(by_mo[mo_a]['qty_shortage'], 0.0)
        self.assertAlmostEqual(by_mo[mo_a]['qty_required'], 15.0)
        self.assertAlmostEqual(by_mo[mo_a]['qty_reserved'], 0.0)
        self.assertAlmostEqual(by_mo[mo_b]['qty_shortage'], 1.0)
        self.assertAlmostEqual(by_mo[mo_b]['qty_required'], 6.0)
