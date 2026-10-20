# -*- coding: utf-8 -*-
from odoo import fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _must_check_constrains_date_sequence(self):
        """Not while the lab's history is replayed.

        Since 1 April 2026 the lab numbers invoices per fiscal year,
        ``B2C/2026-27/001``. Odoo 20 reads that as a YEARLY sequence whose year is
        "27", so no 2026 date ever matches it and every such invoice was refused
        ("The Date (01/04/2026) you've entered isn't aligned with the existing
        sequence number"). The numbers are the ones the lab issued; the migration
        keeps them as they are. Everything posted after the migration is checked
        as usual - only the replay context (see migration.backend._migration_env)
        turns the check off.
        """
        if self.env.context.get('migration_replay'):
            return False
        return super()._must_check_constrains_date_sequence()

    def _get_last_sequence(self, relaxed=False, with_prefix=None):
        """A journal's first Odoo 20 receipt continues the receipts migrated from v17.

        The migration brought receipts across as journal entries (PBNK1/2026-27/2195,
        PCSH1/2026-27/2112, ...), not as account.payment. In a journal that numbers
        payments on their own, Odoo looks only at moves WITH a payment, found none, and
        started a series of its own - PBNK1/26-27/0001 (22 receipts on 2026-10-20 had to
        be renumbered; client: "continue the old series"). When no previous payment
        exists, the last migrated receipt of the same fiscal year is the previous
        number instead. Once one payment carries it, Odoo's own lookup takes over.
        """
        last = super()._get_last_sequence(relaxed=relaxed, with_prefix=with_prefix)
        if (last or relaxed or with_prefix or not self.journal_id.payment_sequence
                or not (self.origin_payment_id or self.env.context.get('is_payment'))):
            return last
        dates = self.company_id.compute_fiscalyear_dates(self.date or fields.Date.context_today(self))
        prefix = 'P%s/%s-%s/' % (self.journal_id.code, dates['date_from'].year, dates['date_to'].strftime('%y'))
        self.env.cr.execute("""
            SELECT name FROM account_move
             WHERE journal_id = %s AND origin_payment_id IS NULL AND state = 'posted'
               AND sequence_prefix = %s
          ORDER BY sequence_number DESC LIMIT 1
        """, (self.journal_id.id, prefix))
        row = self.env.cr.fetchone()
        return row[0] if row else last
