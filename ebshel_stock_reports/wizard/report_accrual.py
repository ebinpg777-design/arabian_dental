# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
"""Report #17 - Inventory vs GL and accruals.

Four listings built from the purchase and sales order lines (DESIGN.md §1.13,
§3 #17): goods received not yet billed ("bill to receive"), billed not yet
received, delivered not yet invoiced ("invoices to be issued") and invoiced
not yet delivered, each at the order price and at cost, grouped per partner.
The summary mode gives one row per listing plus the company's stock value
from the engine and the balance of the stock valuation accounts in the
general ledger, with the difference. Odoo's own Inventory Valuation report
(``stock_account.action_report_stock_valuation``) is one click away.
"""
from collections import defaultdict

from odoo import fields, models
from odoo.tools import SQL

from .report_mixin import col

LISTINGS = [
    ('summary', 'Summary'),
    ('received_not_billed', 'Bill to Receive (received, not billed)'),
    ('billed_not_received', 'Billed, Not Received'),
    ('delivered_not_invoiced', 'Invoices to be Issued (delivered, not invoiced)'),
    ('invoiced_not_delivered', 'Invoiced, Not Delivered'),
]
LISTING_LABELS = dict(LISTINGS)
PURCHASE_LISTINGS = ('received_not_billed', 'billed_not_received')
SALE_LISTINGS = ('delivered_not_invoiced', 'invoiced_not_delivered')


class ReportAccrual(models.TransientModel):
    _name = 'asr.report.accrual'
    _inherit = 'asr.report.mixin'
    _description = 'Inventory vs GL and Accruals'
    _asr_title = 'Inventory vs GL and Accruals'
    _asr_line_model = 'asr.report.accrual.line'
    _asr_uses_engine = False

    listing = fields.Selection(LISTINGS, required=True, default='summary',
                               help="The listings show the quantities open today on the orders confirmed up to "
                                    "the end date. The summary adds the stock value and the general ledger "
                                    "balance of the stock valuation accounts at the end date.")
    line_ids = fields.One2many('asr.report.accrual.line', 'wizard_id')

    def action_open_odoo_valuation(self):
        return self.env['ir.actions.actions']._for_xml_id('stock_account.action_report_stock_valuation')

    # ------------------------------------------------------------------
    # Columns
    # ------------------------------------------------------------------
    def _asr_columns(self):
        if self.listing == 'summary':
            return [
                col('label', 'Listing', width=44),
                col('row_count', 'Lines', 'int', total=True),
                col('amount_price', 'Amount at Order Price', 'monetary', value=True),
                col('amount_cost', 'Amount at Cost', 'monetary', value=True),
                col('note', 'Note', width=50),
            ]
        purchase = self.listing in PURCHASE_LISTINGS
        return [
            col('partner_id', 'Vendor' if purchase else 'Customer', 'many2one', width=28),
            col('order_ref', 'Purchase Order' if purchase else 'Sales Order', width=14),
            col('order_date', 'Order Date', 'date'),
            col('product_id', 'Product', 'many2one', width=34),
            col('default_code', 'Internal Reference', width=14),
            col('uom_id', 'Unit', 'many2one', width=8),
            col('qty_ordered', 'Ordered Qty', 'qty'),
            col('qty_done', 'Received Qty' if purchase else 'Delivered Qty', 'qty'),
            col('qty_invoiced', 'Billed Qty' if purchase else 'Invoiced Qty', 'qty'),
            col('qty_open', 'Open Qty', 'qty'),
            col('price_unit', 'Unit Price', 'monetary', value=True,
                help_text="Order line price per product unit in the company currency, discount applied, "
                          "taxes excluded."),
            col('amount_price', 'Amount at Order Price', 'monetary', value=True, total=True),
            col('unit_cost', 'Unit Cost', 'monetary', value=True,
                help_text="Unit value of the receipts/deliveries of the line when there are any, "
                          "else the product's current cost."),
            col('amount_cost', 'Amount at Cost', 'monetary', value=True, total=True),
            col('date_expected', 'Expected Date', 'date'),
            col('invoice_status', 'Bill Status' if purchase else 'Invoice Status', width=14),
        ]

    def _asr_view_context(self):
        context = {'asr_listing': self.listing}
        if self.listing != 'summary':
            context['search_default_group_partner'] = 1
        return context

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------
    def _asr_rows(self):
        self.ensure_one()
        self.env.flush_all()  # the SQL below reads stored computed columns
        if self.listing == 'summary':
            return self._rows_summary()
        rows = self._listing_rows(self.listing)
        return self._group_by_partner(rows)

    def _end_instant(self):
        date_to = self.date_to or fields.Date.context_today(self)
        return self.company_id.sudo()._asr_day_end_utc(date_to)

    def _product_filter_sql(self, alias):
        if self.product_ids or self.categ_ids:
            return SQL("%s.product_id IN %s", SQL.identifier(alias), tuple(self._asr_products().ids) or (0,))
        return SQL("TRUE")

    def _move_unit_values(self, link_field, line_ids, incoming):
        """{order line id: unit value} of the done valued moves linked to the lines (receipts for
        purchases, deliveries for sales): move value / product-UoM quantity."""
        if not line_ids:
            return {}
        self.env.cr.execute(SQL("""
            SELECT sm.%(link)s, SUM(sm.value), SUM(l.qty)
              FROM stock_move sm
              JOIN (SELECT move_id, SUM(quantity_product_uom) AS qty FROM stock_move_line GROUP BY move_id) l
                ON l.move_id = sm.id
             WHERE sm.%(link)s IN %(ids)s AND sm.state = 'done' AND %(direction)s
             GROUP BY sm.%(link)s
        """, link=SQL.identifier(link_field), ids=tuple(line_ids),
             direction=SQL("sm.is_in") if incoming else SQL("sm.is_out")))
        return {lid: float(value) / float(qty) for lid, value, qty in self.env.cr.fetchall() if qty}

    def _listing_rows(self, listing):
        if listing in PURCHASE_LISTINGS:
            return self._purchase_rows(listing == 'received_not_billed')
        return self._sale_rows(listing == 'delivered_not_invoiced')

    def _purchase_rows(self, received_side):
        company = self.company_id
        self.env.cr.execute(SQL("""
            SELECT pol.id
              FROM purchase_order_line pol
              JOIN purchase_order po ON po.id = pol.order_id
              JOIN product_product pp ON pp.id = pol.product_id
              JOIN product_template pt ON pt.id = pp.product_tmpl_id
             WHERE po.state IN ('purchase', 'done') AND po.company_id = %s
               AND pol.display_type IS NULL AND pt.type <> 'service'
               AND COALESCE(po.date_approve, po.date_order) <= %s
               AND %s AND %s
        """, company.id, self._end_instant(),
             SQL("pol.qty_received - pol.qty_invoiced > 0") if received_side
             else SQL("pol.qty_invoiced - pol.qty_received > 0"),
             self._product_filter_sql('pol')))
        lines = self.env['purchase.order.line'].browse([r[0] for r in self.env.cr.fetchall()])
        if not lines:
            return []
        lines.fetch(['order_id', 'product_id', 'product_uom_id', 'product_qty', 'qty_received', 'qty_invoiced',
                     'date_planned', 'price_unit', 'discount', 'tax_ids'])
        unit_values = self._move_unit_values('purchase_line_id', lines.ids, incoming=True)
        rows = []
        for line in lines:
            product = line.product_id
            uom = product.uom_id
            to_product_uom = (lambda q: line.product_uom_id._compute_quantity(q, uom, rounding_method='HALF-UP')) \
                if line.product_uom_id != uom else (lambda q: q)
            qty_done = to_product_uom(line.qty_received)
            qty_invoiced = to_product_uom(line.qty_invoiced)
            qty_open = uom.round(qty_done - qty_invoiced if received_side else qty_invoiced - qty_done)
            if uom.is_zero(qty_open) or qty_open < 0:
                continue
            price_unit = line._get_stock_move_price_unit()
            unit_cost = unit_values.get(line.id) or product.with_company(company).standard_price
            order = line.order_id
            rows.append({
                'partner_id': order.partner_id, 'order_ref': order.name,
                'order_date': company._asr_local_day(order.date_approve or order.date_order),
                'product_id': product, 'default_code': product.default_code or '', 'uom_id': uom,
                'qty_ordered': to_product_uom(line.product_qty), 'qty_done': qty_done, 'qty_invoiced': qty_invoiced,
                'qty_open': qty_open, 'price_unit': price_unit, 'amount_price': qty_open * price_unit,
                'unit_cost': unit_cost, 'amount_cost': qty_open * unit_cost,
                'date_expected': company._asr_local_day(line.date_planned),
                'invoice_status': dict(order._fields['invoice_status']._description_selection(self.env)).get(
                    order.invoice_status, order.invoice_status or ''),
            })
        return rows

    def _sale_rows(self, delivered_side):
        company = self.company_id
        self.env.cr.execute(SQL("""
            SELECT sol.id
              FROM sale_order_line sol
              JOIN sale_order so ON so.id = sol.order_id
              JOIN product_product pp ON pp.id = sol.product_id
              JOIN product_template pt ON pt.id = pp.product_tmpl_id
             WHERE so.state IN ('sale', 'done') AND so.company_id = %s
               AND sol.display_type IS NULL AND pt.type <> 'service'
               AND so.date_order <= %s
               AND %s AND %s
        """, company.id, self._end_instant(),
             SQL("sol.qty_delivered - sol.qty_invoiced > 0") if delivered_side
             else SQL("sol.qty_invoiced - sol.qty_delivered > 0"),
             self._product_filter_sql('sol')))
        lines = self.env['sale.order.line'].browse([r[0] for r in self.env.cr.fetchall()])
        if not lines:
            return []
        lines.fetch(['order_id', 'product_id', 'product_uom_id', 'product_uom_qty', 'qty_delivered', 'qty_invoiced',
                     'price_unit', 'discount', 'invoice_status', 'currency_id'])
        unit_values = self._move_unit_values('sale_line_id', lines.ids, incoming=False)
        status_labels = dict(self.env['sale.order.line']._fields['invoice_status']._description_selection(self.env))
        rows = []
        for line in lines:
            if delivered_side and line.invoice_status == 'invoiced':
                continue  # fully invoiced by amount (down payments, manual invoices)
            product = line.product_id
            uom = product.uom_id
            line_uom = line.product_uom_id or uom
            to_product_uom = (lambda q: line_uom._compute_quantity(q, uom, rounding_method='HALF-UP')) \
                if line_uom != uom else (lambda q: q)
            qty_done = to_product_uom(line.qty_delivered)
            qty_invoiced = to_product_uom(line.qty_invoiced)
            qty_open = uom.round(qty_done - qty_invoiced if delivered_side else qty_invoiced - qty_done)
            if uom.is_zero(qty_open) or qty_open < 0:
                continue
            order = line.order_id
            price = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
            if line_uom != uom:
                price = price * uom.factor / line_uom.factor
            if order.currency_id != company.currency_id:
                price = order.currency_id._convert(price, company.currency_id, company,
                                                   (order.date_order or fields.Datetime.now()).date(), round=False)
            unit_cost = unit_values.get(line.id) or product.with_company(company).standard_price
            expected = order.commitment_date or order.expected_date
            rows.append({
                'partner_id': order.partner_id, 'order_ref': order.name,
                'order_date': company._asr_local_day(order.date_order),
                'product_id': product, 'default_code': product.default_code or '', 'uom_id': uom,
                'qty_ordered': to_product_uom(line.product_uom_qty), 'qty_done': qty_done,
                'qty_invoiced': qty_invoiced, 'qty_open': qty_open,
                'price_unit': price, 'amount_price': qty_open * price,
                'unit_cost': unit_cost, 'amount_cost': qty_open * unit_cost,
                'date_expected': company._asr_local_day(expected) if expected else False,
                'invoice_status': status_labels.get(line.invoice_status, line.invoice_status or ''),
            })
        return rows

    def _group_by_partner(self, rows):
        """Sort by partner and insert a header per partner carrying the subtotals."""
        rows.sort(key=lambda r: (r['partner_id'].display_name or '', r['partner_id'].id, r['order_ref'],
                                 r['product_id'].id))
        show_values = self.env.user.has_group('ebshel_stock_reports.group_see_values')
        currency = self.company_id.currency_id
        grouped = []
        by_partner = defaultdict(list)
        order = []
        for row in rows:
            key = row['partner_id'].id
            if key not in by_partner:
                order.append(key)
            by_partner[key].append(row)
        for key in order:
            lines = by_partner[key]
            title = self.env._("%(partner)s - %(count)s lines",
                               partner=lines[0]['partner_id'].display_name, count=len(lines))
            if show_values:
                title += self.env._(" - at order price %(price)s - at cost %(cost)s",
                                    price=currency.format(sum(r['amount_price'] for r in lines)),
                                    cost=currency.format(sum(r['amount_cost'] for r in lines)))
            grouped.append({'_group': title})
            grouped.extend(lines)
        return grouped

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    def _engine_stock_value(self):
        """Σ of the engine's latest closing value per product at the end date (None when the engine is empty)."""
        company = self.company_id.sudo()
        self.env['asr.stock.dirty']._ensure_fresh(company)
        self.env.cr.execute(SQL("SELECT DISTINCT product_id FROM asr_stock_value_daily WHERE company_id = %s AND %s",
                                company.id, self._product_filter_sql('asr_stock_value_daily')))
        product_ids = [r[0] for r in self.env.cr.fetchall()]
        if not product_ids:
            return None, 0
        date_to = self.date_to or fields.Date.context_today(self)
        closing = self.env['asr.stock.value.daily'].sudo()._closing_at(company, product_ids, date_to)
        return sum(v for _q, v, _c in closing.values()), len(closing)

    def _valuation_accounts(self):
        company = self.company_id
        Category = self.env['product.category'].with_company(company)
        field = Category._fields['property_stock_valuation_account_id']
        accounts = self.env['account.account']
        for categ in Category.search([]):  # pylint: disable=no-search-all  (categories are few)
            accounts |= categ.property_stock_valuation_account_id or field.get_company_dependent_fallback(categ)
        accounts |= company.account_stock_valuation_id
        return accounts

    def _gl_stock_value(self):
        """Posted balance of the stock valuation accounts at the end date, or None when none is set."""
        accounts = self._valuation_accounts()
        if not accounts:
            return None, accounts
        date_to = self.date_to or fields.Date.context_today(self)
        balances = self.company_id.sudo().stock_accounting_value(
            accounts_by_product={account: {'valuation': account} for account in accounts}, at_date=date_to)
        return sum(balances.values()), accounts

    def _rows_summary(self):
        rows = []
        for key in PURCHASE_LISTINGS + SALE_LISTINGS:
            lines = self._listing_rows(key)
            rows.append({
                'sequence': len(rows), 'listing': key, 'label': self.env._(LISTING_LABELS[key]),
                'row_count': len(lines),
                'amount_price': sum(r['amount_price'] for r in lines),
                'amount_cost': sum(r['amount_cost'] for r in lines),
                'note': '',
            })
        date_to = self.date_to or fields.Date.context_today(self)
        stock_value, product_count = self._engine_stock_value()
        gl_value, accounts = self._gl_stock_value()
        rows.append({'_group': self.env._("Inventory vs general ledger at %s", fields.Date.to_string(date_to))})
        rows.append({
            'sequence': 10, 'listing': 'stock_value', 'label': self.env._("Stock value (daily stock summary)"),
            'row_count': product_count, 'amount_price': 0.0, 'amount_cost': stock_value or 0.0,
            'note': self.env._("Sum of the latest closing value per product; %s products", product_count)
            if stock_value is not None else self.env._("The daily stock summary has no rows for this company yet."),
        })
        rows.append({
            'sequence': 11, 'listing': 'gl_value', 'label': self.env._("General ledger: stock valuation accounts"),
            'row_count': len(accounts), 'amount_price': 0.0, 'amount_cost': gl_value or 0.0,
            'note': ', '.join(accounts.mapped('code')) if accounts
            else self.env._("No stock valuation account is set on the company or the product categories."),
        })
        if stock_value is not None and gl_value is not None:
            rows.append({
                'sequence': 12, 'listing': 'difference', 'label': self.env._("Difference (stock value - ledger)"),
                'row_count': 0, 'amount_price': 0.0, 'amount_cost': stock_value - gl_value,
                'note': self.env._("Receipts not billed and deliveries not invoiced above explain part of it "
                                   "under perpetual valuation; see Odoo's Inventory Valuation report."),
            })
        return rows


class ReportAccrualLine(models.TransientModel):
    _name = 'asr.report.accrual.line'
    _description = 'Inventory vs GL and Accruals Line'
    _order = 'sequence, partner_id, order_ref, id'

    wizard_id = fields.Many2one('asr.report.accrual', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='wizard_id.currency_id')
    sequence = fields.Integer(readonly=True)
    listing = fields.Char('Listing Key', readonly=True)
    label = fields.Char('Listing', readonly=True)
    row_count = fields.Integer('Lines', readonly=True)
    note = fields.Char(readonly=True)
    partner_id = fields.Many2one('res.partner', readonly=True)
    order_ref = fields.Char('Order', readonly=True)
    order_date = fields.Date(readonly=True)
    product_id = fields.Many2one('product.product', readonly=True)
    default_code = fields.Char('Internal Reference', readonly=True)
    uom_id = fields.Many2one('uom.uom', 'Unit', readonly=True)
    qty_ordered = fields.Float('Ordered Qty', digits='Product Unit of Measure', readonly=True)
    qty_done = fields.Float('Received / Delivered Qty', digits='Product Unit of Measure', readonly=True)
    qty_invoiced = fields.Float('Invoiced Qty', digits='Product Unit of Measure', readonly=True)
    qty_open = fields.Float('Open Qty', digits='Product Unit of Measure', readonly=True)
    price_unit = fields.Monetary('Unit Price', currency_field='currency_id', readonly=True,
                                 groups='ebshel_stock_reports.group_see_values')
    amount_price = fields.Monetary('Amount at Order Price', currency_field='currency_id', readonly=True,
                                   groups='ebshel_stock_reports.group_see_values')
    unit_cost = fields.Monetary(currency_field='currency_id', readonly=True,
                                groups='ebshel_stock_reports.group_see_values')
    amount_cost = fields.Monetary('Amount at Cost', currency_field='currency_id', readonly=True,
                                  groups='ebshel_stock_reports.group_see_values')
    date_expected = fields.Date('Expected Date', readonly=True)
    invoice_status = fields.Char(readonly=True)
