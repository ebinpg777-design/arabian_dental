# -*- coding: utf-8 -*-
##############################################################################
#    Ebshel Technologies – Odoo Module
#    Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
#    License OPL-1.
##############################################################################
{
    'name': 'Purchase Request Management',
    'version': '20.0.1.1.1',
    'category': 'Purchase',
    'summary': (
        'Full procurement flow: purchase requests, multi-level approvals, '
        'RFQ management, vendor price comparison, PO creation, and complete traceability.'
    ),
    'description': """
Purchase Request Management
=================================
Manage the full procurement lifecycle from internal purchase request to PO.

Key Features
------------
* Purchase request creation with multi-line products
* Configurable multi-level approval workflow (by amount, department, role)
* RFQ creation from approved request lines (one or many requests)
* Multi-vendor quotation capture and side-by-side comparison
* Winning vendor selection with justification
* New vendor creation directly from quotation
* Purchase Order creation from winning quotation
* Full traceability: PR ↔ RFQ ↔ Vendor Quotation ↔ PO
* Smart buttons, chatter, and mail notifications at every step
* Role-based access control: User, Manager, Approver, Procurement Officer
* Multi-company support
* List, Form, Kanban, Pivot, Graph, and Search views
* PDF reports: PR status, RFQ comparison, vendor price history
    """,
    'author': 'Ebshel Technologies',
    'maintainer': 'Ebshel Technologies',
    'website': 'https://ebshel.com',
    'support': 'ebinpg777@gmail.com',
    'license': 'OPL-1',
    'price': 299.00,
    'currency': 'USD',
    'depends': [
        'purchase',
        'stock',
        'purchase_stock',
        'analytic',
        'mail',
        'account',
    ],
    'data': [
        # Security (must load first)
        'security/groups.xml',
        # Data
        'data/sequences.xml',
        'data/mail_templates.xml',
        # Wizard views
        'wizard/wizard_create_rfq_views.xml',
        'wizard/wizard_select_winner_views.xml',
        'wizard/wizard_create_vendor_views.xml',
        'wizard/wizard_split_award_views.xml',
        'wizard/wizard_bargain_views.xml',
        'wizard/wizard_edit_qty_views.xml',
        'wizard/wizard_product_change_views.xml',
        # Views
        'views/purchase_request_approval_config_views.xml',
        'views/purchase_request_views.xml',
        'views/purchase_request_allocation_views.xml',
        'views/purchase_analytic_line_views.xml',
        'views/account_analytic_account_views.xml',
        'views/purchase_rfq_views.xml',
        'views/purchase_vendor_quotation_views.xml',
        'views/purchase_order_views.xml',
        'views/res_partner_views.xml',
        'views/product_views.xml',
        'views/rfq_terms_views.xml',
        'views/res_config_settings_views.xml',
        # Reports
        'report/purchase_request_report.xml',
        'report/purchase_rfq_report.xml',
        'report/purchase_rfq_comparison_report.xml',
        # Menu (must load last)
        'views/menu.xml',
        # Access (after the models' views and groups are loaded)
        'security/ir.access.csv',
    ],
    'assets': {
        'web.assets_backend': [
            'ebshel_purchase_request/static/src/js/rfq_comparison_view.js',
            'ebshel_purchase_request/static/src/xml/rfq_comparison_view.xml',
            'ebshel_purchase_request/static/src/scss/rfq_comparison.scss',
            'ebshel_purchase_request/static/src/js/procurement_dashboard.js',
            'ebshel_purchase_request/static/src/xml/procurement_dashboard.xml',
            'ebshel_purchase_request/static/src/scss/procurement_dashboard.scss',
            'ebshel_purchase_request/static/src/js/department_insight.js',
            'ebshel_purchase_request/static/src/xml/department_insight.xml',
            'ebshel_purchase_request/static/src/scss/department_insight.scss',
            'ebshel_purchase_request/static/src/js/rfq_vendor_comparison_field.js',
            'ebshel_purchase_request/static/src/xml/rfq_vendor_comparison_field.xml',
            'ebshel_purchase_request/static/src/scss/rfq_vendor_comparison_field.scss',
            'ebshel_purchase_request/static/src/views/purchase_request_dashboard.js',
            'ebshel_purchase_request/static/src/views/purchase_request_listview.js',
            'ebshel_purchase_request/static/src/views/purchase_request_dashboard.xml',
            'ebshel_purchase_request/static/src/views/purchase_request_dashboard.scss',
            'ebshel_purchase_request/static/src/views/purchase_rfq_dashboard.js',
            'ebshel_purchase_request/static/src/views/purchase_rfq_dashboard.xml',
        ],
    },
    'images': ['static/description/banner.png'],
    'application': True,
    'installable': True,
    'auto_install': False,
}
