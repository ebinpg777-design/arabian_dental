# -*- coding: utf-8 -*-
"""Bring two sets of stored figures back to what their records say.

* `lab.case.is_urgent_open` carried two stacked @api.depends and the outer one
  replaced the inner, so the flag never followed priority or state. Recomputed
  in SQL - it is one expression over two columns of the same row.
* `lab.target` achieved figures depended only on the executive and the month,
  so every running target still shows what it said the day it was set. Closed
  targets keep what they closed on.

(2026-09-15)
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE lab_case
           SET is_urgent_open = (priority IN ('urgent', 'emergency')
                                 AND state IN ('draft', 'submitted'))
         WHERE is_urgent_open IS DISTINCT FROM (priority IN ('urgent', 'emergency')
                                                AND state IN ('draft', 'submitted'))
    """)
    _logger.info("lab_fieldwork: corrected is_urgent_open on %s case(s)",
                 cr.rowcount)
    env = api.Environment(cr, SUPERUSER_ID, {})
    targets = env['lab.target'].search([('state', 'in', ('draft', 'open'))])
    targets._refresh_done()
    _logger.info("lab_fieldwork: refreshed %s running target(s)", len(targets))
