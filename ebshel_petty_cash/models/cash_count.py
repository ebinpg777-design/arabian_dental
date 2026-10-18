# Part of ebshel_petty_cash. See LICENSE file for full copyright and licensing details.
from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError


class PettyCashCount(models.Model):
    """Physical count of a fund, by denomination or as one amount.

    Confirming freezes the ledger balance at the count date next to the count.
    A difference is posted as cash over (profit) or short (loss) on the cash
    difference accounts of the fund journal - by a petty cash manager.
    """

    _name = "ebshel.petty.cash.count"
    _description = "Petty Cash Count"
    _inherit = ["mail.thread", "mail.activity.mixin", "ebshel.petty.cash.document.mixin"]
    _order = "date desc, id desc"
    _petty_cash_sequence_code = "ebshel.petty.cash.count"

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("confirmed", "Difference to Post"),
            ("done", "Done"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
        copy=False,
    )
    counted_by_id = fields.Many2one(
        "res.users", default=lambda self: self.env.user, required=True, tracking=True
    )
    witness_id = fields.Many2one("res.users", tracking=True)
    line_ids = fields.One2many("ebshel.petty.cash.count.line", "count_id", copy=True)
    manual_amount = fields.Monetary(
        string="Counted Amount (Manual)",
        help="Used when no denomination is counted.",
    )
    counted_amount = fields.Monetary(compute="_compute_counted_amount", store=True, tracking=True)
    book_balance = fields.Monetary(
        string="Ledger Balance",
        readonly=True,
        copy=False,
        help="Fund balance at the count date, frozen on confirmation.",
    )
    current_book_balance = fields.Monetary(
        compute="_compute_current_book_balance", string="Ledger Balance Now"
    )
    difference = fields.Monetary(
        compute="_compute_difference",
        store=True,
        help="Counted minus ledger balance, once confirmed.",
    )
    expected_difference = fields.Monetary(
        compute="_compute_expected_difference", string="Difference Now"
    )
    note = fields.Text(string="Explanation")

    @api.depends("line_ids.subtotal", "manual_amount")
    def _compute_counted_amount(self):
        for count in self:
            count.counted_amount = (
                sum(count.line_ids.mapped("subtotal")) if count.line_ids else count.manual_amount
            )

    @api.depends("fund_id", "date")
    def _compute_current_book_balance(self):
        for count in self:
            count.current_book_balance = (
                count.fund_id._get_balance(count.date) if count.fund_id and count.date else 0.0
            )

    @api.depends("counted_amount", "current_book_balance")
    def _compute_expected_difference(self):
        for count in self:
            count.expected_difference = count.counted_amount - count.current_book_balance

    @api.depends("counted_amount", "book_balance", "state")
    def _compute_difference(self):
        for count in self:
            count.difference = (
                count.counted_amount - count.book_balance if count.state != "draft" else 0.0
            )

    @api.model
    def _petty_cash_open_states(self):
        return ["draft", "confirmed"]

    def action_load_denominations(self):
        for count in self:
            if count.state != "draft":
                raise UserError(self.env._("Only draft counts can be changed."))
            existing = count.line_ids.denomination_id
            denominations = self.env["ebshel.petty.cash.denomination"].search(
                [("currency_id", "=", count.currency_id.id), ("id", "not in", existing.ids)]
            )
            if not denominations and not existing:
                raise UserError(
                    self.env._(
                        "No denomination is defined for %s (Configuration > Denominations).",
                        count.currency_id.name,
                    )
                )
            count.line_ids = [Command.create({"denomination_id": d.id}) for d in denominations]

    def action_confirm(self):
        for count in self:
            if count.state != "draft":
                raise UserError(self.env._("Only draft counts can be confirmed."))
            count.fund_id._check_custodian()
            book_balance = count.fund_id._get_balance(count.date)
            difference = count.counted_amount - book_balance
            if not count.currency_id.is_zero(difference) and not count.note:
                raise UserError(
                    self.env._(
                        "Explain the %s difference before confirming.",
                        count.currency_id.format(difference),
                    )
                )
            count.write(
                {
                    "book_balance": book_balance,
                    "state": "done" if count.currency_id.is_zero(difference) else "confirmed",
                }
            )
        self.fund_id.activity_unlink(["ebshel_petty_cash.mail_act_petty_cash_count"])

    def action_post_difference(self):
        if not self.env.user.has_group("ebshel_petty_cash.group_petty_cash_manager"):
            raise UserError(self.env._("Only petty cash managers post count differences."))
        for count in self:
            if count.state != "confirmed":
                raise UserError(self.env._("Only confirmed counts with a difference can be posted."))
        for count in self:
            journal = count.fund_id.journal_id.sudo()
            company = count.company_id.sudo()
            if count.difference > 0:
                account = journal.profit_account_id or company.default_cash_difference_income_account_id
            else:
                account = journal.loss_account_id or company.default_cash_difference_expense_account_id
            if not account:
                raise UserError(
                    self.env._(
                        "Set the cash difference accounts on the journal %s.", journal.display_name
                    )
                )
            label = (
                self.env._("%s: cash over", count.name)
                if count.difference > 0
                else self.env._("%s: cash short", count.name)
            )
            move = count._petty_cash_create_move(
                [
                    count._petty_cash_line(count.fund_id.account_id, count.difference, label),
                    count._petty_cash_line(account, -count.difference, label),
                ]
            )
            count.write({"move_id": move.id, "state": "done"})

    def action_cancel(self):
        for count in self:
            if count.move_id:
                if not self.env.user.has_group("ebshel_petty_cash.group_petty_cash_manager"):
                    raise UserError(self.env._("Only petty cash managers cancel posted differences."))
                self._petty_cash_reverse(count.move_id)
        self.state = "cancelled"

    def action_draft(self):
        if any(count.state not in ("cancelled", "confirmed") for count in self):
            raise UserError(self.env._("Only confirmed or cancelled counts go back to draft."))
        self.write({"state": "draft", "move_id": False, "book_balance": 0.0})


class PettyCashCountLine(models.Model):
    _name = "ebshel.petty.cash.count.line"
    _description = "Petty Cash Count Line"
    _order = "count_id, value desc, id"

    count_id = fields.Many2one("ebshel.petty.cash.count", required=True, ondelete="cascade", index=True)
    currency_id = fields.Many2one(related="count_id.currency_id")
    denomination_id = fields.Many2one(
        "ebshel.petty.cash.denomination",
        required=True,
        domain="[('currency_id', '=', currency_id)]",
    )
    value = fields.Float(related="denomination_id.value", store=True, digits=(16, 4))
    kind = fields.Selection(related="denomination_id.kind")
    quantity = fields.Integer()
    subtotal = fields.Monetary(compute="_compute_subtotal", store=True)

    @api.depends("quantity", "value")
    def _compute_subtotal(self):
        for line in self:
            line.subtotal = line.quantity * line.value

    @api.constrains("quantity")
    def _check_quantity(self):
        if any(line.quantity < 0 for line in self):
            raise ValidationError(self.env._("A counted quantity cannot be negative."))
