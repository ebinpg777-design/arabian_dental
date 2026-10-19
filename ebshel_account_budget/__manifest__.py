# Part of ebshel_account_budget. See LICENSE file for full copyright and licensing details.
{
    'name': 'Analytic Budgets',
    'version': '20.0.1.0.0',
    'category': 'Accounting/Accounting',
    'summary': 'Budgets per analytic account for Odoo Community: planned amounts per period, '
               'achieved from the analytic ledger, theoretical to date, revisions and budget analysis',
    'description': """
Analytic Budgets
================
A budget module for Odoo Community, in the spirit of the Enterprise Budget app:

* A budget covers a period and holds one line per analytic account (department,
  cost centre, project...) with the amount planned for it.
* **Achieved** is read live from the analytic items of posted journal entries.
* **Theoretical** is what should have been used by today, pro rata over the period.
* **Committed** is a hook other modules fill (purchase requests and orders with
  Purchase Budget Control), and **Available** is what is left.
* Draft, open, revised, done and cancelled budgets; a revision copies the budget
  and keeps the history.
* A Budgets tab on every analytic account and a Budget Analysis list across
  every budget line.
""",
    'author': 'Ebshel Technologies',
    'maintainer': 'Ebshel Technologies',
    'website': 'https://ebshel.com',
    'support': 'ebinpg777@gmail.com',
    'license': 'OPL-1',
    'price': 49.0,
    'currency': 'USD',
    'depends': ['account', 'analytic', 'mail'],
    'data': [
        'security/ir.access.csv',
        'views/budget_views.xml',
        'views/budget_line_views.xml',
        'views/account_analytic_account_views.xml',
        'views/menus.xml',
    ],
    'images': ['static/description/icon.png'],
    'application': False,
    'installable': True,
}
