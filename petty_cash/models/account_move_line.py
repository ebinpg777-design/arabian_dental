from odoo import fields, models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    # The holder lives on the move; journal items are where the analysis happens
    # (group Journal Items by holder, filter a holder's spend, pull it into a pivot),
    # so it is mirrored down here. Related + stored: it follows the move whenever the
    # holder is set or changed, and it is a real column, so it can be grouped, filtered
    # and read without touching account_move. (client, 2026-08-21)
    petty_cash_holder_id = fields.Many2one(
        'res.partner', string='Petty Cash Holder',
        related='move_id.petty_cash_partner_id',
        store=True, readonly=True, index='btree_not_null', copy=False,
        help='The petty cash holder of this line\'s journal entry.')
