# -*- coding: utf-8 -*-
"""An incentive price used to apply just by being set; now it needs its basis.

A product that carried one would otherwise quietly revert to the full billed amount
and overpay without anyone noticing, so every product with an incentive price is put
on the 'Incentive price' basis. (Ortho, 2026-10-08)
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    # Not ('incentive_price', '!=', 0): on Odoo 20 that also matches the products
    # that never had one (NULL), and would put every product on 'price'. A
    # comparison leaves NULL out.
    products = env['product.template'].with_context(active_test=False).search([
        '|', ('incentive_price', '>', 0), ('incentive_price', '<', 0),
        ('incentive_basis', '=', 'sale')])
    if products:
        products.write({'incentive_basis': 'price'})
    _logger.info("lab_incentive: incentive basis set to 'price' on %d product(s) "
                 "that carried an incentive price", len(products))
