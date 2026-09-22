# -*- coding: utf-8 -*-
"""Stop storing printed invoices, and drop the ones already stored.

The report actions no longer set `attachment`/`attachment_use`, so nothing new is
kept. This clears what earlier prints left behind: those files are what made a
layout change invisible on a reprint, and they are dead weight in the filestore
now that nothing reads them. (client, 2026-08-28)
"""
import logging

_logger = logging.getLogger(__name__)

PATTERNS = ('Tax Invoice - %', 'Tax Invoice np - %', 'Invoice - %')


def migrate(cr, version):
    # Belt and braces: the XML above is the source of truth, but an action edited
    # by hand in the database would keep caching.
    cr.execute("""
        UPDATE ir_act_report_xml
           SET attachment = NULL, attachment_use = FALSE
         WHERE model = 'account.move'
           AND (attachment IS NOT NULL OR attachment_use)
    """)
    _logger.info("invoice reports: stopped caching on %s action(s)", cr.rowcount)

    for pattern in PATTERNS:
        cr.execute("""
            DELETE FROM ir_attachment
             WHERE res_model = 'account.move'
               AND name LIKE %s
        """, (pattern,))
        _logger.info("invoice PDFs: dropped %s stored file(s) matching %s",
                     cr.rowcount, pattern)
