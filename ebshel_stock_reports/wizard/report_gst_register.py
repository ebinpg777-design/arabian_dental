# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
"""Report #19 - GST stock register (Rule 56 of the CGST Rules).

One row per product, grouped by the GST stock class of its category (raw
material, work in progress, finished goods, ...). The quantity columns come
from the daily movement summary: opening, received, issued for supply,
consumed in manufacturing, returned to vendor, the section 17(5)(h) losses
(lost, stolen, destroyed, written off, gift, free sample) split by the reason
tag of the scrap / count / other outgoing moves, other losses, closing. The
opening and closing values are the engine's closing values (ratio method for
a location set, DESIGN.md §2.1).

The engine summary used here (``engine_summary``) is shared with report #20.
"""
from collections import defaultdict
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import SQL

from ..models.product import GST_STOCK_CLASSES
from ..models.stock_move_daily import LEDGER_IN_TYPES, LEDGER_OUT_TYPES
from .report_mixin import col

SUMMARY_TYPES = LEDGER_IN_TYPES + LEDGER_OUT_TYPES + ('transfer_in', 'transfer_out')
IN_TYPES = LEDGER_IN_TYPES + ('transfer_in',)
LOSS_TYPES = ('scrap', 'count_loss', 'other_out')
LOSS_CATEGORIES = ['lost', 'stolen', 'destroyed', 'written_off', 'gift', 'free_sample']
GST_CLASS_ORDER = [key for key, _label in GST_STOCK_CLASSES]
GST_CLASS_LABELS = dict(GST_STOCK_CLASSES)


def engine_summary(wizard, date_from, date_to, product_ids=None):
    """Per-product quantities and values of a period from the daily tables.

    Returns ``{product_id: data}`` where ``data`` has ``moves`` ({move_type:
    qty}), ``values`` ({move_type: value}), ``qty_opening``, ``qty_closing``,
    ``value_opening``, ``value_closing``.  Products are those with movement in
    the period or a non-zero closing quantity, restricted to ``product_ids``
    when given (``None`` = every product).  Location and warehouse filters of
    the wizard are honoured: at location level the closing quantity is the
    quant-anchored level quantity and the values follow Odoo's ratio method.
    """
    company = wizard.company_id.sudo()
    Ledger = wizard.env['asr.report.stock.ledger']
    locations = wizard._asr_locations()
    location_level = bool(wizard.location_ids or wizard.warehouse_ids)
    if product_ids is not None and not product_ids:
        return {}
    wizard.env.cr.execute(SQL("""
        SELECT d.product_id, d.move_type, SUM(d.qty_in), SUM(d.qty_out), SUM(d.value_in), SUM(d.value_out)
          FROM asr_stock_move_daily d
         WHERE d.company_id = %s AND d.day >= %s AND d.day <= %s AND d.move_type IN %s
           AND %s AND %s
         GROUP BY d.product_id, d.move_type
    """, company.id, date_from, date_to, SUMMARY_TYPES,
         Ledger._location_sql(locations), Ledger._product_sql(product_ids)))
    moves = defaultdict(lambda: defaultdict(float))
    values = defaultdict(lambda: defaultdict(float))
    for pid, mtype, qty_in, qty_out, value_in, value_out in wizard.env.cr.fetchall():
        if mtype in IN_TYPES:
            moves[pid][mtype] += float(qty_in or 0)
            values[pid][mtype] += float(value_in or 0)
        else:
            moves[pid][mtype] += float(qty_out or 0)
            values[pid][mtype] += float(value_out or 0)
    closing_qty = Ledger._level_qty_at(company, locations if location_level else None, product_ids, date_to)
    candidate_ids = set(moves) | {pid for pid, q in closing_qty.items() if q}
    if product_ids is not None:
        candidate_ids &= set(product_ids)
    if not candidate_ids:
        return {}
    ValueDaily = wizard.env['asr.stock.value.daily'].sudo()
    day_before = date_from - timedelta(days=1)
    company_close = ValueDaily._closing_at(company, list(candidate_ids), date_to)
    company_open = ValueDaily._closing_at(company, list(candidate_ids), day_before)
    products = wizard.env['product.product'].with_context(active_test=False).browse(sorted(candidate_ids))
    products.fetch(['uom_id', 'standard_price'])
    result = {}
    for product in products:
        pid = product.id
        uom = product.uom_id
        period = moves.get(pid, {})
        net = sum(q if t in IN_TYPES else -q for t, q in period.items())
        c_qty, c_value, _c = company_close.get(pid, (0.0, 0.0, 0.0))
        o_qty, o_value, _o = company_open.get(pid, (0.0, 0.0, 0.0))
        if location_level:
            q_close = uom.round(closing_qty.get(pid, 0.0))
            q_open = uom.round(q_close - net)
            std = product.with_company(company).standard_price
            v_close = ValueDaily._location_value(c_value, c_qty, q_close, std)
            v_open = ValueDaily._location_value(o_value, o_qty, q_open, std)
        else:
            q_close, v_close = uom.round(c_qty), c_value
            q_open, v_open = uom.round(o_qty), o_value
        result[pid] = {
            'product': product, 'moves': period, 'values': values.get(pid, {}),
            'qty_opening': q_open, 'qty_closing': q_close,
            'value_opening': v_open, 'value_closing': v_close,
        }
    return result


class ReportGstRegister(models.TransientModel):
    _name = 'asr.report.gst.register'
    _inherit = 'asr.report.mixin'
    _description = 'GST Stock Register'
    _asr_title = 'GST Stock Register'
    _asr_line_model = 'asr.report.gst.register.line'

    period = fields.Selection([
        ('month', 'Month'),
        ('quarter', 'Quarter'),
        ('year', 'Financial Year (April - March)'),
    ], help="Sets the dates to the month, quarter or financial year that contains the end date.")
    hide_empty = fields.Boolean('Hide products without movement', default=True)
    line_ids = fields.One2many('asr.report.gst.register.line', 'wizard_id')

    @api.onchange('period')
    def _onchange_period(self):
        if not self.period:
            return
        base = self.date_to or fields.Date.context_today(self)
        if self.period == 'month':
            start = base.replace(day=1)
            end = start + relativedelta(months=1, days=-1)
        elif self.period == 'quarter':
            start = base.replace(day=1, month=3 * ((base.month - 1) // 3) + 1)
            end = start + relativedelta(months=3, days=-1)
        else:
            year = base.year if base.month >= 4 else base.year - 1
            start = base.replace(year=year, month=4, day=1)
            end = start + relativedelta(years=1, days=-1)
        self.date_from, self.date_to = start, end

    # ------------------------------------------------------------------
    # GST switch
    # ------------------------------------------------------------------
    def _asr_check(self):
        res = super()._asr_check()
        if not self.company_id.asr_india_gst:
            raise UserError(self.env._("The India GST reports are disabled for %s. Enable them in "
                                       "Inventory > Configuration > Settings > Advanced Stock Reports.",
                                       self.company_id.name))
        return res

    # ------------------------------------------------------------------
    # Columns
    # ------------------------------------------------------------------
    def _asr_columns(self):
        return [
            col('gst_class', 'GST Stock Class', width=18),
            col('product_id', 'Product', 'many2one', width=34),
            col('default_code', 'Internal Reference', width=14),
            col('hsn_code', 'HSN Code', width=10),
            col('uom_id', 'Unit', 'many2one', width=8),
            col('qty_opening', 'Opening Qty', 'qty', total=True),
            col('qty_received', 'Received', 'qty', total=True,
                help_text="Receipts, customer returns, production, count gains, transfers in and other receipts."),
            col('qty_issued', 'Issued for Supply', 'qty', total=True),
            col('qty_consumed', 'Consumed in Manufacturing', 'qty', total=True),
            col('qty_vendor_return', 'Returned to Vendor', 'qty', total=True),
            col('qty_lost', 'Lost', 'qty', total=True),
            col('qty_stolen', 'Stolen', 'qty', total=True),
            col('qty_destroyed', 'Destroyed', 'qty', total=True),
            col('qty_written_off', 'Written Off', 'qty', total=True),
            col('qty_gift', 'Gift', 'qty', total=True),
            col('qty_free_sample', 'Free Sample', 'qty', total=True),
            col('qty_other_loss', 'Other Loss', 'qty', total=True,
                help_text="Scraps, count losses and other issues without a reason or with a reason "
                          "outside the section 17(5)(h) categories."),
            col('qty_transfer_out', 'Transferred Out', 'qty', total=True),
            col('qty_closing', 'Closing Qty', 'qty', total=True),
            col('value_opening', 'Opening Value', 'monetary', value=True, total=True),
            col('value_closing', 'Closing Value', 'monetary', value=True, total=True),
            col('note', 'Note', width=30),
        ]

    def _asr_view_context(self):
        return {'search_default_group_class': 1}

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------
    def _period(self):
        date_to = self.date_to or fields.Date.context_today(self)
        date_from = self.date_from or date_to.replace(day=1)
        return date_from, date_to

    def _filtered_product_ids(self):
        if self.product_ids or self.categ_ids:
            return self._asr_products().ids
        return None

    def _loss_by_category(self, date_from, date_to, product_ids):
        """{product_id: {gst_category: qty}} of the scrap / count-loss / other-out move lines of the
        period, live from the moves, by the GST category of the move's reason tag (None = no reason)."""
        company = self.company_id.sudo()
        Ledger = self.env['asr.report.stock.ledger']
        locations = self._asr_locations()
        location_level = bool(self.location_ids or self.warehouse_ids)
        valued = self.env['stock.location'].with_context(active_test=False).search(
            [('company_id', '=', company.id), ('usage', 'in', ('internal', 'transit'))])
        level_ids = tuple((locations if location_level else valued).ids) or (0,)
        valued_ids = tuple(valued.ids) or (0,)
        self.env.cr.execute(SQL("""
            SELECT sm.product_id, t.asr_gst_category, SUM(sml.quantity_product_uom)
              FROM stock_move_line sml
              JOIN stock_move sm ON sm.id = sml.move_id
              JOIN stock_location ld ON ld.id = sml.location_dest_id
              LEFT JOIN stock_scrap_reason_tag t ON t.id = sm.asr_reason_id
             WHERE sm.state = 'done' AND sm.company_id = %(company)s
               AND sm.date >= %(start)s AND sm.date <= %(end)s
               AND (sml.owner_id IS NULL OR sml.owner_id = %(partner)s)
               AND COALESCE(sml.picked, TRUE)
               AND NOT COALESCE(sm.is_dropship, FALSE)
               AND sml.location_id IN %(level)s AND sml.location_dest_id NOT IN %(level)s
               AND (sm.scrap_id IS NOT NULL OR sm.is_inventory
                    OR (ld.usage NOT IN ('customer', 'supplier', 'production')
                        AND sml.location_dest_id NOT IN %(valued)s))
               AND %(products)s
             GROUP BY sm.product_id, t.asr_gst_category
        """, company=company.id, start=company._asr_day_start_utc(date_from), end=company._asr_day_end_utc(date_to),
             partner=company.partner_id.id, level=level_ids, valued=valued_ids,
             products=Ledger._product_sql(product_ids, 'sm')))
        res = defaultdict(lambda: defaultdict(float))
        for pid, category, qty in self.env.cr.fetchall():
            res[pid][category] += float(qty or 0)
        return res

    def _asr_rows(self):
        self.ensure_one()
        self.env.flush_all()  # the SQL below reads stored computed columns
        date_from, date_to = self._period()
        product_ids = self._filtered_product_ids()
        summary = engine_summary(self, date_from, date_to, product_ids)
        if not summary:
            return []
        losses = self._loss_by_category(date_from, date_to, list(summary))
        has_hsn = 'l10n_in_hsn_code' in self.env['product.template']._fields
        products = self.env['product.product'].with_context(active_test=False).browse(list(summary))
        products.fetch(
            ['default_code', 'categ_id', 'uom_id', 'display_name'] + (['l10n_in_hsn_code'] if has_hsn else []))
        by_class = defaultdict(list)
        for product in products:
            gst_class = product.categ_id.asr_gst_stock_class or 'other'
            by_class[gst_class].append(product)
        rows = []
        for gst_class in GST_CLASS_ORDER:
            group = sorted(by_class.get(gst_class, []), key=lambda p: (p.default_code or '', p.display_name, p.id))
            if not group:
                continue
            rows.append({'_group': GST_CLASS_LABELS[gst_class]})
            for product in group:
                data = summary[product.id]
                m = data['moves']
                uom = product.uom_id
                q_open, q_close = data['qty_opening'], data['qty_closing']
                if self.hide_empty and not m and uom.is_zero(q_open) and uom.is_zero(q_close):
                    continue
                loss_total = sum(m.get(t, 0.0) for t in LOSS_TYPES)
                by_cat = losses.get(product.id, {})
                categorised = {c: by_cat.get(c, 0.0) for c in LOSS_CATEGORIES}
                other_loss = loss_total - sum(categorised.values())
                note = ''
                if not product.categ_id.asr_gst_stock_class:
                    note = self.env._("Category %s has no GST stock class", product.categ_id.display_name)
                rows.append({
                    'gst_class': gst_class,
                    'product_id': product, 'default_code': product.default_code or '',
                    'categ_id': product.categ_id,
                    'hsn_code': (product.l10n_in_hsn_code or '') if has_hsn else '',
                    'uom_id': uom,
                    'qty_opening': q_open,
                    'qty_received': sum(m.get(t, 0.0) for t in IN_TYPES),
                    'qty_issued': m.get('delivery', 0.0),
                    'qty_consumed': m.get('consumption', 0.0),
                    'qty_vendor_return': m.get('vendor_return', 0.0),
                    'qty_lost': categorised['lost'],
                    'qty_stolen': categorised['stolen'],
                    'qty_destroyed': categorised['destroyed'],
                    'qty_written_off': categorised['written_off'],
                    'qty_gift': categorised['gift'],
                    'qty_free_sample': categorised['free_sample'],
                    'qty_other_loss': other_loss,
                    'qty_transfer_out': m.get('transfer_out', 0.0),
                    'qty_closing': q_close,
                    'value_opening': data['value_opening'],
                    'value_closing': data['value_closing'],
                    'note': note,
                })
        return rows


class ReportGstRegisterLine(models.TransientModel):
    _name = 'asr.report.gst.register.line'
    _description = 'GST Stock Register Line'
    _order = 'gst_class, default_code, product_id, id'

    wizard_id = fields.Many2one('asr.report.gst.register', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='wizard_id.currency_id')
    gst_class = fields.Selection(GST_STOCK_CLASSES, 'GST Stock Class', readonly=True)
    product_id = fields.Many2one('product.product', readonly=True)
    default_code = fields.Char('Internal Reference', readonly=True)
    categ_id = fields.Many2one('product.category', 'Category', readonly=True)
    hsn_code = fields.Char('HSN Code', readonly=True)
    uom_id = fields.Many2one('uom.uom', 'Unit', readonly=True)
    qty_opening = fields.Float('Opening Qty', digits='Product Unit of Measure', readonly=True)
    qty_received = fields.Float('Received', digits='Product Unit of Measure', readonly=True)
    qty_issued = fields.Float('Issued for Supply', digits='Product Unit of Measure', readonly=True)
    qty_consumed = fields.Float('Consumed in Manufacturing', digits='Product Unit of Measure', readonly=True)
    qty_vendor_return = fields.Float('Returned to Vendor', digits='Product Unit of Measure', readonly=True)
    qty_lost = fields.Float('Lost', digits='Product Unit of Measure', readonly=True)
    qty_stolen = fields.Float('Stolen', digits='Product Unit of Measure', readonly=True)
    qty_destroyed = fields.Float('Destroyed', digits='Product Unit of Measure', readonly=True)
    qty_written_off = fields.Float('Written Off', digits='Product Unit of Measure', readonly=True)
    qty_gift = fields.Float('Gift', digits='Product Unit of Measure', readonly=True)
    qty_free_sample = fields.Float('Free Sample', digits='Product Unit of Measure', readonly=True)
    qty_other_loss = fields.Float('Other Loss', digits='Product Unit of Measure', readonly=True)
    qty_transfer_out = fields.Float('Transferred Out', digits='Product Unit of Measure', readonly=True)
    qty_closing = fields.Float('Closing Qty', digits='Product Unit of Measure', readonly=True)
    value_opening = fields.Monetary('Opening Value', currency_field='currency_id', readonly=True,
                                    groups='ebshel_stock_reports.group_see_values')
    value_closing = fields.Monetary('Closing Value', currency_field='currency_id', readonly=True,
                                    groups='ebshel_stock_reports.group_see_values')
    note = fields.Char(readonly=True)
