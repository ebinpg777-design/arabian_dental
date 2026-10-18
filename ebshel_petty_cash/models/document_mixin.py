# Part of ebshel_petty_cash. See LICENSE file for full copyright and licensing details.
from odoo import Command, api, fields, models
from odoo.exceptions import UserError


class PettyCashDocumentMixin(models.AbstractModel):
    """Common ground of the petty cash documents: a numbered document of one
    fund, dated, that ends in one journal entry in the fund's cash journal."""

    _name = "ebshel.petty.cash.document.mixin"
    _description = "Petty Cash Document"
    _check_company_auto = True

    #: ir.sequence code of the document number
    _petty_cash_sequence_code = None

    name = fields.Char(
        string="Number",
        required=True,
        readonly=True,
        copy=False,
        default=lambda self: self.env._("New"),
    )
    fund_id = fields.Many2one(
        "ebshel.petty.cash.fund",
        required=True,
        index=True,
        tracking=True,
        check_company=True,
        domain="[('state', '=', 'open')]",
    )
    company_id = fields.Many2one(
        "res.company",
        related="fund_id.company_id",
        store=True,
        precompute=True,
        index=True,
    )
    currency_id = fields.Many2one(
        "res.currency", related="fund_id.currency_id", store=True, precompute=True
    )
    company_currency_id = fields.Many2one(related="company_id.currency_id", string="Company Currency")
    custodian_id = fields.Many2one(related="fund_id.custodian_id")
    date = fields.Date(
        required=True, default=fields.Date.context_today, tracking=True, copy=False
    )
    move_id = fields.Many2one(
        "account.move", string="Journal Entry", readonly=True, copy=False, index="btree_not_null"
    )

    def _valid_field_parameter(self, field, name):
        # tracking takes effect on the documents, which all inherit mail.thread
        return name == "tracking" or super()._valid_field_parameter(field, name)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record in records:
            if record.name in (False, "/", self.env._("New")) and self._petty_cash_sequence_code:
                record.name = (
                    self.env["ir.sequence"]
                    .with_company(record.company_id)
                    .next_by_code(self._petty_cash_sequence_code, sequence_date=record.date)
                    or "/"
                )
        return records

    @api.model
    def _petty_cash_open_states(self):
        """States in which the document still waits for something."""
        return ["draft", "to_approve", "approved"]

    # ------------------------------------------------------------------
    # Journal entries
    # ------------------------------------------------------------------
    def _petty_cash_rate(self, date=None):
        """Fund currency units per company currency unit at the document date."""
        self.ensure_one()
        return self.env["res.currency"]._get_conversion_rate(
            self.company_id.currency_id, self.currency_id, self.company_id, date or self.date
        )

    def _petty_cash_line(self, account, amount_currency, name, partner=None, date=None, **extra):
        """Values of a journal item of ``amount_currency`` (fund currency,
        positive = debit) converted at the document date."""
        self.ensure_one()
        balance = self.company_id.currency_id.round(
            amount_currency / (self._petty_cash_rate(date) or 1.0)
        )
        return {
            "name": name,
            "account_id": account.id,
            "partner_id": partner.id if partner else False,
            "currency_id": self.currency_id.id,
            "amount_currency": self.currency_id.round(amount_currency),
            "balance": balance,
            **extra,
        }

    def _petty_cash_create_move(self, lines, ref=None, date=None, journal=None):
        """Create and post the entry of the document from line values.

        The last line absorbs the conversion rounding so the entry always
        balances in company currency. Created as superuser: the rights to the
        document were checked by the caller, custodians need no accounting
        rights.
        """
        self.ensure_one()
        if not lines:
            return self.env["account.move"]
        company_currency = self.company_id.currency_id
        total = sum(line["balance"] for line in lines[:-1])
        lines[-1]["balance"] = company_currency.round(-total)
        move = (
            self.env["account.move"]
            .sudo()
            .with_company(self.company_id)
            .with_context(default_move_type="entry")
            .create(
                {
                    "move_type": "entry",
                    "journal_id": (journal or self.fund_id.journal_id).id,
                    "date": date or self.date,
                    "ref": ref or self.name,
                    "petty_cash_fund_id": self.fund_id.id,
                    "company_id": self.company_id.id,
                    "line_ids": [Command.create(line) for line in lines],
                }
            )
        )
        move.action_post()
        return move.sudo(self.env.su)

    def _petty_cash_reverse(self, moves, date=None):
        """Reverse posted ``moves`` (cancel mode: reconciled with the original)
        and drop draft ones."""
        moves = moves.sudo().with_context(petty_cash_bypass=True)
        drafts = moves.filtered(lambda m: m.state == "draft")
        drafts.button_cancel()
        posted = moves.filtered(lambda m: m.state == "posted")
        if posted:
            date = date or fields.Date.context_today(self)
            posted._reverse_moves(
                [
                    {
                        "date": max(date, move.date),
                        "ref": self.env._("Reversal of %s", move.ref or move.name),
                    }
                    for move in posted
                ],
                cancel=True,
            )

    def action_view_move(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": self.move_id.id,
            "view_mode": "form",
        }

    @api.ondelete(at_uninstall=False)
    def _unlink_except_processed(self):
        for record in self:
            if record.move_id or record.state not in ("draft", "cancelled", "refused"):
                raise UserError(
                    self.env._(
                        "%s has been processed; cancel it instead of deleting it.",
                        record.display_name,
                    )
                )


class PettyCashApprovalMixin(models.AbstractModel):
    """Submit / approve / refuse flow of the vouchers, advances and
    replenishments.

    States used: draft, to_approve, approved, refused (plus the document's own
    downstream states). A document can need two approvals: the second by a
    petty cash manager, from the company threshold up.
    """

    _name = "ebshel.petty.cash.approval.mixin"
    _description = "Petty Cash Approval"

    requested_by_id = fields.Many2one(
        "res.users",
        required=True,
        default=lambda self: self.env.user,
        tracking=True,
        index=True,
    )
    first_approver_id = fields.Many2one("res.users", readonly=True, copy=False, tracking=True)
    approver_id = fields.Many2one(
        "res.users", string="Approved By", readonly=True, copy=False, tracking=True
    )
    approval_date = fields.Datetime(readonly=True, copy=False)
    refuse_reason = fields.Text(readonly=True, copy=False)
    needs_second_approval = fields.Boolean(compute="_compute_needs_second_approval")
    can_approve = fields.Boolean(compute="_compute_can_approve")
    can_pay = fields.Boolean(
        compute="_compute_can_pay",
        help="The current user may pay out the approved document (custodian or manager).",
    )

    def _valid_field_parameter(self, field, name):
        # tracking takes effect on the documents, which all inherit mail.thread
        return name == "tracking" or super()._valid_field_parameter(field, name)

    # Hooks -------------------------------------------------------------
    def _petty_cash_approval_amount(self):
        """Amount (fund currency) the approval is about."""
        raise NotImplementedError

    def _petty_cash_auto_approved(self):
        """True when the document is approved on submission."""
        return False

    def _petty_cash_check_submit(self):
        """Raise if the document cannot be submitted."""

    def _petty_cash_after_approval(self):
        """Called once a document reaches the approved state."""

    def _petty_cash_approver_group(self):
        return "ebshel_petty_cash.group_petty_cash_approver"

    def _petty_cash_double_validation_applies(self):
        return True

    # Computes ----------------------------------------------------------
    def _compute_needs_second_approval(self):
        for record in self:
            company = record.company_id
            record.needs_second_approval = bool(
                record._petty_cash_double_validation_applies()
                and company.petty_cash_double_validation
                and record.fund_id
                and company.currency_id.compare_amounts(
                    record.fund_id._to_company_currency(
                        record._petty_cash_approval_amount(), record.date
                    ),
                    company.petty_cash_double_validation_amount,
                )
                >= 0
            )

    @api.depends_context("uid")
    def _compute_can_approve(self):
        for record in self:
            record.can_approve = record.state == "to_approve" and not record._petty_cash_approval_error()

    @api.depends_context("uid")
    @api.depends("state", "fund_id")
    def _compute_can_pay(self):
        for record in self:
            record.can_pay = bool(
                record.state == "approved" and record.fund_id and record.fund_id._is_custodian()
            )

    # Rules -------------------------------------------------------------
    def _petty_cash_approval_error(self, user=None):
        """Why ``user`` cannot approve the document now (False if they can)."""
        self.ensure_one()
        user = user or self.env.user
        is_manager = user.has_group("ebshel_petty_cash.group_petty_cash_manager")
        if not user.has_group(self._petty_cash_approver_group()):
            return self.env._("You are not allowed to approve %s.", self._description)
        fund = self.fund_id
        if (
            fund.approver_ids
            and user not in fund.approver_ids
            and not is_manager
            and self._petty_cash_approver_group() != "ebshel_petty_cash.group_petty_cash_manager"
        ):
            return self.env._("Only the approvers of %s can approve it.", fund.display_name)
        if self.requested_by_id == user and not self.company_id.petty_cash_self_approval:
            return self.env._("You cannot approve your own request.")
        if self.first_approver_id:
            if self.first_approver_id == user:
                return self.env._("The second approval must come from another person.")
            if not is_manager:
                return self.env._("The second approval must come from a petty cash manager.")
        return False

    def _petty_cash_approvers(self):
        """Users who receive the approval activity."""
        self.ensure_one()
        users = self.fund_id.approver_ids
        if self._petty_cash_approver_group() == "ebshel_petty_cash.group_petty_cash_manager" or (
            self.first_approver_id
        ):
            users = users.filtered(
                lambda u: u.has_group("ebshel_petty_cash.group_petty_cash_manager")
            )
        return (users - self.requested_by_id - self.first_approver_id).filtered("active")

    # Actions -----------------------------------------------------------
    def action_submit(self):
        for record in self:
            if record.state != "draft":
                raise UserError(self.env._("Only draft documents can be submitted."))
            record.fund_id._check_open()
            record._petty_cash_check_submit()
        for record in self:
            record.write(
                {
                    "state": "to_approve",
                    "first_approver_id": False,
                    "approver_id": False,
                    "approval_date": False,
                    "refuse_reason": False,
                }
            )
            if record._petty_cash_auto_approved():
                record.message_post(body=self.env._("Approved automatically (within the fund limit)."))
                record._petty_cash_set_approved(self.env.user)
            else:
                record._petty_cash_schedule_approval()

    def _petty_cash_schedule_approval(self):
        for record in self:
            for user in record._petty_cash_approvers():
                record.activity_schedule(
                    "ebshel_petty_cash.mail_act_petty_cash_approval",
                    summary=self.env._("Approve %s", record.name),
                    user_id=user.id,
                )

    def action_approve(self):
        for record in self:
            if record.state != "to_approve":
                raise UserError(self.env._("Only documents waiting for approval can be approved."))
            error = record._petty_cash_approval_error()
            if error:
                raise UserError(error)
        for record in self:
            record.activity_unlink(["ebshel_petty_cash.mail_act_petty_cash_approval"])
            if record.needs_second_approval and not record.first_approver_id:
                record.first_approver_id = self.env.user
                record.message_post(
                    body=self.env._("First approval given; a petty cash manager must confirm.")
                )
                record._petty_cash_schedule_approval()
                continue
            record._petty_cash_set_approved(self.env.user)

    def _petty_cash_set_approved(self, user):
        self.write(
            {
                "state": "approved",
                "approver_id": user.id,
                "approval_date": fields.Datetime.now(),
            }
        )
        self._petty_cash_after_approval()

    def action_refuse(self):
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Refuse"),
            "res_model": "ebshel.petty.cash.refuse.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"active_model": self._name, "active_ids": self.ids},
        }

    def _petty_cash_refuse(self, reason):
        for record in self:
            if record.state not in ("to_approve", "approved"):
                raise UserError(self.env._("Only submitted documents can be refused."))
            if not self.env.user.has_group(record._petty_cash_approver_group()):
                raise UserError(self.env._("You are not allowed to refuse %s.", record._description))
        for record in self:
            record.activity_unlink(["ebshel_petty_cash.mail_act_petty_cash_approval"])
            record.write({"state": "refused", "refuse_reason": reason})
            record.message_post(
                body=self.env._("Refused: %s", reason),
                partner_ids=record.requested_by_id.partner_id.ids,
            )

    def action_draft(self):
        for record in self:
            if record.state not in ("refused", "cancelled", "to_approve"):
                raise UserError(self.env._("Only refused, cancelled or submitted documents go back to draft."))
            if record.state == "to_approve" and record.requested_by_id != self.env.user and not (
                self.env.user.has_group(record._petty_cash_approver_group())
            ):
                raise UserError(self.env._("Only the requester can withdraw the request."))
        self.activity_unlink(["ebshel_petty_cash.mail_act_petty_cash_approval"])
        self.write(
            {
                "state": "draft",
                "first_approver_id": False,
                "approver_id": False,
                "approval_date": False,
                "move_id": False,
            }
        )
