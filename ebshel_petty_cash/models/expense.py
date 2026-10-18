# Part of ebshel_petty_cash. See LICENSE file for full copyright and licensing details.
from datetime import timedelta

from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError


class PettyCashExpense(models.Model):
    """A petty cash voucher: one payment out of the fund, split over expense
    lines (and open vendor bills), approved before it is paid."""

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
        help="Shop or person paid. Required to pay vendor bills.",
    )
    reference = fields.Char(string="Receipt Reference", copy=False)
    price_mode = fields.Selection(
        [("included", "Tax Included"), ("excluded", "Tax Excluded")],
        string="Amounts",
        default="included",
        required=True,
        help="Tax Included: the line amounts are receipt totals; the tax is "
        "extracted from them.",
    )
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

    # ------------------------------------------------------------------
    # Amounts (the same tax engine that posts the entry)
    # ------------------------------------------------------------------
    def _prepare_base_lines(self):
        """Base lines of the taxes computation, one per voucher line."""
        self.ensure_one()
        AccountTax = self.env["account.tax"]
        special_mode = "total_included" if self.price_mode == "included" else "total_excluded"
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
                special_mode=special_mode,
                account_id=line.account_id,
                partner_id=line.bill_id.commercial_partner_id or self.partner_id,
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
        "price_mode",
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

    def _petty_cash_auto_approved(self):
        self.ensure_one()
        limit = self.fund_id.auto_approve_limit
        return bool(limit) and self.currency_id.compare_amounts(self.amount_total, limit) <= 0

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
        if self.is_duplicate:
            self.message_post(
                body=self.env._(
                    "Possible duplicate of %s.",
                    ", ".join(self.duplicate_ids.mapped("name")),
                )
            )

    def _petty_cash_after_approval(self):
        to_post = self.filtered(lambda e: e.fund_id.post_on_approval)
        to_post._post()

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
            expense._reconcile_bills()
            expense.message_post(body=self.env._("Paid from %s.", expense.fund_id.display_name))
        self.fund_id._notify_low_balance()

    def _prepare_move_lines(self):
        """Expense lines with their tax lines, the vendor bill lines and the
        credit of the cash account (or of the advance)."""
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
                    "partner_id": (line.bill_id.commercial_partner_id or self.partner_id).id,
                    "analytic_distribution": line.analytic_distribution,
                    "tax_ids": [Command.set(line.tax_ids.ids)],
                    "tax_tag_ids": to_update["tax_tag_ids"],
                    "amount_currency": to_update["amount_currency"],
                    "balance": to_update["balance"],
                    "currency_id": self.currency_id.id,
                    "petty_cash_bill_id": line.bill_id.id,
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

    def _reconcile_bills(self):
        for expense in self.sudo():
            for bill in expense.line_ids.bill_id:
                lines = expense.move_id.line_ids.filtered(
                    lambda aml, b=bill: aml.petty_cash_bill_id == b
                )
                bill_lines = bill.line_ids.filtered(
                    lambda aml, accounts=lines.account_id: aml.account_id in accounts
                    and not aml.reconciled
                )
                (lines + bill_lines).reconcile()

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
            expense.activity_unlink(["ebshel_petty_cash.mail_act_petty_cash_approval"])
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
        locked = {"line_ids", "fund_id", "price_mode", "advance_id", "partner_id", "date"}
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
        check_company=True,
        index=True,
    )
    bill_id = fields.Many2one(
        "account.move",
        string="Vendor Bill",
        check_company=True,
        domain="[('move_type', '=', 'in_invoice'), ('state', '=', 'posted'), "
        "('payment_state', 'in', ('not_paid', 'partial')), ('currency_id', '=', currency_id)]",
        help="Pay this open vendor bill from the cash box.",
    )
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
        help="Receipt amount, tax included or excluded as set on the voucher.",
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

    @api.depends("category_id", "bill_id")
    def _compute_account_taxes(self):
        for line in self:
            if line.bill_id:
                payable = line.bill_id.line_ids.filtered(
                    lambda aml: aml.account_id.account_type == "liability_payable"
                )[:1]
                line.account_id = payable.account_id
                line.tax_ids = False
            elif line.category_id:
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

    @api.onchange("bill_id")
    def _onchange_bill_id(self):
        if self.bill_id:
            self.amount = self.bill_id.amount_residual
            self.name = self.bill_id.ref or self.bill_id.name

    @api.depends(
        "amount",
        "tax_ids",
        "expense_id.price_mode",
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

    @api.constrains("category_id", "bill_id")
    def _check_category_or_bill(self):
        for line in self:
            if not line.category_id and not line.bill_id:
                raise ValidationError(self.env._("Each voucher line needs a category or a vendor bill."))

    def _check_line(self):
        """Checks run on submission (the voucher may still change before)."""
        for line in self:
            expense = line.expense_id
            fund = expense.fund_id
            category = line.category_id
            if category:
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
            if line.bill_id:
                bill = line.bill_id
                if bill.state != "posted" or bill.payment_state not in ("not_paid", "partial"):
                    raise UserError(self.env._("The bill %s is not open any more.", bill.name))
                if bill.currency_id != expense.currency_id:
                    raise UserError(
                        self.env._("The bill %s is not in the fund currency.", bill.name)
                    )
                if line.currency_id.compare_amounts(line.amount, bill.amount_residual) > 0:
                    raise UserError(
                        self.env._(
                            "%(amount)s is more than the %(residual)s due on %(bill)s.",
                            amount=line.currency_id.format(line.amount),
                            residual=line.currency_id.format(bill.amount_residual),
                            bill=bill.name,
                        )
                    )


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    petty_cash_bill_id = fields.Many2one(
        "account.move",
        readonly=True,
        copy=False,
        index="btree_not_null",
        help="Vendor bill paid by this petty cash line.",
    )
