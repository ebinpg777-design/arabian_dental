# -*- coding: utf-8 -*-
"""Re-sync every sticker paper format: smart shrinking is now disabled on them.

The paper formats are generated from the label formats and only rewritten when a
format is edited, so the existing ones would keep shrinking the page until somebody
touched them. Push the new values once.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    formats = env['epg.sticker.format'].with_context(active_test=False).search([])
    for fmt in formats:
        if fmt.paperformat_id:
            fmt.paperformat_id.write(fmt._paperformat_vals())
    _logger.info("sticker paper formats re-synced: %s", len(formats))
