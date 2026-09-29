# -*- coding: utf-8 -*-
{
    'name': 'Advanced Accounting',
    'summary': 'Collections desk with follow-up levels, budgets with monthly phasing, revenue and expense deferrals, '
               'a matching desk, a month-end close cockpit, a 13-week cash forecast and a ledger health scanner',
    'description': """
Advanced Accounting
===================
Everything the accounting team does after the entries are booked, on one set of screens:

* **Collections desk** - follow-up levels, promise-to-pay, call logs, automatic reminders with the statement
  and the invoices attached, average days to pay per customer.
* **Budgets** - per account and analytic account, phased by month, with actual, committed and theoretical
  figures, a live budget board and a Budget vs Actual report.
* **Deferrals** - revenue and expense recognised over the service period straight from the invoice line.
* **Matching desk** - open items paired by amount, reference or sum, with a confidence score, applied in one click;
  bank lines matched to their invoices.
* **Close cockpit** - a month-end checklist with live counts behind every task and the lock date set on close.
* **Cash forecast** - thirteen weeks ahead from open items, planned items and history, with what-if sliders.
* **Ledger health** - a scanner that finds duplicate bills, sequence gaps, unusual amounts, stale drafts and more.
* **Finance cockpit** - the profit and loss as a waterfall, the balance sheet as two stacks, twelve months of trend,
  banker's ratios and the work waiting on the team.
* **Bank reconciliation** - paste the bank statement: rows the books already hold are ticked, the rest are matched
  to invoices, written off, or taught to rules; a reconciliation statement at any date, in PDF.
* **Entry studio** - journal entries typed like a spreadsheet, pasted from Excel, balanced in one key, from
  templates that split an amount by percentage; bulk analytic redistribution of journal items.
""",
    'version': '19.0.1.1.0',
    'category': 'Accounting/Accounting',
    'author': 'Ebshel Technologies',
    'website': 'https://ebshel.com',
    'support': 'ebinpg777@gmail.com',
    'license': 'OPL-1',
    'price': 149.0,
    'currency': 'USD',
    'depends': ['account', 'mail', 'analytic', 'ebshel_account_reports', 'ebshel_account_assets'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/sequence.xml',
        'data/followup_data.xml',
        'data/close_tasks.xml',
        'data/cron.xml',
        'data/reports.xml',
        'data/actions.xml',
        'wizard/followup_remind_views.xml',
        'views/followup_views.xml',
        'views/res_partner_views.xml',
        'views/budget_views.xml',
        'views/deferral_views.xml',
        'views/account_move_views.xml',
        'views/close_views.xml',
        'views/cash_views.xml',
        'views/check_views.xml',
        'views/res_config_views.xml',
        'views/bank_views.xml',
        'views/entry_views.xml',
        'report/followup_letter.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'ebshel_account_advanced/static/src/common/common.scss',
            'ebshel_account_advanced/static/src/common/*.js',
            'ebshel_account_advanced/static/src/common/*.xml',
            'ebshel_account_advanced/static/src/followup/*',
            'ebshel_account_advanced/static/src/budget/*',
            'ebshel_account_advanced/static/src/matching/*',
            'ebshel_account_advanced/static/src/close/*',
            'ebshel_account_advanced/static/src/cash/*',
            'ebshel_account_advanced/static/src/health/*',
            'ebshel_account_advanced/static/src/cockpit/*',
            'ebshel_account_advanced/static/src/bank/*',
            'ebshel_account_advanced/static/src/studio/*',
        ],
    },
    'application': True,
    'installable': True,
}
