# -*- coding: utf-8 -*-
"""Stop work still running for orders cancelled before the module stopped it.

`_action_cancel` has stopped an order's manufacturing since 19.0.1.9.0, but
only from the moment it shipped. Anything cancelled before that kept whatever
the floor had already been told to make: on the live database in September 2026
four cancelled orders still had six open MOs between them, two of them with a
step in progress.

Nothing reaches those except a sweep, so this runs one, once, through the
module's own `_lab_stop_productions` - which skips finished work, cancels the
rest and writes on the order what it stopped, exactly as a cancel today would.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    if 'mrp.production' not in env:
        return
    result = env['sale.order']._lab_stop_orphan_productions()
    if result['orders']:
        _logger.info(
            "lab_order_control: stopped %s job(s) under %s cancelled order(s): %s",
            len(result['names']), len(result['orders']),
            ', '.join(result['orders'].mapped('name')))
    else:
        _logger.info("lab_order_control: no cancelled order had work still running")
