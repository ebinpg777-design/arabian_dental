# -*- coding: utf-8 -*-
{
    'name': 'Lab Manager / CEO Dashboard',
    'version': '19.0.1.15.0',
    'summary': 'CEO-level board: revenue, cash, receivables, coverage and alerts',
    'description': """
Lab Manager / CEO Dashboard
===========================
The board a lab owner opens, as opposed to the one a field manager opens.

* **Headlines with a baseline** - revenue, orders booked and collections each carry the
  immediately preceding window of the same length, so growth is like-for-like.
* **Cash** - liquid balances plus the petty cash float physically out with the field force.
* **Receivables** - open AR and the share of it past due, which is the number that decides
  whether a good revenue month is actually a good month.
* **Twelve-month revenue trend**, top clinics, and sales-team performance.
* **Alerts** - each carrying the magnitude that justifies it, so they can be prioritised.
""",
    'author': 'Ebin P G',
    'maintainer': 'Ebin P G',
    'category': 'Services',
    'license': 'OPL-1',
    'depends': [
        'lab_fieldwork',
        'lab_finance_ops',
        'petty_cash',
        # For the Collections screen on the Management menu. The dependency runs this
        # way round because this module is the consumer: lab_collections knows
        # nothing about the CEO board, and pointing it at one would couple the lab's
        # money screen to a dashboard it does not need. (client, 2026-08-27)
        'lab_collections',
        # Production Floor on the Management menu reuses this module's analysis rather
        # than forming a second opinion on the same numbers. (client, 2026-08-27)
        'lab_workcenter_scan',
    ],
    'data': [
        'security/lab_ceo_security.xml',
        # Management reads the countback's open items behind every money drill.
        # The row lives HERE, not in lab_collections: the consumer grants its
        # own group access to the provider's model, never the other way round.
        'security/ir.model.access.csv',
        'views/lab_ceo_dashboard_views.xml',
        'views/management_dashboards.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'lab_ceo_dashboard/static/src/js/inr.js',
            'lab_ceo_dashboard/static/src/js/ceo_dashboard.js',
            'lab_ceo_dashboard/static/src/xml/ceo_dashboard.xml',
            'lab_ceo_dashboard/static/src/scss/ceo_dashboard.scss',
            'lab_ceo_dashboard/static/src/js/mgmt_pulse.js',
            'lab_ceo_dashboard/static/src/xml/mgmt_pulse.xml',
            'lab_ceo_dashboard/static/src/scss/mgmt_pulse.scss',
            'lab_ceo_dashboard/static/src/xml/sales_day.xml',
            'lab_ceo_dashboard/static/src/scss/sales_day.scss',
            'lab_ceo_dashboard/static/src/js/field_command.js',
            'lab_ceo_dashboard/static/src/xml/field_command.xml',
            'lab_ceo_dashboard/static/src/scss/field_command.scss',
        ],
    },
    'installable': True,
    'application': True,
}
