# -*- coding: utf-8 -*-
"""Say which partners are clinics, because nothing ever did.

`res.partner.is_clinic` is a plain boolean with no default and no code that sets it:
the only writers are `default_is_clinic` on three create actions, so on a database
migrated from v10 it is TRUE for **0 partners out of 7,278** (7,101 false, 177 null).

That single fact empties most of the manager surface, because every "clinic" screen
filters on it:

  * Planning > Clinics has never shown a row;
  * `lab.coverage` — the whole Clinic Coverage report and the Control Tower's
    rescue alert — is a view over partners that reads 6,855 "never visited";
  * the beat and visit pickers, which do NOT filter, offer all 6,862 partners
    including vendors and staff, so a manager plans rounds against the address book.

Meanwhile 2,185 partners have confirmed sales orders. A partner this lab has taken
work from IS a clinic; that is what the word means here. So the flag is filled in
from the ledger, once, and `sale.order` keeps it true from now on.

Deliberately additive: a partner already flagged by hand stays flagged, and nothing
is ever un-flagged. Somebody's manual answer is better evidence than this query.
(client, 2026-08-29)
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE res_partner p
           SET is_clinic = TRUE
         WHERE COALESCE(p.is_clinic, FALSE) IS NOT TRUE
           AND EXISTS (
               SELECT 1
                 FROM sale_order so
                WHERE so.state = 'sale'
                  AND (so.partner_id = p.id
                       OR so.partner_id IN (SELECT c.id FROM res_partner c
                                             WHERE c.commercial_partner_id = p.id))
           )
    """)
    _logger.info("lab_fieldwork: flagged %s partners as clinics from their order "
                 "history", cr.rowcount)
