# -*- coding: utf-8 -*-
{
    'name': 'Direct & Contra Payments',
    'summary': 'Post a payment straight against an Expense/Income account, or as a '
               'cash/bank-to-cash/bank contra transfer - no receivable/payable needed',
    'description': """
Direct & Contra Payments
=========================
Two payment shapes accounting already understands but the Payments screen never
offered directly:

* **Direct** - a payment posted straight against an Expense or Income account
  (petty cash, bank charges, interest received...) instead of forcing it through a
  customer/vendor's receivable or payable account.
* **Contra** - a transfer between two of the company's own Bank/Cash accounts.
  Posting one leg raises its mirror on the other journal automatically, both
  through the company's Internal Transfer account, reconciled against each other
  so that account nets to zero.

Same account.payment model, same posting flow, same journal entries - just a third
way to say who the other side of the money is.
""",
    'version': '19.0.1.0.0',
    'category': 'Accounting/Accounting',
    'author': 'Ebin P G',
    'maintainer': 'Ebin P G',
    'website': 'https://www.arabiandentallab.com',
    'license': 'LGPL-3',
    'depends': ['account'],
    'data': [
        'views/account_payment_views.xml',
        'views/account_payment_menus.xml',
    ],
    'installable': True,
    'application': False,
}
