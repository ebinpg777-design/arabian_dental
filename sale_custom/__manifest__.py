# -*- coding: utf-8 -*-
{
    'name': 'Sales Custom',
    'version': '19.0.1.42.0',
    'category': 'Sales',
    'summary': 'Dental lab customization: per-patient orders, MRP job cards, GST invoicing, daily sales',
    'description': """
Dental / Orthodontic Lab customization
=======================================
Re-implemented for Odoo 19. Provides:

* Per-patient sale orders (patient, age, gender, appliance & impression details, priority)
* Emergency / urgent surcharge line
* Rework order numbering
* MRP integration (colour / U-L carried onto Manufacturing Orders & Work Orders, technicians)
* Courier / dispatch details on transfers
* India GST invoicing fields (GSTIN / PAN / HSN / DCI) + custom Tax Invoice & Job Card PDFs
* Daily Sales field-reporting for sales officers
""",
    'author': 'Migrated to Odoo 19',
    'website': '',
    'depends': [
        'sale_management',
        'sale_stock',
        'sale_mrp',
        'mrp',
        'account',
        'stock',
        'l10n_in',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/sale_security.xml',
        'data/ir_sequence_data.xml',
        'report/paperformat.xml',
        'report/report_layout.xml',
        'report/tax_invoice_report.xml',
        'report/core_reports_phone.xml',
        'report/jobcard_report.xml',
        'report/production_slip_report.xml',
        'report/delivery_slip_report.xml',
        'report/registration_ack_report.xml',
        'report/daily_sales_summary_report.xml',
        'report/report_actions.xml',
        'report/dispatch_register_report.xml',
        'report/order_list_report.xml',
        'report/order_count_report.xml',
        'views/sale_order_views.xml',
        'views/sale_action_defaults.xml',
        'views/search_panel_views.xml',
        'views/account_move_views.xml',
        'views/sale_menus.xml',
        'views/due_for_invoicing_views.xml',
        'views/res_partner_views.xml',
        'views/res_company_views.xml',
        'views/product_views.xml',
        'views/stock_picking_views.xml',
        'views/mrp_views.xml',
        'views/res_users_views.xml',
        'views/crm_team_views.xml',
        'views/res_config_settings_views.xml',
        'views/daily_sales_views.xml',
        'views/dispatch_register_views.xml',
        'views/order_list_views.xml',
        'views/order_count_views.xml',
        'views/confirm_wizard_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'sale_custom/static/src/scss/fdi_teeth_field.scss',
            'sale_custom/static/src/js/fdi_teeth_field.js',
            'sale_custom/static/src/xml/fdi_teeth_field.xml',
        ],
        'web.report_assets_common': [
            'sale_custom/static/src/scss/letterhead.scss',
            'sale_custom/static/src/scss/invoice_report.scss',
            'sale_custom/static/src/scss/dispatch_register.scss',
            'sale_custom/static/src/scss/order_list.scss',
            'sale_custom/static/src/scss/order_count.scss',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
