{
    'name': 'Collections by Sales Route',
    'version': '19.0.2.13.0',
    'summary': 'Route-wise payment tracking and a collection-performance dashboard',
    'description': """
Collections by Sales Route
==========================
Money coming in, read the way the lab is organised.

* Every customer payment carries its clinic's **Sales Route**, so payments can be
  listed, searched and grouped by route in the standard accounting screens.
* A **Collections** dashboard in Accounting > Finance Ops: last month's POSTED
  INVOICES against this month's receipts and the collection percentage - by route
  and by salesperson - plus the open receivable aged by FIFO countback, a chase
  list per route, and drill-through from every figure to the documents behind it.
* The same figures print as a PDF for the route meeting.
""",
    'category': 'Accounting/Accounting',
    'author': 'Ebin P G',
    'maintainer': 'Ebin P G',
    'website': 'https://www.arabiandentallab.com',
    'license': 'LGPL-3',
    # lab_fieldwork: the Collections screen is open to a field-work
    # Executive, scoped to their own route, and names that group in its menu
    # and its access rules. (client, 2026-08-24)
    'depends': ['account', 'sale_custom', 'lab_finance_ops', 'lab_fieldwork',
               'epg_partner_statement'],
    'data': [
        'security/ir.model.access.csv',
        'report/collection_report.xml',
        'views/account_payment_views.xml',
        'views/collection_settings_views.xml',
        'views/collection_drill_views.xml',
        'views/collection_open_item_views.xml',
        'views/statement_wizard_views.xml',
        'views/collection_dashboard_views.xml',
        'views/collection_menus.xml',
        'views/lab_visit_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'lab_collections/static/src/dashboard/collection_dashboard.scss',
            'lab_collections/static/src/dashboard/collection_dashboard.js',
            'lab_collections/static/src/dashboard/collection_dashboard.xml',
            'lab_collections/static/src/dashboard/my_day_collections.xml',
            'lab_collections/static/src/dashboard/my_day_collections.js',
        ],
        'web.report_assets_common': [
            'lab_collections/static/src/scss/collection_report.scss',
        ],
    },
    'installable': True,
    'application': False,
}
