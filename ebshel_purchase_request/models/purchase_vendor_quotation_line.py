# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.
from markupsafe import Markup, escape

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare, float_round


class PurchaseVendorQuotationLine(models.Model):
    _name = 'ebshel.purchase.vendor.quotation.line'
    _description = 'Vendor Quotation Line'
    _order = 'quotation_id, sequence, id'

    quotation_id = fields.Many2one(
        'ebshel.purchase.vendor.quotation', string='Quotation',
        required=True, ondelete='cascade', index=True,
    )
    sequence = fields.Integer('Seq.', default=10)
    rfq_line_id = fields.Many2one(
        'ebshel.purchase.rfq.line', string='RFQ Line',
        ondelete='set null', index=True,
    )

    # ── Product ───────────────────────────────────────────────────────────────
    # Derived from the RFQ line. Stored computed + readonly=False so the server
    # fills them from rfq_line_id even when the web client omits them on save.
    # (The RFQ-line link itself is readonly in the form, so it MUST carry
    # force_save="1" in the view — otherwise it never reaches the server and
    # these all stay empty, leaving only the manually-editable description.)
    product_id = fields.Many2one(
        'product.product', string='Product',
        compute='_compute_from_rfq_line', store=True, readonly=False,
    )
    name = fields.Text(
        'Description',
        compute='_compute_from_rfq_line', store=True, readonly=False,
    )
    qty = fields.Float(
        'Quantity', digits='Product Unit of Measure',
        compute='_compute_from_rfq_line', store=True, readonly=False,
    )
    uom_id = fields.Many2one(
        'uom.uom', string='UOM',
        compute='_compute_from_rfq_line', store=True, readonly=False,
    )

    # ── Vendor Pricing ────────────────────────────────────────────────────────
    price_unit = fields.Monetary('Unit Price', digits='Product Price', default=0.0,currency_field='currency_id')
    discount = fields.Float('Discount (%)', digits='Discount', default=0.0)
    tax_ids = fields.Many2many(
        'account.tax', string='Taxes',
        domain=[('type_tax_use', '=', 'purchase')],
    )
    delivery_date = fields.Date('Delivery Date')
    vendor_remarks = fields.Char('Vendor Remarks')

    # ── Totals ────────────────────────────────────────────────────────────────
    currency_id = fields.Many2one(related='quotation_id.currency_id', readonly=True)
    company_id = fields.Many2one(related='quotation_id.company_id', readonly=True)
    price_subtotal = fields.Monetary(
        'Subtotal', compute='_compute_price_totals', store=True,
        currency_field='currency_id',
    )
    price_tax = fields.Monetary(
        'Tax Amount', compute='_compute_price_totals', store=True,
        currency_field='currency_id',
    )
    price_total = fields.Monetary(
        'Total', compute='_compute_price_totals', store=True,
        currency_field='currency_id',
    )

    # ── Comparison helper ─────────────────────────────────────────────────────
    is_best_price = fields.Boolean(
        'Best Price', compute='_compute_is_best_price',
        help='True if this line has the lowest unit price for the same product in this RFQ.',
    )

    product_category_id = fields.Many2one(
        'product.category', string='Allowed Category',
        related='rfq_line_id.request_line_id.request_id.product_category_id', store=False,
    )
    quotation_pdt_alter = fields.Boolean(
        related='company_id.quotation_pdt_alter', readonly=True)

    # ── Compute ───────────────────────────────────────────────────────────────
    @api.depends('rfq_line_id')
    def _compute_from_rfq_line(self):
        for line in self:
            rl = line.rfq_line_id
            if rl:
                line.product_id = rl.product_id
                line.name = rl.name
                line.qty = rl.qty
                line.uom_id = rl.uom_id
            else:
                if not line.product_id:
                    line.product_id = False
                if not line.name:
                    line.name = ''
                if not line.qty:
                    line.qty = 1.0

    @api.depends('qty', 'price_unit', 'discount', 'tax_ids')
    def _compute_price_totals(self):
        for line in self:
            discounted_price = line.price_unit * (1 - line.discount / 100)
            subtotal = line.qty * discounted_price
            taxes = line.tax_ids.compute_all(
                discounted_price, line.currency_id, line.qty,
                product=line.product_id, partner=line.quotation_id.vendor_id,
            )
            line.price_subtotal = taxes['total_excluded']
            line.price_tax = taxes['total_included'] - taxes['total_excluded']
            line.price_total = taxes['total_included']

    @api.depends('price_unit', 'rfq_line_id')
    def _compute_is_best_price(self):
        # Cheapest unit price per RFQ line, resolved in a single grouped query
        # for the whole recordset instead of one search per line (was N+1 in the
        # vendor comparison view).
        rfq_line_ids = self.rfq_line_id.ids
        min_by_line = {}
        if rfq_line_ids:
            groups = self.env['ebshel.purchase.vendor.quotation.line']._read_group(
                [('rfq_line_id', 'in', rfq_line_ids), ('price_unit', '>', 0)],
                ['rfq_line_id'], ['price_unit:min'])
            min_by_line = {rfq_line.id: price_min for rfq_line, price_min in groups}
        for line in self:
            min_price = min_by_line.get(line.rfq_line_id.id)
            line.is_best_price = bool(
                line.rfq_line_id and line.price_unit
                and min_price is not None and line.price_unit == min_price)

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            self.uom_id = self.product_id.uom_id or self.product_id.uom_id
            self.tax_ids = self.product_id.supplier_taxes_id.filtered(
                lambda t: t.company_id == self.company_id
            )

    # ── Price-revision alert (after a better-price / bargain request) ──────────
    def unlink(self):
        # Quotation lines may only be removed while the quotation is still being
        # prepared or is out with the vendor (Pending / Sent). Once a price has
        # been received / the vendor selected, the lines are locked for the
        # comparison and award trail.
        if not self.env.context.get('ebshel_bypass_line_guard'):
            locked = self.filtered(
                lambda l: l.quotation_id and l.quotation_id.state not in ('draft', 'sent'))
            if locked:
                raise UserError(_(
                    'Vendor quotation lines can only be removed while the quotation '
                    'is Pending or Sent. Affected quotation(s): %s',
                    ', '.join(sorted(set(locked.mapped('quotation_id.display_name'))))))
        return super().unlink()

    def write(self, vals):
        """When a vendor revises a price *after* a better-price request has been
        sent (``bargain_round > 0``), notify the RFQ's procurement officer with a
        before/after note on the quotation chatter."""
        track = 'price_unit' in vals or 'discount' in vals
        old_by_line = {}
        if track:
            for line in self:
                # Only worth snapshotting lines on already-bargained quotations.
                if line.quotation_id.bargain_round > 0:
                    old_by_line[line.id] = (line.price_unit, line.discount)
        res = super().write(vals)
        if old_by_line:
            self.browse(list(old_by_line))._notify_price_revision(old_by_line)
        return res

    def _notify_price_revision(self, old_by_line):
        """Post one chatter message per quotation summarising the revised lines
        and notifying the procurement officer."""
        by_quotation = {}
        for line in self:
            old_price, old_discount = old_by_line.get(line.id, (None, None))
            if old_price is None:
                continue
            rounding = line.currency_id.rounding or 0.01
            old_net = old_price * (1 - (old_discount or 0) / 100)
            new_net = line.price_unit * (1 - (line.discount or 0) / 100)
            # Ignore no-op writes (same effective unit price).
            if float_compare(old_net, new_net, precision_rounding=rounding) == 0:
                continue
            by_quotation.setdefault(line.quotation_id, []).append(
                (line, old_price, old_discount))

        for quotation, changes in by_quotation.items():
            officer = quotation.rfq_id.procurement_officer_id
            rows = Markup('').join(
                Markup(
                    '<tr>'
                    '<td style="padding:4px 8px;border:1px solid #e5e7eb;">%(p)s</td>'
                    '<td style="padding:4px 8px;border:1px solid #e5e7eb;text-align:right;">%(old)s</td>'
                    '<td style="padding:4px 8px;border:1px solid #e5e7eb;text-align:right;font-weight:700;">%(new)s</td>'
                    '<td style="padding:4px 8px;border:1px solid #e5e7eb;text-align:right;color:%(c)s;">%(arrow)s %(delta)s</td>'
                    '</tr>'
                ) % {
                    'p': escape(line.product_id.display_name or line.name or ''),
                    'old': line._fmt_price(old_price, old_discount),
                    'new': line._fmt_price(line.price_unit, line.discount),
                    'arrow': '▼' if line._net_unit(line.price_unit, line.discount)
                             < line._net_unit(old_price, old_discount) else '▲',
                    'c': '#15803d' if line._net_unit(line.price_unit, line.discount)
                         < line._net_unit(old_price, old_discount) else '#b91c1c',
                    'delta': line._fmt_amount(abs(
                        line._net_unit(line.price_unit, line.discount)
                        - line._net_unit(old_price, old_discount))),
                }
                for (line, old_price, old_discount) in changes
            )
            quotation.message_post(
                body=Markup(_(
                    '<p>🔁 <b>%(vendor)s revised their pricing</b> after a '
                    'better-price request (bargain round %(n)s):</p>'
                    '<table style="border-collapse:collapse;margin:8px 0;font-size:13px;">'
                    '<thead><tr style="background:#f3f4f6;">'
                    '<th style="padding:4px 8px;border:1px solid #e5e7eb;text-align:left;">Item</th>'
                    '<th style="padding:4px 8px;border:1px solid #e5e7eb;">Previous</th>'
                    '<th style="padding:4px 8px;border:1px solid #e5e7eb;">Revised</th>'
                    '<th style="padding:4px 8px;border:1px solid #e5e7eb;">Change</th>'
                    '</tr></thead><tbody>%(rows)s</tbody></table>'
                )) % {
                    'vendor': escape(quotation.vendor_id.name
                                     or quotation.new_vendor_name or _('Vendor')),
                    'n': quotation.bargain_round,
                    'rows': rows,
                },
                partner_ids=officer.partner_id.ids if officer else [],
                subtype_xmlid='mail.mt_comment',
            )

    def _net_unit(self, price_unit, discount):
        return (price_unit or 0.0) * (1 - (discount or 0) / 100)

    def _fmt_amount(self, amount):
        cur = self.currency_id
        if cur and cur.symbol:
            return '%s %0.2f' % (cur.symbol, amount or 0.0)
        return '%0.2f' % (amount or 0.0)

    def _fmt_price(self, price_unit, discount):
        net = self._net_unit(price_unit, discount)
        if discount:
            return '%s (-%g%%)' % (self._fmt_amount(net), discount)
        return self._fmt_amount(net)
