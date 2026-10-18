# Part of ebshel_petty_cash. See LICENSE file for full copyright and licensing details.
from odoo import api, fields, models


class PettyCashAdvanceSettleWizard(models.TransientModel):
    _name = "ebshel.petty.cash.advance.settle.wizard"
    _description = "Settle a Petty Cash Advance"

    advance_id = fields.Many2one("ebshel.petty.cash.advance", required=True, readonly=True)
    currency_id = fields.Many2one(related="advance_id.currency_id")
    holder_id = fields.Many2one(related="advance_id.holder_id")
    amount = fields.Monetary(related="advance_id.amount", string="Advanced")
    amount_spent = fields.Monetary(related="advance_id.amount_spent", string="Spent")
    difference = fields.Monetary(compute="_compute_difference")
    direction = fields.Selection(
        [("return", "Returned to the fund"), ("reimburse", "Paid to the holder"), ("none", "Nothing")],
        compute="_compute_difference",
    )
    date = fields.Date(required=True, default=fields.Date.context_today)

    @api.depends("amount", "amount_spent")
    def _compute_difference(self):
        for wizard in self:
            difference = wizard.amount - wizard.amount_spent
            wizard.difference = abs(difference)
            if wizard.currency_id.is_zero(difference):
                wizard.direction = "none"
            else:
                wizard.direction = "return" if difference > 0 else "reimburse"

    def action_settle(self):
        self.ensure_one()
        self.advance_id._settle(self.date)
        return {"type": "ir.actions.act_window_close"}
