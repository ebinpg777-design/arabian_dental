# Part of ebshel_petty_cash. See LICENSE file for full copyright and licensing details.
from datetime import timedelta

from markupsafe import Markup

from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError


class PettyCashExpense(models.Model):
    """A petty cash voucher: one payment out of the fund, split over expense
    lines, approved before it is paid."""

    _name = "ebshel.petty.cash.expense"
    _description = "Petty Cash Voucher"
    _inherit = [
        "mail.thread.main.attachment",
        "mail.activity.mixin",
        "ebshel.petty.cash.document.mixin",
        "ebshel.petty.cash.approval.mixin",
    ]
    _order = "date desc, id desc"
    _petty_cash_sequence_code = "ebshel.petty.cash.expense"

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("to_approve", "To Approve"),
            ("approved", "Approved"),
            ("posted", "Paid"),
            ("refused", "Refused"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
        copy=False,
        index=True,
    )
    description = fields.Char(required=True, tracking=True)
    partner_id = fields.Many2one(
        "res.partner",
        string="Payee",
        tracking=True,
        check_company=True,
        help="Shop or person paid.",
    )
    reference = fields.Char(string="Receipt Reference", copy=False)
    line_ids = fields.One2many(
        "ebshel.petty.cash.expense.line", "expense_id", string="Lines", copy=True
    )
    amount_untaxed = fields.Monetary(compute="_compute_amounts", store=True)
    amount_tax = fields.Monetary(compute="_compute_amounts", store=True, string="Taxes")
    amount_total = fields.Monetary(compute="_compute_amounts", store=True, string="Total", tracking=True)
    amount_total_company = fields.Monetary(
        compute="_compute_amounts",
        store=True,
        currency_field="company_currency_id",
        string="Total (Company Currency)",
    )
    advance_id = fields.Many2one(
        "ebshel.petty.cash.advance",
        string="Against Advance",
        check_company=True,
        index="btree_not_null",
        domain="[('fund_id', '=', fund_id), ('state', '=', 'paid')]",
        tracking=True,
        help="Spent out of a cash advance: the advance is reduced instead of the cash box.",
    )
    replenishment_id = fields.Many2one(
        "ebshel.petty.cash.replenishment",
        readonly=True,
        copy=False,
        index="btree_not_null",
        string="Replenished By",
    )
    paid_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    paid_date = fields.Datetime(readonly=True, copy=False)
    notes = fields.Html()
    attachment_count = fields.Integer(compute="_compute_attachment_count", string="Receipts")
    duplicate_ids = fields.Many2many(
        "ebshel.petty.cash.expense",
        compute="_compute_duplicate_ids",
        string="Possible Duplicates",
    )
    is_duplicate = fields.Boolean(compute="_compute_duplicate_ids")
    category_ids = fields.Many2many(
        "ebshel.petty.cash.category", compute="_compute_category_ids", string="Categories"
    )
    # Risk assessment, made on submission
    risk_score = fields.Integer(readonly=True, copy=False, aggregator="avg")
    risk_level = fields.Selection(
        [("low", "Low"), ("medium", "Medium"), ("high", "High")],
        readonly=True,
        copy=False,
        index=True,
    )
    risk_flags = fields.Text(readonly=True, copy=False)
    risk_flags_html = fields.Html(compute="_compute_risk_flags_html", string="Risk Findings")

    # ------------------------------------------------------------------
    # Amounts (the same tax engine that posts the entry)
    # ------------------------------------------------------------------
    def _prepare_base_lines(self):
        """Base lines of the taxes computation, one per voucher line."""
        self.ensure_one()
        AccountTax = self.env["account.tax"]
        rate = self._petty_cash_rate() if self.fund_id else 1.0
        return [
            AccountTax._prepare_base_line_for_taxes_computation(
                line,
                price_unit=line.amount,
                quantity=1.0,
                discount=0.0,
                tax_ids=line.tax_ids,
                currency_id=self.currency_id or self.env.company.currency_id,
                rate=rate,
                special_mode="total_included",
                account_id=line.account_id,
                partner_id=self.partner_id,
                analytic_distribution=line.analytic_distribution,
                product_id=self.env["product.product"],
                product_uom_id=self.env["uom.uom"],
            )
            for line in self.line_ids
        ]

    def _compute_base_lines(self):
        self.ensure_one()
        company = self.company_id or self.env.company
        AccountTax = self.env["account.tax"]
        base_lines = self._prepare_base_lines()
        AccountTax._add_tax_details_in_base_lines(base_lines, company)
        AccountTax._round_base_lines_tax_details(base_lines, company)
        return base_lines

    @api.depends(
        "line_ids.amount",
        "line_ids.tax_ids",
        "currency_id",
        "date",
        "fund_id",
    )
    def _compute_amounts(self):
        for expense in self:
            untaxed = tax = total_company = 0.0
            for base_line in expense._compute_base_lines():
                line_untaxed, line_tax, line_company = self._tax_details_amounts(base_line["tax_details"])
                untaxed += line_untaxed
                tax += line_tax
                total_company += line_company
            expense.amount_untaxed = untaxed
            expense.amount_tax = tax
            expense.amount_total = untaxed + tax
            expense.amount_total_company = total_company

    @api.model
    def _tax_details_amounts(self, details):
        """(untaxed, tax, total in company currency) of a rounded base line,
        rounding deltas included - the amounts the journal entry will carry."""
        untaxed = details["total_excluded_currency"] + details["delta_total_excluded_currency"]
        tax = sum(tax_data["tax_amount_currency"] for tax_data in details["taxes_data"])
        total_company = (
            details["total_excluded"]
            + details["delta_total_excluded"]
            + sum(tax_data["tax_amount"] for tax_data in details["taxes_data"])
        )
        return untaxed, tax, total_company

    def _compute_attachment_count(self):
        counts = {
            res_id: count
            for res_id, count in self.env["ir.attachment"]._read_group(
                [("res_model", "=", self._name), ("res_id", "in", self.ids)],
                ["res_id"],
                ["__count"],
            )
        }
        for expense in self:
            expense.attachment_count = counts.get(expense.id, 0)

    @api.depends("line_ids.category_id")
    def _compute_category_ids(self):
        for expense in self:
            expense.category_ids = expense.line_ids.category_id

    def _compute_risk_flags_html(self):
        for expense in self:
            flags = (expense.risk_flags or "").splitlines()
            expense.risk_flags_html = (
                Markup("<ul class='mb-0'>%s</ul>") % Markup().join(Markup("<li>%s</li>") % flag for flag in flags)
                if flags
                else False
            )

    @api.depends("amount_total", "partner_id", "reference", "date", "fund_id")
    def _compute_duplicate_ids(self):
        for expense in self:
            days = expense.company_id.petty_cash_duplicate_days
            duplicates = self.browse()
            if days and expense.amount_total and expense.date and (expense.partner_id or expense.reference):
                identity = []
                if expense.partner_id:
                    identity.append(("partner_id", "=", expense.partner_id.id))
                if expense.reference:
                    identity.append(("reference", "=ilike", expense.reference))
                if len(identity) == 2:
                    identity.insert(0, "|")
                duplicates = self.search(
                    [
                        ("id", "!=", expense._origin.id),
                        ("company_id", "=", expense.company_id.id),
                        ("state", "not in", ("refused", "cancelled")),
                        ("amount_total", "=", expense.amount_total),
                        ("date", ">=", expense.date - timedelta(days=days)),
                        ("date", "<=", expense.date + timedelta(days=days)),
                        *identity,
                    ],
                    limit=10,
                )
            expense.duplicate_ids = duplicates
            expense.is_duplicate = bool(duplicates)

    # ------------------------------------------------------------------
    # Constraints and checks
    # ------------------------------------------------------------------
    @api.constrains("advance_id", "fund_id")
    def _check_advance(self):
        for expense in self.filtered("advance_id"):
            if expense.advance_id.fund_id != expense.fund_id:
                raise ValidationError(self.env._("The advance belongs to another fund."))

    def _petty_cash_approval_amount(self):
        return self.amount_total

    def _petty_cash_document_type(self):
        return "expense"

    def _needs_receipt(self):
        self.ensure_one()
        fund = self.fund_id
        if any(line.category_id.receipt_required for line in self.line_ids):
            return True
        if not fund.require_receipt:
            return False
        threshold = fund.receipt_required_amount
        return not threshold or self.currency_id.compare_amounts(self.amount_total, threshold) > 0

    def _petty_cash_check_submit(self):
        self.ensure_one()
        fund = self.fund_id
        if not self.line_ids:
            raise UserError(self.env._("Add at least one line to %s.", self.name))
        if self.currency_id.compare_amounts(self.amount_total, 0.0) <= 0:
            raise UserError(self.env._("The total of %s must be positive.", self.name))
        if fund.max_expense_amount and self.currency_id.compare_amounts(
            self.amount_total, fund.max_expense_amount
        ) > 0:
            raise UserError(
                self.env._(
                    "%(voucher)s exceeds the %(limit)s limit per voucher of %(fund)s. "
                    "Split the spending or use a purchase order.",
                    voucher=self.name,
                    limit=self.currency_id.format(fund.max_expense_amount),
                    fund=fund.display_name,
                )
            )
        if self.requested_by_id != fund.custodian_id and fund.user_ids and (
            self.requested_by_id not in fund.user_ids
        ) and not self.requested_by_id.has_group("ebshel_petty_cash.group_petty_cash_approver"):
            raise UserError(
                self.env._("%s is not a member of this fund.", self.requested_by_id.name)
            )
        self.line_ids._check_line()
        if self._needs_receipt() and not self.attachment_count:
            raise UserError(self.env._("Attach the receipt to %s before submitting it.", self.name))
        if self.advance_id and self.advance_id.state != "paid":
            raise UserError(self.env._("The advance %s is not open.", self.advance_id.name))
        over_budget = self._check_budget()
        score, flags = self._petty_cash_assess_risk(over_budget=over_budget)
        self.write(
            {
                "risk_score": score,
                "risk_level": "high" if score >= 50 else "medium" if score >= 25 else "low",
                "risk_flags": "\n".join(flags) or False,
            }
        )
        if flags:
            self.message_post(
                body=Markup("%s<ul>%s</ul>")
                % (
                    self.env._("Risk assessment: %s / 100", score),
                    Markup().join(Markup("<li>%s</li>") % flag for flag in flags),
                )
            )

    # ------------------------------------------------------------------
    # Budget and risk
    # ------------------------------------------------------------------
    def _month_domain(self):
        self.ensure_one()
        month_start = self.date.replace(day=1)
        next_month = (month_start + timedelta(days=32)).replace(day=1)
        return [
            ("id", "!=", self.id),
            ("fund_id", "=", self.fund_id.id),
            ("state", "in", ("to_approve", "approved", "posted")),
            ("date", ">=", month_start),
            ("date", "<", next_month),
        ]

    def _check_budget(self):
        """Enforce the fund's monthly budget and per-person limit. Returns the
        amount over a budget set to warn (0.0 when within)."""
        self.ensure_one()
        fund = self.fund_id
        currency = self.currency_id
        Expense = self.sudo()
        if fund.user_monthly_limit:
            claimed = sum(
                Expense.search([*self._month_domain(), ("requested_by_id", "=", self.requested_by_id.id)]).mapped(
                    "amount_total"
                )
            )
            if currency.compare_amounts(claimed + self.amount_total, fund.user_monthly_limit) > 0:
                raise UserError(
                    self.env._(
                        "%(user)s would claim %(total)s from %(fund)s this month, over the "
                        "%(limit)s limit per person.",
                        user=self.requested_by_id.name,
                        total=currency.format(claimed + self.amount_total),
                        fund=fund.display_name,
                        limit=currency.format(fund.user_monthly_limit),
                    )
                )
        if not fund.monthly_budget:
            return 0.0
        committed = sum(Expense.search(self._month_domain()).mapped("amount_total"))
        over = currency.round(committed + self.amount_total - fund.monthly_budget)
        if currency.compare_amounts(over, 0.0) <= 0:
            return 0.0
        if fund.budget_policy == "block":
            raise UserError(
                self.env._(
                    "%(voucher)s would take %(fund)s %(over)s over its monthly budget of %(budget)s.",
                    voucher=self.name,
                    fund=fund.display_name,
                    over=currency.format(over),
                    budget=currency.format(fund.monthly_budget),
                )
            )
        return over

    def _petty_cash_assess_risk(self, over_budget=0.0):
        """Score (0-100) and findings of the voucher's red flags."""
        self.ensure_one()
        _ = self.env._
        currency = self.currency_id
        company_currency = self.company_id.currency_id
        today = fields.Date.context_today(self)
        amount_company = self._petty_cash_amount_company()
        Expense = self.sudo()
        score = 0
        flags = []

        def flag(points, message):
            nonlocal score
            score += points
            flags.append(message)

        if self.is_duplicate:
            flag(40, _("Possible duplicate of %s.", ", ".join(self.duplicate_ids.mapped("name"))))
        if not self.attachment_count:
            flag(15, _("No receipt attached."))
        if self.date.weekday() >= 5:
            flag(10, _("Dated on a weekend."))
        if currency.compare_amounts(self.amount_total, 100.0) >= 0 and currency.is_zero(
            self.amount_total % 50.0
        ):
            flag(10, _("Round amount (%s).", currency.format(self.amount_total)))
        late_days = self.company_id.petty_cash_late_days
        if late_days and (today - self.date).days > late_days:
            flag(15, _("Submitted %s days after the spending.", (today - self.date).days))
        if over_budget:
            flag(20, _("Over the monthly budget of the fund by %s.", currency.format(over_budget)))

        # amounts just under an approval threshold, alone or split over the day
        thresholds = sorted(
            {
                level.min_amount
                for level in self.env["ebshel.petty.cash.approval.level"].sudo().search(
                    [("company_id", "in", (False, self.company_id.id)), ("min_amount", ">", 0)]
                )
                if level._petty_cash_applies(self, level.min_amount)
            }
        )
        if self.fund_id.max_expense_amount:
            thresholds.append(self.fund_id._to_company_currency(self.fund_id.max_expense_amount, self.date))
        for threshold in thresholds:
            if company_currency.compare_amounts(amount_company, threshold) < 0 and (
                company_currency.compare_amounts(amount_company, threshold * 0.9) >= 0
            ):
                flag(25, _("Just under the %s approval threshold.", company_currency.format(threshold)))
                break
        same_day = Expense.search(
            [
                ("id", "!=", self.id),
                ("requested_by_id", "=", self.requested_by_id.id),
                ("fund_id", "=", self.fund_id.id),
                ("date", "=", self.date),
                ("state", "in", ("to_approve", "approved", "posted")),
                ("company_id", "=", self.company_id.id),
            ]
        )
        if same_day:
            combined = amount_company + sum(same_day.mapped("amount_total_company"))
            crossed = [
                t
                for t in thresholds
                if company_currency.compare_amounts(amount_company, t) < 0
                and company_currency.compare_amounts(combined, t) >= 0
            ]
            if crossed:
                flag(
                    30,
                    _(
                        "Possible split: with %(others)s the same day, %(total)s crosses the %(threshold)s threshold.",
                        others=", ".join(same_day.mapped("name")),
                        total=company_currency.format(combined),
                        threshold=company_currency.format(crossed[0]),
                    ),
                )
            else:
                flag(10, _("Several vouchers the same day: %s.", ", ".join(same_day.mapped("name"))))

        history = Expense.search(
            [
                ("id", "!=", self.id),
                ("requested_by_id", "=", self.requested_by_id.id),
                ("company_id", "=", self.company_id.id),
                ("state", "=", "posted"),
                ("date", ">=", self.date - timedelta(days=90)),
            ]
        )
        if len(history) >= 3:
            average = sum(history.mapped("amount_total_company")) / len(history)
            if company_currency.compare_amounts(amount_company, 3 * average) > 0:
                flag(
                    15,
                    _(
                        "%(amount)s is more than three times the requester's average (%(average)s).",
                        amount=company_currency.format(amount_company),
                        average=company_currency.format(average),
                    ),
                )
        return min(score, 100), flags

    def _petty_cash_extra_steps(self, levels):
        """A high-risk voucher gets a review by the administrators when no
        administrators' level is in its chain already."""
        self.ensure_one()
        if not (
            self.company_id.petty_cash_risk_review
            and self.risk_level == "high"
            and "managers" not in levels.mapped("approver_type")
        ):
            return []
        approvers = self._petty_cash_fallback_approvers()
        return [
            {
                "name": self.env._("Risk Review"),
                "approval_mode": "any",
                "approver_ids": [(6, 0, approvers.ids)],
            }
        ]

    # ------------------------------------------------------------------
    # Payment
    # ------------------------------------------------------------------
    def action_post(self):
        for expense in self:
            if expense.state != "approved":
                raise UserError(self.env._("Only approved vouchers can be paid."))
            expense.fund_id._check_custodian()
        self._post()

    def _post(self):
        for expense in self:
            expense.fund_id._check_open()
            if not expense.advance_id:
                expense.fund_id._check_cash_available(expense.amount_total)
            elif expense.advance_id.state != "paid":
                raise UserError(self.env._("The advance %s is not open.", expense.advance_id.name))
            # sudo: custodians need no accounting rights; theirs were checked
            move = expense._petty_cash_create_move(expense.sudo()._prepare_move_lines())
            expense.write(
                {
                    "move_id": move.id,
                    "state": "posted",
                    "paid_by_id": self.env.user.id,
                    "paid_date": fields.Datetime.now(),
                }
            )
            expense.message_post(body=self.env._("Paid from %s.", expense.fund_id.display_name))
        self.fund_id._notify_low_balance()

    def _prepare_move_lines(self):
        """Expense lines with their tax lines and the credit of the cash
        account (or of the advance)."""
        self.ensure_one()
        AccountTax = self.env["account.tax"]
        company = self.company_id
        base_lines = self._compute_base_lines()
        AccountTax._add_accounting_data_in_base_lines_tax_details(
            base_lines, company, include_caba_tags=True
        )
        tax_results = AccountTax._prepare_tax_lines(base_lines, company)

        move_lines = []
        for base_line, to_update in tax_results["base_lines_to_update"]:
            line = base_line["record"]
            if not line.account_id:
                raise UserError(
                    self.env._("Set an account on the line %s.", line.name or line.category_id.name)
                )
            move_lines.append(
                {
                    "name": f"{self.name}: {line.name or line.category_id.name or ''}",
                    "account_id": line.account_id.id,
                    "partner_id": self.partner_id.id,
                    "analytic_distribution": line.analytic_distribution,
                    "tax_ids": [Command.set(line.tax_ids.ids)],
                    "tax_tag_ids": to_update["tax_tag_ids"],
                    "amount_currency": to_update["amount_currency"],
                    "balance": to_update["balance"],
                    "currency_id": self.currency_id.id,
                }
            )
        for tax_line in tax_results["tax_lines_to_add"]:
            tax_line.setdefault("partner_id", self.partner_id.id)
            move_lines.append(tax_line)

        amount_currency = sum(line["amount_currency"] for line in move_lines)
        if self.advance_id:
            account = company._petty_cash_advance_account()
            partner = self.advance_id.holder_id
        else:
            account = self.fund_id.account_id
            partner = self.partner_id
        move_lines.append(
            {
                "name": f"{self.name}: {self.description}",
                "account_id": account.id,
                "partner_id": partner.id,
                "currency_id": self.currency_id.id,
                "amount_currency": self.currency_id.round(-amount_currency),
                "balance": 0.0,  # balanced by _petty_cash_create_move
            }
        )
        return move_lines

    def action_cancel(self):
        for expense in self:
            if expense.state not in ("draft", "to_approve", "approved", "posted"):
                raise UserError(self.env._("%s cannot be cancelled.", expense.name))
            if expense.state == "posted":
                expense.fund_id._check_custodian()
                if expense.advance_id.state == "settled":
                    raise UserError(
                        self.env._(
                            "%(voucher)s was settled with the advance %(advance)s; "
                            "it cannot be cancelled any more.",
                            voucher=expense.name,
                            advance=expense.advance_id.name,
                        )
                    )
        for expense in self:
            if expense.move_id:
                self._petty_cash_reverse(expense.move_id)
                expense.message_post(
                    body=self.env._("Entry %s reversed.", expense.move_id.sudo().name)
                )
            expense._petty_cash_cancel_steps()
            expense.write({"state": "cancelled", "replenishment_id": False})

    def action_view_attachments(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Receipts"),
            "res_model": "ir.attachment",
            "view_mode": "kanban,list,form",
            "domain": [("res_model", "=", self._name), ("res_id", "=", self.id)],
            "context": {"default_res_model": self._name, "default_res_id": self.id},
        }

    def action_view_duplicates(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Possible Duplicates"),
            "res_model": self._name,
            "view_mode": "list,form",
            "domain": [("id", "in", self.duplicate_ids.ids)],
        }

    def write(self, vals):
        locked = {"line_ids", "fund_id", "advance_id", "partner_id", "date"}
        if locked & set(vals) and not self.env.su:
            for expense in self:
                if expense.state not in ("draft", "to_approve"):
                    raise UserError(
                        self.env._(
                            "%s is approved or paid; reset it to draft to change it.",
                            expense.name,
                        )
                    )
        return super().write(vals)


class PettyCashExpenseLine(models.Model):
    _name = "ebshel.petty.cash.expense.line"
    _description = "Petty Cash Voucher Line"
    _inherit = ["analytic.mixin"]
    _order = "expense_id, sequence, id"
    _check_company_auto = True

    expense_id = fields.Many2one(
        "ebshel.petty.cash.expense", required=True, ondelete="cascade", index=True
    )
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(related="expense_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="expense_id.currency_id", store=True)
    fund_id = fields.Many2one(related="expense_id.fund_id", store=True, index=True)
    date = fields.Date(related="expense_id.date", store=True)
    state = fields.Selection(related="expense_id.state", store=True, string="Status")
    partner_id = fields.Many2one(related="expense_id.partner_id", store=True, string="Payee")
    category_id = fields.Many2one(
        "ebshel.petty.cash.category",
        required=True,
        check_company=True,
        index=True,
    )
    category_note = fields.Text(related="category_id.note", string="Guidance")
    name = fields.Char(string="Description")
    account_id = fields.Many2one(
        "account.account",
        compute="_compute_account_taxes",
        store=True,
        readonly=False,
        precompute=True,
        check_company=True,
    )
    tax_ids = fields.Many2many(
        "account.tax",
        string="Taxes",
        compute="_compute_account_taxes",
        store=True,
        readonly=False,
        precompute=True,
        check_company=True,
        domain="[('type_tax_use', '=', 'purchase')]",
    )
    amount = fields.Monetary(
        required=True,
        help="Receipt amount, taxes included.",
    )
    amount_untaxed = fields.Monetary(compute="_compute_line_amounts", store=True, string="Untaxed")
    amount_tax = fields.Monetary(compute="_compute_line_amounts", store=True, string="Tax")
    amount_total = fields.Monetary(compute="_compute_line_amounts", store=True, string="Total")
    amount_total_company = fields.Monetary(
        compute="_compute_line_amounts",
        store=True,
        currency_field="company_currency_id",
        string="Total (Company Currency)",
    )
    company_currency_id = fields.Many2one(related="company_id.currency_id", string="Company Currency")

    @api.depends("category_id")
    def _compute_account_taxes(self):
        for line in self:
            if line.category_id:
                line.account_id = line.category_id.account_id
                line.tax_ids = line.category_id.tax_ids

    @api.depends("category_id")
    def _compute_analytic_distribution(self):
        for line in self:
            distribution = (
                line.category_id.analytic_distribution
                or line.expense_id.fund_id.analytic_distribution
            )
            if distribution:
                line.analytic_distribution = distribution

    @api.depends(
        "amount",
        "tax_ids",
        "expense_id.currency_id",
        "expense_id.date",
    )
    def _compute_line_amounts(self):
        for expense, lines in self.grouped("expense_id").items():
            details = {
                base_line["record"]: base_line["tax_details"]
                for base_line in expense._compute_base_lines()
            }
            for line in lines:
                untaxed, tax, total_company = (
                    expense._tax_details_amounts(details[line]) if line in details else (0.0, 0.0, 0.0)
                )
                line.amount_untaxed = untaxed
                line.amount_tax = tax
                line.amount_total = untaxed + tax
                line.amount_total_company = total_company

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._check_editable()
        return lines

    def write(self, vals):
        self._check_editable()
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_locked(self):
        self._check_editable()

    def _check_editable(self):
        if self.env.su:
            return
        for expense in self.expense_id:
            if expense.state not in ("draft", "to_approve"):
                raise UserError(
                    self.env._("%s is approved or paid; reset it to draft to change it.", expense.name)
                )

    @api.constrains("amount")
    def _check_amount(self):
        for line in self:
            if line.currency_id.compare_amounts(line.amount, 0.0) < 0:
                raise ValidationError(self.env._("Voucher line amounts cannot be negative."))

    def _check_line(self):
        """Checks run on submission (the voucher may still change before)."""
        for line in self:
            expense = line.expense_id
            fund = expense.fund_id
            category = line.category_id
            if fund.category_ids and category not in fund.category_ids:
                raise UserError(
                    self.env._(
                        "The category %(category)s is not allowed on %(fund)s.",
                        category=category.name,
                        fund=fund.display_name,
                    )
                )
            if category.partner_required and not expense.partner_id:
                raise UserError(
                    self.env._("The category %s needs a payee on the voucher.", category.name)
                )
            if category.max_amount and line.company_currency_id.compare_amounts(
                line.amount_total_company, category.max_amount
            ) > 0:
                raise UserError(
                    self.env._(
                        "The line %(line)s exceeds the %(limit)s limit of %(category)s.",
                        line=line.name or category.name,
                        limit=line.company_currency_id.format(category.max_amount),
                        category=category.name,
                    )
                )
