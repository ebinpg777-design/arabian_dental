# -*- coding: utf-8 -*-
{
    'name': 'Dynamic Financial Reports',
    'version': '19.0.1.7.0',
    'category': 'Accounting/Accounting',
    'summary': 'Balance sheet, profit & loss, cash flow, ledgers, ageing, tax and your own '
               'reports - folding, drilling, comparing, trending, annotated, exported and '
               'emailed on a schedule',
    'description': """
Dynamic Financial Reports
=========================
Every financial statement the accounts desk needs, computed straight from the
ledger by SQL and read on one screen:

* Balance Sheet, Profit & Loss, Cash Flow (indirect), Executive Summary
* General Ledger, Trial Balance, Journal Audit, Day Book, Cash & Bank Book
* Partner Ledger, Aged Receivable, Aged Payable, Customer and Vendor Statements
* Tax Report by tax and by grid, Analytic Report by plan

Any report folds and unfolds, compares periods (previous period, same period
last year, N periods), shows growth and a twelve-month trend on every line,
drills into the journal items behind a figure and *explains* a figure by
account, partner and month. Notes stay with the line they annotate and print
with the report. Views are saved and shared, exported to Excel and PDF, and
land in inboxes on a schedule.

A report designer builds new statements from account prefixes, account types,
tags or formulas over other lines - no code.
""",
    'author': 'Ebshel Technologies',
    'website': 'https://ebshel.com',
    'support': 'ebinpg777@gmail.com',
    'license': 'OPL-1',
    'price': 199.00,
    'currency': 'USD',
    'depends': ['account'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/paperformat.xml',
        'data/reports.xml',
        'data/report_lines.xml',
        'data/actions.xml',
        'data/cron.xml',
        'data/mail_template.xml',
        'views/report_views.xml',
        'views/extras_views.xml',
        'views/report_pdf.xml',
        'views/settings_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'ebshel_account_reports/static/src/viewer/viewer.scss',
            'ebshel_account_reports/static/src/viewer/viewer.js',
            'ebshel_account_reports/static/src/viewer/viewer.xml',
        ],
    },
    'application': True,
    'installable': True,
}
