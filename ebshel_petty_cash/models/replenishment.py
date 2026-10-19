# Part of ebshel_petty_cash. See LICENSE file for full copyright and licensing details.
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class PettyCashReplenishment(models.Model):
    """Cash moved between the bank and the fund.

    A top-up brings the fund back to its float (the classic imprest claim,
    listing the vouchers it covers); a withdrawal returns cash to the bank
    (reducing the float or closing the fund). The counterpart is by default the
    outstanding payments / receipts account of the bank journal, so the bank
    statement line is reconciled against it like any payment.
    """

    _name = "ebshel.petty.cash.replenishment"
    _description = "Petty Cash Replenishment"
    _inherit = [
        "mail.thread",
        "mail.activity.mixin",
        "ebshel.petty.cash.document.mixin",
        "ebshel.petty.cash.approval.mixin",
    ]
    _order = "date desc, id desc"
    _petty_cash_sequence_code = "ebshel.petty.cash.replenishment"

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("to_approve", "To Approve"),
            ("approved", "Approved"),
            ("posted", "Posted"),
            ("refused", "Refused"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
        copy=False,
        index=True,
    )
    kind = fields.Selection(
        [("replenish", "Top-Up"), ("withdraw", "Return to Bank")],
        string="Type",
        default="replenish",
        required=True,
        tracking=True,
    )
    amount = fields.Monetary(required=True, tracking=True)
    suggested_amount = fields.Monetary(
        related="fund_id.replenish_amount", string="Needed to Reach the Float"
    )
    source_journal_id = fields.Many2one(
        "account.journal",
        string="Bank Journal",
        compute="_compute_source_journal_id",
        store=True,
        readonly=False,
        precompute=True,
        check_company=True,
        domain="[('type', 'in', ('bank', 'cash')), ('id', '!=', fund_journal_id)]",
    )
    fund_journal_id = fields.Many2one(related="fund_id.journal_id")
    counterpart_account_id = fields.Many2one(
        "account.account",
        compute="_compute_counterpart_account_id",
        store=True,
        readonly=False,
        precompute=True,
        check_company=True,
        help="Account credited by a top-up (debited by a withdrawal). By default "
        "the outstanding account of the bank journal, reconciled with the bank "
        "statement line.",
    )
    expense_ids = fields.One2many(
        "ebshel.petty.cash.expense", "replenishment_id", string="Vouchers Covered"
    )
    expense_total = fields.Monetary(compute="_compute_expense_total", string="Vouchers Total")
    note = fields.Html()

    @api.depends("fund_id")
    def _compute_source_journal_id(self):
        for replenishment in self:
            if not replenishment.source_journal_id:
                replenishment.source_journal_id = replenishment.fund_id.sudo().source_journal_id

    @api.depends("source_journal_id", "kind")
    def _compute_counterpart_account_id(self):
        for replenishment in self:
            journal = replenishment.source_journal_id.sudo()
            if not journal:
                replenishment.counterpart_account_id = False
                continue
            methods = (
                journal.outbound_payment_method_line_ids
                if replenishment.kind == "replenish"
                else journal.inbound_payment_method_line_ids
            )
            replenishment.counterpart_account_id = (
                methods.payment_account_id[:1]
                or self._default_outstanding_account(journal.company_id, replenishment.kind)
                or journal.default_account_id
            )

    @api.model
    def _default_outstanding_account(self, company, kind):
        if not company:
            return self.env["account.account"]
        xmlid = (
            "account_journal_payment_credit_account_id"
            if kind == "replenish"
            else "account_journal_payment_debit_account_id"
        )
        chart = self.env["account.chart.template"].with_context(
            allowed_company_ids=company.root_id.ids
        )
        return chart.ref(xmlid, raise_if_not_found=False) or company.transfer_account_id

    @api.depends("expense_ids.amount_total")
    def _compute_expense_total(self):
        for replenishment in self:
            replenishment.expense_total = sum(replenishment.expense_ids.mapped("amount_total"))

    @api.constrains("amount")
    def _check_amount(self):
        for replenishment in self:
            if replenishment.currency_id.compare_amounts(replenishment.amount, 0.0) <= 0:
                raise ValidationError(self.env._("The amount must be positive."))

    # ------------------------------------------------------------------
    # Approval hooks
    # ------------------------------------------------------------------
    def _petty_cash_approval_amount(self):
        return self.amount

    def _petty_cash_document_type(self):
        return "replenishment"

    def _petty_cash_check_submit(self):
        self.ensure_one()
        if not self.counterpart_account_id:
            raise UserError(self.env._("Choose the bank journal of %s.", self.name))
        self.action_load_expenses()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_load_expenses(self):
        """Attach the paid vouchers of the fund not covered by a top-up yet."""
        for replenishment in self:
            if replenishment.kind != "replenish" or replenishment.state not in ("draft", "to_approve"):
                continue
            expenses = self.env["ebshel.petty.cash.expense"].search(
                [
                    ("fund_id", "=", replenishment.fund_id.id),
                    ("state", "=", "posted"),
                    ("advance_id", "=", False),
                    ("replenishment_id", "=", False),
                    ("date", "<=", replenishment.date),
                ]
            )
            expenses.sudo().replenishment_id = replenishment

    def action_post(self):
        if not self.env.user.has_group("ebshel_petty_cash.group_petty_cash_manager"):
            raise UserError(self.env._("Only petty cash managers post replenishments."))
        for replenishment in self:
            if replenishment.state != "approved":
                raise UserError(self.env._("Only approved replenishments can be posted."))
            if replenishment.fund_id.state not in ("open", "suspended"):
                replenishment.fund_id._check_open()
            if not replenishment.counterpart_account_id:
                raise UserError(self.env._("Choose the bank journal of %s.", replenishment.name))
            if replenishment.kind == "withdraw":
                replenishment.fund_id._check_cash_available(replenishment.amount)
        for replenishment in self:
            sign = 1 if replenishment.kind == "replenish" else -1
            label = (
                self.env._("%(number)s: top-up of %(fund)s", number=replenishment.name, fund=replenishment.fund_id.name)
                if sign > 0
                else self.env._("%(number)s: cash returned from %(fund)s", number=replenishment.name, fund=replenishment.fund_id.name)
            )
            move = replenishment._petty_cash_create_move(
                [
                    replenishment._petty_cash_line(
                        replenishment.fund_id.account_id, sign * replenishment.amount, label
                    ),
                    replenishment._petty_cash_line(
                        replenishment.counterpart_account_id, -sign * replenishment.amount, label
                    ),
                ]
            )
            replenishment.write({"move_id": move.id, "state": "posted"})
            replenishment.fund_id.activity_unlink(["ebshel_petty_cash.mail_act_petty_cash_replenish"])

    def action_cancel(self):
        for replenishment in self:
            if replenishment.state not in ("draft", "to_approve", "approved", "posted"):
                raise UserError(self.env._("%s cannot be cancelled.", replenishment.name))
            if replenishment.state == "posted" and not self.env.user.has_group(
                "ebshel_petty_cash.group_petty_cash_manager"
            ):
                raise UserError(self.env._("Only petty cash managers cancel posted replenishments."))
        for replenishment in self:
            if replenishment.move_id:
                if replenishment.kind == "replenish":
                    replenishment.fund_id._check_cash_available(replenishment.amount)
                self._petty_cash_reverse(replenishment.move_id)
            replenishment.expense_ids.sudo().replenishment_id = False
            replenishment._petty_cash_cancel_steps()
            replenishment.state = "cancelled"

    def write(self, vals):
        locked = {"fund_id", "amount", "kind", "counterpart_account_id", "source_journal_id"}
        if locked & set(vals) and not self.env.su:
            for replenishment in self:
                if replenishment.state not in ("draft", "to_approve"):
                    raise UserError(
                        self.env._("%s is approved; reset it to draft to change it.", replenishment.name)
                    )
        return super().write(vals)

    def _category_summary(self):
        """Vouchers covered, totalled per category (for the claim report)."""
        self.ensure_one()
        totals = {}
        for line in self.expense_ids.line_ids:
            key = line.category_id.name
            totals[key] = totals.get(key, 0.0) + line.amount_total
        return sorted(totals.items())
