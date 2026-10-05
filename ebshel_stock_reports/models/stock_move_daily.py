# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
"""Daily movement summary.

One row per (company, day, product, location, move type), built by SQL from
the done move lines. The valued-location rule and the owner exclusion are the
ones ``stock_account`` applies (see DESIGN.md §1.3), the day is the move date
in the company's reporting timezone, and each line carries the share of its
move's value proportional to its quantity, so a move split over several
locations still sums to ``stock.move.value``.
"""
from datetime import date

from odoo import fields, models
from odoo.tools import SQL

FULL_REBUILD_DAY = date(1900, 1, 1)

# Movement types that change the quantity the company owns.  Transfers move
# stock between two valued locations, consignment is not the company's stock
# and dropship never touches a valued location.
LEDGER_IN_TYPES = ('receipt', 'customer_return', 'production', 'count_gain', 'other_in')
LEDGER_OUT_TYPES = ('delivery', 'vendor_return', 'consumption', 'scrap', 'count_loss', 'other_out')
LEDGER_TYPES = LEDGER_IN_TYPES + LEDGER_OUT_TYPES
ISSUE_TYPES = ('delivery', 'consumption')

MOVE_TYPES = [
    ('receipt', 'Receipt'),
    ('customer_return', 'Customer Return'),
    ('production', 'Production'),
    ('count_gain', 'Count Gain'),
    ('other_in', 'Other In'),
    ('delivery', 'Delivery'),
    ('vendor_return', 'Vendor Return'),
    ('consumption', 'Consumption'),
    ('scrap', 'Scrap'),
    ('count_loss', 'Count Loss'),
    ('other_out', 'Other Out'),
    ('transfer_in', 'Transfer In'),
    ('transfer_out', 'Transfer Out'),
    ('consignment', 'Consignment'),
    ('dropship', 'Dropship'),
]


class StockMoveDaily(models.Model):
    _name = 'asr.stock.move.daily'
    _description = 'Daily Stock Movement Summary'
    _order = 'day desc, id desc'
    _rec_name = 'product_id'
    _log_access = False

    company_id = fields.Many2one('res.company', required=True, readonly=True, index=True)
    day = fields.Date(required=True, readonly=True)
    product_id = fields.Many2one('product.product', required=True, readonly=True, index=True)
    categ_id = fields.Many2one('product.category', string='Product Category', readonly=True, index=True)
    location_id = fields.Many2one('stock.location', required=True, readonly=True, index=True)
    warehouse_id = fields.Many2one('stock.warehouse', readonly=True, index=True)
    move_type = fields.Selection(MOVE_TYPES, required=True, readonly=True)
    qty_in = fields.Float('Quantity In', digits='Product Unit of Measure', readonly=True)
    qty_out = fields.Float('Quantity Out', digits='Product Unit of Measure', readonly=True)
    value_in = fields.Monetary(currency_field='currency_id', readonly=True,
                               groups='ebshel_stock_reports.group_see_values')
    value_out = fields.Monetary(currency_field='currency_id', readonly=True,
                                groups='ebshel_stock_reports.group_see_values')
    line_count = fields.Integer('Move Lines', readonly=True)
    currency_id = fields.Many2one(related='company_id.currency_id')

    _company_product_day_idx = models.Index("(company_id, product_id, day)")
    _company_day_idx = models.Index("(company_id, day, move_type)")
    _location_day_idx = models.Index("(location_id, day)")

    # ------------------------------------------------------------------
    # Builder
    # ------------------------------------------------------------------
    def _delete_from(self, company, product_from_day):
        """Drop the rows of ``product_from_day`` ({product_id: first day}) from that day on."""
        if not product_from_day:
            return
        values = SQL(', ').join(SQL('(%s, %s::date)', pid, day) for pid, day in product_from_day.items())
        self.env.cr.execute(SQL("""
            DELETE FROM asr_stock_move_daily d
                  USING (VALUES %s) AS k(product_id, day_from)
                  WHERE d.company_id = %s
                    AND d.product_id = k.product_id
                    AND (d.day >= k.day_from OR k.day_from <= %s)
        """, values, company.id, FULL_REBUILD_DAY))

    def _insert_from(self, company, product_from_day):
        """Insert the rows of the given products from their first day on.

        ``product_from_day`` maps product ids to the first (local) day to rebuild.
        """
        if not product_from_day:
            return
        tz = company.asr_report_tz or 'UTC'
        values = SQL(', ').join(
            SQL('(%s, %s::timestamp)', pid, company._asr_day_start_utc(day))
            for pid, day in product_from_day.items()
        )
        self.env.cr.execute(SQL("""
            INSERT INTO asr_stock_move_daily
                (company_id, day, product_id, categ_id, location_id, warehouse_id, move_type,
                 qty_in, qty_out, value_in, value_out, line_count)
            WITH lines AS (
                SELECT sm.id AS move_id,
                       sm.company_id,
                       sm.product_id,
                       pt.categ_id,
                       ((sm.date AT TIME ZONE 'UTC') AT TIME ZONE %(tz)s)::date AS day,
                       sm.value,
                       sm.is_in,
                       sm.is_out,
                       sm.is_dropship,
                       sm.is_inventory,
                       sm.scrap_id,
                       sml.quantity_product_uom AS qty,
                       sml.location_id AS src_id,
                       sml.location_dest_id AS dst_id,
                       ls.warehouse_id AS src_wh,
                       ld.warehouse_id AS dst_wh,
                       (ls.company_id IS NOT NULL AND ls.usage IN ('internal', 'transit')) AS src_valued,
                       (ld.company_id IS NOT NULL AND ld.usage IN ('internal', 'transit')) AS dst_valued,
                       ls.usage AS src_usage,
                       ld.usage AS dst_usage,
                       (sml.owner_id IS NOT NULL AND sml.owner_id <> rc.partner_id) AS excluded
                  FROM stock_move sm
                  JOIN (VALUES %(keys)s) AS k(product_id, from_ts)
                    ON k.product_id = sm.product_id
                  JOIN stock_move_line sml ON sml.move_id = sm.id
                  JOIN stock_location ls ON ls.id = sml.location_id
                  JOIN stock_location ld ON ld.id = sml.location_dest_id
                  JOIN product_product pp ON pp.id = sm.product_id
                  JOIN product_template pt ON pt.id = pp.product_tmpl_id
                  JOIN res_company rc ON rc.id = sm.company_id
                 WHERE sm.state = 'done'
                   AND sm.company_id = %(company)s
                   AND sm.date >= k.from_ts
                   AND COALESCE(sml.picked, TRUE)
                   AND COALESCE(sml.quantity_product_uom, 0) <> 0
            ), classified AS (
                SELECT l.*,
                       CASE WHEN is_dropship THEN 'dropship'
                            WHEN excluded THEN 'consignment'
                            WHEN NOT src_valued AND dst_valued THEN 'in'
                            WHEN src_valued AND NOT dst_valued THEN 'out'
                            WHEN src_valued AND dst_valued THEN 'transfer'
                       END AS dir
                  FROM lines l
            ), valued AS (
                SELECT c.*,
                       SUM(CASE WHEN dir IN ('in', 'out', 'dropship') THEN qty ELSE 0 END)
                           OVER (PARTITION BY move_id, dir) AS move_dir_qty
                  FROM classified c
                 WHERE dir IS NOT NULL
            ), rows AS (
                -- incoming to a valued location
                SELECT company_id, day, product_id, categ_id, dst_id AS location_id, dst_wh AS warehouse_id,
                       CASE WHEN is_inventory THEN 'count_gain'
                            WHEN src_usage = 'supplier' THEN 'receipt'
                            WHEN src_usage = 'customer' THEN 'customer_return'
                            WHEN src_usage = 'production' THEN 'production'
                            ELSE 'other_in' END AS move_type,
                       qty AS qty_in, 0::numeric AS qty_out,
                       CASE WHEN is_in AND move_dir_qty <> 0 THEN value * qty / move_dir_qty ELSE 0 END AS value_in,
                       0::numeric AS value_out
                  FROM valued WHERE dir = 'in'
                UNION ALL
                -- outgoing from a valued location
                SELECT company_id, day, product_id, categ_id, src_id, src_wh,
                       CASE WHEN scrap_id IS NOT NULL THEN 'scrap'
                            WHEN is_inventory THEN 'count_loss'
                            WHEN dst_usage = 'customer' THEN 'delivery'
                            WHEN dst_usage = 'supplier' THEN 'vendor_return'
                            WHEN dst_usage = 'production' THEN 'consumption'
                            ELSE 'other_out' END,
                       0, qty,
                       0,
                       CASE WHEN is_out AND move_dir_qty <> 0 THEN value * qty / move_dir_qty ELSE 0 END
                  FROM valued WHERE dir = 'out'
                UNION ALL
                -- transfer between two valued locations: one row on each side, no value
                SELECT company_id, day, product_id, categ_id, src_id, src_wh, 'transfer_out', 0, qty, 0, 0
                  FROM valued WHERE dir = 'transfer'
                UNION ALL
                SELECT company_id, day, product_id, categ_id, dst_id, dst_wh, 'transfer_in', qty, 0, 0, 0
                  FROM valued WHERE dir = 'transfer'
                UNION ALL
                -- consignment (owned by somebody else): quantities only
                SELECT company_id, day, product_id, categ_id, dst_id, dst_wh, 'consignment', qty, 0, 0, 0
                  FROM valued WHERE dir = 'consignment' AND dst_valued
                UNION ALL
                SELECT company_id, day, product_id, categ_id, src_id, src_wh, 'consignment', 0, qty, 0, 0
                  FROM valued WHERE dir = 'consignment' AND src_valued
                UNION ALL
                -- dropship: never in a valued location; kept for the registers
                SELECT company_id, day, product_id, categ_id, dst_id, dst_wh, 'dropship', 0, qty, 0,
                       CASE WHEN move_dir_qty <> 0 THEN value * qty / move_dir_qty ELSE 0 END
                  FROM valued WHERE dir = 'dropship'
            )
            SELECT company_id, day, product_id, categ_id, location_id, warehouse_id, move_type,
                   SUM(qty_in), SUM(qty_out), SUM(value_in), SUM(value_out), COUNT(*)
              FROM rows
             GROUP BY company_id, day, product_id, categ_id, location_id, warehouse_id, move_type
        """, tz=tz, keys=values, company=company.id))

    def _rebuild(self, company, product_from_day):
        self._delete_from(company, product_from_day)
        self._insert_from(company, product_from_day)

    # ------------------------------------------------------------------
    # Readers shared by the reports
    # ------------------------------------------------------------------
    def _location_filter_sql(self, warehouses=None, locations=None, alias='d'):
        """SQL fragment restricting rows to a warehouse / location set (child_of for locations)."""
        clauses = []
        if warehouses:
            clauses.append(SQL("%s.warehouse_id IN %s", SQL.identifier(alias), tuple(warehouses.ids)))
        if locations:
            all_locations = self.env['stock.location'].with_context(active_test=False).search(
                [('id', 'child_of', locations.ids)])
            clauses.append(SQL("%s.location_id IN %s", SQL.identifier(alias), tuple(all_locations.ids)))
        if not clauses:
            return SQL("TRUE")
        return SQL(" AND ").join(clauses)
