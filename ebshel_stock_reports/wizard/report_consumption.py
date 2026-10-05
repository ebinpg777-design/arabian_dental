# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
"""Report #2 - Planned vs actual consumption.

One row per done manufacturing order and component. The plan is the bill of
materials quantity per unit of finished product frozen on the raw move at
confirmation (``stock.move.asr_bom_planned_qty_unit``, DESIGN.md §2.3) times
the quantity produced; the actual is what the done raw move lines consumed;
scraps linked to the order are shown beside it. Values are the done raw moves'
values; the plan is valued at the same unit value (or the standard price when
nothing was consumed).
"""
from collections import defaultdict

from odoo import fields, models
from odoo.tools import SQL

from .report_mixin import col


class ReportConsumption(models.TransientModel):
    _name = 'asr.report.consumption'
    _inherit = 'asr.report.mixin'
    _description = 'Planned vs Actual Consumption'
    _asr_title = 'Planned vs Actual Consumption'
    _asr_line_model = 'asr.report.consumption.line'
    _asr_uses_engine = False

    production_ids = fields.Many2many(
        'mrp.production', string='Manufacturing Orders', check_company=True,
        domain="[('state', '=', 'done'), ('company_id', '=', company_id)]",
        help="Leave empty for every order finished in the period.")
    line_ids = fields.One2many('asr.report.consumption.line', 'wizard_id')

    # ------------------------------------------------------------------
    # Columns
    # ------------------------------------------------------------------
    def _asr_columns(self):
        return [
            col('production_id', 'Manufacturing Order', 'many2one', width=18),
            col('product_id', 'Finished Product', 'many2one', width=30),
            col('date_finished', 'Finished On', 'date'),
            col('component_id', 'Component', 'many2one', width=30),
            col('categ_id', 'Component Category', 'many2one', width=18),
            col('uom_id', 'Unit', 'many2one', width=8),
            col('qty_produced', 'Qty Produced', 'qty'),
            col('qty_planned', 'Planned Qty', 'qty', total=True),
            col('estimated', 'Estimated', 'bool', width=9,
                help_text="The plan was backfilled from the move demand, not frozen at confirmation."),
            col('qty_actual', 'Actual Qty', 'qty', total=True),
            col('qty_scrap', 'Scrapped Qty', 'qty', total=True),
            col('qty_variance', 'Variance Qty', 'qty', total=True),
            col('variance_pct', 'Variance %', 'percent'),
            col('value_planned', 'Planned Value', 'monetary', value=True, total=True),
            col('value_actual', 'Actual Value', 'monetary', value=True, total=True),
            col('value_variance', 'Variance Value', 'monetary', value=True, total=True),
        ]

    def _asr_filter_text(self):
        text = super()._asr_filter_text()
        if self.production_ids:
            mo_text = self.env._("Orders: %s", ', '.join(self.production_ids[:5].mapped('name'))
                                 + (' …' if len(self.production_ids) > 5 else ''))
            text = f"{text} | {mo_text}" if text else mo_text
        return text

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------
    def _asr_productions(self):
        """Done orders of the period, filtered on the finished product."""
        company = self.company_id.sudo()
        domain = [('company_id', '=', company.id), ('state', '=', 'done')]
        if self.production_ids:
            domain.append(('id', 'in', self.production_ids.ids))
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

    def _produced_qty(self, production_ids):
        """{production_id: quantity produced in the product UoM}: picked, non-cancelled finished
        moves of the order's product (mrp's ``qty_produced``, which is not stored)."""
        if not production_ids:
            return {}
        self.env.cr.execute(SQL("""
            SELECT sm.production_id, SUM(sml.quantity_product_uom)
              FROM stock_move sm
              JOIN mrp_production mp ON mp.id = sm.production_id
              JOIN stock_move_line sml ON sml.move_id = sm.id
             WHERE sm.production_id IN %s AND sm.state != 'cancel' AND sm.picked
               AND sm.product_id = mp.product_id
             GROUP BY sm.production_id
        """, tuple(production_ids)))
        return {pid: float(qty or 0) for pid, qty in self.env.cr.fetchall()}

    def _raw_move_data(self, production_ids):
        """{(production_id, component_id): dict} with the planned unit sum, the fallback demand,
        the estimated flag and the done value of the raw moves."""
        self.env.cr.execute(SQL("""
            SELECT sm.raw_material_production_id, sm.product_id,
                   SUM(sm.asr_bom_planned_qty_unit),
                   SUM(CASE WHEN COALESCE(sm.asr_bom_planned_qty_unit, 0) = 0 THEN sm.product_qty ELSE 0 END),
                   BOOL_OR(COALESCE(sm.asr_planned_estimated, FALSE) OR COALESCE(sm.asr_bom_planned_qty_unit, 0) = 0),
                   SUM(CASE WHEN sm.state = 'done' THEN COALESCE(sm.value, 0) ELSE 0 END)
              FROM stock_move sm
             WHERE sm.raw_material_production_id IN %s
             GROUP BY sm.raw_material_production_id, sm.product_id
        """, tuple(production_ids)))
        return {
            (pid, cid): {'unit': float(unit or 0), 'fallback': float(fallback or 0),
                         'estimated': bool(estimated), 'value': float(value or 0)}
            for pid, cid, unit, fallback, estimated, value in self.env.cr.fetchall()
        }

    def _actual_qty(self, production_ids):
        """{(production_id, component_id): consumed quantity} from done raw move lines, product UoM."""
        self.env.cr.execute(SQL("""
            SELECT sm.raw_material_production_id, sm.product_id, SUM(sml.quantity_product_uom)
              FROM stock_move_line sml
              JOIN stock_move sm ON sm.id = sml.move_id
             WHERE sm.raw_material_production_id IN %s AND sm.state = 'done'
             GROUP BY sm.raw_material_production_id, sm.product_id
        """, tuple(production_ids)))
        return {(pid, cid): float(qty or 0) for pid, cid, qty in self.env.cr.fetchall()}

    def _scrap_qty(self, production_ids):
        """{(production_id, component_id): scrapped quantity} from done scraps linked to the order."""
        self.env.cr.execute(SQL("""
            SELECT ss.production_id, sm.product_id, SUM(sml.quantity_product_uom)
              FROM stock_scrap ss
              JOIN stock_move sm ON sm.scrap_id = ss.id
              JOIN stock_move_line sml ON sml.move_id = sm.id
             WHERE ss.production_id IN %s AND ss.state = 'done' AND sm.state = 'done'
             GROUP BY ss.production_id, sm.product_id
        """, tuple(production_ids)))
        return {(pid, cid): float(qty or 0) for pid, cid, qty in self.env.cr.fetchall()}

    def _asr_rows(self):
        self.ensure_one()
        self.env.flush_all()  # the SQL below reads moves, lines and scraps written in this transaction
        company = self.company_id.sudo()
        productions = self._asr_productions()
        if not productions:
            return []
        production_ids = productions.ids
        produced = self._produced_qty(production_ids)
        raw = self._raw_move_data(production_ids)
        actual = self._actual_qty(production_ids)
        scrap = self._scrap_qty(production_ids)
        keys = set(raw) | set(actual) | set(scrap)
        component_recs = self.env['product.product'].with_context(active_test=False).browse(
            sorted({cid for _pid, cid in keys}))
        component_recs.fetch(['display_name', 'categ_id', 'uom_id', 'default_code'])
        components = {p.id: p for p in component_recs}
        by_production = defaultdict(list)
        for pid, cid in keys:
            by_production[pid].append(cid)
        productions.fetch(['name', 'product_id', 'date_finished', 'display_name'])
        rows = []
        for production in productions:
            component_ids = by_production.get(production.id)
            if not component_ids:
                continue
            qty_produced = produced.get(production.id, 0.0)
            rows.append({'_group': f"{production.name} - {production.product_id.display_name}"})
            ordered = sorted(component_ids, key=lambda cid: (components[cid].default_code or '',
                                                             components[cid].display_name, cid))
            for cid in ordered:
                component = components[cid]
                data = raw.get((production.id, cid), {'unit': 0.0, 'fallback': 0.0, 'estimated': False, 'value': 0.0})
                uom = component.uom_id
                qty_planned = uom.round(data['unit'] * qty_produced + data['fallback'])
                qty_actual = uom.round(actual.get((production.id, cid), 0.0))
                qty_scrap = uom.round(scrap.get((production.id, cid), 0.0))
                value_actual = data['value']
                if qty_actual:
                    unit_value = value_actual / qty_actual
                else:
                    unit_value = component.with_company(company).standard_price
                value_planned = qty_planned * unit_value
                qty_variance = qty_actual - qty_planned
                rows.append({
                    'production_id': production,
                    'product_id': production.product_id,
                    'date_finished': company._asr_local_day(production.date_finished),
                    'component_id': component,
                    'categ_id': component.categ_id,
                    'uom_id': uom,
                    'qty_produced': qty_produced,
                    'qty_planned': qty_planned,
                    'estimated': data['estimated'],
                    'qty_actual': qty_actual,
                    'qty_scrap': qty_scrap,
                    'qty_variance': qty_variance,
                    'variance_pct': (qty_variance / qty_planned * 100.0) if qty_planned else 0.0,
                    'value_planned': value_planned,
                    'value_actual': value_actual,
                    'value_variance': value_actual - value_planned,
                })
        return rows


class ReportConsumptionLine(models.TransientModel):
    _name = 'asr.report.consumption.line'
    _description = 'Planned vs Actual Consumption Line'
    _order = 'date_finished, production_id, id'

    wizard_id = fields.Many2one('asr.report.consumption', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='wizard_id.currency_id')
    production_id = fields.Many2one('mrp.production', 'Manufacturing Order', readonly=True)
    product_id = fields.Many2one('product.product', 'Finished Product', readonly=True)
    date_finished = fields.Date('Finished On', readonly=True)
    component_id = fields.Many2one('product.product', readonly=True)
    categ_id = fields.Many2one('product.category', 'Component Category', readonly=True)
    uom_id = fields.Many2one('uom.uom', 'Unit', readonly=True)
    qty_produced = fields.Float(digits='Product Unit of Measure', readonly=True, aggregator=None)
    qty_planned = fields.Float('Planned Qty', digits='Product Unit of Measure', readonly=True)
    estimated = fields.Boolean(readonly=True)
    qty_actual = fields.Float('Actual Qty', digits='Product Unit of Measure', readonly=True)
    qty_scrap = fields.Float('Scrapped Qty', digits='Product Unit of Measure', readonly=True)
    qty_variance = fields.Float('Variance Qty', digits='Product Unit of Measure', readonly=True)
    variance_pct = fields.Float('Variance %', digits=(16, 2), readonly=True, aggregator='avg')
    value_planned = fields.Monetary('Planned Value', currency_field='currency_id', readonly=True,
                                    groups='ebshel_stock_reports.group_see_values')
    value_actual = fields.Monetary('Actual Value', currency_field='currency_id', readonly=True,
                                   groups='ebshel_stock_reports.group_see_values')
    value_variance = fields.Monetary('Variance Value', currency_field='currency_id', readonly=True,
                                     groups='ebshel_stock_reports.group_see_values')
