# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
"""Report #15 - Movement registers (inward / outward / internal / dropship).

Live from done move lines. A line belongs to a register by the code of its
move's picking type (``incoming``, ``outgoing``, ``internal``; ``dropship``
is added by stock_dropshipping) or, for moves without a picking type, by the
usages of its locations (supplier -> internal is inward, internal -> customer
outward, internal -> internal internal, supplier -> customer dropship).
Values are the move value split over the lines of the move; 0 when the move
is not valued (DESIGN.md §2.1).
"""
from odoo import fields, models
from odoo.tools import SQL

from .report_mixin import col

REGISTERS = [
    ('inward', 'Inward Register'),
    ('outward', 'Outward Register'),
    ('internal', 'Internal Transfer Register'),
    ('dropship', 'Dropship Register'),
]
PICKING_CODE = {'inward': 'incoming', 'outward': 'outgoing', 'internal': 'internal', 'dropship': 'dropship'}
# usage rule for moves without a picking type: (source usages, destination usages)
USAGE_RULE = {
    'inward': (('supplier',), ('internal', 'transit')),
    'outward': (('internal', 'transit'), ('customer',)),
    'internal': (('internal', 'transit'), ('internal', 'transit')),
    'dropship': (('supplier',), ('customer',)),
}


class ReportRegister(models.TransientModel):
    _name = 'asr.report.register'
    _inherit = 'asr.report.mixin'
    _description = 'Movement Registers'
    _asr_title = 'Movement Register'
    _asr_line_model = 'asr.report.register.line'
    _asr_uses_engine = False

    register = fields.Selection(REGISTERS, default='inward', required=True)
    line_ids = fields.One2many('asr.report.register.line', 'wizard_id')

    # ------------------------------------------------------------------
    # Columns
    # ------------------------------------------------------------------
    def _asr_columns(self):
        columns = [
            col('serial', 'No.', 'int', width=6),
            col('date', 'Date', 'datetime'),
            col('reference', 'Reference', width=18),
            col('picking_type_id', 'Operation Type', 'many2one', width=18),
            col('partner_id', 'Partner', 'many2one', width=22),
            col('origin', 'Source Document', width=16),
        ]
        if self.register in ('inward', 'dropship'):
            columns.append(col('purchase_order', 'Purchase Order', width=12))
        if self.register in ('outward', 'dropship'):
            columns.append(col('sale_order', 'Sale Order', width=12))
        columns += [
            col('product_id', 'Product', 'many2one', width=34),
            col('lot_id', 'Lot/Serial', 'many2one'),
            col('package_id', 'Package', 'many2one'),
            col('location_id', 'From', 'many2one', width=20),
            col('location_dest_id', 'To', 'many2one', width=20),
            col('quantity', 'Quantity', 'qty', total=True),
            col('uom_id', 'Unit', 'many2one', width=8),
            col('value', 'Value', 'monetary', value=True, total=True,
                help_text="Share of the move value for this line; 0 when the move is not valued."),
            col('unit_value', 'Unit Value', 'monetary', value=True),
        ]
        return columns

    def _asr_filter_text(self):
        text = dict(REGISTERS).get(self.register, '')
        base = super()._asr_filter_text()
        return f"{text} | {base}" if base else text

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------
    def _period(self):
        date_to = self.date_to or fields.Date.context_today(self)
        date_from = self.date_from or date_to.replace(day=1)
        return date_from, date_to

    def _register_sql(self):
        src_usages, dst_usages = USAGE_RULE[self.register]
        return SQL("""(spt.code = %s OR (sm.picking_type_id IS NULL
                       AND ls.usage IN %s AND ld.usage IN %s))""",
                   PICKING_CODE[self.register], src_usages, dst_usages)

    def _asr_rows(self):
        self.ensure_one()
        company = self.company_id.sudo()
        date_from, date_to = self._period()
        start = company._asr_day_start_utc(date_from)
        end = company._asr_day_end_utc(date_to)
        if self.location_ids:
            ids = tuple(self._asr_locations().ids) or (0,)
            location_sql = SQL("(l.location_id IN %(ids)s OR l.location_dest_id IN %(ids)s)", ids=ids)
        elif self.warehouse_ids:
            ids = tuple(self.warehouse_ids.ids)
            location_sql = SQL("""(l.pt_warehouse_id IN %(ids)s OR l.src_warehouse_id IN %(ids)s
                                   OR l.dst_warehouse_id IN %(ids)s)""", ids=ids)
        else:
            location_sql = SQL("TRUE")
        if self.product_ids or self.categ_ids:
            product_sql = SQL("sm.product_id IN %s", tuple(self._asr_products().ids) or (0,))
        else:
            product_sql = SQL("TRUE")
        self.env.cr.execute(SQL("""
            WITH lines AS (
                SELECT sml.id AS line_id, sm.id AS move_id, sm.date, sm.product_id, sm.picking_type_id,
                       COALESCE(sp.name, sm.reference) AS reference,
                       COALESCE(sp.partner_id, sm.partner_id) AS partner_id,
                       COALESCE(sp.origin, sm.origin) AS origin,
                       po.name AS purchase_order, so.name AS sale_order,
                       sml.lot_id, COALESCE(sml.result_package_id, sml.package_id) AS package_id,
                       sml.location_id, sml.location_dest_id,
                       spt.warehouse_id AS pt_warehouse_id, ls.warehouse_id AS src_warehouse_id,
                       ld.warehouse_id AS dst_warehouse_id,
                       sml.quantity_product_uom AS qty,
                       sm.value, (sm.is_in OR sm.is_out OR sm.is_dropship) AS valued,
                       (sml.owner_id IS NOT NULL AND sml.owner_id <> %(partner)s) AS excluded,
                       SUM(CASE WHEN sml.owner_id IS NULL OR sml.owner_id = %(partner)s
                                THEN sml.quantity_product_uom ELSE 0 END)
                           OVER (PARTITION BY sm.id) AS move_qty
                  FROM stock_move sm
                  JOIN stock_move_line sml ON sml.move_id = sm.id
                  JOIN stock_location ls ON ls.id = sml.location_id
                  JOIN stock_location ld ON ld.id = sml.location_dest_id
                  LEFT JOIN stock_picking_type spt ON spt.id = sm.picking_type_id
                  LEFT JOIN stock_picking sp ON sp.id = sm.picking_id
                  LEFT JOIN purchase_order_line pol ON pol.id = sm.purchase_line_id
                  LEFT JOIN purchase_order po ON po.id = pol.order_id
                  LEFT JOIN sale_order_line sol ON sol.id = sm.sale_line_id
                  LEFT JOIN sale_order so ON so.id = sol.order_id
                 WHERE sm.state = 'done' AND sm.company_id = %(company)s
                   AND sm.date >= %(start)s AND sm.date <= %(end)s
                   AND COALESCE(sml.picked, TRUE)
                   AND COALESCE(sml.quantity_product_uom, 0) <> 0
                   AND %(register)s
                   AND %(products)s
            )
            SELECT l.* FROM lines l
             WHERE %(locations)s
             ORDER BY l.date, l.move_id, l.line_id
        """, partner=company.partner_id.id, company=company.id, start=start, end=end,
             register=self._register_sql(), products=product_sql, locations=location_sql))
        data = self.env.cr.dictfetchall()
        if not data:
            return []
        products = self._browse_map('product.product', [r['product_id'] for r in data])
        partners = self._browse_map('res.partner', [r['partner_id'] for r in data])
        picking_types = self._browse_map('stock.picking.type', [r['picking_type_id'] for r in data])
        location_objs = self._browse_map(
            'stock.location', [r['location_id'] for r in data] + [r['location_dest_id'] for r in data])
        lots = self._browse_map('stock.lot', [r['lot_id'] for r in data])
        packages = self._browse_map('stock.package', [r['package_id'] for r in data])
        rows = []
        for serial, r in enumerate(data, start=1):
            qty = float(r['qty'] or 0)
            move_qty = float(r['move_qty'] or 0)
            value = (float(r['value'] or 0) * qty / move_qty) \
                if move_qty and r['valued'] and not r['excluded'] else 0.0
            product = products.get(r['product_id'])
            rows.append({
                'serial': serial,
                'date': r['date'],
                'reference': r['reference'] or '',
                'picking_type_id': picking_types.get(r['picking_type_id']),
                'partner_id': partners.get(r['partner_id']),
                'origin': r['origin'] or '',
                'purchase_order': r['purchase_order'] or '',
                'sale_order': r['sale_order'] or '',
                'product_id': product,
                'lot_id': lots.get(r['lot_id']),
                'package_id': packages.get(r['package_id']),
                'location_id': location_objs.get(r['location_id']),
                'location_dest_id': location_objs.get(r['location_dest_id']),
                'quantity': qty,
                'uom_id': product.uom_id if product else False,
                'value': value,
                'unit_value': value / qty if qty else 0.0,
            })
        return rows

    def _browse_map(self, model, ids):
        records = self.env[model].with_context(active_test=False).browse(list({i for i in ids if i}))
        return {record.id: record for record in records}


class ReportRegisterLine(models.TransientModel):
    _name = 'asr.report.register.line'
    _description = 'Movement Registers Line'
    _order = 'serial, id'

    wizard_id = fields.Many2one('asr.report.register', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='wizard_id.currency_id')
    serial = fields.Integer('No.', readonly=True, aggregator=None)
    date = fields.Datetime(readonly=True)
    reference = fields.Char(readonly=True)
    picking_type_id = fields.Many2one('stock.picking.type', 'Operation Type', readonly=True)
    partner_id = fields.Many2one('res.partner', readonly=True)
    origin = fields.Char('Source Document', readonly=True)
    purchase_order = fields.Char(readonly=True)
    sale_order = fields.Char(readonly=True)
    product_id = fields.Many2one('product.product', readonly=True)
    lot_id = fields.Many2one('stock.lot', 'Lot/Serial', readonly=True)
    package_id = fields.Many2one('stock.package', readonly=True)
    location_id = fields.Many2one('stock.location', 'From', readonly=True)
    location_dest_id = fields.Many2one('stock.location', 'To', readonly=True)
    quantity = fields.Float(digits='Product Unit of Measure', readonly=True)
    uom_id = fields.Many2one('uom.uom', 'Unit', readonly=True)
    value = fields.Monetary(currency_field='currency_id', readonly=True,
                            groups='ebshel_stock_reports.group_see_values')
    unit_value = fields.Monetary(currency_field='currency_id', readonly=True, aggregator=None,
                                 groups='ebshel_stock_reports.group_see_values')
