# -*- coding: utf-8 -*-
from odoo import models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    def _auto_init(self):
        res = super()._auto_init()
        # The FIFO countback behind every open-money figure walks each clinic's
        # receivable lines in (partner, date, id) order and sums debits. Without this
        # index Postgres read the table and sorted 1,45,000 rows on every dashboard
        # load — an external merge sort spilling 4.4 MB to disk. Ordered and covering,
        # the same pass becomes an index-only scan: 586 ms -> 326 ms on arabian_dental_v19
        # (measured 2026-08-21).
        #
        # The INCLUDE columns are exactly the ones the countback reads, so the scan
        # never goes to the heap. `debit > 0` is deliberately NOT in the predicate:
        # the same index also serves the credit side — what each clinic has paid.
        self.env.cr.execute("""
            CREATE INDEX IF NOT EXISTS account_move_line_lab_countback_idx
                ON account_move_line (partner_id, date, id)
                INCLUDE (debit, credit, move_id, account_id, parent_state, company_id)
             WHERE partner_id IS NOT NULL
        """)
        return res
