# -*- coding: utf-8 -*-
"""Drop the duplicated patient list from account_move_line.

`account_move_line.patient_name` was a stored copy of `account_move.patient_names`,
kept in step by a compute that depended on the parent move's lines - so touching one
invoice line rewrote every line of that move. It is now a related field, and Odoo
never drops a column on its own, so the old one is removed here: 2,44,000 rows of
duplicated text that nothing reads any more.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'account_move_line' AND column_name = 'patient_name'
    """)
    if not cr.fetchone():
        return
    cr.execute("ALTER TABLE account_move_line DROP COLUMN patient_name")
    _logger.info("sale_custom: dropped account_move_line.patient_name "
                 "(now related to account_move.patient_names)")
