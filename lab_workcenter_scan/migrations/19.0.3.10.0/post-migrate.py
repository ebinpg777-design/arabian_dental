# -*- coding: utf-8 -*-
"""Achieved % becomes a fraction.

Every row written before this version stored `100 * done / target`, and the
views draw the field with Odoo's percentage widget, which multiplies by 100
again - so half a target read as 5000%. Every existing row is in the old unit,
so all of them are divided once; version-gated, this runs a single time.
(client, 2026-09-14)
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("UPDATE lab_work_target SET achieved_pct = achieved_pct / 100.0 "
               "WHERE achieved_pct <> 0")
    _logger.info("Achieved %%: %s target row(s) converted to a fraction", cr.rowcount)
