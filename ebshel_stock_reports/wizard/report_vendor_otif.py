# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
"""Report #9 - Vendor OTIF (on time, in full).

One row per purchase order line of a storable product whose first receipt
(done move from a supplier location) falls in the period. DESIGN.md §1.10
and §2.3:

* promised date = ``purchase.order.line.date_planned``;
* received date = date of the first done receipt move of the line;
* on time compares calendar days, as Odoo's ``vendor.delay.report`` does
  (``pol.date_planned::date >= m.date::date``); here the days are taken in
  the company reporting timezone, Odoo takes the UTC dates;
* in full = received qty >= ordered qty x (1 - ``asr_otif_tolerance`` / 100).

Beside each vendor the Odoo figures are shown: the weighted on-time rate of
``vendor.delay.report`` (sum qty_on_time / sum qty_total x 100 over the
period) and the average ``purchase.report.days_to_arrival``.
"""
from collections import defaultdict

from odoo import fields, models
from odoo.tools import SQL, float_compare

from .report_mixin import col


class ReportVendorOtif(models.TransientModel):
    _name = 'asr.report.vendor.otif'
    _inherit = 'asr.report.mixin'
    _description = 'Vendor OTIF'
    _asr_title = 'Vendor OTIF'
    _asr_line_model = 'asr.report.vendor.otif.line'
    _asr_uses_engine = False

    group_by = fields.Selection([
        ('line', 'Order lines'),
        ('vendor', 'Vendors: counts and rates'),
    ], default='line', required=True, string='Show')
    partner_ids = fields.Many2many('res.partner', string='Vendors')
    line_ids = fields.One2many('asr.report.vendor.otif.line', 'wizard_id')

    # ------------------------------------------------------------------
    # Columns
    # ------------------------------------------------------------------
    def _asr_columns(self):
        odoo_columns = [
            col('odoo_on_time_rate', 'Odoo On-Time Rate %', 'percent',
                help_text="Odoo's Vendor Delay report for this vendor and period: on-time quantity / total "
                          "quantity x 100. Odoo compares calendar dates (UTC), not datetimes."),
            col('odoo_days_to_arrival', 'Odoo Days to Arrival', 'float',
                help_text="Average 'Effective Days To Arrival' of Odoo's Purchase Analysis for this vendor's "
                          "orders received in the period (first receipt date minus order date)."),
        ]
        if self.group_by == 'vendor':
            return [
                col('partner_id', 'Vendor', 'many2one', width=30),
                col('line_count', 'Lines', 'int', total=True),
                col('on_time_count', 'On Time', 'int', total=True),
                col('in_full_count', 'In Full', 'int', total=True),
                col('otif_count', 'OTIF', 'int', total=True),
                col('on_time_rate', 'On Time %', 'percent'),
                col('in_full_rate', 'In Full %', 'percent'),
                col('otif_rate', 'OTIF %', 'percent'),
            ] + odoo_columns
        return [
            col('order_id', 'Order', 'many2one', width=14),
            col('partner_id', 'Vendor', 'many2one', width=26),
            col('product_id', 'Product', 'many2one', width=30),
            col('date_promised', 'Promised', 'date', help_text="Expected arrival of the purchase order line."),
            col('date_received', 'Received', 'date', help_text="Date of the first done receipt of the line."),
            col('days_late', 'Days Late', 'int',
                help_text="Received day minus promised day (negative: early), in the company reporting timezone. "
                          "Odoo's Vendor Delay report compares the UTC dates, not datetimes."),
            col('qty_ordered', 'Ordered', 'qty', total=True),
            col('qty_received', 'Received Qty', 'qty', total=True),
            col('on_time', 'On Time', 'bool'),
            col('in_full', 'In Full', 'bool'),
            col('otif', 'OTIF', 'bool'),
        ] + odoo_columns

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
        if self.group_by == 'vendor':
            return self._vendor_rows(lines)
        return lines

    def _odoo_figures(self, partner_ids, start, end):
        """{partner_id: (on_time_rate or None, days_to_arrival or None)} from Odoo's own reports."""
        if not partner_ids:
            return {}
        figures = {}
        delay = self.env['vendor.delay.report'].sudo()._read_group(
            [('partner_id', 'in', partner_ids), ('date', '>=', start), ('date', '<=', end)],
            ['partner_id'], ['qty_on_time:sum', 'qty_total:sum'])
        for partner, on_time, total in delay:
            rate = (100.0 * (on_time or 0.0) / total) if total else None
            figures[partner.id] = [rate, None]
        arrival = self.env['purchase.report'].sudo().with_company(self.company_id)._read_group(
            [('partner_id', 'in', partner_ids), ('company_id', '=', self.company_id.id),
             ('state', 'in', ('purchase', 'done')), ('effective_date', '>=', start), ('effective_date', '<=', end)],
            ['partner_id'], ['days_to_arrival:avg'])
        for partner, days in arrival:
            figures.setdefault(partner.id, [None, None])[1] = days
        return figures

    def _line_rows(self):
        company = self.company_id.sudo()
        date_from, date_to = self._period()
        start = company._asr_day_start_utc(date_from)
        end = company._asr_day_end_utc(date_to)
        tolerance = (company.asr_otif_tolerance or 0.0) / 100.0

        filters = [SQL("TRUE")]
        if self.product_ids or self.categ_ids:
            product_ids = tuple(self._asr_products().ids) or (0,)
            filters.append(SQL("pol.product_id IN %s", product_ids))
        if self.warehouse_ids:
            filters.append(SQL("""po.picking_type_id IN (
                SELECT id FROM stock_picking_type WHERE warehouse_id IN %s)""", tuple(self.warehouse_ids.ids)))
        if self.partner_ids:
            filters.append(SQL("po.partner_id IN %s", tuple(self.partner_ids.ids)))

        self.env.flush_all()
        self.env.cr.execute(SQL("""
            SELECT pol.id, pol.order_id, po.partner_id, pol.product_id, pol.date_planned,
                   pol.product_uom_qty AS qty_ordered,
                   pol.qty_received * lu.factor / pu.factor AS qty_received,
                   MIN(sm.date) AS received_at
              FROM purchase_order_line pol
              JOIN purchase_order po ON po.id = pol.order_id
              JOIN product_product pp ON pp.id = pol.product_id
              JOIN product_template pt ON pt.id = pp.product_tmpl_id
              JOIN uom_uom lu ON lu.id = pol.product_uom_id
              JOIN uom_uom pu ON pu.id = pt.uom_id
              JOIN stock_move sm ON sm.purchase_line_id = pol.id
              JOIN stock_location ls ON ls.id = sm.location_id
             WHERE po.company_id = %(company)s AND po.state IN ('purchase', 'done')
               AND pt.is_storable AND pol.display_type IS NULL
               AND sm.state = 'done' AND ls.usage = 'supplier'
               AND %(filters)s
             GROUP BY pol.id, po.id, lu.factor, pu.factor
            HAVING MIN(sm.date) BETWEEN %(start)s AND %(end)s
             ORDER BY po.partner_id, po.id, pol.id
        """, company=company.id, filters=SQL(" AND ").join(filters), start=start, end=end))
        data = self.env.cr.fetchall()
        if not data:
            return []

        orders = {o.id: o for o in self.env['purchase.order'].browse(list({r[1] for r in data}))}
        partner_ids = list({r[2] for r in data})
        partners = {p.id: p for p in self.env['res.partner'].browse(partner_ids)}
        products = {p.id: p for p in self.env['product.product'].with_context(active_test=False).browse(
            list({r[3] for r in data}))}
        odoo = self._odoo_figures(partner_ids, start, end)

        rows = []
        for pol_id, order_id, partner_id, product_id, date_planned, qty_ordered, qty_received, received_at in data:
            promised = company._asr_local_day(date_planned) if date_planned else False
            received = company._asr_local_day(received_at)
            product = products[product_id]
            qty_ordered = float(qty_ordered or 0.0)
            qty_received = float(qty_received or 0.0)
            on_time = bool(promised and received <= promised)
            in_full = float_compare(qty_received, qty_ordered * (1.0 - tolerance),
                                    precision_rounding=product.uom_id.rounding) >= 0
            otif = on_time and in_full
            figures = odoo.get(partner_id, [None, None])
            rows.append({
                'order_id': orders[order_id], 'partner_id': partners.get(partner_id), 'product_id': product,
                'date_promised': promised, 'date_received': received,
                'days_late': (received - promised).days if promised else 0,
                'qty_ordered': qty_ordered, 'qty_received': qty_received,
                'on_time': on_time, 'in_full': in_full, 'otif': otif,
                'line_count': 1, 'on_time_count': int(on_time), 'in_full_count': int(in_full), 'otif_count': int(otif),
                'odoo_on_time_rate': figures[0], 'odoo_days_to_arrival': figures[1],
            })
        return rows

    def _vendor_rows(self, lines):
        totals = defaultdict(lambda: defaultdict(int))
        info = {}
        for line in lines:
            partner = line['partner_id']
            key = partner.id if partner else 0
            info[key] = line
            bucket = totals[key]
            bucket['line_count'] += 1
            bucket['on_time_count'] += line['on_time_count']
            bucket['in_full_count'] += line['in_full_count']
            bucket['otif_count'] += line['otif_count']
        rows = []
        def partner_name(key):
            partner = info[key]['partner_id']
            return (partner.display_name if partner else '', key)

        for key in sorted(totals, key=partner_name):
            bucket = totals[key]
            count = bucket['line_count'] or 1
            rows.append({
                'partner_id': info[key]['partner_id'], 'line_count': bucket['line_count'],
                'on_time_count': bucket['on_time_count'], 'in_full_count': bucket['in_full_count'],
                'otif_count': bucket['otif_count'],
                'on_time_rate': 100.0 * bucket['on_time_count'] / count,
                'in_full_rate': 100.0 * bucket['in_full_count'] / count,
                'otif_rate': 100.0 * bucket['otif_count'] / count,
                'odoo_on_time_rate': info[key]['odoo_on_time_rate'],
                'odoo_days_to_arrival': info[key]['odoo_days_to_arrival'],
            })
        return rows


class ReportVendorOtifLine(models.TransientModel):
    _name = 'asr.report.vendor.otif.line'
    _description = 'Vendor OTIF Line'
    _order = 'partner_id, order_id, id'

    wizard_id = fields.Many2one('asr.report.vendor.otif', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='wizard_id.currency_id')
    order_id = fields.Many2one('purchase.order', readonly=True)
    partner_id = fields.Many2one('res.partner', 'Vendor', readonly=True)
    product_id = fields.Many2one('product.product', readonly=True)
    date_promised = fields.Date('Promised', readonly=True)
    date_received = fields.Date('Received', readonly=True)
    days_late = fields.Integer(readonly=True, aggregator='avg',
                               help="Received day minus promised day. Odoo's Vendor Delay report compares "
                                    "calendar dates (UTC), not datetimes.")
    qty_ordered = fields.Float('Ordered', digits='Product Unit of Measure', readonly=True)
    qty_received = fields.Float('Received Qty', digits='Product Unit of Measure', readonly=True)
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
    odoo_on_time_rate = fields.Float('Odoo On-Time Rate %', readonly=True, aggregator='avg',
                                     help="Odoo's Vendor Delay report rate for the vendor and period "
                                          "(quantity weighted; Odoo compares dates, not datetimes).")
    odoo_days_to_arrival = fields.Float('Odoo Days to Arrival', readonly=True, aggregator='avg',
                                        help="Average 'Effective Days To Arrival' of Odoo's Purchase Analysis.")
