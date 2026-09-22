# -*- coding: utf-8 -*-
"""Drop the invoice PDFs cached under the previous design (invoice redesign).

Posted invoices are rendered once and kept as an attachment (that is what makes a reprint
instant). After a layout change those stored copies would keep the OLD design forever, so
they are removed here; the next print re-renders and re-caches the new one.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        DELETE FROM ir_attachment
         WHERE res_model = 'account.move'
           AND (name LIKE 'Tax Invoice - %%' OR name LIKE 'Tax Invoice %%')
           AND mimetype = 'application/pdf'
    """)
    _logger.info("cached invoice PDFs cleared: %s", cr.rowcount)
