# -*- coding: utf-8 -*-
"""Take every job out of "To Close".

A case here is closed by its DELIVERY, so a job whose bench work is finished
belongs in progress until it goes out - not in a state the scanning board reads
as finished and hides. The rule now says so (mrp_production._compute_state);
this is the same correction for rows already written down, where no recompute is
coming. (client, 2026-09-09)
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    moved = env['mrp.production']._lab_repair_premature_to_close()
    _logger.info("%s job(s) taken out of To Close: %s", len(moved),
                 ', '.join(moved.mapped('name')[:20]) or 'none')
