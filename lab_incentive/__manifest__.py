# -*- coding: utf-8 -*-
{
    'name': 'Lab Incentive',
    'version': '19.0.1.4.0',
    'summary': "Monthly executive incentives: calculate, approve, disburse (R19/R20)",
    'author': 'Ebin P G',
    'maintainer': 'Ebin P G',
    'category': 'Sales',
    'license': 'LGPL-3',
    # lab_collections: the 'paid' basis asks its countback - payment_state never
    # reaches 'paid' on a ledger whose receipts are never reconciled.
    'depends': ['lab_fieldwork', 'lab_delivery', 'dynamic_filter_tiles',
                'lab_collections'],
    'data': [
        'security/incentive_security.xml',
        'security/ir.model.access.csv',
        'data/ir_sequence.xml',
        'data/ir_cron.xml',
        'views/product_views.xml',
        'views/incentive_rule_views.xml',
        'views/incentive_sheet_views.xml',
        'wizard/generate_sheets_views.xml',
        'views/menus.xml',
        'report/incentive_report.xml',
        'report/incentive_report_templates.xml',
        'data/filter_tiles.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'lab_incentive/static/src/xml/my_day_incentive.xml',
            'lab_incentive/static/src/js/slab_widget.js',
            'lab_incentive/static/src/xml/slab_widget.xml',
            'lab_incentive/static/src/scss/slab_widget.scss',
        ],
    },
    'installable': True,
    'application': False,
}
