# Part of ebshel_petty_cash. See LICENSE file for full copyright and licensing details.
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class PettyCashTransfer(models.Model):
    """Cash carried from one fund to another of the same company and currency."""

    _name = "ebshel.petty.cash.transfer"
    _description = "Petty Cash Transfer"
    _inherit = ["mail.thread", "mail.activity.mixin", "ebshel.petty.cash.document.mixin"]
    _order = "date desc, id desc"
    _petty_cash_sequence_code = "ebshel.petty.cash.transfer"

    state = fields.Selection(
        [("draft", "Draft"), ("posted", "Posted"), ("cancelled", "Cancelled")],
        default="draft",
        required=True,
        tracking=True,
        copy=False,
    )
    fund_id = fields.Many2one(string="From Fund")
    dest_fund_id = fields.Many2one(
        "ebshel.petty.cash.fund",
        string="To Fund",
        required=True,
        check_company=True,
        tracking=True,
        domain="[('state', '=', 'open'), ('id', '!=', fund_id), ('currency_id', '=', currency_id)]",
    )
    amount = fields.Monetary(required=True, tracking=True)
    note = fields.Text()

    @api.constrains("fund_id", "dest_fund_id", "amount")
    def _check_funds(self):
        for transfer in self:
            if transfer.fund_id == transfer.dest_fund_id:
                raise ValidationError(self.env._("A transfer needs two different funds."))
            if transfer.dest_fund_id.currency_id != transfer.fund_id.currency_id:
                raise ValidationError(self.env._("Both funds must use the same currency."))
            if transfer.currency_id.compare_amounts(transfer.amount, 0.0) <= 0:
                raise ValidationError(self.env._("The amount must be positive."))

    @api.model
    def _petty_cash_open_states(self):
        return ["draft"]

    def action_post(self):
        for transfer in self:
            if transfer.state != "draft":
                raise UserError(self.env._("Only draft transfers can be posted."))
            transfer.fund_id._check_custodian()
            (transfer.fund_id | transfer.dest_fund_id)._check_open()
            transfer.fund_id._check_cash_available(transfer.amount)
        for transfer in self:
            label = self.env._(
                "%(number)s: %(origin)s to %(dest)s",
                number=transfer.name,
                origin=transfer.fund_id.name,
                dest=transfer.dest_fund_id.name,
            )
            move = transfer._petty_cash_create_move(
                [
                    transfer._petty_cash_line(transfer.dest_fund_id.account_id, transfer.amount, label),
                    transfer._petty_cash_line(transfer.fund_id.account_id, -transfer.amount, label),
                ]
            )
            transfer.write({"move_id": move.id, "state": "posted"})
        self.fund_id._notify_low_balance()

    def action_cancel(self):
        for transfer in self:
            if transfer.state == "posted":
                transfer.fund_id._check_custodian()
                transfer.dest_fund_id._check_cash_available(transfer.amount)
        for transfer in self:
            if transfer.move_id:
                self._petty_cash_reverse(transfer.move_id)
            transfer.state = "cancelled"

    def action_draft(self):
        if any(transfer.state != "cancelled" for transfer in self):
            raise UserError(self.env._("Only cancelled transfers go back to draft."))
        self.write({"state": "draft", "move_id": False})
