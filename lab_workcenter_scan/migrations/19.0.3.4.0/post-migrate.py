# -*- coding: utf-8 -*-
"""Release the jobs stranded in "To Close" with steps still on a bench.

Two orders reached the floor this way (MO/284110 and MO/284111, both with two of five
steps finished) and neither could be scanned at all: starting the next step makes core
propagate the start date up to the order, the order reads that as a reschedule and
tries to unplan itself, and unplanning refuses once any step is done. The state is
stored-computed and nothing dirties it again, so the fix in the code cannot reach a row
that is already wrong. (client, 2026-09-09)
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    moved = env['mrp.production']._lab_repair_premature_to_close()
    if moved:
        _logger.info(
            "lab_workcenter_scan: released %s job(s) from a premature To Close: %s",
            len(moved), ', '.join(moved.mapped('name')))
    else:
        _logger.info("lab_workcenter_scan: no job stranded in To Close")
