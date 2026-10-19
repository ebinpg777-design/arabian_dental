# Part of ebshel_petty_cash. See LICENSE file for full copyright and licensing details.
from datetime import timedelta

from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare


class PettyCashFund(models.Model):
    """An imprest petty cash fund.

    A fund is a cash journal with its own liquidity account, a custodian and a
    float. Its balance is never stored: it is the balance of the posted journal
    items on the fund account, so the fund always agrees with the general
    ledger - whatever posted the entries.
    """

    _name = "ebshel.petty.cash.fund"
    _description = "Petty Cash Fund"
    _inherit = ["mail.thread", "mail.activity.mixin", "analytic.mixin"]
    _order = "sequence, name"
    _check_company_auto = True

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(
        required=True,
        size=5,
        help="Short code, also used for the cash journal created with the fund.",
    )
    sequence = fields.Integer(default=10)
    color = fields.Integer()
    active = fields.Boolean(default=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("open", "Open"),
            ("suspended", "Suspended"),
            ("closed", "Closed"),
        ],
        default="draft",
        required=True,
        tracking=True,
        copy=False,
    )
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    company_currency_id = fields.Many2one(related="company_id.currency_id", string="Company Currency")
    currency_id = fields.Many2one(
        "res.currency",
        required=True,
        default=lambda self: self.env.company.currency_id,
        tracking=True,
    )
    journal_id = fields.Many2one(
        "account.journal",
        string="Cash Journal",
        check_company=True,
        copy=False,
        domain="[('type', '=', 'cash')]",
        help="Cash journal of the fund. Left empty, one is created with the fund.",
    )
    account_id = fields.Many2one(
        related="journal_id.default_account_id", string="Cash Account", store=True
    )
    custodian_id = fields.Many2one(
        "res.users",
        required=True,
        default=lambda self: self.env.user,
        tracking=True,
        help="Holds the cash: pays approved vouchers and advances, counts the box.",
    )
    user_ids = fields.Many2many(
        "res.users",
        "ebshel_petty_cash_fund_member_rel",
        "fund_id",
        "user_id",
        string="Members",
        help="Users who may raise vouchers and advance requests on this fund.",
    )
    category_ids = fields.Many2many(
        "ebshel.petty.cash.category",
        string="Allowed Categories",
        check_company=True,
        help="Empty: every category.",
    )
    source_journal_id = fields.Many2one(
        "account.journal",
        string="Replenish From",
        check_company=True,
        domain="[('type', 'in', ('bank', 'cash')), ('id', '!=', journal_id)]",
        help="Default bank journal of the replenishments.",
    )

    # Limits and policy, in fund currency
    float_amount = fields.Monetary(
        string="Float",
        tracking=True,
        help="Imprest amount: replenishments top the cash back up to it.",
    )
    min_balance = fields.Monetary(
        string="Replenish Below",
        help="When the balance falls below this amount the custodian is reminded "
        "(and a replenishment request is drafted with auto-replenishment).",
    )
    max_expense_amount = fields.Monetary(
        string="Max per Voucher", help="0 = no limit."
    )
    require_receipt = fields.Boolean(string="Receipts Required", default=True)
    receipt_required_amount = fields.Monetary(
        string="Receipt Required Above",
        help="Vouchers above this amount need an attached receipt. 0 = always.",
    )
    monthly_budget = fields.Monetary(
        tracking=True,
        help="Most the fund may spend in a calendar month (vouchers submitted, "
        "approved or paid). 0 = no budget.",
    )
    budget_policy = fields.Selection(
        [("warn", "Warn (risk flag)"), ("block", "Block")],
        string="Over Budget",
        default="warn",
        required=True,
    )
    user_monthly_limit = fields.Monetary(
        string="Monthly Limit per Person",
        help="Most one requester may claim from the fund in a calendar month. 0 = no limit.",
    )
    auto_replenish = fields.Boolean(
        string="Auto-Replenishment",
        help="Draft a replenishment request when the balance falls below the "
        "threshold (daily check).",
    )
    count_interval_days = fields.Integer(
        string="Count Every (Days)",
        help="Remind the custodian to count the cash. 0 = no reminder.",
    )
    note = fields.Html()

    # Live figures (from the ledger and the open documents)
    balance = fields.Monetary(compute="_compute_balance", string="Cash Balance")
    pending_amount = fields.Monetary(
        compute="_compute_document_figures",
        string="Committed",
        help="Approved vouchers and advances not paid yet.",
    )
    available_amount = fields.Monetary(
        compute="_compute_document_figures",
        string="Available",
        help="Cash balance minus the committed amount.",
    )
    advance_outstanding = fields.Monetary(
        compute="_compute_document_figures",
        string="Advances Outstanding",
        help="Cash handed out as advances and not settled yet.",
    )
    replenish_amount = fields.Monetary(
        compute="_compute_balance",
        string="To Replenish",
        help="Float minus cash balance.",
    )
    balance_ratio = fields.Float(compute="_compute_balance", string="Fill Level")
    is_low = fields.Boolean(compute="_compute_balance", string="Low Balance")
    expense_count = fields.Integer(compute="_compute_document_figures")
    to_approve_count = fields.Integer(compute="_compute_document_figures")
    to_pay_count = fields.Integer(compute="_compute_document_figures")
    advance_count = fields.Integer(compute="_compute_document_figures")
    open_advance_count = fields.Integer(compute="_compute_document_figures")
    replenishment_count = fields.Integer(compute="_compute_document_figures")
    count_count = fields.Integer(compute="_compute_document_figures")
    last_count_date = fields.Date(compute="_compute_document_figures")
    month_spent = fields.Monetary(
        compute="_compute_document_figures",
        string="Spent This Month",
        help="Paid vouchers dated this month.",
    )
    month_committed = fields.Monetary(
        compute="_compute_document_figures",
        string="Committed This Month",
        help="Vouchers dated this month, submitted, approved or paid.",
    )
    budget_used_pct = fields.Float(compute="_compute_document_figures", string="Budget Used")
    budget_remaining = fields.Monetary(compute="_compute_document_figures")
    overdue_advance_count = fields.Integer(compute="_compute_document_figures")
    count_due = fields.Boolean(
        compute="_compute_document_figures", help="The cash count interval has passed."
    )
    attention_count = fields.Integer(
        compute="_compute_document_figures",
        string="Alerts",
        help="Low balance, count due, overdue advances, budget exceeded.",
    )

    _code_company_uniq = models.Constraint(
        "UNIQUE(code, company_id)", "The fund code must be unique per company."
    )
    _journal_uniq = models.Constraint(
        "UNIQUE(journal_id)", "A cash journal can only belong to one fund."
    )

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains("journal_id", "currency_id")
    def _check_journal(self):
        # sudo: fund managers need no accounting rights
        for fund in self.sudo().filtered("journal_id"):
            journal_currency = fund.journal_id.currency_id or fund.company_id.currency_id
            if journal_currency != fund.currency_id:
                raise ValidationError(
                    self.env._(
                        "The cash journal %(journal)s is in %(journal_currency)s, "
                        "the fund in %(currency)s.",
                        journal=fund.journal_id.display_name,
                        journal_currency=journal_currency.name,
                        currency=fund.currency_id.name,
                    )
                )
            if not fund.journal_id.default_account_id:
                raise ValidationError(
                    self.env._("The cash journal %s has no cash account.", fund.journal_id.display_name)
                )
            sharing = self.search(
                [
                    ("id", "!=", fund.id),
                    ("account_id", "=", fund.journal_id.default_account_id.id),
                ],
                limit=1,
            )
            if sharing:
                raise ValidationError(
                    self.env._(
                        "The funds %(fund)s and %(other)s would share the cash account "
                        "%(account)s: each fund needs its own account, its balance is "
                        "read from it.",
                        fund=fund.name,
                        other=sharing.name,
                        account=fund.journal_id.default_account_id.display_name,
                    )
                )

    @api.constrains(
        "float_amount",
        "min_balance",
        "max_expense_amount",
        "receipt_required_amount",
        "count_interval_days",
        "monthly_budget",
        "user_monthly_limit",
    )
    def _check_amounts(self):
        for fund in self:
            if any(
                value < 0
                for value in (
                    fund.float_amount,
                    fund.min_balance,
                    fund.max_expense_amount,
                    fund.receipt_required_amount,
                    fund.count_interval_days,
                    fund.monthly_budget,
                    fund.user_monthly_limit,
                )
            ):
                raise ValidationError(self.env._("The fund limits cannot be negative."))
            if fund.float_amount and fund.min_balance > fund.float_amount:
                raise ValidationError(
                    self.env._("The replenishment threshold cannot exceed the float.")
                )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("journal_id"):
                vals["journal_id"] = self._create_cash_journal(vals).id
        return super().create(vals_list)

    @api.model
    def _create_cash_journal(self, vals):
        company = self.env["res.company"].browse(vals.get("company_id")) or self.env.company
        currency = self.env["res.currency"].browse(vals.get("currency_id")) or company.currency_id
        Journal = self.env["account.journal"].sudo().with_company(company)
        code = (vals.get("code") or "").upper()
        if not code or Journal.search_count(
            [*Journal._check_company_domain(company), ("code", "=", code)], limit=1
        ):
            code = Journal._get_next_journal_default_code("cash", company)
        journal_vals = {
            "name": vals.get("name") or self.env._("Petty Cash"),
            "code": code,
            "type": "cash",
            "company_id": company.id,
        }
        if currency != company.currency_id:
            journal_vals["currency_id"] = currency.id
        return Journal.create(journal_vals)

    def write(self, vals):
        if "currency_id" in vals and self._has_entries():
            raise UserError(self.env._("The currency of a fund with entries cannot change."))
        if "journal_id" in vals and self._has_entries():
            raise UserError(self.env._("The cash journal of a fund with entries cannot change."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_used(self):
        if self._has_entries():
            raise UserError(
                self.env._("A fund with entries cannot be deleted; close or archive it.")
            )
        documents = sum(
            self.env[model].sudo().search_count([("fund_id", "in", self.ids)], limit=1)
            for model in self._document_models()
        )
        if documents:
            raise UserError(
                self.env._("A fund with documents cannot be deleted; close or archive it.")
            )

    def _has_entries(self):
        accounts = self.account_id
        return bool(
            accounts
            and self.env["account.move.line"]
            .sudo()
            .search_count([("account_id", "in", accounts.ids)], limit=1)
        )

    @api.model
    def _document_models(self):
        return [
            "ebshel.petty.cash.expense",
            "ebshel.petty.cash.advance",
            "ebshel.petty.cash.replenishment",
            "ebshel.petty.cash.transfer",
            "ebshel.petty.cash.count",
        ]

    # ------------------------------------------------------------------
    # Figures
    # ------------------------------------------------------------------
    def _get_balance(self, date=None, date_from=None):
        """Ledger balance of the fund account in fund currency, up to ``date``
        included (all posted entries when omitted). With ``date_from``, the
        movement between the two dates only."""
        self.ensure_one()
        return self._get_balances(date=date, date_from=date_from).get(self.id, 0.0)

    def _get_balances(self, date=None, date_from=None):
        funds = self.filtered("account_id")
        if not funds:
            return {}
        domain = [
            ("account_id", "in", funds.account_id.ids),
            ("parent_state", "=", "posted"),
        ]
        if date:
            domain.append(("date", "<=", date))
        if date_from:
            domain.append(("date", ">=", date_from))
        totals = {
            account.id: (balance, amount_currency)
            for account, balance, amount_currency in self.env["account.move.line"]
            .sudo()
            ._read_group(
                domain,
                groupby=["account_id"],
                aggregates=["balance:sum", "amount_currency:sum"],
            )
        }
        result = {}
        for fund in funds:
            balance, amount_currency = totals.get(fund.account_id.id, (0.0, 0.0))
            foreign = fund.currency_id != fund.company_id.currency_id
            result[fund.id] = fund.currency_id.round(amount_currency if foreign else balance)
        return result

    @api.depends("account_id", "float_amount", "min_balance")
    def _compute_balance(self):
        balances = self._get_balances()
        for fund in self:
            balance = balances.get(fund.id, 0.0)
            fund.balance = balance
            fund.replenish_amount = max(fund.float_amount - balance, 0.0)
            fund.balance_ratio = (
                max(min(balance / fund.float_amount * 100.0, 100.0), 0.0)
                if fund.float_amount
                else 0.0
            )
            fund.is_low = bool(
                fund.state == "open"
                and fund.min_balance
                and float_compare(balance, fund.min_balance, precision_rounding=fund.currency_id.rounding) < 0
            )

    def _compute_document_figures(self):
        Expense = self.env["ebshel.petty.cash.expense"]
        Advance = self.env["ebshel.petty.cash.advance"]

        def grouped(model, domain, groupby, aggregates):
            if not self.ids:
                return []
            return self.env[model]._read_group(
                [("fund_id", "in", self.ids), *domain], groupby, aggregates
            )

        expense_states = {
            (fund.id, state): (count, total)
            for fund, state, count, total in grouped(
                Expense._name, [], ["fund_id", "state"], ["__count", "amount_total:sum"]
            )
        }
        approved_cash = {
            fund.id: total
            for fund, total in grouped(
                Expense._name,
                [("state", "=", "approved"), ("advance_id", "=", False)],
                ["fund_id"],
                ["amount_total:sum"],
            )
        }
        today = fields.Date.context_today(self)
        month_start = today.replace(day=1)
        month_spent = {
            fund.id: total
            for fund, total in grouped(
                Expense._name,
                [("state", "=", "posted"), ("date", ">=", month_start)],
                ["fund_id"],
                ["amount_total:sum"],
            )
        }
        month_committed = {
            fund.id: total
            for fund, total in grouped(
                Expense._name,
                [("state", "in", ("to_approve", "approved", "posted")), ("date", ">=", month_start)],
                ["fund_id"],
                ["amount_total:sum"],
            )
        }
        overdue_advances = {
            fund.id: count
            for fund, count in grouped(Advance._name, [("is_overdue", "=", True)], ["fund_id"], ["__count"])
        }
        advance_states = {
            (fund.id, state): (count, amount, due)
            for fund, state, count, amount, due in grouped(
                Advance._name,
                [],
                ["fund_id", "state"],
                ["__count", "amount:sum", "amount_due:sum"],
            )
        }
        replenishments = {
            fund.id: count
            for fund, count in grouped(
                "ebshel.petty.cash.replenishment", [], ["fund_id"], ["__count"]
            )
        }
        counts = {
            fund.id: count
            for fund, count in grouped(
                "ebshel.petty.cash.count", [("state", "!=", "cancelled")], ["fund_id"], ["__count"]
            )
        }
        last_counts = {
            fund.id: last
            for fund, last in grouped(
                "ebshel.petty.cash.count",
                [("state", "in", ("confirmed", "done"))],
                ["fund_id"],
                ["date:max"],
            )
        }
        for fund in self:
            fund_id = fund.id
            fund.expense_count = sum(
                count for (fid, _state), (count, _total) in expense_states.items() if fid == fund_id
            )
            fund.to_approve_count = expense_states.get((fund_id, "to_approve"), (0, 0))[0] + (
                advance_states.get((fund_id, "to_approve"), (0, 0, 0))[0]
            )
            fund.to_pay_count = expense_states.get((fund_id, "approved"), (0, 0))[0] + (
                advance_states.get((fund_id, "approved"), (0, 0, 0))[0]
            )
            fund.advance_count = sum(
                count for (fid, _state), (count, _a, _d) in advance_states.items() if fid == fund_id
            )
            fund.open_advance_count = advance_states.get((fund_id, "paid"), (0, 0, 0))[0]
            fund.advance_outstanding = advance_states.get((fund_id, "paid"), (0, 0, 0))[2]
            fund.pending_amount = approved_cash.get(fund_id, 0.0) + advance_states.get(
                (fund_id, "approved"), (0, 0, 0)
            )[1]
            fund.available_amount = fund.balance - fund.pending_amount
            fund.replenishment_count = replenishments.get(fund_id, 0)
            fund.count_count = counts.get(fund_id, 0)
            fund.last_count_date = last_counts.get(fund_id, False)
            fund.month_spent = month_spent.get(fund_id, 0.0)
            fund.month_committed = month_committed.get(fund_id, 0.0)
            fund.budget_remaining = (
                fund.monthly_budget - fund.month_committed if fund.monthly_budget else 0.0
            )
            fund.budget_used_pct = (
                fund.month_committed / fund.monthly_budget * 100.0 if fund.monthly_budget else 0.0
            )
            fund.overdue_advance_count = overdue_advances.get(fund_id, 0)
            fund.count_due = bool(
                fund.state == "open"
                and fund.count_interval_days
                and (fund.last_count_date or (fund.create_date and fund.create_date.date()) or today)
                + timedelta(days=fund.count_interval_days)
                <= today
            )
            fund.attention_count = sum(
                (
                    fund.is_low,
                    fund.count_due,
                    bool(fund.overdue_advance_count),
                    bool(fund.monthly_budget and fund.budget_used_pct > 100.0),
                )
            )

    # ------------------------------------------------------------------
    # Rights and checks used by the documents
    # ------------------------------------------------------------------
    def _check_open(self):
        for fund in self:
            if fund.state != "open":
                raise UserError(
                    self.env._(
                        "The petty cash fund %s is not open.", fund.display_name
                    )
                )

    def _is_custodian(self, user=None):
        self.ensure_one()
        user = user or self.env.user
        return self.custodian_id == user or user.has_group(
            "ebshel_petty_cash.group_petty_cash_manager"
        )

    def _check_custodian(self):
        for fund in self:
            if not fund._is_custodian():
                raise UserError(
                    self.env._(
                        "Only the custodian of %(fund)s (%(custodian)s) or a petty cash "
                        "manager can pay out of the fund.",
                        fund=fund.display_name,
                        custodian=fund.custodian_id.name,
                    )
                )

    def _check_cash_available(self, amount, date=None):
        """Raise when paying ``amount`` (fund currency) would overdraw the fund."""
        self.ensure_one()
        balance = self._get_balance()
        if float_compare(amount, balance, precision_rounding=self.currency_id.rounding) > 0:
            raise UserError(
                self.env._(
                    "Not enough cash in %(fund)s: %(amount)s requested, %(balance)s on hand. "
                    "Replenish the fund first.",
                    fund=self.display_name,
                    amount=self.currency_id.format(amount),
                    balance=self.currency_id.format(balance),
                )
            )

    def _to_company_currency(self, amount, date=None):
        self.ensure_one()
        return self.currency_id._convert(
            amount,
            self.company_id.currency_id,
            self.company_id,
            date or fields.Date.context_today(self),
        )

    def _notify_low_balance(self):
        """Remind the custodian once when the balance falls under the threshold."""
        activity_type = self.env.ref("ebshel_petty_cash.mail_act_petty_cash_replenish")
        for fund in self.sudo().filtered("is_low"):
            if not fund.activity_ids.filtered(lambda a: a.activity_type_id == activity_type):
                fund.activity_schedule(
                    "ebshel_petty_cash.mail_act_petty_cash_replenish",
                    note=self.env._(
                        "The balance %(balance)s is below %(threshold)s.",
                        balance=fund.currency_id.format(fund.balance),
                        threshold=fund.currency_id.format(fund.min_balance),
                    ),
                    user_id=fund.custodian_id.id,
                )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_open(self):
        for fund in self:
            if fund.state not in ("draft", "suspended"):
                raise UserError(self.env._("Only draft or suspended funds can be opened."))
            if not fund.journal_id:
                raise UserError(self.env._("Set the cash journal of the fund first."))
        self.state = "open"

    def action_suspend(self):
        if any(fund.state != "open" for fund in self):
            raise UserError(self.env._("Only open funds can be suspended."))
        self.state = "suspended"

    def action_close(self):
        for fund in self:
            if fund.state not in ("open", "suspended"):
                raise UserError(self.env._("Only open or suspended funds can be closed."))
            if not fund.currency_id.is_zero(fund._get_balance()):
                raise UserError(
                    self.env._(
                        "Return the remaining cash of %s to the bank (a withdrawal) "
                        "before closing the fund.",
                        fund.display_name,
                    )
                )
            for model in self._document_models():
                open_states = self.env[model]._petty_cash_open_states()
                if self.env[model].search_count(
                    [("fund_id", "=", fund.id), ("state", "in", open_states)], limit=1
                ):
                    raise UserError(
                        self.env._(
                            "%(fund)s still has open %(documents)s.",
                            fund=fund.display_name,
                            documents=self.env[model]._description,
                        )
                    )
        self.state = "closed"

    def action_reset_draft(self):
        if any(fund.state != "closed" for fund in self):
            raise UserError(self.env._("Only closed funds can be reopened."))
        self.state = "draft"

    def _action_documents(self, model, name, extra_domain=None, extra_context=None):
        self.ensure_one()
        context = {"default_fund_id": self.id, **(extra_context or {})}
        return {
            "type": "ir.actions.act_window",
            "name": name,
            "res_model": model,
            "view_mode": "list,form",
            "domain": [("fund_id", "=", self.id), *(extra_domain or [])],
            "context": context,
        }

    def action_view_expenses(self):
        return self._action_documents("ebshel.petty.cash.expense", self.env._("Vouchers"))

    def action_view_to_approve(self):
        action = self._action_documents(
            "ebshel.petty.cash.expense",
            self.env._("Vouchers to Approve"),
            [("state", "=", "to_approve")],
        )
        return action

    def action_view_to_pay(self):
        return self._action_documents(
            "ebshel.petty.cash.expense",
            self.env._("Vouchers to Pay"),
            [("state", "=", "approved")],
        )

    def action_view_advances(self):
        return self._action_documents("ebshel.petty.cash.advance", self.env._("Advances"))

    def action_view_replenishments(self):
        return self._action_documents(
            "ebshel.petty.cash.replenishment", self.env._("Replenishments")
        )

    def action_view_counts(self):
        return self._action_documents("ebshel.petty.cash.count", self.env._("Cash Counts"))

    def action_view_journal_items(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Cash Book - %s", self.name),
            "res_model": "account.move.line",
            "view_mode": "list,form",
            "domain": [("account_id", "=", self.account_id.id), ("parent_state", "=", "posted")],
            "context": {"search_default_group_by_move": False},
        }

    def _action_new_document(self, model, name):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": name,
            "res_model": model,
            "view_mode": "form",
            "target": "current",
            "context": {"default_fund_id": self.id},
        }

    def action_new_expense(self):
        return self._action_new_document("ebshel.petty.cash.expense", self.env._("New Voucher"))

    def action_new_advance(self):
        return self._action_new_document("ebshel.petty.cash.advance", self.env._("New Advance"))

    def action_new_count(self):
        return self._action_new_document("ebshel.petty.cash.count", self.env._("New Cash Count"))

    def action_new_replenishment(self):
        self.ensure_one()
        action = self._action_new_document(
            "ebshel.petty.cash.replenishment", self.env._("New Replenishment")
        )
        action["context"]["default_amount"] = self.replenish_amount
        return action

    def action_print_statement(self):
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Fund Statement"),
            "res_model": "ebshel.petty.cash.statement.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_fund_ids": [Command.set(self.ids)]},
        }

    # ------------------------------------------------------------------
    # Daily controls
    # ------------------------------------------------------------------
    @api.model
    def _cron_petty_cash_controls(self):
        funds = self.search([("state", "=", "open")])
        funds._notify_low_balance()
        funds._auto_replenish()
        funds._remind_cash_count()
        self.env["ebshel.petty.cash.advance"]._remind_overdue()

    def _auto_replenish(self):
        Replenishment = self.env["ebshel.petty.cash.replenishment"]
        for fund in self.filtered(lambda f: f.auto_replenish and f.is_low and f.replenish_amount):
            if Replenishment.search_count(
                [
                    ("fund_id", "=", fund.id),
                    ("kind", "=", "replenish"),
                    ("state", "in", ("draft", "to_approve", "approved")),
                ],
                limit=1,
            ):
                continue
            replenishment = Replenishment.with_company(fund.company_id).create(
                {
                    "fund_id": fund.id,
                    "kind": "replenish",
                    "amount": fund.replenish_amount,
                    "requested_by_id": fund.custodian_id.id,
                }
            )
            replenishment.action_load_expenses()
            replenishment.activity_schedule(
                "ebshel_petty_cash.mail_act_petty_cash_approval",
                summary=self.env._("Review the automatic replenishment request"),
                user_id=fund.custodian_id.id,
            )

    def _remind_cash_count(self):
        today = fields.Date.context_today(self)
        activity_type = self.env.ref("ebshel_petty_cash.mail_act_petty_cash_count")
        for fund in self.filtered("count_interval_days"):
            reference = fund.last_count_date or fund.create_date.date()
            if reference + timedelta(days=fund.count_interval_days) > today:
                continue
            if fund.activity_ids.filtered(lambda a: a.activity_type_id == activity_type):
                continue
            fund.activity_schedule("ebshel_petty_cash.mail_act_petty_cash_count", user_id=fund.custodian_id.id)

    # ------------------------------------------------------------------
    # Dashboard shortcuts
    # ------------------------------------------------------------------
    def action_view_approvals(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Waiting for Approval - %s", self.name),
            "res_model": "ebshel.petty.cash.approval.step",
            "view_mode": "list,form",
            "domain": [("fund_id", "=", self.id), ("state", "=", "pending")],
        }

    def action_view_overdue_advances(self):
        return self._action_documents(
            "ebshel.petty.cash.advance", self.env._("Overdue Advances"), [("is_overdue", "=", True)]
        )

    def action_print_position(self):
        return self.env.ref("ebshel_petty_cash.action_report_petty_cash_fund_position").report_action(self)
