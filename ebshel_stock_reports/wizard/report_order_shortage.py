# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
"""Report #12 - Open order shortage (estimate).

Open sale order lines (order confirmed, storable product, quantity still to
deliver) in promised-date order, with the quantity reserved for the line,
the free quantity of the order warehouse and the incoming quantity. Free
stock is allocated to the earlier lines of each product and warehouse
first, so a line's shortage is what is left after the lines promised before
it. DESIGN.md §3 #12: this is an estimate; Odoo's Reception report remains
the tool to assign incoming quantities to orders.
"""
from collections import defaultdict

from odoo import fields, models
from odoo.tools import SQL

from .report_mixin import col


class ReportOrderShortage(models.TransientModel):
    _name = 'asr.report.order.shortage'
    _inherit = 'asr.report.mixin'
    _description = 'Open Order Shortage'
    _asr_title = 'Open Order Shortage'
    _asr_line_model = 'asr.report.order.shortage.line'
    _asr_uses_engine = False

    show_all = fields.Boolean(
        'Show all open lines', default=False,
        help="Unticked: only the lines short of stock after reservations and free stock are listed.")
    partner_ids = fields.Many2many('res.partner', string='Customers')
    line_ids = fields.One2many('asr.report.order.shortage.line', 'wizard_id')

    # ------------------------------------------------------------------
    # Columns
    # ------------------------------------------------------------------
    def _asr_columns(self):
        return [
            col('order_id', 'Order', 'many2one', width=14),
            col('partner_id', 'Customer', 'many2one', width=26),
            col('date_promised', 'Promised', 'date',
                help_text="Commitment date of the order, else its expected date, else the deadline of the moves."),
            col('warehouse_id', 'Warehouse', 'many2one', width=14),
            col('product_id', 'Product', 'many2one', width=30),
            col('qty_to_deliver', 'To Deliver', 'qty', total=True, help_text="Ordered minus delivered, product unit."),
            col('qty_reserved', 'Reserved', 'qty', total=True,
                help_text="Quantity reserved by the line's open moves."),
            col('qty_free', 'Free', 'qty',
                help_text="Free quantity of the product in the order warehouse still unallocated when this line "
                          "is reached (earlier promised lines are served first)."),
            col('qty_allocated', 'Allocated', 'qty', total=True,
                help_text="Free quantity allocated to this line: the lesser of the unreserved need and the free "
                          "quantity left."),
            col('qty_shortage', 'Shortage', 'qty', total=True,
                help_text="To deliver - reserved - allocated, not below zero."),
            col('qty_incoming', 'Incoming', 'qty',
                help_text="Incoming quantity of the product in the warehouse still unallocated when this line is "
                          "reached."),
            col('qty_shortage_after', 'Shortage After Incoming', 'qty', total=True,
                help_text="Shortage not covered by the incoming quantity left for this line."),
        ]

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------
    def _asr_rows(self):
        self.ensure_one()
        company = self.company_id.sudo()
        today = fields.Date.context_today(self)

        filters = [SQL("TRUE")]
        if self.product_ids or self.categ_ids:
            product_ids = tuple(self._asr_products().ids) or (0,)
            filters.append(SQL("sol.product_id IN %s", product_ids))
        if self.warehouse_ids:
            filters.append(SQL("so.warehouse_id IN %s", tuple(self.warehouse_ids.ids)))
        if self.partner_ids:
            filters.append(SQL("so.partner_id IN %s", tuple(self.partner_ids.ids)))

        self.env.flush_all()
        self.env.cr.execute(SQL("""
            SELECT sol.id, sol.order_id, so.partner_id, so.warehouse_id, sol.product_id, so.commitment_date,
                   (sol.product_uom_qty - sol.qty_delivered) * lu.factor / pu.factor AS qty_to_deliver,
                   COALESCE(SUM(CASE WHEN sm.state IN ('assigned', 'partially_available')
                                     THEN sm.quantity * mu.factor / pu.factor END), 0) AS qty_reserved,
                   MIN(sm.date_deadline) AS deadline
              FROM sale_order_line sol
              JOIN sale_order so ON so.id = sol.order_id
              JOIN product_product pp ON pp.id = sol.product_id
              JOIN product_template pt ON pt.id = pp.product_tmpl_id
              JOIN uom_uom lu ON lu.id = sol.product_uom_id
              JOIN uom_uom pu ON pu.id = pt.uom_id
              LEFT JOIN stock_move sm ON sm.sale_line_id = sol.id AND sm.state NOT IN ('done', 'cancel')
              LEFT JOIN uom_uom mu ON mu.id = sm.product_uom
             WHERE so.company_id = %(company)s AND so.state = 'sale'
               AND pt.is_storable AND sol.display_type IS NULL
               AND sol.product_uom_qty - sol.qty_delivered > 0
               AND %(filters)s
             GROUP BY sol.id, so.id, lu.factor, pu.factor
             ORDER BY so.id, sol.id
        """, company=company.id, filters=SQL(" AND ").join(filters)))
        data = self.env.cr.fetchall()
        if not data:
            return []

        orders = {o.id: o for o in self.env['sale.order'].browse(list({r[1] for r in data}))}
        need_expected = self.env['sale.order'].browse([oid for oid, o in orders.items() if not o.commitment_date])
        expected = {o.id: o.expected_date for o in need_expected}
        partners = {p.id: p for p in self.env['res.partner'].browse(list({r[2] for r in data}))}
        warehouses = {w.id: w for w in self.env['stock.warehouse'].browse(list({r[3] for r in data if r[3]}))}
        Product = self.env['product.product'].with_context(active_test=False)
        products = {p.id: p for p in Product.browse(list({r[4] for r in data}))}

        # free and incoming quantities per warehouse, one batch per warehouse
        by_warehouse = defaultdict(set)
        for row in data:
            by_warehouse[row[3]].add(row[4])
        free = {}
        incoming = {}
        for wh_id, pids in by_warehouse.items():
            recs = Product.with_company(company).browse(sorted(pids))
            if wh_id:
                recs = recs.with_context(warehouse_id=wh_id)
            for product in recs:
                free[(wh_id, product.id)] = product.free_qty
                incoming[(wh_id, product.id)] = product.incoming_qty

        lines = []
        for sol_id, order_id, partner_id, wh_id, product_id, commitment, to_deliver, reserved, deadline in data:
            promised_dt = commitment or expected.get(order_id) or deadline
            promised = company._asr_local_day(promised_dt) if promised_dt else False
            lines.append((promised or today, order_id, sol_id, partner_id, wh_id, product_id, promised,
                          float(to_deliver or 0.0), float(reserved or 0.0)))
        lines.sort(key=lambda item: item[:3])

        rows = []
        for _sort_day, order_id, _sol_id, partner_id, wh_id, product_id, promised, to_deliver, reserved in lines:
            product = products[product_id]
            uom = product.uom_id
            key = (wh_id, product_id)
            free_left = max(free.get(key, 0.0), 0.0)
            incoming_left = max(incoming.get(key, 0.0), 0.0)
            need = max(to_deliver - reserved, 0.0)
            allocated = min(need, free_left)
            free[key] = free_left - allocated
            shortage = uom.round(max(to_deliver - reserved - allocated, 0.0))
            covered = min(shortage, incoming_left)
            incoming[key] = incoming_left - covered
            shortage_after = uom.round(shortage - covered)
            if not self.show_all and uom.is_zero(shortage):
                continue
            rows.append({
                'order_id': orders[order_id], 'partner_id': partners.get(partner_id),
                'date_promised': promised, 'warehouse_id': warehouses.get(wh_id), 'product_id': product,
                'qty_to_deliver': to_deliver, 'qty_reserved': reserved, 'qty_free': free_left,
                'qty_allocated': allocated, 'qty_shortage': shortage, 'qty_incoming': incoming_left,
                'qty_shortage_after': shortage_after,
            })
        return rows


class ReportOrderShortageLine(models.TransientModel):
    _name = 'asr.report.order.shortage.line'
    _description = 'Open Order Shortage Line'
    _order = 'date_promised, order_id, id'

    wizard_id = fields.Many2one('asr.report.order.shortage', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='wizard_id.currency_id')
    order_id = fields.Many2one('sale.order', readonly=True)
    partner_id = fields.Many2one('res.partner', 'Customer', readonly=True)
    date_promised = fields.Date('Promised', readonly=True)
    warehouse_id = fields.Many2one('stock.warehouse', readonly=True)
    product_id = fields.Many2one('product.product', readonly=True)
    qty_to_deliver = fields.Float('To Deliver', digits='Product Unit of Measure', readonly=True)
    qty_reserved = fields.Float('Reserved', digits='Product Unit of Measure', readonly=True)
    qty_free = fields.Float('Free', digits='Product Unit of Measure', readonly=True, aggregator=None)
    qty_allocated = fields.Float('Allocated', digits='Product Unit of Measure', readonly=True)
    qty_shortage = fields.Float('Shortage', digits='Product Unit of Measure', readonly=True)
    qty_incoming = fields.Float('Incoming', digits='Product Unit of Measure', readonly=True, aggregator=None)
    qty_shortage_after = fields.Float('Shortage After Incoming', digits='Product Unit of Measure', readonly=True)
