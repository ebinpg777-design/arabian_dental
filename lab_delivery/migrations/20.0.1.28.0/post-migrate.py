# -*- coding: utf-8 -*-
"""Close the dispatches the office had already shipped.

Until this version, validating a transfer at the counter never closed its
dispatch, so every such dispatch sat in "assigned" or "out" for good and refused
the executive's checkout as Delivered. Only those two states are swept here:
they are what the checkout check and the delay engine act on. Drafts are left
to _live_ids, as before.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    stuck = env['lab.delivery'].search([('direction', '=', 'out'), ('state', 'in', ('assigned', 'out'))])
    left = stuck._close_if_shipped()
    _logger.info("lab_delivery: closed %s shipped dispatches, %s genuinely still out",
                 len(stuck) - len(left), len(left))
