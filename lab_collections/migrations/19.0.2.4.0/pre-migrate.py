# -*- coding: utf-8 -*-
"""Drop the SQL view 19.0.2.3.0 shipped, so the table can take its place.

The open items were briefly a database view. Postgres re-planned the whole FIFO
window aggregate for every filtered query against it - a bare count took 32
seconds - so they are ordinary (transient) rows now, filled from the same
countback. The view has to go before Odoo tries to create a table by that name.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("DROP VIEW IF EXISTS lab_collection_open_item CASCADE")
    _logger.info("lab_collections: open-item SQL view dropped; now a table")
