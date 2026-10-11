# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1.
{
    'name': 'Purchase Advance Bills',
    'version': '20.0.5.0.0',
    'category': 'Inventory/Purchase',
    'summary': 'Create advance vendor bills from a Purchase Order; each posted '
               'advance bill is deducted as its own line when the main bill is generated.',
    'description': """
Purchase Advance Bills
======================
Replace one-step "pay now" advances with a proper Advance Bill workflow:

1. **Create Advance Bill** – from the PO, open the smart wizard to generate a
   draft vendor bill against a dedicated purchase advance account.

2. **Post & Pay Normally** – confirm the advance bill and register payment
   through Odoo's standard Register Payment flow (bank/cash, partial,
   multi-currency). Payment is optional for the deduction.

3. **Auto-Deduction** – when you create the main vendor bill from the PO, ONE
   deduction line per **posted** advance bill is added automatically, each
   referencing its source advance bill. Deduction follows the posted advance
   bill amount (not the cash paid), so the advance account always clears to
   zero and the vendor is never over-billed.

Key features
------------
* Posted advance bills are deducted in full – one line each, with reference.
* Per-advance-bill tracking – partial deductions spread across multiple bills.
* Rich wizard UI – order lines preview, existing advance summary, headroom bar.
* Flexible amounts – fixed, percentage, or full remaining headroom.
* Create / post advance bills at any time, including after full billing.
* Multi-currency & multi-company safe.
* Manual "Apply Advance Deductions" button on draft vendor bills.
* Settings: auto-deduct toggle, order-total cap, configurable advance account.
* Per-product / per-product-category advance account (company-specific): the
  account is resolved product → category → company default, so a product-level
  override always wins over its category, which in turn wins over the company
  default set in Purchase > Settings.
""",
    'author': 'Ebshel Technologies',
    'maintainer': 'Ebshel Technologies',
    'website': 'https://ebshel.com',
    'support': 'ebinpg777@gmail.com',
    'license': 'OPL-1',
    'price': 79.00,
    'currency': 'USD',
    'depends': ['purchase', 'account'],
    'data': [
        'security/ir.access.csv',
        'wizard/purchase_advance_bill_wizard_views.xml',
        'views/purchase_order_views.xml',
        'views/account_move_views.xml',
        'views/product_views.xml',
        'views/product_category_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'ebshel_purchase_advance/static/src/scss/purchase_advance_wizard.scss',
        ],
    },
    'images': [
        'static/description/banner.png',
        'static/description/icon.png',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
}
