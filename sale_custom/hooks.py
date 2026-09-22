# -*- coding: utf-8 -*-
"""Dates read dd/mm/yyyy, everywhere.

The lab is in India: 08/09/2026 must mean 8 September, not 9 August. Odoo formats every
date - in the web client and in every report - from the LANGUAGE's `date_format`, and the
English (US) locale ships %m/%d/%Y. Rather than force a format in each template (which
would leave the screens disagreeing with the print-outs), the active languages are
switched to %d/%m/%Y once, here and in the matching migration.
"""
import logging

_logger = logging.getLogger(__name__)

DATE_FORMAT = '%d/%m/%Y'


def _set_date_format(env):
    langs = env['res.lang'].with_context(active_test=False).search([('active', '=', True)])
    changed = langs.filtered(lambda l: l.date_format != DATE_FORMAT)
    if changed:
        changed.write({'date_format': DATE_FORMAT})
        _logger.info("date format set to %s for: %s", DATE_FORMAT, ', '.join(changed.mapped('code')))


def post_init_hook(env):
    _set_date_format(env)
