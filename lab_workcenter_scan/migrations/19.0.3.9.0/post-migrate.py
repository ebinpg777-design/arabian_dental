# -*- coding: utf-8 -*-
"""Give the jobs already on the floor their second arch.

A case sold as Upper & Lower is one appliance in two pieces, and until now the
routing was a single chain: one job card, one queue entry, and no way to hand
the upper on while the lower was still at the wire. New orders are split at
confirmation; the cases already in production need the same treatment or the
floor would run two rules at once.

Only OPEN cases, and only ones whose steps carry no arch yet - the split is
idempotent by construction, so running this twice changes nothing. Jobs whose
work has already been accepted or handed over keep those stamps on the upper
chain: the piece somebody is holding is the one they started.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    cases = env['mrp.production'].search([
        ('ul', '=', 'ul'),
        ('state', 'not in', ('done', 'cancel')),
    ])
    if not cases:
        return
    made = cases._lab_split_arches()
    _logger.info(
        "Upper/Lower split: %s open case(s) examined, %s second-arch job(s) created",
        len(cases), len(made))
