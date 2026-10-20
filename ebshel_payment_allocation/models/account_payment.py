# Part of ebshel_payment_allocation. See LICENSE file for full copyright and licensing details.
"""Open invoices and bills on the payment form, settled when the payment is confirmed.

Odoo's own way to pay specific invoices is the Register Payment wizard, which starts
from the invoices. This module adds the other direction: a payment typed in by hand
lists the partner's open items - one ``account.payment.allocation`` per receivable or
payable journal item that is posted, unreconciled, on the payment's counterpart
account, in its currency and with the sign the payment direction settles - the user
ticks what the money is for, and confirming the payment books one counterpart journal
item per allocation (``_prepare_move_lines_per_type``), reconciles each with its
invoice and drops the lines that were not ticked.

The list is built by onchange, not by a stored compute: payments made by the wizard,
the bank reconciliation or any code never load a partner's open items - only a form
does, and only while the payment is draft.

How the amount and the allocations relate (``amount_from_allocation``):

* while the amount follows the allocations (the default), ticking an item allocates
  its whole open amount and the payment amount becomes the total;
* once the user types an amount it is theirs: ticking fills only what is left of it,
  lowering it trims the newest allocations, raising it leaves the rest unallocated (an
  open credit on the partner's account); clearing it hands it back to the allocations.
"""
from itertools import zip_longest

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import formatLang

# More open items than this on one partner is a collections problem, not a picker one.
OPEN_ITEMS_LIMIT = 1000


class AccountPayment(models.Model):
    _inherit = "account.payment"

    allocation_ids = fields.One2many(
        "account.payment.allocation",
        "payment_id",
        string="Open Items",
        copy=False,
        help="The partner's open invoices or bills; tick the ones this payment settles.",
    )
    amount_from_allocation = fields.Boolean(
        string="Amount Follows the Allocation",
        default=True,
        copy=False,
        help="On: the payment amount is the total of the allocated open items. "
        "Off: the amount was typed in and the allocations fit into it.",
    )
    open_items_count = fields.Integer(compute="_compute_allocation_figures")
    open_items_total = fields.Monetary(
        compute="_compute_allocation_figures", currency_field="currency_id", string="Open"
    )
    allocated_amount = fields.Monetary(
        compute="_compute_allocation_figures", currency_field="currency_id", string="Allocated"
    )
    unallocated_amount = fields.Monetary(
        compute="_compute_allocation_figures", currency_field="currency_id", string="Unallocated"
    )
    allocation_warning = fields.Char(compute="_compute_allocation_figures")
    allocation_note = fields.Char(compute="_compute_allocation_figures")

    # -- figures ----------------------------------------------------------------------

    def _allocation_currency(self):
        return self.currency_id or self.company_id.currency_id or self.env.company.currency_id

    def _money(self, amount):
        return formatLang(self.env, amount, currency_obj=self._allocation_currency())

    @api.depends(
        "amount", "currency_id", "state", "partner_id",
        "allocation_ids.selected", "allocation_ids.allocate", "allocation_ids.amount_open",
    )
    def _compute_allocation_figures(self):
        for pay in self:
            lines = pay.allocation_ids
            chosen = lines.filtered("selected")
            pay.open_items_count = len(lines)
            pay.open_items_total = sum(lines.mapped("amount_open"))
            pay.allocated_amount = sum(chosen.mapped("allocate"))
            pay.unallocated_amount = pay.amount - pay.allocated_amount
            pay.allocation_warning = pay._allocation_problem(chosen)
            pay.allocation_note = pay._allocation_hint(chosen)

    def _allocation_problem(self, chosen=None):
        """What stops the payment from being confirmed with its allocations, or False."""
        self.ensure_one()
        currency = self._allocation_currency()
        if chosen is None:
            chosen = self.allocation_ids.filtered("selected")
        for line in chosen:
            if currency.compare_amounts(line.allocate, 0) < 0:
                return _("%s: a negative allocation.", line.display_name)
            if currency.compare_amounts(line.allocate, line.amount_open) > 0:
                return _(
                    "%(item)s: %(allocated)s allocated, but only %(open)s is open.",
                    item=line.display_name,
                    allocated=self._money(line.allocate),
                    open=self._money(line.amount_open),
                )
        total = sum(chosen.mapped("allocate"))
        if chosen and currency.compare_amounts(total, self.amount) > 0:
            return _(
                "The allocations (%(total)s) exceed the payment amount (%(amount)s) by %(excess)s.",
                total=self._money(total),
                amount=self._money(self.amount),
                excess=self._money(total - self.amount),
            )
        return False

    def _allocation_hint(self, chosen):
        self.ensure_one()
        if self.state not in (False, "draft"):
            return False
        if not self.allocation_ids:
            if self.partner_id and self.payment_type in ("inbound", "outbound"):
                return _("No open invoice or bill for this partner in this currency.")
            return False
        if not chosen:
            return _(
                "Tick what this payment is for: an item is allocated in full, "
                "lower its allocation for a part payment."
            )
        unallocated = self.amount - sum(chosen.mapped("allocate"))
        if self._allocation_currency().compare_amounts(unallocated, 0) > 0:
            return _(
                "%s of the payment is not allocated: it stays as an open credit "
                "on the partner's account.",
                self._money(unallocated),
            )
        return False

    # -- the partner's open items -----------------------------------------------------

    def _open_items_domain(self):
        """The receivable or payable items this payment could settle, or None when it
        cannot settle anything (no partner, no counterpart account, a transfer)."""
        self.ensure_one()
        partner = self.partner_id.commercial_partner_id
        account = self.destination_account_id
        if (
            not partner
            or not account
            or self.payment_type not in ("inbound", "outbound")
            or account.account_type not in ("asset_receivable", "liability_payable")
        ):
            return None
        # Money coming in settles what the partner owes us (a positive residual: invoices,
        # vendor refunds); money going out settles what we owe (a negative one: bills,
        # credit notes, a customer's overpayment).
        operator = ">" if self.payment_type == "inbound" else "<"
        company = self.company_id or self.env.company
        domain = [
            ("company_id", "=", company.id),
            ("partner_id", "=", partner.id),
            ("account_id", "=", account.id),
            ("parent_state", "=", "posted"),
            ("reconciled", "=", False),
            ("currency_id", "=", self._allocation_currency().id),
            ("amount_residual_currency", operator, 0),
        ]
        if self.move_id:
            domain.append(("move_id", "!=", self.move_id.id))
        return domain

    def _open_items(self):
        self.ensure_one()
        domain = self._open_items_domain()
        if domain is None:
            return self.env["account.move.line"]
        return self.env["account.move.line"].search(
            domain, order="date_maturity, date, id", limit=OPEN_ITEMS_LIMIT
        )

    def _load_open_items(self):
        """List the partner's open items afresh, keeping the allocations of those still open."""
        for pay in self:
            kept = {
                line.move_line_id.id: line.allocate
                for line in pay.allocation_ids
                if line.selected and line.allocate
            }
            commands = [Command.clear()]
            for item in pay._open_items():
                allocate = kept.get(item.id, 0.0)
                commands.append(
                    Command.create(
                        {"move_line_id": item.id, "selected": bool(allocate), "allocate": allocate}
                    )
                )
            pay.allocation_ids = commands
            pay._follow_allocations()
        return True

    def _follow_allocations(self):
        """The amount is the allocated total, while it follows the allocations."""
        for pay in self:
            if pay.amount_from_allocation:
                pay.amount = sum(pay.allocation_ids.filtered("selected").mapped("allocate"))

    def _trim_allocations(self, amount):
        """Cut the allocations down to `amount`, the newest due dates first."""
        self.ensure_one()
        currency = self._allocation_currency()
        chosen = self.allocation_ids.filtered("selected")
        excess = sum(chosen.mapped("allocate")) - amount
        for line in list(chosen)[::-1]:
            if currency.compare_amounts(excess, 0) <= 0:
                break
            cut = min(line.allocate, excess)
            line.allocate = currency.round(line.allocate - cut)
            excess -= cut
            if currency.is_zero(line.allocate):
                line.selected = False

    @api.onchange("partner_id", "partner_type", "payment_type", "company_id", "currency_id")
    def _onchange_open_items(self):
        for pay in self:
            if pay.state in (False, "draft"):
                pay._load_open_items()

    @api.onchange("allocation_ids")
    def _onchange_allocations(self):
        self._follow_allocations()

    @api.onchange("amount")
    def _onchange_amount_allocation(self):
        for pay in self:
            currency = pay._allocation_currency()
            chosen = pay.allocation_ids.filtered("selected")
            if not chosen:
                # typed before anything is ticked, the amount is the user's; cleared, it is free
                pay.amount_from_allocation = currency.is_zero(pay.amount)
            elif currency.compare_amounts(pay.amount, 0) <= 0:
                # cleared: the allocations take over again
                pay.amount_from_allocation = True
                pay.amount = sum(chosen.mapped("allocate"))
            elif not currency.is_zero(pay.amount - sum(chosen.mapped("allocate"))):
                pay.amount_from_allocation = False
                pay._trim_allocations(pay.amount)

    # -- buttons ----------------------------------------------------------------------

    def _draft_for_allocation(self):
        if any(pay.state != "draft" for pay in self):
            raise UserError(_("Only a draft payment's allocation can be changed."))
        return self

    def action_refresh_open_items(self):
        return self._draft_for_allocation()._load_open_items()

    def action_allocate_all(self):
        """Every open item, oldest first; up to the amount when the user typed one."""
        for pay in self._draft_for_allocation():
            currency = pay._allocation_currency()
            remaining = None if pay.amount_from_allocation else pay.amount
            for line in pay.allocation_ids:
                if remaining is None:
                    line.allocate = line.amount_open
                else:
                    line.allocate = min(line.amount_open, max(remaining, 0.0))
                    remaining -= line.allocate
                line.selected = not currency.is_zero(line.allocate)
            pay._follow_allocations()
        return True

    def action_allocate_none(self):
        for pay in self._draft_for_allocation():
            pay.allocation_ids.write({"selected": False, "allocate": 0.0})
            pay._follow_allocations()
        return True

    # -- confirmation: split, post, reconcile -----------------------------------------

    def _allocations_to_apply(self):
        """The ticked lines with an amount: what the confirmation books and reconciles."""
        self.ensure_one()
        if getattr(self, "is_multi_partner", False):
            # ebshel_multi_partner_payment books one counterpart per partner line instead
            return self.env["account.payment.allocation"]
        currency = self._allocation_currency()
        return self.allocation_ids.filtered(
            lambda line: line.selected and currency.compare_amounts(line.allocate, 0) > 0
        )

    def _check_allocations(self):
        for pay in self:
            chosen = pay.allocation_ids.filtered("selected")
            if not chosen:
                continue
            if getattr(pay, "is_multi_partner", False):
                raise UserError(
                    _(
                        "A multi-partner payment cannot be allocated to invoices or bills: "
                        "clear the selection on the Open Invoices & Bills tab."
                    )
                )
            domain = pay._open_items_domain()
            if domain is None:
                raise UserError(_("The payment has no partner or counterpart account to allocate on."))
            # an item settled since the list was built says so, before its amounts do
            to_apply = pay._allocations_to_apply()
            still_open = self.env["account.move.line"].search(
                [("id", "in", to_apply.move_line_id.ids)] + domain
            )
            for line in to_apply:
                if line.move_line_id not in still_open:
                    raise UserError(
                        _(
                            "%s is no longer open for this payment: it was paid, cancelled or "
                            "moved to another account or currency. Refresh the open items.",
                            line.display_name,
                        )
                    )
            problem = pay._allocation_problem(chosen)
            if problem:
                raise UserError(problem)

    def action_post(self):
        self._check_allocations()
        self.filtered(
            lambda pay: pay.move_id and pay.move_id.state == "draft" and pay._allocations_to_apply()
        )._sync_allocations_to_draft_move()
        result = super(AccountPayment, self.with_context(payment_allocation_split=True)).action_post()
        self._reconcile_allocations()
        return result

    def _prepare_move_lines_per_type(self, write_off_line_vals=None, force_balance=None):
        vals = super()._prepare_move_lines_per_type(
            write_off_line_vals=write_off_line_vals, force_balance=force_balance
        )
        # Only while confirming: Odoo's own synchronisation of a draft entry expects ONE
        # counterpart line, so a draft payment keeps the standard entry.
        if not self.env.context.get("payment_allocation_split"):
            return vals
        allocations = self._allocations_to_apply()
        counterparts = vals.get("counterpart_lines") or []
        if allocations and len(counterparts) == 1:
            vals["counterpart_lines"] = self._split_counterpart(counterparts[0], allocations)
        return vals

    def _split_counterpart(self, base, allocations):
        """One counterpart item per allocation, plus one for whatever is left."""
        self.ensure_one()
        currency = self._allocation_currency()
        company_currency = self.company_id.currency_id
        total_currency = base["amount_currency"]
        total_balance = base["balance"]
        allocated = sum(allocations.mapped("allocate"))
        if currency.compare_amounts(allocated, abs(total_currency)) > 0:
            raise UserError(
                _(
                    "The allocations (%(allocated)s) exceed what the payment books on the "
                    "partner's account (%(total)s).",
                    allocated=self._money(allocated),
                    total=self._money(abs(total_currency)),
                )
            )
        sign = -1 if total_currency < 0 else 1
        lines = []
        for allocation in allocations:
            amount_currency = currency.round(sign * allocation.allocate)
            balance = (
                company_currency.round(total_balance * amount_currency / total_currency)
                if total_currency
                else 0.0
            )
            lines.append(
                {
                    **base,
                    "name": "%s: %s" % (base["name"], allocation.display_name)
                    if base.get("name")
                    else allocation.display_name,
                    "amount_currency": amount_currency,
                    "balance": balance,
                    "allocation_id": allocation.id,
                }
            )
        rest_currency = currency.round(
            total_currency - sum(line["amount_currency"] for line in lines)
        )
        rest_balance = company_currency.round(total_balance - sum(line["balance"] for line in lines))
        if not currency.is_zero(rest_currency):
            lines.append(
                {**base, "amount_currency": rest_currency, "balance": rest_balance, "allocation_id": False}
            )
        elif not company_currency.is_zero(rest_balance):
            # conversion dust: onto the last allocation, so the entry balances
            lines[-1]["balance"] = company_currency.round(lines[-1]["balance"] + rest_balance)
        return lines

    def _sync_allocations_to_draft_move(self):
        """Rebuild the draft entry's items with the split - what Odoo's own synchronisation
        does, but with any number of counterpart lines (a payment reset to draft keeps its
        entry, and confirming it again posts that entry as it is)."""
        for pay in self.with_context(payment_allocation_split=True):
            liquidity_lines, counterpart_lines, writeoff_lines = pay._seek_for_lines()
            write_off_line_vals = []
            if liquidity_lines and counterpart_lines and writeoff_lines:
                write_off_line_vals.append(
                    {
                        "name": writeoff_lines[0].name,
                        "account_id": writeoff_lines[0].account_id.id,
                        "partner_id": writeoff_lines[0].partner_id.id,
                        "currency_id": writeoff_lines[0].currency_id.id,
                        "amount_currency": sum(writeoff_lines.mapped("amount_currency")),
                        "balance": sum(writeoff_lines.mapped("balance")),
                    }
                )
            per_type = pay._prepare_move_lines_per_type(write_off_line_vals=write_off_line_vals)
            commands = []
            for existing, new_vals_list in (
                (liquidity_lines, per_type.get("liquidity_lines", [])),
                (counterpart_lines, per_type.get("counterpart_lines", [])),
            ):
                for line, new_vals in zip_longest(existing, new_vals_list):
                    if line and new_vals:
                        commands.append(Command.update(line.id, new_vals))
                    elif new_vals:
                        commands.append(Command.create(new_vals))
                    elif line:
                        commands.append(Command.delete(line.id))
            commands += [Command.delete(line.id) for line in writeoff_lines]
            commands += [
                Command.create(vals)
                for vals in per_type.get("write_off_lines", []) + per_type.get("withholding_lines", [])
            ]
            pay.move_id.with_context(skip_invoice_sync=True).write(
                {
                    "date": pay.date,
                    "partner_id": pay.commercial_partner_id.id,
                    "currency_id": pay.currency_id.id,
                    "partner_bank_id": pay.partner_bank_id.id,
                    "line_ids": commands,
                }
            )

    def _reconcile_allocations(self):
        for pay in self:
            allocations = pay._allocations_to_apply()
            if not allocations or pay.move_id.state != "posted":
                continue
            _liquidity, counterparts, _writeoff = pay._seek_for_lines()
            by_allocation = {
                line.allocation_id.id: line for line in counterparts if line.allocation_id
            }
            for allocation in allocations:
                line = by_allocation.get(allocation.id)
                if line:  # the entry was built with the split
                    (line + allocation.move_line_id).reconcile()
            # a confirmed payment keeps only what it settled
            (pay.allocation_ids - allocations).unlink()

    def action_draft(self):
        result = super().action_draft()
        self.filtered(lambda pay: pay.move_id and pay.move_id.state == "draft")._merge_counterpart_lines()
        return result

    def _merge_counterpart_lines(self):
        """Back to one receivable or payable item - what Odoo's synchronisation of a draft
        payment expects; the allocations stay for the next confirmation."""
        for pay in self:
            _liquidity, counterparts, _writeoff = pay._seek_for_lines()
            if len(counterparts) <= 1 and not counterparts.allocation_id:
                continue
            keep = counterparts[0]
            name = "".join(x[1] for x in pay._get_aml_default_display_name_list() if x[1])
            commands = [
                Command.update(
                    keep.id,
                    {
                        "name": name,
                        "amount_currency": sum(counterparts.mapped("amount_currency")),
                        "balance": sum(counterparts.mapped("balance")),
                        "allocation_id": False,
                    },
                )
            ] + [Command.delete(line.id) for line in counterparts[1:]]
            pay.move_id.with_context(skip_invoice_sync=True).write({"line_ids": commands})
