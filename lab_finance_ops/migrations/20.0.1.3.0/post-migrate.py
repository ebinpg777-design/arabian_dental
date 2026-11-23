# -*- coding: utf-8 -*-
"""Each cheque lists the entries it made (2026-11-23): its receipt payments - every
presentation's - their reversals, its bank entry and that one's reversal."""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        WITH own AS (
            SELECT c.id AS cheque_id, p.move_id
              FROM lab_cheque c
              JOIN account_payment p ON p.cheque_id = c.id OR p.id = c.payment_id
             WHERE p.move_id IS NOT NULL
            UNION
            SELECT c.id, c.deposit_move_id
              FROM lab_cheque c
             WHERE c.deposit_move_id IS NOT NULL
        ), linked AS (
            SELECT cheque_id, move_id FROM own
            UNION
            SELECT own.cheque_id, r.id
              FROM own
              JOIN account_move r ON r.reversed_entry_id = own.move_id
        )
        INSERT INTO lab_cheque_account_move_rel (cheque_id, move_id)
        SELECT cheque_id, move_id FROM linked
        ON CONFLICT DO NOTHING
    """)
    _logger.info("lab_finance_ops: %s journal entries linked to their cheques", cr.rowcount)
