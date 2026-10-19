# Part of ebshel_petty_cash. See LICENSE file for full copyright and licensing details.
from urllib.parse import urlencode

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

    def _petty_cash_qr_src(self):
        """Image URL of a QR code linking to the document, for printouts."""
        self.ensure_one()
        url = f"{self.get_base_url()}/odoo/{self._name}/{self.id}"
        return "/report/barcode/?" + urlencode(
            {"barcode_type": "QR", "value": url, "width": 180, "height": 180}
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
    replenishments, driven by the approval matrix.

    States used: draft, to_approve, approved, refused (plus the document's own
    downstream states). Submitting builds one approval step per matching
    approval level; the steps are approved one after the other, each by one or
    by every approver of its level. No matching level: approved on submission.
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
    approver_id = fields.Many2one(
        "res.users", string="Approved By", readonly=True, copy=False, tracking=True,
        help="Who gave the final approval.",
    )
    approval_date = fields.Datetime(readonly=True, copy=False)
    submitted_date = fields.Datetime(readonly=True, copy=False)
    refuse_reason = fields.Text(readonly=True, copy=False)
    approval_step_ids = fields.One2many(
        "ebshel.petty.cash.approval.step",
        "res_id",
        string="Approval Chain",
        copy=False,
        bypass_search_access=True,
    )
    current_step_id = fields.Many2one(
        "ebshel.petty.cash.approval.step", compute="_compute_approval_status", string="Current Level"
    )
    approval_progress = fields.Char(compute="_compute_approval_status", string="Approval")
    pending_approver_ids = fields.Many2many(
        "res.users", compute="_compute_approval_status", string="Waiting For"
    )
    can_approve = fields.Boolean(compute="_compute_can_approve")
    is_my_approval = fields.Boolean(
        compute="_compute_can_approve", search="_search_is_my_approval", string="Waiting for Me"
    )
    can_pay = fields.Boolean(
        compute="_compute_can_pay",
        help="The current user may pay out the approved document (custodian or manager).",
    )

    def _valid_field_parameter(self, field, name):
        # tracking takes effect on the documents, which all inherit mail.thread
        return name == "tracking" or super()._valid_field_parameter(field, name)

    # Hooks -------------------------------------------------------------
    def _petty_cash_document_type(self):
        """Key of the document in the approval levels (see DOCUMENT_TYPES)."""
        raise NotImplementedError

    def _petty_cash_approval_amount(self):
        """Amount (fund currency) the approval is about."""
        raise NotImplementedError

    def _petty_cash_check_submit(self):
        """Raise if the document cannot be submitted."""

    def _petty_cash_extra_steps(self, levels):
        """Values of steps added to those of the matching ``levels``."""
        return []

    def _petty_cash_after_approval(self):
        """Called once a document reaches the approved state."""

    # Computes ----------------------------------------------------------
    @api.depends("approval_step_ids.state", "approval_step_ids.approver_ids", "approval_step_ids.approved_user_ids")
    def _compute_approval_status(self):
        for record in self:
            steps = record.approval_step_ids.filtered(lambda s: s.state != "cancelled")
            current = steps.filtered(lambda s: s.state == "pending")[:1]
            record.current_step_id = current
            record.pending_approver_ids = current.approver_ids - current.approved_user_ids
            done = len(steps.filtered(lambda s: s.state == "approved"))
            record.approval_progress = f"{done} / {len(steps)}" if steps else False

    @api.depends_context("uid")
    def _compute_can_approve(self):
        for record in self:
            record.can_approve = record.state == "to_approve" and not record._petty_cash_approval_error()
            record.is_my_approval = bool(
                record.state == "to_approve"
                and record.sudo().current_step_id._petty_cash_is_approver(self.env.user)
            )

    def _search_is_my_approval(self, operator, value):
        if operator not in ("=", "!=") or not isinstance(value, bool):
            return NotImplemented
        steps = self.env["ebshel.petty.cash.approval.step"].sudo().search(
            [("res_model", "=", self._name), ("is_my_approval", "=", True)]
        )
        domain = [("id", "in", steps.mapped("res_id"))]
        return domain if (operator == "=") == value else ["!", *domain]

    @api.depends_context("uid")
    @api.depends("state", "fund_id")
    def _compute_can_pay(self):
        for record in self:
            record.can_pay = bool(
                record.state == "approved" and record.fund_id and record.fund_id._is_custodian()
            )

    # Rules -------------------------------------------------------------
    def _petty_cash_amount_company(self):
        self.ensure_one()
        return self.fund_id._to_company_currency(self._petty_cash_approval_amount(), self.date)

    def _petty_cash_group_users(self, group_xmlid):
        """Internal users of the group with access to the document's company."""
        self.ensure_one()
        group = self.env.ref(group_xmlid)
        return (
            self.env["res.users"]
            .sudo()
            .search(
                [
                    ("all_group_ids", "in", group.id),
                    ("company_ids", "in", self.company_id.id),
                    ("share", "=", False),
                ]
            )
        )

    def _petty_cash_fallback_approvers(self):
        """Approvers of a level whose own approvers are all excluded."""
        self.ensure_one()
        users = self._petty_cash_group_users("ebshel_petty_cash.group_petty_cash_manager")
        if not self.company_id.petty_cash_self_approval:
            users -= self.requested_by_id
        return users

    def _petty_cash_applicable_levels(self):
        self.ensure_one()
        levels = self.env["ebshel.petty.cash.approval.level"].sudo().search(
            [("company_id", "in", (False, self.company_id.id))]
        )
        amount = self._petty_cash_amount_company()
        return levels.filtered(lambda level: level._petty_cash_applies(self, amount))

    def _petty_cash_approval_error(self, user=None):
        """Why ``user`` cannot approve the document now (False if they can)."""
        self.ensure_one()
        user = user or self.env.user
        if self.state != "to_approve":
            return self.env._("%s is not waiting for an approval.", self.display_name)
        step = self.sudo().current_step_id
        if not step:
            return self.env._("%s has no approval level waiting.", self.display_name)
        if not step._petty_cash_is_approver(user):
            return self.env._(
                "You are not an approver of the level %(level)s of %(document)s.",
                level=step.name,
                document=self.display_name,
            )
        if self.requested_by_id == user and not self.company_id.petty_cash_self_approval:
            return self.env._("You cannot approve your own request.")
        principal = step._petty_cash_principal(user)
        if principal in step.approved_user_ids:
            return self.env._("You already approved the level %s.", step.name)
        if self.company_id.petty_cash_distinct_approvers:
            earlier = self.sudo().approval_step_ids.filtered(
                lambda s: s.state == "approved" and s.sequence < step.sequence
            )
            if user in earlier.approved_user_ids | earlier.done_by_id:
                return self.env._(
                    "You approved an earlier level of %s: another person must approve this one.",
                    self.display_name,
                )
        return False

    def _petty_cash_create_steps(self):
        """Build the approval chain of the submitted documents. Returns the
        documents that need no approval."""
        Step = self.env["ebshel.petty.cash.approval.step"].sudo()
        no_approval = self.browse()
        for record in self:
            levels = record._petty_cash_applicable_levels()
            amount = record._petty_cash_amount_company()
            common = {
                "res_model": record._name,
                "res_id": record.id,
                "document_type": record._petty_cash_document_type(),
                "document_name": record.name,
                "company_id": record.company_id.id,
                "fund_id": record.fund_id.id,
                "requested_by_id": record.requested_by_id.id,
                "amount_company": amount,
                "risk_level": record._fields.get("risk_level") and record.risk_level or False,
            }
            values = []
            for index, level in enumerate(levels, start=1):
                approvers = level._petty_cash_resolve_approvers(record)
                if not record.company_id.petty_cash_self_approval:
                    approvers -= record.requested_by_id
                values.append(
                    {
                        **common,
                        "level_id": level.id,
                        "name": level.name,
                        "sequence": index * 10,
                        "approval_mode": level.approval_mode,
                        "approver_ids": [(6, 0, approvers.ids)],
                    }
                )
            for index, extra in enumerate(record._petty_cash_extra_steps(levels), start=len(levels) + 1):
                values.append({**common, "sequence": index * 10, **extra})
            if not values:
                no_approval |= record
                continue
            Step.create(values)
        return no_approval

    # Actions -----------------------------------------------------------
    def action_submit(self):
        for record in self:
            if record.state != "draft":
                raise UserError(self.env._("Only draft documents can be submitted."))
            record.fund_id._check_open()
            record._petty_cash_check_submit()
        for record in self:
            record.approval_step_ids.sudo().unlink()
            record.write(
                {
                    "state": "to_approve",
                    "approver_id": False,
                    "approval_date": False,
                    "refuse_reason": False,
                    "submitted_date": fields.Datetime.now(),
                }
            )
        no_approval = self._petty_cash_create_steps()
        for record in no_approval:
            record.message_post(body=self.env._("No approval level applies: approved on submission."))
            record._petty_cash_set_approved(self.env.user)
        for record in self - no_approval:
            first = record.approval_step_ids.sorted("sequence")[:1]
            first._petty_cash_activate()
            record.message_post(
                body=self.env._(
                    "Submitted for approval: %s.",
                    " → ".join(record.approval_step_ids.sorted("sequence").mapped("name")),
                )
            )

    def action_approve(self, comment=None):
        for record in self:
            error = record._petty_cash_approval_error()
            if error:
                raise UserError(error)
        user = self.env.user
        for record in self:
            step = record.sudo().current_step_id
            principal = step._petty_cash_principal(user)
            approved = step.approved_user_ids | principal
            on_behalf = (
                self.env._(" (on behalf of %s)", principal.name) if principal != user else ""
            )
            record.activity_unlink(["ebshel_petty_cash.mail_act_petty_cash_approval"], user_id=user.id)
            values = {"approved_user_ids": [(4, principal.id)]}
            complete = step.approval_mode == "any" or not (step.approver_ids - approved)
            if complete:
                values.update(
                    {
                        "state": "approved",
                        "done_by_id": user.id,
                        "done_date": fields.Datetime.now(),
                        "comment": comment or False,
                    }
                )
            step.write(values)
            record.message_post(
                body=self.env._("%(level)s approved by %(user)s%(behalf)s.", level=step.name, user=user.name, behalf=on_behalf)
                + (f" {comment}" if comment else "")
            )
            if not complete:
                continue
            record.activity_unlink(["ebshel_petty_cash.mail_act_petty_cash_approval"])
            following = record.sudo().approval_step_ids.filtered(lambda s: s.state == "waiting").sorted("sequence")[:1]
            if following:
                following._petty_cash_activate()
            else:
                record._petty_cash_set_approved(user)
        return True

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
        user = self.env.user
        for record in self:
            if record.state not in ("to_approve", "approved"):
                raise UserError(self.env._("Only submitted documents can be refused."))
            step = record.sudo().current_step_id
            allowed = (step and step._petty_cash_is_approver(user)) or user.has_group(
                "ebshel_petty_cash.group_petty_cash_manager"
            )
            if not allowed:
                raise UserError(self.env._("You are not allowed to refuse %s.", record.display_name))
        for record in self:
            steps = record.sudo().approval_step_ids
            steps.filtered(lambda s: s.state == "pending").write(
                {"state": "refused", "done_by_id": user.id, "done_date": fields.Datetime.now(), "comment": reason}
            )
            steps.filtered(lambda s: s.state == "waiting").write({"state": "cancelled"})
            record.activity_unlink(["ebshel_petty_cash.mail_act_petty_cash_approval"])
            record.write({"state": "refused", "refuse_reason": reason})
            record.message_post(
                body=self.env._("Refused by %(user)s: %(reason)s", user=user.name, reason=reason),
                partner_ids=record.requested_by_id.partner_id.ids,
            )

    def action_draft(self):
        for record in self:
            if record.state not in ("refused", "cancelled", "to_approve"):
                raise UserError(self.env._("Only refused, cancelled or submitted documents go back to draft."))
            if record.state == "to_approve" and record.requested_by_id != self.env.user and not (
                self.env.user.has_group("ebshel_petty_cash.group_petty_cash_manager")
            ):
                raise UserError(self.env._("Only the requester can withdraw the request."))
        self.activity_unlink(["ebshel_petty_cash.mail_act_petty_cash_approval"])
        self.sudo().approval_step_ids.unlink()
        self.write(
            {
                "state": "draft",
                "approver_id": False,
                "approval_date": False,
                "submitted_date": False,
                "move_id": False,
            }
        )

    def _petty_cash_cancel_steps(self):
        """Close the open approval steps of cancelled documents."""
        steps = self.sudo().approval_step_ids.filtered(lambda s: s.state in ("waiting", "pending"))
        steps.write({"state": "cancelled"})
        self.activity_unlink(["ebshel_petty_cash.mail_act_petty_cash_approval"])

    def unlink(self):
        steps = self.sudo().approval_step_ids
        result = super().unlink()
        steps.unlink()
        return result
