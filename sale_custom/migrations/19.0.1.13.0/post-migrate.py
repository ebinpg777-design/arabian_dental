# -*- coding: utf-8 -*-
"""Fill the new Products column on existing orders.

`sale.order.product_names` is a stored compute, and a stored compute is only recomputed
when one of its dependencies changes — an order written years ago would keep an empty
cell until someone touched a line. Ask the ORM to compute it once for every order.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    orders = env['sale.order'].with_context(active_test=False).search([])
    env.add_to_compute(env['sale.order']._fields['product_names'], orders)
    orders.flush_recordset(['product_names'])
    _logger.info("product names filled for %s orders", len(orders))
