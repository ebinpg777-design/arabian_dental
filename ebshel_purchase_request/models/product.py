# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.

from odoo import fields, models

class ProductTemplate(models.Model):
    _inherit = 'product.template'

    purchase_request = fields.Boolean(
        'Procure via Purchase Request', company_dependent=True,
        help='When reordering rules or procurement trigger a buy for this '
             'product, generate an internal Purchase Request to be approved '
             'instead of a direct RFQ.',
    )
    
class ProductProduct(models.Model):
    _inherit = 'product.product'

    def _get_purchase_description(self):
        """Multiline purchase description (Odoo 19 replacement for the removed
        get_product_multiline_description_purchase helper)."""
        self.ensure_one()
        name = self.display_name
        if self.description_purchase:
            name += '\n' + self.description_purchase
        return name
