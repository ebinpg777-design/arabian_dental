# -*- coding: utf-8 -*-
"""The first unit at one price, the rest at another.

An acrylic partial denture is priced by the unit: the first unit is one product
(AC1002 "Normal Acrylic RPD SINGLE UNIT"), every further unit another (AC1003
"... ADDITIONAL PER UNIT"). Entered as one line of three units, the case went to
the office as three single units and somebody split it by hand. Now the product
says which product its further units are, and a case line of more than one unit
is split as it is saved: one unit of the product, the rest as its additional
units - and the teeth with them, the first tooth on the first unit.
(client, 2026-11-26: "Some product's first quantity has a price and the rest has
another price, so we created separate products. When the executive adds the first
product with more than 1 qty, split it into the other product.")
"""
import re

from odoo import _, api, fields, models

FDI = re.compile(r'\b[1-8][1-8]\b')


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    lab_extra_unit_product_id = fields.Many2one(
        'product.product', 'Additional Units As',
        domain="[('sale_ok', '=', True), ('product_tmpl_id', '!=', id)]",
        help="Where only the first unit is sold at this product's price: the product "
             "the further units are sold as. A case line of more than one unit of this "
             "product is split into one unit of it and the rest of that product.")


class LabCaseLine(models.Model):
    _inherit = 'lab.case.line'

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        if not self.env.context.get('lab_extra_split'):
            lines._lab_split_extra_units()
        return lines

    def write(self, vals):
        result = super().write(vals)
        if ('quantity' in vals or 'product_id' in vals) \
                and not self.env.context.get('lab_extra_split'):
            self._lab_split_extra_units()
        return result

    @api.model
    def _lab_teeth_split(self, teeth, quantity):
        """(first, rest) of a line's teeth: the first tooth goes with the first unit
        when there is one tooth per unit; otherwise the teeth stay where they were
        written, on the first line, rather than be shared out by guesswork."""
        found = FDI.findall(teeth or '')
        if len(found) > 1 and len(found) == round(quantity):
            return found[0], ', '.join(found[1:])
        return teeth or False, False

    def _lab_split_extra_units(self):
        Line = self.with_context(lab_extra_split=True)
        for line in self.exists():
            extra = line.product_id.product_tmpl_id.lab_extra_unit_product_id
            if not extra or extra == line.product_id or line.quantity <= 1:
                continue
            rest = line.quantity - 1
            first_teeth, rest_teeth = self._lab_teeth_split(line.teeth, line.quantity)
            line.with_context(lab_extra_split=True).write(
                {'quantity': 1, 'teeth': first_teeth})
            # further units of the same appliance - the same jaw and shade - are one line
            twin = line.case_id.line_ids.filtered(
                lambda l: l != line and l.product_id == extra and l.ul == line.ul
                and l.colour_id == line.colour_id)[:1]
            if twin:
                twin.with_context(lab_extra_split=True).write({
                    'quantity': twin.quantity + rest,
                    'teeth': ', '.join(filter(None, [twin.teeth, rest_teeth])) or False,
                })
            else:
                Line.create({
                    'case_id': line.case_id.id, 'sequence': line.sequence,
                    'product_id': extra.id, 'quantity': rest, 'ul': line.ul,
                    'colour_id': line.colour_id.id or False, 'teeth': rest_teeth,
                    'cast': line.cast or False, 'is_urgent': line.is_urgent,
                })
            line.case_id.message_post(body=_(
                "%(qty)s units of %(product)s: the first as %(product)s, the other "
                "%(rest)s as %(extra)s.", qty=('%g' % (rest + 1)),
                product=line.product_id.display_name, rest=('%g' % rest),
                extra=extra.display_name))
