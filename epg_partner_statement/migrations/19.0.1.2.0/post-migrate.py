# -*- coding: utf-8 -*-
"""Move the statement onto the letterhead-in-body paper format.

Until now the statement reserved a 40 mm header band and drew the letterhead into it.
wkhtmltopdf plus Odoo's own header padding pushed that card down over the first lines of
the statement, and left ~33 mm of blank paper above it. The letterhead is now part of the
page body, so the format has to start near the top of the sheet instead.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    report = env.ref('epg_partner_statement.action_report_partner_statement',
                     raise_if_not_found=False)
    paperformat = env.ref('sale_custom.paperformat_lab_invoice', raise_if_not_found=False)
    if report and paperformat:
        report.paperformat_id = paperformat
        _logger.info("statement paper format set to %s", paperformat.name)
