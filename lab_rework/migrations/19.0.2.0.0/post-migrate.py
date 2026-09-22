# -*- coding: utf-8 -*-
"""Remove what is left of the old rework document."""
import logging

_logger = logging.getLogger(__name__)

OBSOLETE_MODELS = ('lab.rework', 'lab.rework.line')


def migrate(cr, version):
    if not version:
        return
    for model in OBSOLETE_MODELS:
        table = model.replace('.', '_')
        cr.execute("DELETE FROM ir_model_data WHERE model = %s", (model,))
        cr.execute("DELETE FROM ir_model_fields WHERE model = %s", (model,))
        cr.execute("DELETE FROM ir_model WHERE model = %s", (model,))
        cr.execute("DROP TABLE IF EXISTS %s CASCADE" % table)
    # the old rework columns on sale.order
    for column in ('rework_of_id', 'rework_no_invoice'):
        cr.execute("ALTER TABLE sale_order DROP COLUMN IF EXISTS %s" % column)
    _logger.info("lab.rework document removed; reworks are sale orders now")
