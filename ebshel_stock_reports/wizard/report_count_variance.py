# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
"""Report #13 - Valued count variance.

Live from the move lines of done inventory adjustments (``stock.move.is_inventory``).
One row per move line: the location counted, the system quantity before the
count and the counted quantity (copied onto the move by the module, DESIGN.md
§1.9), the signed difference and its value (the move value split over the
lines of the move). Counts recorded before the module existed carry no
before/counted quantities: they show the difference only and are flagged.
"""

from odoo import fields, models
from odoo.tools import SQL

from .report_mixin import col


class ReportCountVariance(models.TransientModel):
    _name = 'asr.report.count.variance'
    _inherit = 'asr.report.mixin'
    _description = 'Valued Count Variance'
    _asr_title = 'Valued Count Variance'
    _asr_line_model = 'asr.report.count.variance.line'
    _asr_uses_engine = False

    group_by_location = fields.Boolean('Group by Location', default=False)
    line_ids = fields.One2many('asr.report.count.variance.line', 'wizard_id')

    # ------------------------------------------------------------------
    # Columns
    # ------------------------------------------------------------------
    def _asr_columns(self):
        return [
            col('date', 'Date', 'datetime'),
            col('reference', 'Reference', width=18),
            col('location_id', 'Location', 'many2one', width=22),
            col('product_id', 'Product', 'many2one', width=34),
            col('lot_id', 'Lot/Serial', 'many2one'),
            col('reason_id', 'Reason', 'many2one', width=16),
            col('qty_before', 'Qty Before', 'qty'),
            col('qty_counted', 'Counted', 'qty'),
            col('qty_diff', 'Difference', 'qty', total=True,
                help_text="Positive for a count gain, negative for a count loss (product unit)."),
            col('value_gain', 'Gain Value', 'monetary', value=True, total=True),
            col('value_loss', 'Loss Value', 'monetary', value=True, total=True),
            col('value_diff', 'Net Value', 'monetary', value=True, total=True),
            col('unit_cost', 'Unit Cost', 'monetary', value=True),
            col('difference_only', 'Difference Only', 'bool',
                help_text="The count was applied before this module was installed: only the difference is known."),
        ]

    def _asr_view_context(self):
        return {'search_default_group_location': 1} if self.group_by_location else {}

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------
    def _period(self):
        date_to = self.date_to or fields.Date.context_today(self)
        date_from = self.date_from or date_to.replace(day=1)
        return date_from, date_to

    def _asr_rows(self):
        self.ensure_one()
        company = self.company_id.sudo()
        date_from, date_to = self._period()
        start = company._asr_day_start_utc(date_from)
        end = company._asr_day_end_utc(date_to)
        locations = self._asr_locations()
        if locations:
            location_sql = SQL("l.counted_location_id IN %s", tuple(locations.ids))
        else:
            location_sql = SQL("TRUE")
        if self.product_ids or self.categ_ids:
            product_sql = SQL("sm.product_id IN %s", tuple(self._asr_products().ids) or (0,))
        else:
            product_sql = SQL("TRUE")
        self.env.cr.execute(SQL("""
            WITH lines AS (
                SELECT sml.id AS line_id, sm.id AS move_id, sm.date, sm.reference, sm.product_id, sml.lot_id,
                       sm.asr_reason_id, sm.asr_qty_before, sm.asr_qty_counted, sm.value, sm.is_in, sm.is_out,
                       sml.quantity_product_uom AS qty,
                       (ld.company_id IS NOT NULL AND ld.usage IN ('internal', 'transit')) AS is_gain,
                       CASE WHEN ld.company_id IS NOT NULL AND ld.usage IN ('internal', 'transit')
                            THEN sml.location_dest_id ELSE sml.location_id END AS counted_location_id,
                       (sml.owner_id IS NOT NULL AND sml.owner_id <> %(partner)s) AS excluded,
                       SUM(CASE WHEN sml.owner_id IS NULL OR sml.owner_id = %(partner)s
                                THEN sml.quantity_product_uom ELSE 0 END)
                           OVER (PARTITION BY sm.id) AS move_qty
                  FROM stock_move sm
                  JOIN stock_move_line sml ON sml.move_id = sm.id
                  JOIN stock_location ls ON ls.id = sml.location_id
                  JOIN stock_location ld ON ld.id = sml.location_dest_id
                 WHERE sm.state = 'done' AND sm.is_inventory AND sm.company_id = %(company)s
                   AND sm.date >= %(start)s AND sm.date <= %(end)s
                   AND COALESCE(sml.picked, TRUE)
                   AND COALESCE(sml.quantity_product_uom, 0) <> 0
                   AND (ls.company_id IS NOT NULL AND ls.usage IN ('internal', 'transit'))
                       <> (ld.company_id IS NOT NULL AND ld.usage IN ('internal', 'transit'))
                   AND %(products)s
            )
            SELECT l.* FROM lines l
             WHERE %(locations)s
             ORDER BY l.counted_location_id, l.date, l.move_id, l.line_id
        """, partner=company.partner_id.id, company=company.id, start=start, end=end,
             products=product_sql, locations=location_sql))
        data = self.env.cr.dictfetchall()
        if not data:
            return []
        products = self._browse_map('product.product', [r['product_id'] for r in data])
        location_objs = self._browse_map('stock.location', [r['counted_location_id'] for r in data])
        lots = self._browse_map('stock.lot', [r['lot_id'] for r in data if r['lot_id']])
        reasons = self._browse_map('stock.scrap.reason.tag', [r['asr_reason_id'] for r in data if r['asr_reason_id']])
        rows = []
        if not self.group_by_location:
            data.sort(key=lambda r: (r['date'], r['move_id'], r['line_id']))
        current_location = None
        for r in data:
            qty = float(r['qty'] or 0)
            move_qty = float(r['move_qty'] or 0)
            share = (float(r['value'] or 0) * qty / move_qty) if move_qty and not r['excluded'] else 0.0
            if r['is_gain']:
                qty_diff = qty
                value_diff = share if r['is_in'] else 0.0
            else:
                qty_diff = -qty
                value_diff = -share if r['is_out'] else 0.0
            before = float(r['asr_qty_before'] or 0)
            counted = float(r['asr_qty_counted'] or 0)
            difference_only = not before and not counted
            location = location_objs.get(r['counted_location_id'])
            if self.group_by_location and current_location != r['counted_location_id']:
                current_location = r['counted_location_id']
                rows.append({'_group': location.complete_name if location else ''})
            rows.append({
                'date': r['date'],
                'reference': r['reference'] or '',
                'location_id': location,
                'product_id': products.get(r['product_id']),
                'lot_id': lots.get(r['lot_id']),
                'reason_id': reasons.get(r['asr_reason_id']),
                'qty_before': None if difference_only else before,
                'qty_counted': None if difference_only else counted,
                'qty_diff': qty_diff,
                'value_gain': value_diff if value_diff > 0 else 0.0,
                'value_loss': value_diff if value_diff < 0 else 0.0,
                'value_diff': value_diff,
                'unit_cost': abs(value_diff) / qty if qty else 0.0,
                'difference_only': difference_only,
            })
        return rows

    def _browse_map(self, model, ids):
        records = self.env[model].with_context(active_test=False).browse(list({i for i in ids if i}))
        return {record.id: record for record in records}


class ReportCountVarianceLine(models.TransientModel):
    _name = 'asr.report.count.variance.line'
    _description = 'Valued Count Variance Line'
    _order = 'date, id'

    wizard_id = fields.Many2one('asr.report.count.variance', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='wizard_id.currency_id')
    date = fields.Datetime(readonly=True)
    reference = fields.Char(readonly=True)
    location_id = fields.Many2one('stock.location', readonly=True)
    product_id = fields.Many2one('product.product', readonly=True)
    lot_id = fields.Many2one('stock.lot', 'Lot/Serial', readonly=True)
    reason_id = fields.Many2one('stock.scrap.reason.tag', readonly=True)
    qty_before = fields.Float(digits='Product Unit of Measure', readonly=True, aggregator=None)
    qty_counted = fields.Float('Counted', digits='Product Unit of Measure', readonly=True, aggregator=None)
    qty_diff = fields.Float('Difference', digits='Product Unit of Measure', readonly=True)
    value_gain = fields.Monetary('Gain Value', currency_field='currency_id', readonly=True,
                                 groups='ebshel_stock_reports.group_see_values')
    value_loss = fields.Monetary('Loss Value', currency_field='currency_id', readonly=True,
                                 groups='ebshel_stock_reports.group_see_values')
    value_diff = fields.Monetary('Net Value', currency_field='currency_id', readonly=True,
                                 groups='ebshel_stock_reports.group_see_values')
    unit_cost = fields.Monetary(currency_field='currency_id', readonly=True, aggregator=None,
                                groups='ebshel_stock_reports.group_see_values')
    difference_only = fields.Boolean(readonly=True)
