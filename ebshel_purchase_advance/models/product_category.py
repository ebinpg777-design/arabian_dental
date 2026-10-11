# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1.

from odoo import models, fields

# Exclude accounts that must never act as an advance (bridge) account.
# NB: Odoo 19 dropped ``account.account.deprecated`` in favour of the standard
# ``active`` flag, so archived accounts are filtered with ('active', '=', True).
ADVANCE_ACCOUNT_DOMAIN = (
    "[('account_type', 'not in', "
    "('asset_receivable', 'liability_payable', 'asset_cash', "
    "'liability_credit_card', 'off_balance')), "
    "('active', '=', True)]"
)


class ProductCategory(models.Model):
    _inherit = "product.category"

    property_purchase_advance_account_id = fields.Many2one(
        comodel_name="account.account",
        company_dependent=True,
        string="Purchase Advance Account",
        domain=ADVANCE_ACCOUNT_DOMAIN,
        ondelete="restrict",
        tracking=True,
        help="Ledger account used as the bridge for advance bills of products in "
             "this category. This value is company-specific and takes precedence "
             "over the company-level default set in Purchase > Settings.",
    )

    def _get_purchase_advance_account(self):
        """Return the purchase advance account defined on this category or the
        nearest ancestor that has one (company-dependent). Returns an empty
        recordset if none is configured anywhere in the hierarchy.

        Read in the current company context — callers that need a specific
        company must use ``category.with_company(company)``.
        """
        self.ensure_one()
        categ = self
        while categ:
            account = categ.property_purchase_advance_account_id
            if account:
                return account
            categ = categ.parent_id
        return self.env["account.account"]
