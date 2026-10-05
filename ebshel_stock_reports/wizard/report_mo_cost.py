# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
"""Report #18 - Manufacturing order cost and production analysis.

Per done order, the real cost exactly as ``mrp.production._cal_price`` built it
(DESIGN.md §1.8): components = Σ value of the done raw moves, operations =
Σ ``workorder._cal_cost()``, extra = ``extra_cost`` × quantity produced, less
the by-product cost share; against the bill of materials cost computed like
the MO Overview (``report.mrp.report_mo_overview``): exploded BoM lines at the
component standard price plus the operations' expected cost, scaled to the
quantity produced. Optionally summed per finished product and month.
"""
from collections import defaultdict

from odoo import fields, models
from odoo.tools import SQL

from .report_mixin import col


class ReportMoCost(models.TransientModel):
    _name = 'asr.report.mo.cost'
    _inherit = 'asr.report.mixin'
    _description = 'MO Cost / Production Analysis'
    _asr_title = 'MO Cost / Production Analysis'
    _asr_line_model = 'asr.report.mo.cost.line'
    _asr_uses_engine = False

    group_by = fields.Selection([
        ('mo', 'One row per manufacturing order'),
        ('product_month', 'One row per finished product and month'),
    ], default='mo', required=True)
    line_ids = fields.One2many('asr.report.mo.cost.line', 'wizard_id')

    # ------------------------------------------------------------------
    # Columns
    # ------------------------------------------------------------------
    def _asr_columns(self):
        columns = []
        if self.group_by == 'mo':
            columns += [
                col('production_id', 'Manufacturing Order', 'many2one', width=18),
                col('product_id', 'Finished Product', 'many2one', width=30),
                col('categ_id', 'Category', 'many2one', width=18),
                col('date_finished', 'Finished On', 'date'),
            ]
        else:
            columns += [
                col('product_id', 'Finished Product', 'many2one', width=30),
                col('categ_id', 'Category', 'many2one', width=18),
                col('month', 'Month', 'date'),
                col('mo_count', 'Orders', 'int', total=True),
            ]
        columns += [
            col('uom_id', 'Unit', 'many2one', width=8),
            col('qty_produced', 'Qty Produced', 'qty', total=True),
            col('cost_component', 'Component Cost', 'monetary', value=True, total=True),
            col('cost_operation', 'Operation Cost', 'monetary', value=True, total=True),
            col('cost_extra', 'Extra Cost', 'monetary', value=True, total=True),
            col('byproduct_share', 'By-product Share %', 'percent', value=True),
            col('cost_total', 'Total Cost', 'monetary', value=True, total=True),
            col('cost_finished', 'Finished Product Cost', 'monetary', value=True, total=True,
                help_text="Total cost less the by-product cost share: what the finished moves were valued at."),
            col('cost_unit', 'Unit Cost', 'monetary', value=True),
            col('cost_bom', 'BoM Cost', 'monetary', value=True, total=True,
                help_text="Bill of materials at today's component standard prices plus the expected operation "
                          "cost, scaled to the quantity produced (the MO Overview's BoM cost)."),
            col('cost_bom_unit', 'BoM Unit Cost', 'monetary', value=True),
            col('variance', 'Variance', 'monetary', value=True, total=True),
            col('variance_pct', 'Variance %', 'percent', value=True),
        ]
        return columns

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------
    def _asr_productions(self):
        company = self.company_id.sudo()
        domain = [('company_id', '=', company.id), ('state', '=', 'done')]
        if self.date_from:
            domain.append(('date_finished', '>=', company._asr_day_start_utc(self.date_from)))
        if self.date_to:
            domain.append(('date_finished', '<=', company._asr_day_end_utc(self.date_to)))
        if self.warehouse_ids:
            domain.append(('picking_type_id.warehouse_id', 'in', self.warehouse_ids.ids))
        if self.product_ids:
            domain.append(('product_id', 'in', self.product_ids.ids))
        if self.categ_ids:
            domain.append(('product_id.categ_id', 'child_of', self.categ_ids.ids))
        return self.env['mrp.production'].sudo().search(domain, order='date_finished, id')

    def _move_sums(self, production_ids):
        """({production_id: qty produced}, {production_id: Σ done raw move value},
        {production_id: Σ by-product cost share})."""
        self.env.cr.execute(SQL("""
            SELECT sm.production_id, SUM(sml.quantity_product_uom)
              FROM stock_move sm
              JOIN mrp_production mp ON mp.id = sm.production_id
              JOIN stock_move_line sml ON sml.move_id = sm.id
             WHERE sm.production_id IN %s AND sm.state != 'cancel' AND sm.picked
               AND sm.product_id = mp.product_id
             GROUP BY sm.production_id
        """, tuple(production_ids)))
        produced = {pid: float(qty or 0) for pid, qty in self.env.cr.fetchall()}
        self.env.cr.execute(SQL("""
            SELECT sm.raw_material_production_id, SUM(COALESCE(sm.value, 0))
              FROM stock_move sm
             WHERE sm.raw_material_production_id IN %s AND sm.state = 'done'
             GROUP BY sm.raw_material_production_id
        """, tuple(production_ids)))
        component_value = {pid: float(value or 0) for pid, value in self.env.cr.fetchall()}
        self.env.cr.execute(SQL("""
            SELECT sm.production_id, SUM(COALESCE(sm.cost_share, 0))
              FROM stock_move sm
              JOIN mrp_production mp ON mp.id = sm.production_id
             WHERE sm.production_id IN %s AND sm.state != 'cancel' AND sm.product_id != mp.product_id
             GROUP BY sm.production_id
        """, tuple(production_ids)))
        cost_share = {pid: float(share or 0) for pid, share in self.env.cr.fetchall()}
        return produced, component_value, cost_share

    def _operation_cost(self, productions):
        """{production_id: Σ workorder._cal_cost()} - the figure ``_cal_price`` used."""
        workorders = productions.workorder_ids
        workorders.fetch(['production_id', 'state', 'duration_expected', 'cost_mode', 'costs_hour', 'workcenter_id'])
        workorders.time_ids.fetch(['date_start', 'date_end', 'workorder_id'])
        result = defaultdict(float)
        for workorder in workorders:
            result[workorder.production_id.id] += workorder._cal_cost()
        return result

    def _bom_cost(self, production, bom_cache):
        """BoM cost for the order quantity, like the MO Overview: exploded BoM lines at the
        component standard price plus the expected cost of the BoM operations."""
        bom = production.bom_id
        if not bom:
            return 0.0
        company = production.company_id
        key = (bom.id, production.product_id.id, production.product_qty, production.product_uom_id.id)
        if key in bom_cache:
            return bom_cache[key]
        factor = production.product_uom_id._compute_quantity(
            production.product_qty, bom.product_uom_id, round=False) / (bom.product_qty or 1.0)
        _boms, lines = bom.explode(production.product_id, factor, picking_type=bom.picking_type_id,
                                   never_attribute_values=production.never_product_template_attribute_value_ids)
        cost = 0.0
        for bom_line, line_data in lines:
            if bom_line.child_bom_id and bom_line.child_bom_id.type == 'phantom':
                continue
            component = bom_line.product_id.with_company(company)
            unit_cost = component.uom_id._compute_price(component.standard_price, bom_line.product_uom_id)
            cost += unit_cost * line_data['qty']
        for operation in bom.operation_ids:
            if operation._skip_operation_line(production.product_id):
                continue
            cost += operation.with_context(product=production.product_id, quantity=production.product_qty,
                                           unit=production.product_uom_id).cost
        bom_cache[key] = cost
        return cost

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------
    def _mo_rows(self):
        self.env.flush_all()  # the SQL below reads moves and lines written in this transaction
        company = self.company_id.sudo()
        currency = company.currency_id
        productions = self._asr_productions()
        if not productions:
            return []
        productions.fetch(['name', 'product_id', 'product_qty', 'product_uom_id', 'bom_id', 'date_finished',
                           'extra_cost', 'workorder_ids', 'never_product_template_attribute_value_ids'])
        produced, component_value, cost_share = self._move_sums(productions.ids)
        operation_cost = self._operation_cost(productions)
        bom_cache = {}
        rows = []
        for production in productions:
            product = production.product_id
            qty_produced = produced.get(production.id, 0.0)
            cost_component = component_value.get(production.id, 0.0)
            cost_operation = operation_cost.get(production.id, 0.0)
            cost_extra = (production.extra_cost or 0.0) * qty_produced
            share = cost_share.get(production.id, 0.0)
            cost_total = cost_component + cost_operation + cost_extra
            cost_finished = cost_total * (1 - share / 100.0)
            mo_qty = production.product_uom_id._compute_quantity(production.product_qty, product.uom_id, round=False)
            bom_cost_order = self._bom_cost(production, bom_cache)
            cost_bom = bom_cost_order * (qty_produced / mo_qty) if mo_qty else bom_cost_order
            variance = cost_finished - cost_bom
            rows.append({
                'production_id': production,
                'product_id': product,
                'categ_id': product.categ_id,
                'date_finished': company._asr_local_day(production.date_finished),
                'month': company._asr_local_day(production.date_finished).replace(day=1)
                if production.date_finished else False,
                'mo_count': 1,
                'uom_id': product.uom_id,
                'qty_produced': qty_produced,
                'cost_component': currency.round(cost_component),
                'cost_operation': currency.round(cost_operation),
                'cost_extra': currency.round(cost_extra),
                'byproduct_share': share,
                'cost_total': currency.round(cost_total),
                'cost_finished': currency.round(cost_finished),
                'cost_unit': (cost_finished / qty_produced) if qty_produced else 0.0,
                'cost_bom': currency.round(cost_bom),
                'cost_bom_unit': (cost_bom / qty_produced) if qty_produced else 0.0,
                'variance': currency.round(variance),
                'variance_pct': (variance / cost_bom * 100.0) if cost_bom else 0.0,
            })
        return rows

    def _asr_rows(self):
        self.ensure_one()
        rows = self._mo_rows()
        if self.group_by == 'mo' or not rows:
            return rows
        sums = {}
        for row in rows:
            key = (row['product_id'].id, row['month'])
            if key not in sums:
                sums[key] = dict(row, production_id=False, date_finished=False, mo_count=0, qty_produced=0.0,
                                 cost_component=0.0, cost_operation=0.0, cost_extra=0.0, cost_total=0.0,
                                 cost_finished=0.0, cost_bom=0.0, variance=0.0, byproduct_share=0.0)
            total = sums[key]
            total['mo_count'] += 1
            for field in ('qty_produced', 'cost_component', 'cost_operation', 'cost_extra', 'cost_total',
                          'cost_finished', 'cost_bom', 'variance'):
                total[field] += row[field]
        result = []
        for key in sorted(sums, key=lambda k: (k[1] or fields.Date.today(), sums[k]['product_id'].display_name, k[0])):
            total = sums[key]
            qty = total['qty_produced']
            total['cost_unit'] = (total['cost_finished'] / qty) if qty else 0.0
            total['cost_bom_unit'] = (total['cost_bom'] / qty) if qty else 0.0
            total['byproduct_share'] = (100.0 - total['cost_finished'] / total['cost_total'] * 100.0) \
                if total['cost_total'] else 0.0
            total['variance_pct'] = (total['variance'] / total['cost_bom'] * 100.0) if total['cost_bom'] else 0.0
            result.append(total)
        return result


class ReportMoCostLine(models.TransientModel):
    _name = 'asr.report.mo.cost.line'
    _description = 'MO Cost / Production Analysis Line'
    _order = 'month, date_finished, production_id, id'

    wizard_id = fields.Many2one('asr.report.mo.cost', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='wizard_id.currency_id')
    production_id = fields.Many2one('mrp.production', 'Manufacturing Order', readonly=True)
    product_id = fields.Many2one('product.product', 'Finished Product', readonly=True)
    categ_id = fields.Many2one('product.category', 'Category', readonly=True)
    date_finished = fields.Date('Finished On', readonly=True)
    month = fields.Date(readonly=True)
    mo_count = fields.Integer('Orders', readonly=True)
    uom_id = fields.Many2one('uom.uom', 'Unit', readonly=True)
    qty_produced = fields.Float(digits='Product Unit of Measure', readonly=True)
    cost_component = fields.Monetary('Component Cost', currency_field='currency_id', readonly=True,
                                     groups='ebshel_stock_reports.group_see_values')
    cost_operation = fields.Monetary('Operation Cost', currency_field='currency_id', readonly=True,
                                     groups='ebshel_stock_reports.group_see_values')
    cost_extra = fields.Monetary('Extra Cost', currency_field='currency_id', readonly=True,
                                 groups='ebshel_stock_reports.group_see_values')
    byproduct_share = fields.Float('By-product Share %', digits=(16, 2), readonly=True, aggregator='avg',
                                   groups='ebshel_stock_reports.group_see_values')
    cost_total = fields.Monetary('Total Cost', currency_field='currency_id', readonly=True,
                                 groups='ebshel_stock_reports.group_see_values')
    cost_finished = fields.Monetary('Finished Product Cost', currency_field='currency_id', readonly=True,
                                    groups='ebshel_stock_reports.group_see_values')
    cost_unit = fields.Monetary('Unit Cost', currency_field='currency_id', readonly=True, aggregator='avg',
                                groups='ebshel_stock_reports.group_see_values')
    cost_bom = fields.Monetary('BoM Cost', currency_field='currency_id', readonly=True,
                               groups='ebshel_stock_reports.group_see_values')
    cost_bom_unit = fields.Monetary('BoM Unit Cost', currency_field='currency_id', readonly=True, aggregator='avg',
                                    groups='ebshel_stock_reports.group_see_values')
    variance = fields.Monetary(currency_field='currency_id', readonly=True,
                               groups='ebshel_stock_reports.group_see_values')
    variance_pct = fields.Float('Variance %', digits=(16, 2), readonly=True, aggregator='avg',
                                groups='ebshel_stock_reports.group_see_values')
