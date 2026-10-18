# Part of ebshel_petty_cash. See LICENSE file for full copyright and licensing details.
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class PettyCashAdvance(models.Model):
    """Cash handed to a person before the spending (an IOU).

    Paying the advance moves the cash from the fund account to the advance
    account (with the holder as partner). The holder's vouchers are booked
    against the advance account; the settlement returns the unspent cash to
    the fund, or reimburses the overspending, and reconciles the advance.
    """

    _name = "ebshel.petty.cash.advance"
    _description = "Petty Cash Advance"
    _inherit = [
        "mail.thread",
        "mail.activity.mixin",
        "ebshel.petty.cash.document.mixin",
        "ebshel.petty.cash.approval.mixin",
    ]
    _order = "date desc, id desc"
    _petty_cash_sequence_code = "ebshel.petty.cash.advance"

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("to_approve", "To Approve"),
            ("approved", "Approved"),
            ("paid", "Paid Out"),
            ("settled", "Settled"),
            ("refused", "Refused"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
        copy=False,
        index=True,
    )
    holder_id = fields.Many2one(
        "res.partner",
        string="Paid To",
        required=True,
        tracking=True,
        check_company=True,
        default=lambda self: self.env.user.partner_id,
        help="Person receiving the cash and accountable for it.",
    )
    purpose = fields.Char(required=True, tracking=True)
    amount = fields.Monetary(required=True, tracking=True)
    due_date = fields.Date(
        string="Settle By",
        compute="_compute_due_date",
        store=True,
        readonly=False,
        precompute=True,
        tracking=True,
    )
    expense_ids = fields.One2many("ebshel.petty.cash.expense", "advance_id", string="Vouchers")
    expense_count = fields.Integer(compute="_compute_expense_count")
    amount_spent = fields.Monetary(
        compute="_compute_amounts", store=True, help="Paid vouchers booked against the advance."
    )
    amount_due = fields.Monetary(
        compute="_compute_amounts",
        store=True,
        string="Balance",
        help="Cash the holder still has to account for (negative: owed to the holder).",
    )
    settlement_move_id = fields.Many2one(
        "account.move", string="Settlement Entry", readonly=True, copy=False
    )
    settlement_amount = fields.Monetary(
        readonly=True,
        copy=False,
        help="Cash returned to the fund (positive) or paid to the holder (negative) "
        "on settlement.",
    )
    settled_date = fields.Date(readonly=True, copy=False)
    is_overdue = fields.Boolean(compute="_compute_is_overdue", search="_search_is_overdue")
    note = fields.Html()

    @api.depends("date", "company_id")
    def _compute_due_date(self):
        for advance in self:
            if advance.date and not advance.due_date:
                days = (advance.company_id or self.env.company).petty_cash_advance_due_days
                advance.due_date = advance.date + timedelta(days=days)

    @api.depends("amount", "state", "expense_ids.state", "expense_ids.amount_total")
    def _compute_amounts(self):
        for advance in self:
            posted = advance.expense_ids.filtered(lambda e: e.state == "posted")
            advance.amount_spent = sum(posted.mapped("amount_total"))
            advance.amount_due = (
                advance.amount - advance.amount_spent if advance.state in ("paid", "settled") else 0.0
            )
            if advance.state == "settled":
                advance.amount_due = 0.0

    def _compute_expense_count(self):
        for advance in self:
            advance.expense_count = len(advance.expense_ids)

    def _compute_is_overdue(self):
        today = fields.Date.context_today(self)
        for advance in self:
            advance.is_overdue = bool(
                advance.state == "paid" and advance.due_date and advance.due_date < today
            )

    def _search_is_overdue(self, operator, value):
        if operator not in ("=", "!=") or not isinstance(value, bool):
            return NotImplemented
        domain = [
            ("state", "=", "paid"),
            ("due_date", "<", fields.Date.context_today(self)),
        ]
        if (operator == "=") == value:
            return domain
        return ["!", "&", *domain]

    @api.constrains("amount")
    def _check_amount(self):
        for advance in self:
            if advance.currency_id.compare_amounts(advance.amount, 0.0) <= 0:
                raise ValidationError(self.env._("The advance amount must be positive."))

    @api.constrains("date", "due_date")
    def _check_due_date(self):
        for advance in self:
            if advance.due_date and advance.date and advance.due_date < advance.date:
                raise ValidationError(self.env._("The settlement date cannot precede the advance."))

    # ------------------------------------------------------------------
    # Approval hooks
    # ------------------------------------------------------------------
    def _petty_cash_approval_amount(self):
        return self.amount

    def _petty_cash_auto_approved(self):
        self.ensure_one()
        limit = self.fund_id.auto_approve_limit
        return bool(limit) and self.currency_id.compare_amounts(self.amount, limit) <= 0

    def _petty_cash_check_submit(self):
        self.ensure_one()
        fund = self.fund_id
        if fund.max_expense_amount and self.currency_id.compare_amounts(
            self.amount, fund.max_expense_amount
        ) > 0:
            raise UserError(
                self.env._(
                    "%(advance)s exceeds the %(limit)s limit of %(fund)s.",
                    advance=self.name,
                    limit=self.currency_id.format(fund.max_expense_amount),
                    fund=fund.display_name,
                )
            )
        overdue = self.search_count(
            [
                ("holder_id", "=", self.holder_id.id),
                ("company_id", "=", self.company_id.id),
                ("is_overdue", "=", True),
            ],
            limit=1,
        )
        if overdue:
            raise UserError(
                self.env._(
                    "%s has an overdue advance: settle it before requesting a new one.",
                    self.holder_id.name,
                )
            )

    @api.model
    def _petty_cash_open_states(self):
        return ["draft", "to_approve", "approved", "paid"]

    # ------------------------------------------------------------------
    # Pay out, settle, cancel
    # ------------------------------------------------------------------
    def action_pay(self):
        for advance in self:
            if advance.state != "approved":
                raise UserError(self.env._("Only approved advances can be paid out."))
            advance.fund_id._check_custodian()
            advance.fund_id._check_open()
            advance.fund_id._check_cash_available(advance.amount)
        for advance in self:
            account = advance.company_id._petty_cash_advance_account()
            label = f"{advance.name}: {advance.purpose}"
            move = advance._petty_cash_create_move(
                [
                    advance._petty_cash_line(account, advance.amount, label, advance.holder_id),
                    advance._petty_cash_line(
                        advance.fund_id.account_id, -advance.amount, label, advance.holder_id
                    ),
                ]
            )
            advance.write({"move_id": move.id, "state": "paid"})
        self.fund_id._notify_low_balance()

    def action_settle(self):
        self.ensure_one()
        if self.state != "paid":
            raise UserError(self.env._("Only paid-out advances can be settled."))
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Settle %s", self.name),
            "res_model": "ebshel.petty.cash.advance.settle.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_advance_id": self.id},
        }

    def _settle(self, date=None):
        """Return the unspent cash to the fund (or reimburse the holder),
        reconcile the advance account and close the advance."""
        self.ensure_one()
        if self.state != "paid":
            raise UserError(self.env._("Only paid-out advances can be settled."))
        self.fund_id._check_custodian()
        pending = self.expense_ids.filtered(lambda e: e.state in ("draft", "to_approve", "approved"))
        if pending:
            raise UserError(
                self.env._(
                    "Pay or cancel the vouchers of the advance first: %s.",
                    ", ".join(pending.mapped("name")),
                )
            )
        date = date or fields.Date.context_today(self)
        difference = self.currency_id.round(self.amount - self.amount_spent)
        account = self.company_id._petty_cash_advance_account()
        move = self.env["account.move"]
        if not self.currency_id.is_zero(difference):
            if difference < 0:
                self.fund_id._check_cash_available(-difference)
            label = (
                self.env._("%s: unspent cash returned", self.name)
                if difference > 0
                else self.env._("%s: overspending reimbursed", self.name)
            )
            move = self._petty_cash_create_move(
                [
                    self._petty_cash_line(
                        self.fund_id.account_id, difference, label, self.holder_id, date=date
                    ),
                    self._petty_cash_line(account, -difference, label, self.holder_id, date=date),
                ],
                ref=label,
                date=date,
            )
        self.write(
            {
                "state": "settled",
                "settlement_move_id": move.id,
                "settlement_amount": difference,
                "settled_date": date,
            }
        )
        self._reconcile_advance_account()
        self.activity_unlink(["ebshel_petty_cash.mail_act_petty_cash_settle"])
        self.fund_id._notify_low_balance()

    def _reconcile_advance_account(self):
        for advance in self.sudo():
            account = advance.company_id._petty_cash_advance_account()
            moves = advance.move_id | advance.settlement_move_id | advance.expense_ids.filtered(
                lambda e: e.state == "posted"
            ).move_id
            lines = moves.line_ids.filtered(
                lambda aml, acc=account: aml.account_id == acc and not aml.reconciled
            )
            if lines and account.reconcile:
                lines.reconcile()

    def action_cancel(self):
        for advance in self:
            if advance.state not in ("draft", "to_approve", "approved", "paid"):
                raise UserError(self.env._("%s cannot be cancelled.", advance.name))
            if advance.state == "paid":
                advance.fund_id._check_custodian()
                if advance.expense_ids.filtered(lambda e: e.state not in ("cancelled", "refused")):
                    raise UserError(
                        self.env._(
                            "%s has vouchers: cancel them, or settle the advance.", advance.name
                        )
                    )
        for advance in self:
            if advance.move_id:
                self._petty_cash_reverse(advance.move_id)
            advance.activity_unlink(["ebshel_petty_cash.mail_act_petty_cash_approval", "ebshel_petty_cash.mail_act_petty_cash_settle"])
            advance.state = "cancelled"

    def action_new_expense(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("New Voucher"),
            "res_model": "ebshel.petty.cash.expense",
            "view_mode": "form",
            "context": {
                "default_fund_id": self.fund_id.id,
                "default_advance_id": self.id,
                "default_description": self.purpose,
            },
        }

    def action_view_expenses(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Vouchers"),
            "res_model": "ebshel.petty.cash.expense",
            "view_mode": "list,form",
            "domain": [("advance_id", "=", self.id)],
            "context": {"default_fund_id": self.fund_id.id, "default_advance_id": self.id},
        }

    def write(self, vals):
        locked = {"fund_id", "amount", "holder_id"}
        if locked & set(vals) and not self.env.su:
            for advance in self:
                if advance.state not in ("draft", "to_approve"):
                    raise UserError(
                        self.env._("%s is approved; reset it to draft to change it.", advance.name)
                    )
        return super().write(vals)

    @api.model
    def _remind_overdue(self):
        activity_type = self.env.ref("ebshel_petty_cash.mail_act_petty_cash_settle")
        for advance in self.search([("is_overdue", "=", True)]):
            if advance.activity_ids.filtered(lambda a: a.activity_type_id == activity_type):
                continue
            advance.activity_schedule("ebshel_petty_cash.mail_act_petty_cash_settle", user_id=advance.fund_id.custodian_id.id)
