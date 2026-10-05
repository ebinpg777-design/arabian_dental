# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
"""Report #5 - Slow and non-moving stock.

One row per product with stock on hand (or every storable product on request):
the last issue day and the last receipt day from the daily movement summary,
the days since the last issue, the quantity and value on hand at the report
date, and a status. An "issue" is a delivery or a consumption only (DESIGN.md
§2.3). With a warehouse or location filter the quantities are those of the
selected locations and the value is Odoo's ratio of the company value.
"""

from odoo import api, fields, models
from odoo.tools import SQL

from ..models.stock_move_daily import ISSUE_TYPES, LEDGER_IN_TYPES
from .report_mixin import col

STATUS = [
    ('active', 'Active'),
    ('slow', 'Slow Moving'),
    ('non_moving', 'Non-Moving'),
]


class ReportSlowMoving(models.TransientModel):
    _name = 'asr.report.slow.moving'
    _inherit = 'asr.report.mixin'
    _description = 'Slow / Non-Moving Stock'
    _asr_title = 'Slow / Non-Moving Stock'
    _asr_line_model = 'asr.report.slow.moving.line'

    date_from = fields.Date(default=False)  # one reference date: date_to
    status = fields.Selection([
        ('all', 'All products'),
        ('slow', 'Slow moving only'),
        ('non_moving', 'Non-moving only'),
        ('slow_non_moving', 'Slow and non-moving'),
    ], default='slow_non_moving', required=True, string='Show')
    include_without_stock = fields.Boolean(
        'Include products without stock', default=False,
        help="Also list storable products with no quantity on hand at the report date.")
    slow_days = fields.Integer(compute='_compute_slow_days')
    line_ids = fields.One2many('asr.report.slow.moving.line', 'wizard_id')

    @api.depends('company_id')
    def _compute_slow_days(self):
        for wizard in self:
            wizard.slow_days = wizard.company_id.sudo().asr_slow_days or 90

    # ------------------------------------------------------------------
    # Columns
    # ------------------------------------------------------------------
    def _asr_columns(self):
        return [
            col('product_id', 'Product', 'many2one', width=34),
            col('default_code', 'Internal Reference', width=14),
            col('categ_id', 'Category', 'many2one', width=20),
            col('uom_id', 'Unit', 'many2one', width=8),
            col('last_issue_day', 'Last Issue', 'date'),
            col('last_receipt_day', 'Last Receipt', 'date'),
            col('days_since_issue', 'Days Since Issue', 'int'),
            col('qty_on_hand', 'On Hand', 'qty', total=True),
            col('value_on_hand', 'Value On Hand', 'monetary', value=True, total=True),
            col('status', 'Status'),
            col('abc_class', 'ABC', width=6),
            col('xyz_class', 'XYZ', width=6),
            col('combined_class', 'Class', width=8),
        ]

    def _asr_filter_text(self):
        text = super()._asr_filter_text()
        days = self.env._("Slow after %s days", self.slow_days)
        return f"{text} | {days}" if text else days

    def _status_label(self, key):
        return dict(STATUS).get(key, key)

    def _asr_xlsx_cell(self, sheet, r, c, value, column, formats, fallback):
        if column['key'] == 'status':
            value = self.env._(self._status_label(value)) if value else ''
        return super()._asr_xlsx_cell(sheet, r, c, value, column, formats, fallback)

    def _asr_serialise_row(self, row, columns):
        out = super()._asr_serialise_row(row, columns)
        if out.get('status'):
            out['status'] = self.env._(self._status_label(out['status']))
        return out

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------
    def _filtered_product_ids(self):
        if self.product_ids or self.categ_ids:
            return self._asr_products().ids
        return None

    def _product_sql(self, product_ids, alias='d'):
        if product_ids is None:
            return SQL("TRUE")
        if not product_ids:
            return SQL("FALSE")
        return SQL("%s.product_id IN %s", SQL.identifier(alias), tuple(product_ids))

    def _last_days(self, company, day, locations, location_level, product_ids):
        """{product_id: (last issue day, last receipt day)} at or before ``day``."""
        in_types = LEDGER_IN_TYPES + (('transfer_in',) if location_level else ())
        location_sql = SQL("d.location_id IN %s", tuple(locations.ids)) if location_level else SQL("TRUE")
        self.env.cr.execute(SQL("""
            SELECT d.product_id,
                   MAX(CASE WHEN d.move_type IN %s AND d.qty_out <> 0 THEN d.day END),
                   MAX(CASE WHEN d.move_type IN %s AND d.qty_in <> 0 THEN d.day END)
              FROM asr_stock_move_daily d
             WHERE d.company_id = %s AND d.day <= %s AND d.move_type IN %s
               AND %s AND %s
             GROUP BY d.product_id
        """, ISSUE_TYPES, in_types, company.id, day, ISSUE_TYPES + in_types,
             location_sql, self._product_sql(product_ids)))
        return {pid: (issue, receipt) for pid, issue, receipt in self.env.cr.fetchall()}

    def _asr_rows(self):
        self.ensure_one()
        self.env.flush_all()
        company = self.company_id.sudo()
        today = fields.Date.context_today(self)
        day = min(self.date_to or today, today)
        locations = self._asr_locations()
        location_level = bool(self.location_ids or self.warehouse_ids)
        product_ids = self._filtered_product_ids()
        slow_days = company.asr_slow_days or 90

        last = self._last_days(company, day, locations, location_level, product_ids)
        ValueDaily = self.env['asr.stock.value.daily'].sudo()
        if location_level:
            level_qty = self.env['asr.report.stock.ledger']._level_qty_at(company, locations, product_ids, day)
            candidates = set(level_qty) | set(last)
        else:
            level_qty = {}
            self.env.cr.execute(SQL("""
                SELECT DISTINCT ON (product_id) product_id, closing_qty
                  FROM asr_stock_value_daily
                 WHERE company_id = %s AND day <= %s AND %s
                 ORDER BY product_id, day DESC
            """, company.id, day, self._product_sql(product_ids, 'asr_stock_value_daily')))
            level_qty = {pid: float(q or 0) for pid, q in self.env.cr.fetchall()}
            candidates = set(level_qty) | set(last)
        if self.include_without_stock:
            candidates |= set(self._asr_products().ids) if product_ids is None else set(product_ids)
        if product_ids is not None:
            candidates &= set(product_ids)
        if not candidates:
            return []
        products = self.env['product.product'].with_context(active_test=False).browse(sorted(candidates))
        products.fetch(['default_code', 'categ_id', 'uom_id', 'standard_price', 'display_name'])
        products = products.sorted(key=lambda p: (p.default_code or '', p.display_name, p.id))
        company_close = ValueDaily._closing_at(company, products.ids, day)
        classes = {}
        for record in self.env['asr.product.class'].sudo().search(
                [('company_id', '=', company.id), ('product_id', 'in', products.ids)]):
            classes[record.product_id.id] = record

        rows = []
        for product in products:
            uom = product.uom_id
            c_qty, c_value, _c = company_close.get(product.id, (0.0, 0.0, 0.0))
            qty = uom.round(level_qty.get(product.id, 0.0))
            if uom.compare(qty, 0) <= 0 and not self.include_without_stock:
                continue
            if location_level:
                value = ValueDaily._location_value(c_value, c_qty, qty, product.with_company(company).standard_price)
            else:
                value = c_value if qty else 0.0
            last_issue, last_receipt = last.get(product.id, (None, None))
            days_since = (day - last_issue).days if last_issue else None
            if last_issue is None or days_since > 2 * slow_days:
                status = 'non_moving'
            elif days_since > slow_days:
                status = 'slow'
            else:
                status = 'active'
            if self.status == 'slow' and status != 'slow':
                continue
            if self.status == 'non_moving' and status != 'non_moving':
                continue
            if self.status == 'slow_non_moving' and status == 'active':
                continue
            klass = classes.get(product.id)
            rows.append({
                'product_id': product, 'default_code': product.default_code or '',
                'categ_id': product.categ_id, 'uom_id': uom,
                'last_issue_day': last_issue, 'last_receipt_day': last_receipt,
                'days_since_issue': days_since, 'qty_on_hand': qty, 'value_on_hand': value,
                'status': status,
                'abc_class': klass.abc_class if klass else False,
                'xyz_class': klass.xyz_class if klass else False,
                'combined_class': klass.combined_class if klass else '',
            })
        return rows


class ReportSlowMovingLine(models.TransientModel):
    _name = 'asr.report.slow.moving.line'
    _description = 'Slow / Non-Moving Stock Line'
    _order = 'days_since_issue desc, product_id, id'

    wizard_id = fields.Many2one('asr.report.slow.moving', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='wizard_id.currency_id')
    product_id = fields.Many2one('product.product', readonly=True)
    default_code = fields.Char('Internal Reference', readonly=True)
    categ_id = fields.Many2one('product.category', 'Category', readonly=True)
    uom_id = fields.Many2one('uom.uom', 'Unit', readonly=True)
    last_issue_day = fields.Date('Last Issue', readonly=True)
    last_receipt_day = fields.Date('Last Receipt', readonly=True)
    days_since_issue = fields.Integer(readonly=True, aggregator=None)
    qty_on_hand = fields.Float('On Hand', digits='Product Unit of Measure', readonly=True)
    value_on_hand = fields.Monetary(currency_field='currency_id', readonly=True,
                                    groups='ebshel_stock_reports.group_see_values')
    status = fields.Selection(STATUS, readonly=True)
    abc_class = fields.Selection([('A', 'A'), ('B', 'B'), ('C', 'C')], string='ABC', readonly=True)
    xyz_class = fields.Selection([('X', 'X'), ('Y', 'Y'), ('Z', 'Z')], string='XYZ', readonly=True)
    combined_class = fields.Char('Class', readonly=True)
