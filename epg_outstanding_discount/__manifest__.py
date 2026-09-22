# -*- coding: utf-8 -*-
{
    'name': 'Outstanding Discount',
    'summary': 'Give a customer a discount on their total outstanding, as one '
               'posted journal entry allocated oldest-invoice-first',
    'description': """
The accountant picks a clinic, sees its outstanding, and grants a discount three ways:
a percentage, a fixed amount, or "settle to" a round figure. Posting creates one
journal entry (discount expense against receivables) and reconciles it against the
oldest open invoices, so the partner ledger and every statement immediately agree.

The outstanding itself is worked out either As on Date (the running balance, full
history) or over a Period (only what was billed inside a start/end window is kept
open) - and each customer can carry their own standard discount percentage, offered
as the starting point the moment they are picked.
""",
    'version': '19.0.1.3.0',
    'category': 'Accounting',
    'author': 'EPG',
    'license': 'LGPL-3',
    'depends': ['account'],
    'data': [
        'security/discount_security.xml',
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'views/discount_views.xml',
        'views/res_partner_views.xml',
    ],
    'installable': True,
    'application': False,
}
