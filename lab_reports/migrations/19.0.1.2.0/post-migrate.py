# -*- coding: utf-8 -*-
"""Start the CR number sequence after the highest numeric CR number in use.

CR numbers used to be max()+1 read without a lock, so two clinics saved together
could share one. They now come from an ir.sequence; this lifts it past what
already exists so the first new clinic does not collide. Existing partners keep
their numbers - duplicates already on record are left exactly as they are.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    sequence = env['res.partner']._cr_sequence()
    if sequence:
        _logger.info("CR number sequence starts at %s", sequence.number_next_actual)
