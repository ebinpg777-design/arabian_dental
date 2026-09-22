# -*- coding: utf-8 -*-
{
    'name': 'Lab Finance Ops — Cheques, Approvals, Daybook & EOD',
    'version': '19.0.1.0.1',
    'category': 'Accounting/Accounting',
    'summary': 'Cheque register with invoice-wise allocation, multi-step payment approval, '
               'daybook, doctor-wise outstanding and a consolidated end-of-day report',
    'description': """
Lab Finance Operations (proposal gaps 9, 19, 20, 23, 26)
=============================================================
* **Cheque register with invoice-wise allocation (gap 9)** — Odoo treats a cheque as just
  another payment method and has no notion of a cheque in hand that has not cleared.
  This adds the register the lab actually keeps: cheque number, drawee bank, cheque
  date, received / deposited / cleared / bounced status, and an allocation grid saying
  which of the doctor's invoices the cheque settles. Clearing it posts a customer payment
  and reconciles exactly those invoices — no manual matching.
* **Payment approval workflow (gap 20)** — an optional to-approve gate on account.payment
  above a configurable threshold, held by a Payment Approver role, blocking posting
  until released.
* **Daybook (gap 19)** — the day-wise cash/bank/journal book Indian practice expects,
  as a wizard onto the native journal items with opening and closing balances.
* **Doctor-wise / invoice-wise outstanding (gap 23)** — a read-only analysis of open
  receivables per clinic and per invoice, with ageing buckets and days overdue.
* **End-of-day consolidated report (gap 26)** — one page covering the day's
  registrations, confirmations, dispatches, invoices, collections by mode, cheques
  received and field visits.

Discount management (gap 22) and bank/partner reconciliation (gap 21) need no
development — they are native Odoo — so this module only names them in the menu where
the client expects to find them.
""",
    'author': 'Ebin P G',
    'website': 'https://www.arabiandentallab.com',
    # Community only. `account_accountant` was declared but nothing from it was used —
    # no accountant model, view or action is referenced anywhere in this module.
    'depends': ['lab_access_control', 'sale_custom', 'account'],
    'data': [
        'security/lab_finance_ops_security.xml',
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'views/lab_cheque_views.xml',
        'views/lab_finance_lockdown_views.xml',
        'views/account_payment_views.xml',
        'views/lab_outstanding_report_views.xml',
        'wizard/lab_daybook_views.xml',
        'wizard/lab_eod_report_views.xml',
        'report/lab_eod_report_templates.xml',
        'report/report_actions.xml',
        'views/res_config_settings_views.xml',
        'views/lab_finance_ops_menus.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
