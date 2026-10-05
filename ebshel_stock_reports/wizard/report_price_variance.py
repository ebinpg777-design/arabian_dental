# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
"""Report #10 - Purchase price variance.

One row per done receipt move of a purchase order line (``is_in``,
``purchase_line_id`` set) dated in the period. DESIGN.md §2.3:

* PO price = ``purchase_line._get_stock_move_price_unit()`` (company currency,
  product UoM, tax-included prices handled), converted at the move date like
  ``_get_value_from_quotation`` does;
* valued price = ``move.value`` / valued quantity: the PO price until the
  line is billed, then the bill price (§1.4), for every cost method;
* standard cost at receipt: for standard-cost products the last product-level
  ``product.value`` (no move, no lot, this company) dated at or before the
  move, else the current standard price; for AVCO/FIFO products the current
  standard price, hence the label "standard / avg cost".

Variances: PO vs standard = (PO - standard) x qty; invoice vs PO =
(valued - PO) x qty; total = (valued - standard) x qty.
"""
from collections import defaultdict

from odoo import fields, models
from odoo.tools import SQL

from .report_mixin import col


class ReportPriceVariance(models.TransientModel):
    _name = 'asr.report.price.variance'
    _inherit = 'asr.report.mixin'
    _description = 'Purchase Price Variance'
    _asr_title = 'Purchase Price Variance'
    _asr_line_model = 'asr.report.price.variance.line'
    _asr_uses_engine = False

    group_by = fields.Selection([
        ('receipt', 'Receipt moves'),
        ('product', 'Products: sums and average prices'),
    ], default='receipt', required=True, string='Show')
    partner_ids = fields.Many2many('res.partner', string='Vendors')
    line_ids = fields.One2many('asr.report.price.variance.line', 'wizard_id')

    # ------------------------------------------------------------------
    # Columns
    # ------------------------------------------------------------------
    def _asr_columns(self):
        if self.group_by == 'product':
            head = [
                col('product_id', 'Product', 'many2one', width=34),
                col('categ_id', 'Category', 'many2one', width=20),
                col('move_count', 'Receipts', 'int', total=True),
            ]
        else:
            head = [
                col('reference', 'Receipt', width=16),
                col('date', 'Date', 'datetime'),
                col('partner_id', 'Vendor', 'many2one', width=24),
                col('order_id', 'Purchase Order', 'many2one', width=14),
                col('product_id', 'Product', 'many2one', width=30),
            ]
        return head + [
            col('quantity', 'Received Qty', 'qty', total=True,
                help_text="Valued quantity of the receipt in the product unit."),
            col('price_po', 'PO Price', 'monetary', value=True,
                help_text="Purchase order line price per product unit in the company currency, discounts and "
                          "tax-included prices handled as Odoo values the receipt."),
            col('price_valued', 'Valued Price', 'monetary', value=True,
                help_text="Value of the receipt move per unit: the PO price until billed, the bill price once "
                          "the vendor bill is posted."),
            col('price_standard', 'Standard / Avg Cost', 'monetary', value=True,
                help_text="Standard-cost products: the standard price in force at the receipt date. "
                          "AVCO and FIFO products: the current cost of the product."),
            col('var_po_std', 'PO vs Standard', 'monetary', value=True, total=True,
                help_text="(PO price - standard cost) x quantity."),
            col('var_po_std_pct', 'PO vs Standard %', 'percent', value=True),
            col('var_inv_po', 'Invoice vs PO', 'monetary', value=True, total=True,
                help_text="(valued price - PO price) x quantity: zero until the bill is posted at another price."),
            col('var_inv_po_pct', 'Invoice vs PO %', 'percent', value=True),
            col('var_total', 'Total Variance', 'monetary', value=True, total=True,
                help_text="(valued price - standard cost) x quantity."),
            col('var_total_pct', 'Total Variance %', 'percent', value=True),
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
        rows = self._move_rows()
        if self.group_by == 'product':
            return self._product_rows(rows)
        return rows

    @staticmethod
    def _pct(amount, base_price, qty):
        base = base_price * qty
        return (100.0 * amount / base) if base else 0.0

    def _move_rows(self):
        company = self.company_id.sudo()
        date_from, date_to = self._period()
        start = company._asr_day_start_utc(date_from)
        end = company._asr_day_end_utc(date_to)

        filters = [SQL("TRUE")]
        if self.product_ids or self.categ_ids:
            product_ids = tuple(self._asr_products().ids) or (0,)
            filters.append(SQL("sm.product_id IN %s", product_ids))
        if self.warehouse_ids or self.location_ids:
            filters.append(SQL("sm.location_dest_id IN %s", tuple(self._asr_locations().ids) or (0,)))
        if self.partner_ids:
            filters.append(SQL("po.partner_id IN %s", tuple(self.partner_ids.ids)))

        # valued quantity: the picked move lines entering a valued location from a non-valued one,
        # owned by the company (stock.move._get_in_move_lines)
        self.env.flush_all()
        self.env.cr.execute(SQL("""
            SELECT sm.id, sm.date, sm.reference, sm.purchase_line_id, pol.order_id, po.partner_id, sm.product_id,
                   sm.value,
                   (SELECT COALESCE(SUM(ml.quantity_product_uom), 0)
                      FROM stock_move_line ml
                      JOIN stock_location ls ON ls.id = ml.location_id
                      JOIN stock_location ld ON ld.id = ml.location_dest_id
                     WHERE ml.move_id = sm.id AND ml.picked
                       AND (ml.owner_id IS NULL OR ml.owner_id = %(partner)s)
                       AND NOT (ls.company_id IS NOT NULL AND ls.usage IN ('internal', 'transit'))
                       AND (ld.company_id IS NOT NULL AND ld.usage IN ('internal', 'transit'))) AS valued_qty,
                   pv.value AS standard_at_date
              FROM stock_move sm
              JOIN purchase_order_line pol ON pol.id = sm.purchase_line_id
              JOIN purchase_order po ON po.id = pol.order_id
              LEFT JOIN LATERAL (
                   SELECT value FROM product_value
                    WHERE product_id = sm.product_id AND move_id IS NULL AND lot_id IS NULL
                      AND company_id = %(company)s AND date <= sm.date
                    ORDER BY date DESC, id DESC LIMIT 1) pv ON TRUE
             WHERE sm.state = 'done' AND sm.is_in AND sm.company_id = %(company)s
               AND sm.date >= %(start)s AND sm.date <= %(end)s
               AND %(filters)s
             ORDER BY sm.date, sm.id
        """, partner=company.partner_id.id, company=company.id, start=start, end=end,
             filters=SQL(" AND ").join(filters)))
        data = self.env.cr.fetchall()
        if not data:
            return []

        lines = {line.id: line for line in self.env['purchase.order.line'].browse(list({r[3] for r in data}))}
        orders = {o.id: o for o in self.env['purchase.order'].browse(list({r[4] for r in data}))}
        partners = {p.id: p for p in self.env['res.partner'].browse(list({r[5] for r in data if r[5]}))}
        products = {p.id: p.with_company(company) for p in self.env['product.product'].with_context(
            active_test=False).browse(list({r[6] for r in data}))}
        currency = company.currency_id

        rows = []
        price_cache = {}
        for move_id, date, reference, pol_id, order_id, partner_id, product_id, value, valued_qty, std_at_date in data:
            product = products[product_id]
            qty = float(valued_qty or 0.0)
            value = float(value or 0.0)
            key = (pol_id, date.date() if lines[pol_id].order_id.currency_id != currency else None)
            if key not in price_cache:
                price_cache[key] = lines[pol_id].with_context(conversion_date=date)._get_stock_move_price_unit()
            price_po = price_cache[key]
            price_valued = (value / qty) if qty else 0.0
            if product.cost_method == 'standard' and std_at_date is not None:
                price_std = float(std_at_date)
            else:
                price_std = product.standard_price
            var_po_std = (price_po - price_std) * qty
            var_inv_po = (price_valued - price_po) * qty
            var_total = (price_valued - price_std) * qty
            rows.append({
                'move_id': self.env['stock.move'].browse(move_id), 'reference': reference or '', 'date': date,
                'partner_id': partners.get(partner_id), 'order_id': orders[order_id],
                'product_id': product, 'categ_id': product.categ_id, 'move_count': 1,
                'quantity': qty, 'price_po': price_po, 'price_valued': price_valued, 'price_standard': price_std,
                'var_po_std': var_po_std, 'var_po_std_pct': self._pct(var_po_std, price_std, qty),
                'var_inv_po': var_inv_po, 'var_inv_po_pct': self._pct(var_inv_po, price_po, qty),
                'var_total': var_total, 'var_total_pct': self._pct(var_total, price_std, qty),
            })
        return rows

    def _product_rows(self, rows):
        totals = defaultdict(lambda: defaultdict(float))
        products = {}
        for row in rows:
            product = row['product_id']
            products[product.id] = product
            bucket = totals[product.id]
            qty = row['quantity']
            bucket['move_count'] += 1
            bucket['quantity'] += qty
            bucket['amount_po'] += row['price_po'] * qty
            bucket['amount_valued'] += row['price_valued'] * qty
            bucket['amount_standard'] += row['price_standard'] * qty
            bucket['var_po_std'] += row['var_po_std']
            bucket['var_inv_po'] += row['var_inv_po']
            bucket['var_total'] += row['var_total']
        out = []
        for pid in sorted(totals, key=lambda p: (products[p].default_code or '', products[p].display_name, p)):
            product = products[pid]
            bucket = totals[pid]
            qty = bucket['quantity']
            price_po = (bucket['amount_po'] / qty) if qty else 0.0
            price_valued = (bucket['amount_valued'] / qty) if qty else 0.0
            price_std = (bucket['amount_standard'] / qty) if qty else 0.0
            out.append({
                'product_id': product, 'categ_id': product.categ_id, 'move_count': int(bucket['move_count']),
                'quantity': qty, 'price_po': price_po, 'price_valued': price_valued, 'price_standard': price_std,
                'var_po_std': bucket['var_po_std'], 'var_po_std_pct': self._pct(bucket['var_po_std'], price_std, qty),
                'var_inv_po': bucket['var_inv_po'], 'var_inv_po_pct': self._pct(bucket['var_inv_po'], price_po, qty),
                'var_total': bucket['var_total'], 'var_total_pct': self._pct(bucket['var_total'], price_std, qty),
            })
        return out


class ReportPriceVarianceLine(models.TransientModel):
    _name = 'asr.report.price.variance.line'
    _description = 'Purchase Price Variance Line'
    _order = 'date, id'

    wizard_id = fields.Many2one('asr.report.price.variance', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='wizard_id.currency_id')
    move_id = fields.Many2one('stock.move', readonly=True)
    reference = fields.Char('Receipt', readonly=True)
    date = fields.Datetime(readonly=True)
    partner_id = fields.Many2one('res.partner', 'Vendor', readonly=True)
    order_id = fields.Many2one('purchase.order', 'Purchase Order', readonly=True)
    product_id = fields.Many2one('product.product', readonly=True)
    categ_id = fields.Many2one('product.category', 'Category', readonly=True)
    move_count = fields.Integer('Receipts', readonly=True)
    quantity = fields.Float('Received Qty', digits='Product Unit of Measure', readonly=True)
    price_po = fields.Monetary('PO Price', currency_field='currency_id', readonly=True, aggregator='avg',
                               groups='ebshel_stock_reports.group_see_values')
    price_valued = fields.Monetary('Valued Price', currency_field='currency_id', readonly=True, aggregator='avg',
                                   groups='ebshel_stock_reports.group_see_values')
    price_standard = fields.Monetary('Standard / Avg Cost', currency_field='currency_id', readonly=True,
                                     aggregator='avg', groups='ebshel_stock_reports.group_see_values')
    var_po_std = fields.Monetary('PO vs Standard', currency_field='currency_id', readonly=True,
                                 groups='ebshel_stock_reports.group_see_values')
    var_po_std_pct = fields.Float('PO vs Standard %', readonly=True, aggregator='avg',
                                  groups='ebshel_stock_reports.group_see_values')
    var_inv_po = fields.Monetary('Invoice vs PO', currency_field='currency_id', readonly=True,
                                 groups='ebshel_stock_reports.group_see_values')
    var_inv_po_pct = fields.Float('Invoice vs PO %', readonly=True, aggregator='avg',
                                  groups='ebshel_stock_reports.group_see_values')
    var_total = fields.Monetary('Total Variance', currency_field='currency_id', readonly=True,
                                groups='ebshel_stock_reports.group_see_values')
    var_total_pct = fields.Float('Total Variance %', readonly=True, aggregator='avg',
                                 groups='ebshel_stock_reports.group_see_values')
