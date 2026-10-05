# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
"""Report #14 - Scrap and returns by reason.

Live from the move lines of done scrap moves (``stock.move.scrap_id``) and
done return moves (``stock.move.origin_returned_move_id``). The reason is the
move's ``asr_reason_id`` (set by the scrap, the return wizard or the count
wizard), else the scrap's first reason tag. Values are the move value split
over the lines of the move: a scrap or vendor return at its out value, a
customer return at its in value (DESIGN.md §2.1).
"""
from collections import defaultdict

from odoo import fields, models
from odoo.tools import SQL, format_amount

from ..models.stock_scrap import GST_CATEGORIES
from .report_mixin import VALUE_GROUP, col

KINDS = [
    ('scrap', 'Scrap'),
    ('customer_return', 'Customer Return'),
    ('vendor_return', 'Vendor Return'),
    ('other_return', 'Other Return'),
]


class ReportScrapReturn(models.TransientModel):
    _name = 'asr.report.scrap.return'
    _inherit = 'asr.report.mixin'
    _description = 'Scrap and Returns by Reason'
    _asr_title = 'Scrap and Returns by Reason'
    _asr_line_model = 'asr.report.scrap.return.line'
    _asr_uses_engine = False

    kind = fields.Selection([
        ('scrap', 'Scraps'),
        ('return', 'Returns'),
        ('both', 'Scraps and Returns'),
    ], default='both', required=True)
    group_by = fields.Selection([
        ('reason', 'Reason'),
        ('product', 'Product'),
        ('partner', 'Partner'),
        ('month', 'Month'),
    ], default='reason', required=True)
    line_ids = fields.One2many('asr.report.scrap.return.line', 'wizard_id')

    # ------------------------------------------------------------------
    # Columns
    # ------------------------------------------------------------------
    def _asr_columns(self):
        return [
            col('date', 'Date', 'datetime'),
            col('kind', 'Type', width=16),
            col('reference', 'Reference', width=18),
            col('reason_id', 'Reason', 'many2one', width=16),
            col('gst_category', 'GST Category', width=12),
            col('partner_id', 'Partner', 'many2one', width=22),
            col('product_id', 'Product', 'many2one', width=34),
            col('lot_id', 'Lot/Serial', 'many2one'),
            col('quantity', 'Quantity', 'qty', total=True),
            col('uom_id', 'Unit', 'many2one', width=8),
            col('value', 'Value', 'monetary', value=True, total=True),
            col('location_id', 'From', 'many2one', width=20),
            col('location_dest_id', 'To', 'many2one', width=20),
            col('month', 'Month', width=8),
        ]

    def _asr_view_context(self):
        return {'search_default_group_%s' % self.group_by: 1}

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------
    def _period(self):
        date_to = self.date_to or fields.Date.context_today(self)
        date_from = self.date_from or date_to.replace(day=1)
        return date_from, date_to

    def _kind_sql(self):
        if self.kind == 'scrap':
            return SQL("sm.scrap_id IS NOT NULL")
        if self.kind == 'return':
            return SQL("sm.origin_returned_move_id IS NOT NULL")
        return SQL("(sm.scrap_id IS NOT NULL OR sm.origin_returned_move_id IS NOT NULL)")

    def _asr_rows(self):
        self.ensure_one()
        company = self.company_id.sudo()
        date_from, date_to = self._period()
        start = company._asr_day_start_utc(date_from)
        end = company._asr_day_end_utc(date_to)
        tz = company.asr_report_tz or 'UTC'
        locations = self._asr_locations()
        if locations:
            location_sql = SQL("(l.location_id IN %(ids)s OR l.location_dest_id IN %(ids)s)",
                               ids=tuple(locations.ids))
        else:
            location_sql = SQL("TRUE")
        if self.product_ids or self.categ_ids:
            product_sql = SQL("sm.product_id IN %s", tuple(self._asr_products().ids) or (0,))
        else:
            product_sql = SQL("TRUE")
        self.env.cr.execute(SQL("""
            WITH lines AS (
                SELECT sml.id AS line_id, sm.id AS move_id, sm.date, sm.reference, sm.product_id, sml.lot_id,
                       sm.value, sm.is_in, sm.is_out, sm.scrap_id,
                       sml.location_id, sml.location_dest_id,
                       to_char((sm.date AT TIME ZONE 'UTC') AT TIME ZONE %(tz)s, 'YYYY-MM') AS month,
                       COALESCE(sp.partner_id, sm.partner_id) AS partner_id,
                       COALESCE(sm.asr_reason_id, (
                           SELECT rel.stock_scrap_reason_tag_id
                             FROM stock_scrap_stock_scrap_reason_tag_rel rel
                             JOIN stock_scrap_reason_tag t ON t.id = rel.stock_scrap_reason_tag_id
                            WHERE rel.stock_scrap_id = sm.scrap_id
                            ORDER BY t.sequence, t.id LIMIT 1)) AS reason_id,
                       CASE WHEN sm.scrap_id IS NOT NULL THEN 'scrap'
                            WHEN ls.usage = 'customer' THEN 'customer_return'
                            WHEN ld.usage = 'supplier' THEN 'vendor_return'
                            ELSE 'other_return' END AS kind,
                       sml.quantity_product_uom AS qty,
                       (sml.owner_id IS NOT NULL AND sml.owner_id <> %(partner)s) AS excluded,
                       SUM(CASE WHEN sml.owner_id IS NULL OR sml.owner_id = %(partner)s
                                THEN sml.quantity_product_uom ELSE 0 END)
                           OVER (PARTITION BY sm.id) AS move_qty
                  FROM stock_move sm
                  JOIN stock_move_line sml ON sml.move_id = sm.id
                  JOIN stock_location ls ON ls.id = sml.location_id
                  JOIN stock_location ld ON ld.id = sml.location_dest_id
                  LEFT JOIN stock_picking sp ON sp.id = sm.picking_id
                 WHERE sm.state = 'done' AND sm.company_id = %(company)s
                   AND %(kind)s
                   AND sm.date >= %(start)s AND sm.date <= %(end)s
                   AND COALESCE(sml.picked, TRUE)
                   AND COALESCE(sml.quantity_product_uom, 0) <> 0
                   AND %(products)s
            )
            SELECT l.* FROM lines l
             WHERE %(locations)s
             ORDER BY l.date, l.move_id, l.line_id
        """, tz=tz, partner=company.partner_id.id, company=company.id, kind=self._kind_sql(),
             start=start, end=end, products=product_sql, locations=location_sql))
        data = self.env.cr.dictfetchall()
        if not data:
            return []
        products = self._browse_map('product.product', [r['product_id'] for r in data])
        partners = self._browse_map('res.partner', [r['partner_id'] for r in data])
        location_objs = self._browse_map(
            'stock.location', [r['location_id'] for r in data] + [r['location_dest_id'] for r in data])
        lots = self._browse_map('stock.lot', [r['lot_id'] for r in data])
        reasons = self._browse_map('stock.scrap.reason.tag', [r['reason_id'] for r in data])
        kind_labels = dict(KINDS)
        entries = []
        for r in data:
            qty = float(r['qty'] or 0)
            move_qty = float(r['move_qty'] or 0)
            share = (float(r['value'] or 0) * qty / move_qty) if move_qty and not r['excluded'] else 0.0
            value = share if (r['is_in'] or r['is_out']) else 0.0
            product = products.get(r['product_id'])
            reason = reasons.get(r['reason_id'])
            entries.append({
                'date': r['date'],
                'kind': kind_labels.get(r['kind'], r['kind']),
                'kind_code': r['kind'],
                'reference': r['reference'] or '',
                'reason_id': reason,
                'gst_category': reason.asr_gst_category if reason else False,
                'partner_id': partners.get(r['partner_id']),
                'product_id': product,
                'lot_id': lots.get(r['lot_id']),
                'quantity': qty,
                'uom_id': product.uom_id if product else False,
                'value': value,
                'location_id': location_objs.get(r['location_id']),
                'location_dest_id': location_objs.get(r['location_dest_id']),
                'month': r['month'],
            })
        return self._grouped(entries)

    def _group_key(self, entry):
        if self.group_by == 'product':
            record = entry['product_id']
            return ((record.default_code or '', record.display_name, record.id) if record else ('', '', 0),
                    record.display_name if record else self.env._("No product"))
        if self.group_by == 'partner':
            record = entry['partner_id']
            return ((0, record.display_name, record.id) if record else (1, '', 0),
                    record.display_name if record else self.env._("No partner"))
        if self.group_by == 'month':
            return ((entry['month'] or '',), entry['month'] or '')
        record = entry['reason_id']
        return ((0, record.sequence, record.name, record.id) if record else (1, 0, '', 0),
                record.display_name if record else self.env._("No reason"))

    def _grouped(self, entries):
        """Sort by group key, then date; one ``_group`` header with the subtotals before each group."""
        decorated = sorted(((self._group_key(e), e) for e in entries),
                           key=lambda item: (item[0][0], item[1]['date'], item[1]['reference']))
        show_values = self.env.user.has_group(VALUE_GROUP)
        subtotals = defaultdict(lambda: [0.0, 0.0])
        for (key, _title), entry in decorated:
            subtotals[key][0] += entry['quantity']
            subtotals[key][1] += entry['value']
        rows = []
        current = None
        for (key, title), entry in decorated:
            if key != current:
                current = key
                qty, value = subtotals[key]
                label = self.env._("%(title)s - quantity %(qty)s", title=title, qty=f"{qty:,.2f}")
                if show_values:
                    label = self.env._("%(label)s, value %(value)s", label=label,
                                       value=format_amount(self.env, value, self.currency_id))
                rows.append({'_group': label})
            rows.append(entry)
        return rows

    def _browse_map(self, model, ids):
        records = self.env[model].with_context(active_test=False).browse(list({i for i in ids if i}))
        return {record.id: record for record in records}


class ReportScrapReturnLine(models.TransientModel):
    _name = 'asr.report.scrap.return.line'
    _description = 'Scrap and Returns by Reason Line'
    _order = 'date, id'

    wizard_id = fields.Many2one('asr.report.scrap.return', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='wizard_id.currency_id')
    date = fields.Datetime(readonly=True)
    kind = fields.Char('Type', readonly=True)
    kind_code = fields.Selection(KINDS, 'Type Code', readonly=True)
    reference = fields.Char(readonly=True)
    reason_id = fields.Many2one('stock.scrap.reason.tag', readonly=True)
    gst_category = fields.Selection(GST_CATEGORIES, 'GST Category', readonly=True)
    partner_id = fields.Many2one('res.partner', readonly=True)
    product_id = fields.Many2one('product.product', readonly=True)
    lot_id = fields.Many2one('stock.lot', 'Lot/Serial', readonly=True)
    quantity = fields.Float(digits='Product Unit of Measure', readonly=True)
    uom_id = fields.Many2one('uom.uom', 'Unit', readonly=True)
    value = fields.Monetary(currency_field='currency_id', readonly=True,
                            groups='ebshel_stock_reports.group_see_values')
    location_id = fields.Many2one('stock.location', 'From', readonly=True)
    location_dest_id = fields.Many2one('stock.location', 'To', readonly=True)
    month = fields.Char(readonly=True)
