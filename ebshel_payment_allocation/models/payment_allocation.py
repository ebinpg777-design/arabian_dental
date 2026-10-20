# Part of ebshel_payment_allocation. See LICENSE file for full copyright and licensing details.
"""One open invoice or bill a payment may settle, and for how much.

The payment form lists the partner's open items as these lines (see
``account.payment._load_open_items``); ticking one allocates it. When the payment is
confirmed its receivable or payable journal item is split, one item per allocation,
and each is reconciled with its invoice; lines left unticked are dropped at that
point, so a confirmed payment keeps only what it settled.
"""
from odoo import api, fields, models


class PaymentAllocation(models.Model):
    _name = "account.payment.allocation"
    _description = "Payment Allocation"
    _order = "date_due, date, id"

    payment_id = fields.Many2one(
        "account.payment", string="Payment", required=True, ondelete="cascade", index=True
    )
    move_line_id = fields.Many2one(
        "account.move.line",
        string="Open Item",
        required=True,
        ondelete="cascade",
        index=True,
        help="The receivable or payable journal item of the invoice or bill.",
    )
    move_id = fields.Many2one(
        related="move_line_id.move_id", string="Invoice / Bill", store=True, index=True
    )
    company_id = fields.Many2one(related="payment_id.company_id", store=True)
    currency_id = fields.Many2one(related="payment_id.currency_id")
    date = fields.Date(related="move_line_id.date", store=True)
    date_due = fields.Date(related="move_line_id.date_maturity", string="Due Date", store=True)
    ref = fields.Char(related="move_id.ref", string="Reference")
    payment_state = fields.Selection(related="move_id.payment_state")
    amount = fields.Monetary(
        compute="_compute_amounts",
        string="Amount",
        help="What the invoice or bill booked on the partner's account.",
    )
    amount_open = fields.Monetary(
        compute="_compute_amounts",
        string="Open Amount",
        help="What is still unpaid on it right now.",
    )
    selected = fields.Boolean(string="Select")
    allocate = fields.Monetary(
        string="Allocate", help="How much of this payment goes to this invoice or bill."
    )

    _item_once = models.Constraint(
        "unique(payment_id, move_line_id)",
        "An open item can only be listed once on a payment.",
    )

    @api.depends("move_line_id.amount_currency", "move_line_id.amount_residual_currency")
    def _compute_amounts(self):
        for line in self:
            item = line.move_line_id
            line.amount = abs(item.amount_currency)
            line.amount_open = abs(item.amount_residual_currency)

    @api.depends("move_id.name", "move_line_id.display_name")
    def _compute_display_name(self):
        for line in self:
            line.display_name = line.move_id.name or line.move_line_id.display_name or ""

    # -- ticking ----------------------------------------------------------------------

    @api.onchange("selected")
    def _onchange_selected(self):
        for line in self:
            if not line.selected:
                line.allocate = 0.0
            elif not line.allocate:
                line.allocate = line._suggested_allocation()

    @api.onchange("allocate")
    def _onchange_allocate(self):
        for line in self:
            line.selected = line.allocate > 0

    def _suggested_allocation(self):
        """The whole open amount - or, once the user has typed the payment amount,
        what is left of it after the other ticked items."""
        self.ensure_one()
        payment = self.payment_id
        if payment.amount_from_allocation or not payment.amount:
            return self.amount_open
        others = sum(
            other.allocate
            for other in payment.allocation_ids
            if other.selected and other.move_line_id != self.move_line_id
        )
        remaining = payment.amount - others
        if remaining <= 0:
            # nothing left: the whole item, and the form says the amount is short
            return self.amount_open
        return min(self.amount_open, remaining)


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    allocation_id = fields.Many2one(
        "account.payment.allocation",
        string="Payment Allocation",
        ondelete="set null",
        index="btree_not_null",
        copy=False,
        help="The allocation this receivable or payable item of a payment was split off for.",
    )
