# -*- coding: utf-8 -*-
"""Put back the jobs that were stranded in "To Close" with steps still to do.

`mrp.production._compute_state` could fire core's no-work-orders branch against a
moment where `workorder_ids` read empty, closing a job as soon as its FIRST step was
accepted — `button_start` sets `qty_producing` to the full quantity there. Nothing
dirtied the field afterwards, so the wrong value stayed in the database.

The override in `models/mrp_production.py` stops it happening again; this repairs what
it already did. Recomputing is enough — with the override loaded, `_compute_state`
now returns the state each job should have had. (client, 2026-08-29)
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})

    stranded = env['mrp.production'].search([
        ('state', '=', 'to_close'), ('workorder_ids', '!=', False),
    ]).filtered(
        lambda m: any(w.state not in ('done', 'cancel') for w in m.workorder_ids))

    if not stranded:
        _logger.info("work centre scan: no job stranded in 'to close'")
        return

    names = stranded.mapped('name')
    stranded._compute_state()
    env.flush_all()
    _logger.info("work centre scan: %s job(s) taken back out of 'to close': %s",
                 len(names), ', '.join(names[:20]))
