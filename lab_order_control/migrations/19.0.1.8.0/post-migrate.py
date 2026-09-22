# -*- coding: utf-8 -*-
"""Restate `is_urgent_open` on every urgent or emergency order.

The compute never looked at the order's state, and its "finished" test was a substring
match, so cancelled and invoiced jobs stayed on the urgent list. The stored flag only
recomputes when a dependency changes, so the existing rows are restated here.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    orders = env['sale.order'].search(
        ['|', ('priority', 'in', ('urgent', 'emergency')),
         ('is_urgent_open', '=', True)])
    orders._compute_is_urgent_open()
    orders.flush_recordset(['is_urgent_open'])
    _logger.info("is_urgent_open restated on %s orders, %s still urgent",
                 len(orders), len(orders.filtered('is_urgent_open')))
