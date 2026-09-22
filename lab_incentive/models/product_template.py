# -*- coding: utf-8 -*-
"""The incentive price (R20).

Client rule (2026-08-09): "incentive calculated based on the sale amount of the
products, but some products has cost is high so incentive sale price configuration
needed in the products for incentive calculation."

Read plainly: for most products the incentive base is what the doctor was actually
charged. For a handful of expensive-to-make products that would over-reward an
executive relative to what the lab keeps, so those products carry their OWN price for
incentive purposes — set once on the product, not negotiated per sheet.
"""
from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    incentive_price = fields.Monetary(
        'Incentive Price', currency_field='currency_id',
        help="What one unit of this product counts as, for incentive calculation "
             "only — never for invoicing. Leave blank to use the line's actual sale "
             "price. Set this on products whose real cost is high enough that paying "
             "incentive on the full sale price would over-reward the executive "
             "relative to what the lab actually keeps.")
