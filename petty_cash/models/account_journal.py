from odoo import api, fields, models


class AccountJournal(models.Model):
    _inherit = 'account.journal'

    is_petty_cash = fields.Boolean(string='Is Petty Cash', default=False, help="Designates this journal as a petty cash journal.")

    def _petty_cash_configure_outstanding(self):
        """Ensure petty-cash payments generate a posted journal entry.

        In Odoo 18 an ``account.payment`` posted on a journal whose payment method
        lines have no ``payment_account_id`` stays in state ``in_process`` and creates
        NO ``account.move`` (so there is no GL entry). Pointing the outstanding account
        at the journal's own cash/bank account makes ``action_post`` produce a posted
        move and move the payment straight to ``paid`` (cash accounts are non-reconcilable).
        """
        for journal in self.filtered(lambda j: j.is_petty_cash and j.default_account_id):
            lines = journal.inbound_payment_method_line_ids | journal.outbound_payment_method_line_ids
            lines.filtered(lambda l: not l.payment_account_id).payment_account_id = journal.default_account_id

    @api.model_create_multi
    def create(self, vals_list):
        journals = super().create(vals_list)
        journals._petty_cash_configure_outstanding()
        return journals

    def write(self, vals):
        res = super().write(vals)
        if {'is_petty_cash', 'default_account_id'} & set(vals):
            self._petty_cash_configure_outstanding()
        return res
