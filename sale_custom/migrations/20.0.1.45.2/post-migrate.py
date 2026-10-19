# -*- coding: utf-8 -*-
"""Take Ortho Creation's UPI QR off the company (2026-10-19).

The module shipped Ortho's payment QR (UPI 124378187005338@cnrb) as the default for
every company, and the fork carried it into the lab: the lab's invoices and partner
statements asked clinics to pay Ortho. Only a QR that is still exactly that file is
cleared - a QR the lab uploaded itself is left alone.
"""
import logging

_logger = logging.getLogger(__name__)

# sha1 of the shipped image (sale_custom/static/src/img/payment.png, removed)
ORTHO_QR_SHA1 = 'b38ff86137d7cabd5ec9bc6c12a1bf6b468f1d48'


def migrate(cr, version):
    cr.execute("""
        DELETE FROM ir_attachment
         WHERE res_model = 'res.company' AND res_field = 'payment_qr'
           AND checksum = %s
     RETURNING res_id
    """, (ORTHO_QR_SHA1,))
    cleared = [r[0] for r in cr.fetchall()]
    if cleared:
        _logger.warning("sale_custom: removed Ortho Creation's payment QR from company(ies) %s; "
                        "upload the lab's own QR in Settings > Companies", cleared)
