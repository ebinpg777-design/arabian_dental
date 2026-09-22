from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    is_petty_cash = fields.Boolean(
        string='Is Petty Cash Payment',
        compute='_compute_is_petty_cash',
        help='Designates this payment as a petty cash payment.')
    petty_cash_partner_id = fields.Many2one(
        'res.partner', string='Petty Cash Holder', readonly=False, copy=False)

    @api.depends('journal_id.is_petty_cash')
    def _compute_is_petty_cash(self):
        for rec in self:
            rec.is_petty_cash = rec.journal_id.is_petty_cash

    def action_post(self):
        for rec in self.filtered('is_petty_cash'):
            if not rec.petty_cash_partner_id:
                raise UserError(_(
                    'Please set a petty cash holder before posting this payment.'))
        res = super().action_post()
        # Stamp the generated move so it is recognised as a petty-cash entry
        # without relying on an expensive account-level depends compute.
        pc_payments = self.filtered(lambda p: p.is_petty_cash and p.move_id)
        for rec in pc_payments:
            rec.move_id.write({
                'is_petty_cash': True,
                'petty_cash_partner_id': rec.petty_cash_partner_id.id,
            })
        # The move is stamped only after ``super().action_post`` has already run
        # ``account.move._post`` — so trigger petty-cash record generation here so
        # a manual payment on a petty cash journal reduces the allocation balance.
        # Skipped when our own transaction flow posts the payment (it sets
        # ``skip_petty_cash_auto_transaction`` and manages the transaction itself).
        if pc_payments and not self.env.context.get('skip_petty_cash_auto_transaction'):
            pc_payments.mapped('move_id')._petty_cash_auto_generate()
        return res
