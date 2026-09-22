# -*- coding: utf-8 -*-
"""Drop the cached invoice PDFs so the scan barcode actually reaches a reprint.

The invoice report is `attachment_use`: the first render of a posted invoice is
stored and every later print serves that stored file. A layout change therefore
reaches new invoices only, and the 20,000-odd already printed would keep coming out
without the barcode - which is exactly the set an executive is delivering today.
Deleting the cache costs one re-render per invoice, on demand. (client, 2026-08-28)
"""
import logging

_logger = logging.getLogger(__name__)

PATTERNS = ('Tax Invoice - %', 'Tax Invoice np - %')


def migrate(cr, version):
    for pattern in PATTERNS:
        cr.execute("""
            DELETE FROM ir_attachment
             WHERE res_model = 'account.move'
               AND name LIKE %s
        """, (pattern,))
        _logger.info("invoice PDF cache: dropped %s rows matching %s",
                     cr.rowcount, pattern)
