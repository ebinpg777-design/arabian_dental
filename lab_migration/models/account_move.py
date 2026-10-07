# -*- coding: utf-8 -*-
from odoo import models


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
