# -*- coding: utf-8 -*-
import re

from odoo import models
from odoo.tools.misc import formatLang


class PurchaseOrder(models.Model):
    """What the purchase print-outs need to look like the lab's tax invoice.

    The client asked for the PO "like the current invoice print-out" (2026-11-27):
    the same letterhead card, the line table with each GST in its own column, the
    total in words and the closing bar. These are the purchase counterparts of the
    invoice's helpers on account.move (sale_custom), kept here so sale_custom needs
    no dependency on purchase.
    """
    _inherit = 'purchase.order'

    def _lab_print_lines(self):
        """The lines a print-out shows: sections and notes, and every product line
        with a quantity (a zero line is a leftover, as on Odoo's own print)."""
        self.ensure_one()
        return self.order_line.filtered(lambda l: l.display_type or l.product_qty)

    def _lab_print_title(self):
        """(title, number label) - an order not yet confirmed is a request for
        quotation, whichever print button made it."""
        self.ensure_one()
        if self.state in ('draft', 'sent', 'to approve'):
            return ('REQUEST FOR QUOTATION', 'RFQ No.')
        return ('PURCHASE ORDER', 'PO No.')

    def _lab_money(self, amount):
        """Symbol first, as the invoice prints money (₹ 1,234.00)."""
        self.ensure_one()
        currency = self.currency_id or self.company_id.currency_id
        number = formatLang(self.env, amount or 0.0, digits=currency.decimal_places)
        return '%s %s' % (currency.symbol or '', number)

    def _lab_rate(self, amount):
        """A unit price: money, with the third decimal a purchase price can carry
        (Product Price precision) kept when it is there - 12.345 a gram stays
        12.345, 185.710 prints as 185.71."""
        self.ensure_one()
        currency = self.currency_id or self.company_id.currency_id
        digits = max(self.env['decimal.precision'].precision_get('Product Price'),
                     currency.decimal_places)
        number = formatLang(self.env, amount or 0.0, digits=digits)
        while digits > currency.decimal_places and number.endswith('0'):
            number, digits = number[:-1], digits - 1
        return '%s\u00a0%s' % (currency.symbol or '', number)

    def _lab_label(self, line):
        """The line's description (product, then any extra text), without the
        blank lines a typed description can carry."""
        return '\n'.join(part for part in (line.label or '').splitlines() if part.strip())

    def _lab_words(self, amount):
        """The amount in words the way the invoice writes it: "Four Hundred Rupees
        and Zero Paise", every word capitalised but "and"."""
        self.ensure_one()
        text = (self.currency_id or self.company_id.currency_id).amount_to_text(abs(amount)) or ''
        text = text.replace('INR', 'Rupees').replace('Cents', 'Paise').replace('Cent', 'Paise')
        if text and 'pais' not in text.lower():
            text += ' and Zero Paise'
        return re.sub(r'\b[A-Za-z]+\b',
                      lambda m: 'and' if m.group().lower() == 'and' else m.group()[:1].upper() + m.group()[1:],
                      text)

    def _lab_hsn(self, line):
        product = line.product_id
        return (product.hsn_number or '').strip() if product and 'hsn_number' in product._fields else ''

    def _lab_gst_table(self):
        """(group names, {line id: {group name: (rate, amount)}}) - each line's GST
        split into its SGST / CGST (or IGST) columns, computed the way the invoice
        does it, with the company's rounding method."""
        self.ensure_one()
        groups, table = [], {}
        rounding = self.company_id.tax_calculation_rounding_method
        for line in self._lab_print_lines().filtered(lambda l: not l.display_type):
            cells = {}
            if line.tax_ids:
                res = line.tax_ids.compute_all(
                    line.price_unit * (1 - (line.discount or 0.0) / 100.0),
                    currency=self.currency_id, quantity=line.product_qty,
                    product=line.product_id, partner=self.partner_id,
                    rounding_method=rounding)
                for data in res['taxes']:
                    tax = self.env['account.tax'].browse(data['id'])
                    name = tax.tax_group_id.name or tax.name
                    if name not in groups:
                        groups.append(name)
                    rate, amount = cells.get(name, (0.0, 0.0))
                    if tax.amount_type == 'percent':
                        rate += tax.amount
                    cells[name] = (rate, amount + data['amount'])
            table[line.id] = cells
        return groups, table

    def _lab_column_totals(self, gst=None):
        """The foot of the line table. The quantity is summed only when every line
        counts in the same unit: twelve bottles and ten boxes add up to nothing."""
        self.ensure_one()
        names, table = gst or self._lab_gst_table()
        lines = self._lab_print_lines().filtered(lambda l: not l.display_type)
        sums = {name: 0.0 for name in names}
        for line in lines:
            for name, (_rate, amount) in table.get(line.id, {}).items():
                sums[name] = sums.get(name, 0.0) + amount
        qty_text = ''
        if lines and len(lines.uom_id) <= 1:
            digits = self.env['purchase.order.line']._fields['product_qty'].get_digits(self.env)
            qty_text = formatLang(self.env, sum(lines.mapped('product_qty')),
                                  digits=digits[1] if digits else 2)
        return {
            'qty_text': qty_text,
            'taxable': sum(lines.mapped('price_subtotal')),
            'gst': sums,
            'total': sum(lines.mapped('price_total')),
        }

    def _lab_address_lines(self, partner):
        """The partner's address as lines, written the way the letterhead writes the
        lab's own: a space after every comma, none before, and no country line for
        an address in the company's own country."""
        country = partner.country_id
        lines = []
        for line in partner._display_address(without_name=True).splitlines():
            line = re.sub(r'\s*,\s*', ', ', ' '.join(line.split())).strip(' ,')
            if not line or (country and country == self.company_id.country_id and line == country.name):
                continue
            lines.append(line)
        return lines

    def _lab_deliver_to(self):
        """Where the goods go: the drop-ship address, else the receiving warehouse's."""
        self.ensure_one()
        if self.dest_address_id:
            return self.dest_address_id
        picking_type = self.picking_type_id if 'picking_type_id' in self._fields else False
        warehouse = picking_type and picking_type.warehouse_id
        return (warehouse and warehouse.partner_id) or self.company_id.partner_id
