# -*- coding: utf-8 -*-
"""Fill `statement_date` on the journal items that already exist.

A newly added stored field is computed for records written afterwards, not for the
ledger already on the books — and the statement now filters, sorts and cuts its opening
balance on this column, so leaving it null would empty every statement of account on the
database.

One UPDATE rather than an ORM recompute: the value is COALESCE of two stored columns on
the same row, and there are hundreds of thousands of rows.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'account_move_line' AND column_name = 'statement_date'
    """)
    if not cr.fetchone():
        return
    cr.execute("""
        UPDATE account_move_line
           SET statement_date = COALESCE(original_date, date)
         WHERE statement_date IS DISTINCT FROM COALESCE(original_date, date)
    """)
    _logger.info("epg_partner_statement: dated %s journal items for statements",
                 cr.rowcount)
