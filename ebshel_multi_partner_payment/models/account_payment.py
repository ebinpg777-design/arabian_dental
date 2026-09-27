# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.

from itertools import zip_longest

from odoo import models, fields, api, _, Command
from odoo.exceptions import UserError


class AccountPayment(models.Model):
    _inherit = "account.payment"

    is_multi_partner = fields.Boolean(string="Enable Multi Partner Payment")

    multi_partner_payment_ids = fields.One2many(
        comodel_name="account.multi.partner.payment.line",
        inverse_name="multi_payment_id",
        string="Multi Partner Payments",
    )

    partner_ids = fields.Many2many(
        comodel_name="res.partner",
        string="Partners",
        compute="_compute_partner_ids",
        store=True,
    )

    @api.depends("is_multi_partner", "partner_id", "multi_partner_payment_ids.partner_id")
    def _compute_partner_ids(self):
        for payment in self:
            if payment.is_multi_partner:
                partners = payment.multi_partner_payment_ids.mapped("partner_id")
            else:
                partners = payment.partner_id
            payment.partner_ids = [Command.set(partners.ids)]

    @api.model
    def _get_trigger_fields_to_synchronize(self):
        return super()._get_trigger_fields_to_synchronize() + ("multi_partner_payment_ids",)

    def _prepare_move_lines_per_type(self, write_off_line_vals=None, force_balance=None):
        self.ensure_one()

        if not self.is_multi_partner:
            return super()._prepare_move_lines_per_type(
                write_off_line_vals=write_off_line_vals,
                force_balance=force_balance,
            )

        return self._prepare_multi_partner_move_lines(
            write_off_line_vals=write_off_line_vals,
            force_balance=force_balance,
        )

    def _prepare_multi_partner_move_lines(self, write_off_line_vals=None, force_balance=None):
        self.ensure_one()

        if not self.outstanding_account_id:
            raise UserError(_(
                "You can't create a new payment without an outstanding payments/receipts account "
                "set either on the company or the %(payment_method)s payment method in the %(journal)s journal.",
                payment_method=self.payment_method_line_id.name,
                journal=self.journal_id.display_name,
            ))

        line_name = "".join(x[1] for x in self._get_aml_default_display_name_list() if x[1])

        write_off_lines = write_off_line_vals or []
        write_off_amount_currency = sum(x.get("amount_currency", 0.0) for x in write_off_lines)
        write_off_balance = sum(x.get("balance", 0.0) for x in write_off_lines)

        withholding_lines = self._prepare_move_withholding_lines({})
        withholding_amount_currency = sum(x.get("amount_currency", 0.0) for x in withholding_lines)
        withholding_balance = sum(x.get("balance", 0.0) for x in withholding_lines)

        # Same behaviour as Odoo core: do not combine write-off and withholding lines.
        if withholding_lines and write_off_lines:
            write_off_lines = []
            write_off_amount_currency = 0.0
            write_off_balance = 0.0

        if self.payment_type == "inbound":
            liquidity_amount_currency = self.amount
        elif self.payment_type == "outbound":
            liquidity_amount_currency = -self.amount
        else:
            liquidity_amount_currency = 0.0

        if not write_off_line_vals and force_balance is not None:
            sign = 1 if liquidity_amount_currency > 0 else -1
            liquidity_balance = sign * abs(force_balance)
        else:
            liquidity_balance = self.currency_id._convert(
                liquidity_amount_currency,
                self.company_id.currency_id,
                self.company_id,
                self.date,
            )

        liquidity_amount_currency -= withholding_amount_currency
        liquidity_balance -= withholding_balance

        liquidity_lines = self._prepare_move_liquidity_lines({
            "name": line_name,
            "balance": liquidity_balance,
            "amount_currency": liquidity_amount_currency,
        })

        counterpart_lines = []

        # Counterpart lines must balance the liquidity + write-off + withholding lines.
        # Inbound: liquidity is positive, counterpart lines are negative.
        # Outbound: liquidity is negative, counterpart lines are positive.
        counterpart_sign = -1 if self.payment_type == "inbound" else 1

        for line in self.multi_partner_payment_ids:
            counterpart_amount_currency = counterpart_sign * line.amount
            counterpart_balance = self.currency_id._convert(
                counterpart_amount_currency,
                self.company_id.currency_id,
                self.company_id,
                self.date,
            )

            counterpart_lines.append({
                "name": line.ref or line_name,
                "date_maturity": self.date,
                "partner_id": line.partner_id.id,
                "account_id": line.account_id.id,
                "currency_id": self.currency_id.id,
                "balance": counterpart_balance,
                "amount_currency": counterpart_amount_currency,
                "multi_payment_line_id": line.id,
            })

        # If write-off/withholding are present, adjust counterpart balance by their totals.
        # For normal multi-partner payments this block has no practical effect.
        if counterpart_lines and (write_off_amount_currency or write_off_balance or withholding_amount_currency or withholding_balance):
            counterpart_total_amount_currency = sum(x["amount_currency"] for x in counterpart_lines)
            counterpart_total_balance = sum(x["balance"] for x in counterpart_lines)

            expected_counterpart_amount_currency = (
                -liquidity_amount_currency
                - write_off_amount_currency
                - withholding_amount_currency
            )
            expected_counterpart_balance = (
                -liquidity_balance
                - write_off_balance
                - withholding_balance
            )

            amount_currency_delta = expected_counterpart_amount_currency - counterpart_total_amount_currency
            balance_delta = expected_counterpart_balance - counterpart_total_balance

            counterpart_lines[-1]["amount_currency"] += amount_currency_delta
            counterpart_lines[-1]["balance"] += balance_delta

        return {
            "liquidity_lines": liquidity_lines,
            "counterpart_lines": counterpart_lines,
            "write_off_lines": write_off_lines,
            "withholding_lines": withholding_lines,
        }
        
    def _synchronize_to_moves(self, changed_fields):
        multi_partner_payments = self.filtered("is_multi_partner")
        if multi_partner_payments:
            multi_partner_payments._synchronize_multi_partner_to_moves(changed_fields)
        return super(AccountPayment, self - multi_partner_payments)._synchronize_to_moves(changed_fields)

    def _synchronize_multi_partner_to_moves(self, changed_fields):
        if self.env.context.get("skip_account_move_synchronization"):
            return

        if not any(field_name in changed_fields for field_name in self._get_trigger_fields_to_synchronize()):
            return

        for pay in self.with_context(skip_account_move_synchronization=True):
            if not pay.move_id or pay.move_id.state == "posted":
                continue

            liquidity_lines, counterpart_lines, writeoff_lines = pay._seek_for_lines()

            if "amount" in changed_fields and len(liquidity_lines) > 1:
                raise UserError(_("You cannot change the amount of a payment with multiple liquidity lines."))

            write_off_line_vals = []
            if liquidity_lines and counterpart_lines and writeoff_lines:
                write_off_line_vals.append({
                    "name": writeoff_lines[0].name,
                    "account_id": writeoff_lines[0].account_id.id,
                    "partner_id": writeoff_lines[0].partner_id.id,
                    "currency_id": writeoff_lines[0].currency_id.id,
                    "amount_currency": sum(writeoff_lines.mapped("amount_currency")),
                    "balance": sum(writeoff_lines.mapped("balance")),
                })

            line_vals_per_type = pay._prepare_move_lines_per_type(write_off_line_vals=write_off_line_vals)
            line_ids_commands = []

            for liquidity_line, new_line_vals in zip_longest(
                liquidity_lines,
                line_vals_per_type.get("liquidity_lines", []),
            ):
                if liquidity_line and new_line_vals:
                    line_ids_commands.append(Command.update(liquidity_line.id, new_line_vals))
                elif not liquidity_line and new_line_vals:
                    line_ids_commands.append(Command.create(new_line_vals))
                elif liquidity_line and not new_line_vals:
                    line_ids_commands.append(Command.delete(liquidity_line.id))

            new_counterpart_vals = line_vals_per_type.get("counterpart_lines", [])
            used_counterpart_lines = self.env["account.move.line"]

            for vals in new_counterpart_vals:
                multi_payment_line_id = vals.get("multi_payment_line_id")
                existing_line = counterpart_lines.filtered(
                    lambda aml: aml.multi_payment_line_id.id == multi_payment_line_id
                )[:1]

                if existing_line:
                    line_ids_commands.append(Command.update(existing_line.id, vals))
                    used_counterpart_lines |= existing_line
                else:
                    line_ids_commands.append(Command.create(vals))

            for obsolete_line in counterpart_lines - used_counterpart_lines:
                line_ids_commands.append(Command.delete(obsolete_line.id))

            for line in writeoff_lines:
                line_ids_commands.append(Command.delete(line.id))

            for vals in line_vals_per_type.get("write_off_lines", []) + line_vals_per_type.get("withholding_lines", []):
                line_ids_commands.append(Command.create(vals))

            to_write = {
                "date": pay.date,
                "partner_id": pay.partner_id.id,
                "currency_id": pay.currency_id.id,
                "partner_bank_id": pay.partner_bank_id.id,
                "line_ids": line_ids_commands,
            }

            if "journal_id" in changed_fields:
                to_write.update({
                    "name": "/",
                    "journal_id": pay.journal_id.id,
                })

            pay.move_id.with_context(
                skip_invoice_sync=True,
                check_move_validity=False,
            ).write(to_write)


class AccountMultiPartnerPaymentLine(models.Model):
    _name = "account.multi.partner.payment.line"
    _description = "Multi Partner Payment Line"

    multi_payment_id = fields.Many2one(
        comodel_name="account.payment",
        string="Payment",
        ondelete="cascade",
        index=True,
    )

    currency_id = fields.Many2one(
        comodel_name="res.currency",
        string="Currency",
        required=True,
        default=lambda self: self.env.company.currency_id,
        readonly=True,
    )

    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        default=lambda self: self.env.company,
        required=True,
    )

    partner_type = fields.Selection(
        related="multi_payment_id.partner_type",
        store=True,
    )

    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Customer/Vendor",
        store=True,
        readonly=False,
        ondelete="restrict",
        domain="['|', ('parent_id', '=', False), ('is_company', '=', True)]",
        check_company=True,
    )

    account_id = fields.Many2one(
        comodel_name="account.account",
        string="Account",
        required=True,
        store=True,
        readonly=False,
        compute="_compute_account_id",
        domain="[('account_type', 'in', ('asset_receivable', 'liability_payable'))]",
        check_company=True,
    )

    amount = fields.Monetary(currency_field="currency_id")
    ref = fields.Char(string="Memo")

    @api.depends("partner_id", "partner_type", "company_id")
    def _compute_account_id(self):
        self.account_id = False
        for line in self:
            if line.partner_type == "customer":
                if line.partner_id:
                    line.account_id = line.partner_id.with_company(line.company_id).property_account_receivable_id
                else:
                    line.account_id = self.env["account.account"].with_company(line.company_id).search([
                        *self.env["account.account"]._check_company_domain(line.company_id),
                        ("account_type", "=", "asset_receivable"),
                    ], limit=1)
            elif line.partner_type == "supplier":
                if line.partner_id:
                    line.account_id = line.partner_id.with_company(line.company_id).property_account_payable_id
                else:
                    line.account_id = self.env["account.account"].with_company(line.company_id).search([
                        *self.env["account.account"]._check_company_domain(line.company_id),
                        ("account_type", "=", "liability_payable"),
                    ], limit=1)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._sync_parent_payment_moves()
        return records

    def write(self, vals):
        res = super().write(vals)
        self._sync_parent_payment_moves()
        return res

    def unlink(self):
        payments = self.mapped("multi_payment_id")
        res = super().unlink()
        payments._synchronize_multi_partner_to_moves({"multi_partner_payment_ids"})
        return res

    def _sync_parent_payment_moves(self):
        payments = self.mapped("multi_payment_id")
        payments._synchronize_multi_partner_to_moves({"multi_partner_payment_ids"})


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    multi_payment_line_id = fields.Many2one(
        comodel_name="account.multi.partner.payment.line",
        string="Multi Payment Line",
        ondelete="set null",
        index=True,
    )