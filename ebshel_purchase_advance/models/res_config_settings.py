# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1.

from odoo import models, fields, _
from odoo.exceptions import UserError


class ResCompany(models.Model):
    _inherit = "res.company"

    purchase_advance_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Purchase Advance Account",
        domain="[('company_ids', 'in', [id])]",
        help="Company-level default ledger account used as the bridge for advance "
             "bills when the product category has no advance account configured. "
             "Typically an asset/prepaid account (e.g. 'Purchase Advance Prepayments'). "
             "This account is debited when the advance bill is posted and credited "
             "when the deduction line is added to the main vendor bill.",
    )
    purchase_advance_auto_deduct = fields.Boolean(
        string="Auto-Deduct Advances on Bill Creation",
        default=True,
        help="When a vendor bill is created from a Purchase Order, automatically "
             "add deduction lines for any paid advance bills linked to that order.",
    )
    purchase_advance_limit_to_order = fields.Boolean(
        string="Limit Advance to Order Amount",
        default=True,
        help="Prevent creating advance bills that would exceed the total amount "
             "of the Purchase Order.",
    )

    def _get_purchase_advance_account(self):
        """Return the company-level configured advance account.

        Pure getter: returns an empty recordset when nothing is configured — it
        never creates an account silently. Callers decide whether a missing
        account is an error (see ``_require_purchase_advance_account``).
        """
        self.ensure_one()
        return self.purchase_advance_account_id

    def _require_purchase_advance_account(self):
        """Return the company advance account, raising a clear, actionable error
        when it has not been configured."""
        self.ensure_one()
        account = self._get_purchase_advance_account()
        if not account:
            raise UserError(_(
                "No Purchase Advance Account is configured for company "
                "\"%(company)s\".\n\n"
                "Set one on the product (Accounting tab), on the product "
                "category (Inventory > Configuration > Product Categories) or as "
                "the company default in Purchase > Settings > Purchase Advance "
                "Bills.",
                company=self.display_name,
            ))
        return account


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    purchase_advance_account_id = fields.Many2one(
        related="company_id.purchase_advance_account_id",
        readonly=False,
        string="Purchase Advance Account",
    )
    purchase_advance_auto_deduct = fields.Boolean(
        related="company_id.purchase_advance_auto_deduct",
        readonly=False,
    )
    purchase_advance_limit_to_order = fields.Boolean(
        related="company_id.purchase_advance_limit_to_order",
        readonly=False,
    )
