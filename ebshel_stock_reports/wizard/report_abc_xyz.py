# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
"""Report #6 - ABC / XYZ analysis.

Reads ``asr.product.class`` (the monthly classification) and recomputes it on
the fly with ``_classify(company, months, date_to)`` when the wizard asks for
it or when no class exists yet. ABC ranks the products by issue value at cost
(deliveries and consumption, DESIGN.md §2.3) over the window; XYZ measures
the regularity of the monthly issue quantity (coefficient of variation).
The on-hand quantity and value are those of the report date; with a
warehouse or location filter they are the selected locations' share.
A summary block per combined class closes the report.
"""
from collections import defaultdict

from odoo import fields, models
from odoo.tools import SQL

from .report_mixin import col


class ReportAbcXyz(models.TransientModel):
    _name = 'asr.report.abc.xyz'
    _inherit = 'asr.report.mixin'
    _description = 'ABC / XYZ Analysis'
    _asr_title = 'ABC / XYZ Analysis'
    _asr_line_model = 'asr.report.abc.xyz.line'

    date_from = fields.Date(default=False)  # the window is date_to and `months` back
    months = fields.Integer(default=12, required=True,
                            help="Classification window: this many months back from the report date.")
    recompute = fields.Boolean('Recompute now', default=False,
                               help="Rebuild the classes for the window above before showing the report; "
                                    "otherwise the classes of the last monthly run are shown.")
    include_summary = fields.Boolean('Summary per class', default=True)
    line_ids = fields.One2many('asr.report.abc.xyz.line', 'wizard_id')

    # ------------------------------------------------------------------
    # Columns
    # ------------------------------------------------------------------
    def _asr_columns(self):
        return [
            col('product_id', 'Product', 'many2one', width=34),
            col('default_code', 'Internal Reference', width=14),
            col('categ_id', 'Category', 'many2one', width=20),
            col('abc_class', 'ABC', width=6),
            col('xyz_class', 'XYZ', width=6),
            col('combined_class', 'Class', width=8),
            col('product_count', 'Products', 'int'),
            col('issue_qty', 'Issue Qty', 'qty', total=True),
            col('issue_value', 'Issue Value at Cost', 'monetary', value=True, total=True),
            col('cumulative_share', 'Cumulative Share %', 'percent'),
            col('variation', 'Coefficient of Variation', 'float'),
            col('qty_on_hand', 'On Hand', 'qty', total=True),
            col('value_on_hand', 'Value On Hand', 'monetary', value=True, total=True),
        ]

    def _asr_totals(self, rows, columns):
        return super()._asr_totals([r for r in rows if not r.get('is_summary')], columns)

    def _asr_filter_text(self):
        text = super()._asr_filter_text()
        window = self.env._("Window: %s months", self.months)
        return f"{text} | {window}" if text else window

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------
    def _filtered_product_ids(self):
        if self.product_ids or self.categ_ids:
            return self._asr_products().ids
        return None

    def _product_sql(self, product_ids, alias):
        if product_ids is None:
            return SQL("TRUE")
        if not product_ids:
            return SQL("FALSE")
        return SQL("%s.product_id IN %s", SQL.identifier(alias), tuple(product_ids))

    def _classes(self, company, day):
        ProductClass = self.env['asr.product.class'].sudo()
        domain = [('company_id', '=', company.id)]
        if self.recompute or not ProductClass.search_count(domain):
            ProductClass.with_company(company)._classify(company, months=max(self.months, 1), date_to=day)
        return ProductClass.search(domain, order='issue_value desc, id')

    def _asr_rows(self):
        self.ensure_one()
        self.env.flush_all()
        company = self.company_id.sudo()
        today = fields.Date.context_today(self)
        day = min(self.date_to or today, today)
        locations = self._asr_locations()
        location_level = bool(self.location_ids or self.warehouse_ids)
        product_ids = self._filtered_product_ids()

        classes = self._classes(company, day)
        if product_ids is not None:
            classes = classes.filtered(lambda c: c.product_id.id in product_ids)
        if not classes:
            return []
        products = classes.product_id.with_context(active_test=False)
        products.fetch(['default_code', 'categ_id', 'uom_id', 'standard_price', 'display_name'])
        ValueDaily = self.env['asr.stock.value.daily'].sudo()
        company_close = ValueDaily._closing_at(company, products.ids, day)
        if location_level:
            level_qty = self.env['asr.report.stock.ledger']._level_qty_at(company, locations, products.ids, day)
        else:
            level_qty = {pid: data[0] for pid, data in company_close.items()}

        rows = []
        summary = defaultdict(lambda: defaultdict(float))
        for klass in classes:
            product = klass.product_id
            uom = product.uom_id
            c_qty, c_value, _c = company_close.get(product.id, (0.0, 0.0, 0.0))
            qty = uom.round(level_qty.get(product.id, 0.0))
            if location_level:
                value = ValueDaily._location_value(c_value, c_qty, qty, product.with_company(company).standard_price)
            else:
                value = c_value if qty else 0.0
            if not klass.issue_qty and uom.is_zero(qty):
                continue
            rows.append({
                'product_id': product, 'default_code': product.default_code or '',
                'categ_id': product.categ_id, 'abc_class': klass.abc_class, 'xyz_class': klass.xyz_class,
                'combined_class': klass.combined_class, 'product_count': 1,
                'issue_qty': klass.issue_qty, 'issue_value': klass.issue_value,
                'cumulative_share': klass.cumulative_share, 'variation': klass.variation,
                'qty_on_hand': qty, 'value_on_hand': value, 'is_summary': False,
            })
            bucket = summary[(klass.abc_class or '', klass.xyz_class or '')]
            bucket['product_count'] += 1
            bucket['issue_qty'] += klass.issue_qty
            bucket['issue_value'] += klass.issue_value
            bucket['qty_on_hand'] += qty
            bucket['value_on_hand'] += value
        if self.include_summary and rows:
            total_issue = sum(b['issue_value'] for b in summary.values())
            rows.append({'_group': self.env._("Summary per class")})
            for (abc, xyz), bucket in sorted(summary.items()):
                rows.append({
                    'product_id': False, 'default_code': '', 'categ_id': False,
                    'abc_class': abc or False, 'xyz_class': xyz or False, 'combined_class': abc + xyz,
                    'product_count': int(bucket['product_count']),
                    'issue_qty': bucket['issue_qty'], 'issue_value': bucket['issue_value'],
                    'cumulative_share': (bucket['issue_value'] / total_issue * 100.0) if total_issue else 0.0,
                    'variation': None, 'qty_on_hand': bucket['qty_on_hand'],
                    'value_on_hand': bucket['value_on_hand'], 'is_summary': True,
                })
        return rows


class ReportAbcXyzLine(models.TransientModel):
    _name = 'asr.report.abc.xyz.line'
    _description = 'ABC / XYZ Analysis Line'
    _order = 'is_summary, issue_value desc, id'

    wizard_id = fields.Many2one('asr.report.abc.xyz', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='wizard_id.currency_id')
    product_id = fields.Many2one('product.product', readonly=True)
    default_code = fields.Char('Internal Reference', readonly=True)
    categ_id = fields.Many2one('product.category', 'Category', readonly=True)
    abc_class = fields.Selection([('A', 'A'), ('B', 'B'), ('C', 'C')], string='ABC', readonly=True)
    xyz_class = fields.Selection([('X', 'X'), ('Y', 'Y'), ('Z', 'Z')], string='XYZ', readonly=True)
    combined_class = fields.Char('Class', readonly=True)
    is_summary = fields.Boolean('Summary Row', readonly=True)
    product_count = fields.Integer('Products', readonly=True)
    issue_qty = fields.Float(digits='Product Unit of Measure', readonly=True)
    issue_value = fields.Monetary('Issue Value at Cost', currency_field='currency_id', readonly=True,
                                  groups='ebshel_stock_reports.group_see_values')
    cumulative_share = fields.Float('Cumulative Share %', readonly=True, aggregator=None)
    variation = fields.Float('Coefficient of Variation', digits=(16, 3), readonly=True, aggregator='avg')
    qty_on_hand = fields.Float('On Hand', digits='Product Unit of Measure', readonly=True)
    value_on_hand = fields.Monetary(currency_field='currency_id', readonly=True,
                                    groups='ebshel_stock_reports.group_see_values')
