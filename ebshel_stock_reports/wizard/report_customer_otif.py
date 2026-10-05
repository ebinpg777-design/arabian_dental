# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
"""Report #8 - Customer OTIF (on time, in full).

One row per confirmed sale order line of a storable product whose delivery
date falls in the period, plus the lines promised in the period that have
not shipped at all yet (late open lines). Definitions follow DESIGN.md §2.3:

* promised date = ``sale.order.commitment_date``, else the computed
  ``expected_date``, else the earliest ``date_deadline`` of the line's moves;
* delivery date = ``date_done`` of the first or last done delivery (picking
  to a customer location) of the line, per ``company.asr_otif_basis``;
* on time = delivery day (company reporting timezone) <= promised day;
* in full = delivered qty >= ordered qty x (1 - ``asr_otif_tolerance`` / 100);
* OTIF = on time and in full.

The customer mode aggregates the same lines per customer (counts and rates).
"""
from collections import defaultdict

from odoo import fields, models
from odoo.tools import SQL, float_compare

from .report_mixin import col


class ReportCustomerOtif(models.TransientModel):
    _name = 'asr.report.customer.otif'
    _inherit = 'asr.report.mixin'
    _description = 'Customer OTIF'
    _asr_title = 'Customer OTIF'
    _asr_line_model = 'asr.report.customer.otif.line'
    _asr_uses_engine = False

    group_by = fields.Selection([
        ('line', 'Order lines'),
        ('customer', 'Customers: counts and rates'),
    ], default='line', required=True, string='Show')
    partner_ids = fields.Many2many('res.partner', string='Customers')
    line_ids = fields.One2many('asr.report.customer.otif.line', 'wizard_id')

    # ------------------------------------------------------------------
    # Columns
    # ------------------------------------------------------------------
    def _asr_columns(self):
        if self.group_by == 'customer':
            return [
                col('partner_id', 'Customer', 'many2one', width=30),
                col('line_count', 'Lines', 'int', total=True),
                col('on_time_count', 'On Time', 'int', total=True),
                col('in_full_count', 'In Full', 'int', total=True),
                col('otif_count', 'OTIF', 'int', total=True),
                col('on_time_rate', 'On Time %', 'percent'),
                col('in_full_rate', 'In Full %', 'percent'),
                col('otif_rate', 'OTIF %', 'percent'),
            ]
        return [
            col('order_id', 'Order', 'many2one', width=14),
            col('partner_id', 'Customer', 'many2one', width=26),
            col('product_id', 'Product', 'many2one', width=30),
            col('date_promised', 'Promised', 'date',
                help_text="Commitment date of the order, else its expected date, else the deadline of the moves."),
            col('date_delivered', 'Delivered', 'date',
                help_text="Date of the first (or last, per the company setting) done delivery of the line. "
                          "Empty when nothing has shipped."),
            col('days_late', 'Days Late', 'int',
                help_text="Delivered day minus promised day (negative: early). Open lines: today minus promised day."),
            col('qty_ordered', 'Ordered', 'qty', total=True),
            col('qty_delivered', 'Delivered Qty', 'qty', total=True),
            col('on_time', 'On Time', 'bool'),
            col('in_full', 'In Full', 'bool'),
            col('otif', 'OTIF', 'bool'),
        ]

    def _asr_view_context(self):
        return {'asr_group_by': self.group_by}

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------
    def _period(self):
        date_to = self.date_to or fields.Date.context_today(self)
        date_from = self.date_from or date_to.replace(day=1)
        return date_from, date_to

    def _asr_rows(self):
        self.ensure_one()
        lines = self._line_rows()
        if self.group_by == 'customer':
            return self._customer_rows(lines)
        return lines

    def _line_rows(self):
        company = self.company_id.sudo()
        date_from, date_to = self._period()
        start = company._asr_day_start_utc(date_from)
        end = company._asr_day_end_utc(date_to)
        today = fields.Date.context_today(self)
        tolerance = (company.asr_otif_tolerance or 0.0) / 100.0
        basis_sql = SQL("MAX") if company.asr_otif_basis == 'last' else SQL("MIN")

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
            SELECT sol.id, sol.order_id, so.partner_id, sol.product_id, so.commitment_date,
                   sol.product_uom_qty * lu.factor / pu.factor AS qty_ordered,
                   sol.qty_delivered * lu.factor / pu.factor AS qty_delivered,
                   MIN(sm.date_deadline) AS deadline,
                   %(basis)s(COALESCE(sp.date_done, sm.date))
                       FILTER (WHERE sm.state = 'done' AND ld.usage = 'customer') AS delivered_at
              FROM sale_order_line sol
              JOIN sale_order so ON so.id = sol.order_id
              JOIN product_product pp ON pp.id = sol.product_id
              JOIN product_template pt ON pt.id = pp.product_tmpl_id
              JOIN uom_uom lu ON lu.id = sol.product_uom_id
              JOIN uom_uom pu ON pu.id = pt.uom_id
              LEFT JOIN stock_move sm ON sm.sale_line_id = sol.id
              LEFT JOIN stock_picking sp ON sp.id = sm.picking_id
              LEFT JOIN stock_location ld ON ld.id = sm.location_dest_id
             WHERE so.company_id = %(company)s AND so.state = 'sale'
               AND pt.is_storable AND sol.display_type IS NULL AND sol.product_uom_qty > 0
               AND %(filters)s
             GROUP BY sol.id, so.id, lu.factor, pu.factor
            HAVING %(basis)s(COALESCE(sp.date_done, sm.date))
                       FILTER (WHERE sm.state = 'done' AND ld.usage = 'customer') BETWEEN %(start)s AND %(end)s
                OR COUNT(sm.id) FILTER (WHERE sm.state = 'done' AND ld.usage = 'customer') = 0
             ORDER BY so.partner_id, so.id, sol.id
        """, basis=basis_sql, company=company.id, filters=SQL(" AND ").join(filters), start=start, end=end))
        data = self.env.cr.fetchall()
        if not data:
            return []

        orders = {o.id: o for o in self.env['sale.order'].browse(list({r[1] for r in data}))}
        # expected_date is not stored: compute it once, only for the orders without a commitment date
        need_expected = self.env['sale.order'].browse([oid for oid, o in orders.items() if not o.commitment_date])
        expected = {o.id: o.expected_date for o in need_expected}
        partners = {p.id: p for p in self.env['res.partner'].browse(list({r[2] for r in data}))}
        products = {p.id: p for p in self.env['product.product'].with_context(active_test=False).browse(
            list({r[3] for r in data}))}

        rows = []
        for (_sol_id, order_id, partner_id, product_id, commitment, qty_ordered, qty_delivered, deadline,
             delivered_at) in data:
            promised_dt = commitment or expected.get(order_id) or deadline
            promised = company._asr_local_day(promised_dt) if promised_dt else False
            delivered = company._asr_local_day(delivered_at) if delivered_at else False
            if not delivered:
                # late open line: promised in the period and already past
                if not promised or promised < date_from or promised > date_to or promised >= today:
                    continue
            product = products[product_id]
            qty_ordered = float(qty_ordered or 0.0)
            qty_delivered = float(qty_delivered or 0.0)
            on_time = bool(delivered and promised and delivered <= promised)
            in_full = float_compare(qty_delivered, qty_ordered * (1.0 - tolerance),
                                    precision_rounding=product.uom_id.rounding) >= 0
            otif = on_time and in_full
            if delivered and promised:
                days_late = (delivered - promised).days
            elif promised:
                days_late = (today - promised).days
            else:
                days_late = 0
            rows.append({
                'order_id': orders[order_id], 'partner_id': partners.get(partner_id), 'product_id': product,
                'date_promised': promised, 'date_delivered': delivered, 'days_late': days_late,
                'qty_ordered': qty_ordered, 'qty_delivered': qty_delivered,
                'on_time': on_time, 'in_full': in_full, 'otif': otif,
                'line_count': 1, 'on_time_count': int(on_time), 'in_full_count': int(in_full), 'otif_count': int(otif),
            })
        return rows

    def _customer_rows(self, lines):
        totals = defaultdict(lambda: defaultdict(int))
        partners = {}
        for line in lines:
            partner = line['partner_id']
            key = partner.id if partner else 0
            partners[key] = partner
            bucket = totals[key]
            bucket['line_count'] += 1
            bucket['on_time_count'] += line['on_time_count']
            bucket['in_full_count'] += line['in_full_count']
            bucket['otif_count'] += line['otif_count']
        rows = []
        for key in sorted(totals, key=lambda k: (partners[k].display_name if partners[k] else '', k)):
            bucket = totals[key]
            count = bucket['line_count'] or 1
            rows.append({
                'partner_id': partners[key], 'line_count': bucket['line_count'],
                'on_time_count': bucket['on_time_count'], 'in_full_count': bucket['in_full_count'],
                'otif_count': bucket['otif_count'],
                'on_time_rate': 100.0 * bucket['on_time_count'] / count,
                'in_full_rate': 100.0 * bucket['in_full_count'] / count,
                'otif_rate': 100.0 * bucket['otif_count'] / count,
            })
        return rows


class ReportCustomerOtifLine(models.TransientModel):
    _name = 'asr.report.customer.otif.line'
    _description = 'Customer OTIF Line'
    _order = 'partner_id, order_id, id'

    wizard_id = fields.Many2one('asr.report.customer.otif', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='wizard_id.currency_id')
    order_id = fields.Many2one('sale.order', readonly=True)
    partner_id = fields.Many2one('res.partner', 'Customer', readonly=True)
    product_id = fields.Many2one('product.product', readonly=True)
    date_promised = fields.Date('Promised', readonly=True)
    date_delivered = fields.Date('Delivered', readonly=True)
    days_late = fields.Integer(readonly=True, aggregator='avg')
    qty_ordered = fields.Float('Ordered', digits='Product Unit of Measure', readonly=True)
    qty_delivered = fields.Float('Delivered Qty', digits='Product Unit of Measure', readonly=True)
    on_time = fields.Boolean(readonly=True)
    in_full = fields.Boolean(readonly=True)
    otif = fields.Boolean('OTIF', readonly=True)
    line_count = fields.Integer('Lines', readonly=True)
    on_time_count = fields.Integer('On Time (count)', readonly=True)
    in_full_count = fields.Integer('In Full (count)', readonly=True)
    otif_count = fields.Integer('OTIF (count)', readonly=True, help="1 when the line is on time and in full, else 0.")
    on_time_rate = fields.Float('On Time %', readonly=True, aggregator='avg')
    in_full_rate = fields.Float('In Full %', readonly=True, aggregator='avg')
    otif_rate = fields.Float('OTIF %', readonly=True, aggregator='avg')
