# Part of ebshel_petty_cash. See LICENSE file for full copyright and licensing details.
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import ValidationError

DOCUMENT_TYPES = [
    ("expense", "Voucher"),
    ("advance", "Advance"),
    ("replenishment", "Replenishment"),
]

APPROVER_TYPES = [
    ("approvers", "Any Petty Cash Approver"),
    ("managers", "Petty Cash Administrators"),
    ("custodian", "Fund Custodian"),
    ("users", "Specific Users"),
]


class PettyCashApprovalLevel(models.Model):
    """One tier of the approval matrix.

    On submission, every active level that matches the document (type, fund,
    categories, amount from) becomes an approval step, in sequence. A document
    no level matches is approved on submission.
    """

    _name = "ebshel.petty.cash.approval.level"
    _description = "Petty Cash Approval Level"
    _order = "sequence, min_amount, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10, help="Order of the levels in the approval chain.")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", index=True, help="Empty: the level applies in every company."
    )
    currency_id = fields.Many2one("res.currency", compute="_compute_currency_id")
    apply_on_expense = fields.Boolean(string="Vouchers", default=True)
    apply_on_advance = fields.Boolean(string="Advances", default=True)
    apply_on_replenishment = fields.Boolean(string="Replenishments")
    min_amount = fields.Monetary(
        string="Applies From",
        help="The level applies to documents of this amount and up (company "
        "currency). 0: every document.",
    )
    fund_ids = fields.Many2many(
        "ebshel.petty.cash.fund",
        string="Funds",
        domain="[('company_id', 'in', company_id and [company_id] or allowed_company_ids)]",
        help="Empty: every fund.",
    )
    category_ids = fields.Many2many(
        "ebshel.petty.cash.category",
        string="Only for Categories",
        domain="[('company_id', 'in', company_id and [company_id] or allowed_company_ids)]",
        help="Vouchers only: the level applies when a line is in one of these "
        "categories (e.g. entertainment needs the director). Empty: any category.",
    )
    approver_type = fields.Selection(
        APPROVER_TYPES, string="Approved By", required=True, default="approvers"
    )
    user_ids = fields.Many2many(
        "res.users",
        "ebshel_petty_cash_level_user_rel",
        "level_id",
        "user_id",
        string="Approvers",
        domain="[('share', '=', False)]",
    )
    approval_mode = fields.Selection(
        [("any", "One of them"), ("all", "Each of them")],
        string="Approval Needed From",
        required=True,
        default="any",
    )
    escalation_days = fields.Integer(
        string="Escalate After (Days)",
        help="When the level waits longer, the escalation user may approve it "
        "too (and any one approver is then enough). 0: no escalation.",
    )
    escalation_user_id = fields.Many2one("res.users", domain="[('share', '=', False)]")
    note = fields.Text(string="Description", translate=True)

    @api.depends("company_id")
    def _compute_currency_id(self):
        for level in self:
            level.currency_id = (level.company_id or self.env.company).currency_id

    @api.constrains("min_amount", "escalation_days")
    def _check_amounts(self):
        for level in self:
            if level.min_amount < 0 or level.escalation_days < 0:
                raise ValidationError(self.env._("Approval level amounts and days cannot be negative."))

    @api.constrains(
        "approver_type", "user_ids", "apply_on_expense", "apply_on_advance", "apply_on_replenishment",
        "company_id", "fund_ids", "category_ids",
    )
    def _check_setup(self):
        approver_group = "ebshel_petty_cash.group_petty_cash_approver"
        for level in self:
            if not (level.apply_on_expense or level.apply_on_advance or level.apply_on_replenishment):
                raise ValidationError(
                    self.env._("The level %s applies to no document type.", level.name)
                )
            if level.company_id and (
                (level.fund_ids.company_id | level.category_ids.company_id) - level.company_id
            ):
                raise ValidationError(
                    self.env._("The funds and categories of the level %s belong to another company.", level.name)
                )
            if level.approver_type == "users":
                if not level.user_ids:
                    raise ValidationError(self.env._("Choose the approvers of the level %s.", level.name))
                missing = level.user_ids.filtered(lambda u: not u.has_group(approver_group))
                if missing:
                    raise ValidationError(
                        self.env._(
                            "%(users)s need the Petty Cash Approver right to approve the level %(level)s.",
                            users=", ".join(missing.mapped("name")),
                            level=level.name,
                        )
                    )

    def _petty_cash_applies(self, document, amount_company):
        """True when the level applies to ``document`` of ``amount_company``."""
        self.ensure_one()
        doc_type = document._petty_cash_document_type()
        if not self[f"apply_on_{doc_type}"]:
            return False
        if self.company_id and self.company_id != document.company_id:
            return False
        if self.fund_ids and document.fund_id not in self.fund_ids:
            return False
        if self.category_ids and (
            doc_type != "expense" or not (document.line_ids.category_id & self.category_ids)
        ):
            return False
        currency = document.company_id.currency_id
        return currency.compare_amounts(amount_company, self.min_amount) >= 0

    def _petty_cash_resolve_approvers(self, document):
        """Users who approve this level for ``document``."""
        self.ensure_one()
        if self.approver_type == "users":
            users = self.user_ids
        elif self.approver_type == "custodian":
            users = document.fund_id.custodian_id
        else:
            group = (
                "ebshel_petty_cash.group_petty_cash_manager"
                if self.approver_type == "managers"
                else "ebshel_petty_cash.group_petty_cash_approver"
            )
            users = document._petty_cash_group_users(group)
        return users.filtered("active")


class PettyCashApprovalStep(models.Model):
    """One level of the approval chain of a voucher, advance or replenishment."""

    _name = "ebshel.petty.cash.approval.step"
    _description = "Petty Cash Approval Step"
    _order = "res_model, res_id, sequence, id"
    _rec_name = "name"

    res_model = fields.Char(required=True, index=True, readonly=True)
    res_id = fields.Many2oneReference(model_field="res_model", required=True, index=True, readonly=True)
    document_type = fields.Selection(DOCUMENT_TYPES, required=True, readonly=True)
    document_name = fields.Char(string="Document", readonly=True)
    level_id = fields.Many2one(
        "ebshel.petty.cash.approval.level", string="Approval Level", ondelete="set null", readonly=True
    )
    name = fields.Char(string="Level", required=True, readonly=True)
    sequence = fields.Integer(readonly=True)
    state = fields.Selection(
        [
            ("waiting", "Waiting"),
            ("pending", "To Approve"),
            ("approved", "Approved"),
            ("refused", "Refused"),
            ("cancelled", "Cancelled"),
        ],
        default="waiting",
        required=True,
        readonly=True,
        index=True,
    )
    company_id = fields.Many2one("res.company", readonly=True, index=True)
    company_currency_id = fields.Many2one(related="company_id.currency_id", string="Currency")
    fund_id = fields.Many2one("ebshel.petty.cash.fund", readonly=True, index=True)
    requested_by_id = fields.Many2one("res.users", readonly=True)
    amount_company = fields.Monetary(
        string="Amount", currency_field="company_currency_id", readonly=True
    )
    risk_level = fields.Selection(
        [("low", "Low"), ("medium", "Medium"), ("high", "High")], readonly=True
    )
    approval_mode = fields.Selection(
        [("any", "One of them"), ("all", "Each of them")], default="any", readonly=True
    )
    approver_ids = fields.Many2many(
        "res.users",
        "ebshel_petty_cash_step_approver_rel",
        "step_id",
        "user_id",
        string="Approvers",
        readonly=True,
    )
    approved_user_ids = fields.Many2many(
        "res.users",
        "ebshel_petty_cash_step_approved_rel",
        "step_id",
        "user_id",
        string="Approved By",
        readonly=True,
        help="Approvers who have given their approval (for delegated approvals, "
        "the person delegating).",
    )
    done_by_id = fields.Many2one("res.users", string="Decided By", readonly=True)
    done_date = fields.Datetime(string="Decided On", readonly=True)
    activated_date = fields.Datetime(string="Waiting Since", readonly=True)
    deadline = fields.Date(readonly=True)
    escalated = fields.Boolean(readonly=True)
    comment = fields.Text(readonly=True)
    duration_hours = fields.Float(
        string="Hours to Decide",
        compute="_compute_duration_hours",
        store=True,
        aggregator="avg",
    )
    is_overdue = fields.Boolean(compute="_compute_is_overdue")
    can_approve = fields.Boolean(compute="_compute_can_approve")
    is_my_approval = fields.Boolean(
        compute="_compute_can_approve", search="_search_is_my_approval", string="Mine to Approve"
    )

    @api.depends("activated_date", "done_date")
    def _compute_duration_hours(self):
        for step in self:
            if step.activated_date and step.done_date:
                step.duration_hours = round(
                    (step.done_date - step.activated_date).total_seconds() / 3600.0, 2
                )
            else:
                step.duration_hours = 0.0

    def _compute_is_overdue(self):
        today = fields.Date.context_today(self)
        for step in self:
            step.is_overdue = bool(step.state == "pending" and step.deadline and step.deadline < today)

    @api.depends_context("uid")
    def _compute_can_approve(self):
        for step in self:
            step.is_my_approval = step.state == "pending" and step._petty_cash_is_approver(self.env.user)
            step.can_approve = step.is_my_approval and not (
                step.sudo()._document()._petty_cash_approval_error(self.env.user)
            )

    def _search_is_my_approval(self, operator, value):
        if operator not in ("=", "!=") or not isinstance(value, bool):
            return NotImplemented
        users = self.env.user | self.env["ebshel.petty.cash.delegation"]._petty_cash_principals(self.env.user)
        domain = [("state", "=", "pending"), ("approver_ids", "in", users.ids)]
        return domain if (operator == "=") == value else ["!", "&", *domain]

    # ------------------------------------------------------------------
    def _document(self):
        self.ensure_one()
        return self.env[self.res_model].browse(self.res_id)

    def _petty_cash_principal(self, user):
        """The approver ``user`` acts for: himself, or the approver who
        delegated to him. False when ``user`` cannot approve the step."""
        self.ensure_one()
        if user in self.approver_ids:
            return user
        if not self.approver_ids and user.has_group("ebshel_petty_cash.group_petty_cash_manager"):
            # nobody could be found for the level: administrators unblock it
            return user
        principals = self.env["ebshel.petty.cash.delegation"]._petty_cash_principals(user)
        candidates = (self.approver_ids & principals) - self.approved_user_ids
        return candidates[:1] or (self.approver_ids & principals)[:1]

    def _petty_cash_is_approver(self, user):
        return bool(self._petty_cash_principal(user))

    def _petty_cash_activate(self):
        """Make the steps the current ones: deadline, approvers, activities."""
        today = fields.Date.context_today(self)
        for step in self.sudo():
            document = step._document()
            approvers = step.approver_ids
            if document.company_id.petty_cash_distinct_approvers:
                earlier = document.approval_step_ids.filtered(
                    lambda s, seq=step.sequence: s.state == "approved" and s.sequence < seq
                )
                approvers -= earlier.approved_user_ids
            if not approvers:
                approvers = document._petty_cash_fallback_approvers() - document.approval_step_ids.approved_user_ids
            days = step.level_id.escalation_days
            step.write(
                {
                    "state": "pending",
                    "approver_ids": [(6, 0, (approvers or step.approver_ids).ids)],
                    "activated_date": fields.Datetime.now(),
                    "deadline": today + timedelta(days=days) if days else False,
                }
            )
            for user in step.approver_ids:
                document.activity_schedule(
                    "ebshel_petty_cash.mail_act_petty_cash_approval",
                    date_deadline=step.deadline or today,
                    summary=self.env._("%(level)s: approve %(document)s", level=step.name, document=document.name),
                    user_id=user.id,
                )

    # Actions from the approvals inbox ----------------------------------
    def action_approve(self):
        for step in self:
            step._document().action_approve()
        return True

    def action_refuse(self):
        documents = {}
        for step in self:
            documents.setdefault(step.res_model, []).append(step.res_id)
        if len(documents) != 1:
            # one wizard per model: refuse the documents of the first model only
            documents = dict([next(iter(documents.items()))])
        (model, ids), = documents.items()
        return self.env[model].browse(ids).action_refuse()

    def action_open_document(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": self.res_model,
            "res_id": self.res_id,
            "view_mode": "form",
            "target": "current",
        }

    @api.model
    def _cron_escalate(self):
        """Steps past their deadline: open them to the escalation user."""
        today = fields.Date.context_today(self)
        steps = self.sudo().search(
            [("state", "=", "pending"), ("deadline", "<", today), ("escalated", "=", False)]
        )
        for step in steps:
            document = step._document()
            escalation = step.level_id.escalation_user_id
            values = {"escalated": True}
            if escalation:
                values.update(
                    {"approver_ids": [(4, escalation.id)], "approval_mode": "any"}
                )
            step.write(values)
            document.message_post(
                body=self.env._(
                    "The approval level %(level)s has been waiting since %(date)s; escalated%(to)s.",
                    level=step.name,
                    date=step.activated_date.date() if step.activated_date else "",
                    to=self.env._(" to %s", escalation.name) if escalation else "",
                )
            )
            for user in escalation or step.approver_ids:
                document.activity_schedule(
                    "ebshel_petty_cash.mail_act_petty_cash_approval",
                    date_deadline=today,
                    summary=self.env._("Overdue approval: %(level)s of %(document)s", level=step.name, document=document.name),
                    user_id=user.id,
                )


class PettyCashDelegation(models.Model):
    """An approver hands his approvals to a colleague for a period."""

    _name = "ebshel.petty.cash.delegation"
    _description = "Petty Cash Approval Delegation"
    _order = "date_from desc, id desc"

    user_id = fields.Many2one(
        "res.users",
        string="Approver",
        required=True,
        default=lambda self: self.env.user,
        index=True,
        domain="[('share', '=', False)]",
    )
    delegate_id = fields.Many2one(
        "res.users", string="Delegate To", required=True, index=True, domain="[('share', '=', False)]"
    )
    date_from = fields.Date(required=True, default=fields.Date.context_today)
    date_to = fields.Date(required=True)
    active = fields.Boolean(default=True)
    note = fields.Char(string="Reason", help="e.g. annual leave")
    is_current = fields.Boolean(compute="_compute_is_current")

    def _compute_is_current(self):
        today = fields.Date.context_today(self)
        for delegation in self:
            delegation.is_current = delegation.date_from <= today <= delegation.date_to

    @api.constrains("user_id", "delegate_id", "date_from", "date_to")
    def _check_delegation(self):
        for delegation in self:
            if delegation.user_id == delegation.delegate_id:
                raise ValidationError(self.env._("Delegate to someone else."))
            if delegation.date_to < delegation.date_from:
                raise ValidationError(self.env._("The delegation ends before it starts."))
            if not delegation.delegate_id.has_group("ebshel_petty_cash.group_petty_cash_approver"):
                raise ValidationError(
                    self.env._("%s needs the Petty Cash Approver right.", delegation.delegate_id.name)
                )

    @api.depends("user_id", "delegate_id")
    def _compute_display_name(self):
        for delegation in self:
            delegation.display_name = self.env._(
                "%(user)s to %(delegate)s", user=delegation.user_id.name, delegate=delegation.delegate_id.name
            )

    @api.model
    def _petty_cash_principals(self, user):
        """Approvers who delegated to ``user`` today."""
        today = fields.Date.context_today(self)
        return (
            self.sudo()
            .search(
                [
                    ("delegate_id", "=", user.id),
                    ("date_from", "<=", today),
                    ("date_to", ">=", today),
                ]
            )
            .user_id
        )
