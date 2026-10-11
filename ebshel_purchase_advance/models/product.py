# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1.

from odoo import models, fields

from .product_category import ADVANCE_ACCOUNT_DOMAIN


class ProductTemplate(models.Model):
    _inherit = "product.template"

    property_purchase_advance_account_id = fields.Many2one(
        comodel_name="account.account",
        company_dependent=True,
        string="Purchase Advance Account",
        domain=ADVANCE_ACCOUNT_DOMAIN,
        ondelete="restrict",
        help="Ledger account used as the bridge for advance bills of this "
             "product. This value is company-specific and takes precedence over "
             "the product category and the company-level default set in "
             "Purchase > Settings. Keep empty to fall back to the category, then "
             "the company default.",
    )

    def _get_purchase_advance_account(self):
        """Return the purchase advance account configured directly on this
        product (company-dependent), or an empty recordset when none is set.

        This resolves the *product* layer only — the product category and the
        company-level fallbacks are composed by the caller (see
        ``purchase.order._get_advance_account``), mirroring how the native
        expense/income account cascade is built.

        Read in the current company context — callers that need a specific
        company must use ``product.with_company(company)``.
        """
        self.ensure_one()
        return self.property_purchase_advance_account_id


class ProductProduct(models.Model):
    _inherit = "product.product"

    def _get_purchase_advance_account(self):
        """Delegate to the product template (the company-dependent property is
        defined there), matching Odoo's native product-account delegation."""
        self.ensure_one()
        return self.product_tmpl_id._get_purchase_advance_account()
