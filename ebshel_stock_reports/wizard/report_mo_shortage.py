# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
"""Report #11 - Component shortage of open manufacturing orders.

Open orders (confirmed, in progress, to close) starting up to a horizon, in
start-date order. For every raw move that still has a quantity to consume, the
component's on-hand, free and incoming quantities in the order's warehouse
(``_compute_quantities_dict`` with ``warehouse_id`` in context, one batch per
warehouse) are compared with the unreserved demand. The free quantity is
consumed cumulatively, so a later order only sees what earlier orders leave.
"""
from collections import defaultdict
from datetime import timedelta

from odoo import fields, models

from .report_mixin import col

OPEN_MO_STATES = ('confirmed', 'progress', 'to_close')


class ReportMoShortage(models.TransientModel):
    _name = 'asr.report.mo.shortage'
    _inherit = 'asr.report.mixin'
    _description = 'MO Component Shortage'
    _asr_title = 'MO Component Shortage'
    _asr_line_model = 'asr.report.mo.shortage.line'
    _asr_uses_engine = False

    horizon_date = fields.Date(
        'Horizon', required=True, default=lambda self: fields.Date.context_today(self) + timedelta(days=30),
        help="Orders scheduled to start up to this day are considered.")
    show_all = fields.Boolean(
        'Show All Components', default=False,
        help="Also list components that are fully covered.")
    line_ids = fields.One2many('asr.report.mo.shortage.line', 'wizard_id')

    # ------------------------------------------------------------------
    # Columns
    # ------------------------------------------------------------------
    def _asr_columns(self):
        return [
            col('production_id', 'Manufacturing Order', 'many2one', width=18),
            col('product_id', 'Finished Product', 'many2one', width=30),
            col('date_start', 'Scheduled Start', 'datetime'),
            col('state', 'MO State', width=12),
            col('warehouse_id', 'Warehouse', 'many2one', width=14),
            col('component_id', 'Component', 'many2one', width=30),
            col('uom_id', 'Unit', 'many2one', width=8),
            col('qty_required', 'Required', 'qty', total=True),
            col('qty_reserved', 'Reserved', 'qty', total=True),
            col('qty_to_consume', 'Still to Consume', 'qty', total=True),
            col('qty_on_hand', 'On Hand', 'qty'),
            col('qty_free', 'Free', 'qty'),
            col('qty_incoming', 'Incoming', 'qty'),
            col('qty_shortage', 'Shortage', 'qty', total=True,
                help_text="Unreserved demand not covered by the free quantity left after earlier orders."),
            col('value_shortage', 'Shortage Value', 'monetary', value=True, total=True,
                help_text="Shortage at the component's standard price."),
        ]

    def _asr_check(self):
        self.ensure_one()

    def _asr_filter_text(self):
        parts = [self.env._("Orders starting up to %s", fields.Date.to_string(self.horizon_date))]
        if self.warehouse_ids:
            parts.append(self.env._("Warehouses: %s", ', '.join(self.warehouse_ids.mapped('name'))))
        if self.categ_ids:
            parts.append(self.env._("Categories: %s", ', '.join(self.categ_ids.mapped('complete_name'))))
        if self.product_ids:
            parts.append(self.env._("Products: %s", ', '.join(self.product_ids[:5].mapped('display_name'))
                                    + (' …' if len(self.product_ids) > 5 else '')))
        if self.show_all:
            parts.append(self.env._("All components"))
        return ' | '.join(parts)

    def _asr_xlsx_filename(self):
        return f"MO_Component_Shortage_{fields.Date.to_string(self.horizon_date)}.xlsx"

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------
    def _asr_productions(self):
        company = self.company_id.sudo()
        domain = [('company_id', '=', company.id), ('state', 'in', OPEN_MO_STATES),
                  ('date_start', '<=', company._asr_day_end_utc(self.horizon_date))]
        if self.warehouse_ids:
            domain.append(('picking_type_id.warehouse_id', 'in', self.warehouse_ids.ids))
        if self.product_ids:
            domain.append(('product_id', 'in', self.product_ids.ids))
        if self.categ_ids:
            domain.append(('product_id.categ_id', 'child_of', self.categ_ids.ids))
        return self.env['mrp.production'].sudo().search(domain, order='date_start, id')

    def _quantities(self, components_by_warehouse):
        """{(warehouse_id, product_id): quantities dict} in one batch per warehouse."""
        result = {}
        for warehouse_id, products in components_by_warehouse.items():
            products = products.with_company(self.company_id)
            if warehouse_id:
                products = products.with_context(warehouse_id=warehouse_id)
            quantities = products._compute_quantities_dict(False, False, False)
            for product_id, values in quantities.items():
                result[(warehouse_id, product_id)] = values
        return result

    def _asr_rows(self):
        self.ensure_one()
        company = self.company_id.sudo()
        productions = self._asr_productions()
        if not productions:
            return []
        productions.fetch(['name', 'product_id', 'state', 'date_start', 'picking_type_id', 'location_src_id',
                           'move_raw_ids'])
        moves = productions.move_raw_ids.filtered(lambda m: m.state not in ('done', 'cancel'))
        moves.fetch(['product_id', 'product_uom', 'product_uom_qty', 'product_qty', 'quantity', 'state',
                     'raw_material_production_id'])
        products = moves.product_id
        products.fetch(['display_name', 'uom_id', 'is_storable', 'default_code'])
        # Demand per move in the product UoM
        demand = []  # (production, move, required, reserved, warehouse_id)
        components_by_warehouse = defaultdict(lambda: self.env['product.product'])
        for production in productions:
            warehouse = production.picking_type_id.warehouse_id or production.location_src_id.warehouse_id
            for move in production.move_raw_ids:
                if move.state in ('done', 'cancel') or not move.product_id.is_storable:
                    continue
                product_uom = move.product_id.uom_id
                required = move.product_uom._compute_quantity(move.product_uom_qty, product_uom, round=False)
                reserved = move.product_uom._compute_quantity(move.quantity, product_uom, round=False)
                if product_uom.compare(required - reserved, 0.0) <= 0:
                    continue
                demand.append((production, move, required, min(reserved, required), warehouse))
                components_by_warehouse[warehouse.id] |= move.product_id
        if not demand:
            return []
        quantities = self._quantities(components_by_warehouse)
        pool = {}  # (warehouse_id, product_id) -> free quantity left for later orders
        state_labels = dict(self.env['mrp.production']._fields['state']._description_selection(self.env))
        rows = []
        current = None
        for production, move, required, reserved, warehouse in demand:
            product = move.product_id
            key = (warehouse.id, product.id)
            values = quantities.get(key, {})
            free = float(values.get('free_qty') or 0.0)
            if key not in pool:
                pool[key] = max(free, 0.0)
            unreserved = required - reserved
            shortage = max(unreserved - pool[key], 0.0)
            pool[key] = max(pool[key] - unreserved, 0.0)
            uom = product.uom_id
            shortage = uom.round(shortage)
            if not self.show_all and uom.is_zero(shortage):
                continue
            if current != production.id:
                current = production.id
                rows.append({'_group': f"{production.name} - {production.product_id.display_name}"})
            rows.append({
                'production_id': production,
                'product_id': production.product_id,
                'date_start': production.date_start,
                'state': state_labels.get(production.state, production.state),
                'warehouse_id': warehouse,
                'component_id': product,
                'uom_id': uom,
                'qty_required': uom.round(required),
                'qty_reserved': uom.round(reserved),
                'qty_to_consume': uom.round(unreserved),
                'qty_on_hand': float(values.get('qty_available') or 0.0),
                'qty_free': free,
                'qty_incoming': float(values.get('incoming_qty') or 0.0),
                'qty_shortage': shortage,
                'value_shortage': shortage * product.with_company(company).standard_price,
            })
        return rows


class ReportMoShortageLine(models.TransientModel):
    _name = 'asr.report.mo.shortage.line'
    _description = 'MO Component Shortage Line'
    _order = 'date_start, production_id, id'

    wizard_id = fields.Many2one('asr.report.mo.shortage', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='wizard_id.currency_id')
    production_id = fields.Many2one('mrp.production', 'Manufacturing Order', readonly=True)
    product_id = fields.Many2one('product.product', 'Finished Product', readonly=True)
    date_start = fields.Datetime('Scheduled Start', readonly=True)
    state = fields.Char('MO State', readonly=True)
    warehouse_id = fields.Many2one('stock.warehouse', readonly=True)
    component_id = fields.Many2one('product.product', readonly=True)
    uom_id = fields.Many2one('uom.uom', 'Unit', readonly=True)
    qty_required = fields.Float('Required', digits='Product Unit of Measure', readonly=True)
    qty_reserved = fields.Float('Reserved', digits='Product Unit of Measure', readonly=True)
    qty_to_consume = fields.Float('Still to Consume', digits='Product Unit of Measure', readonly=True)
    qty_on_hand = fields.Float('On Hand', digits='Product Unit of Measure', readonly=True, aggregator=None)
    qty_free = fields.Float('Free', digits='Product Unit of Measure', readonly=True, aggregator=None)
    qty_incoming = fields.Float('Incoming', digits='Product Unit of Measure', readonly=True, aggregator=None)
    qty_shortage = fields.Float('Shortage', digits='Product Unit of Measure', readonly=True)
    value_shortage = fields.Monetary('Shortage Value', currency_field='currency_id', readonly=True,
                                     groups='ebshel_stock_reports.group_see_values')
