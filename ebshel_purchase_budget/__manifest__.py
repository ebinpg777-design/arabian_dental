# Part of ebshel_purchase_budget. See LICENSE file for full copyright and licensing details.
{
    'name': 'Purchase Budget Control',
    'version': '20.0.1.0.0',
    'category': 'Purchase',
    'summary': 'Check purchase requests and purchase orders against analytic budgets, and show '
               'what open requests and orders reserve on every budget line',
    'description': """
Purchase Budget Control
=======================
Connects Purchase Request Management with Analytic Budgets (both Odoo Community):

* The department budget card on a purchase request reads the open analytic budget
  of its department: budgeted, achieved (posted bills), committed (open requests
  and orders) and available, before and after the request.
* Over-budget requests are blocked or sent for budget approval, and over-budget
  purchase orders are blocked, following the purchase request settings.
* Every budget line shows **Purchased** (open purchase requests), **Invoiced**
  (open, not yet billed purchase orders) and **Committed** (their sum), and its
  Available amount deducts them.

Installed automatically when both modules are installed.
""",
    'author': 'Ebshel Technologies',
    'maintainer': 'Ebshel Technologies',
    'website': 'https://ebshel.com',
    'support': 'ebinpg777@gmail.com',
    'license': 'OPL-1',
    'price': 0.0,
    'currency': 'USD',
    'depends': ['ebshel_account_budget', 'ebshel_purchase_request'],
    'data': [
        'views/budget_views.xml',
    ],
    'images': ['static/description/icon.png'],
    'installable': True,
    'auto_install': True,
}
