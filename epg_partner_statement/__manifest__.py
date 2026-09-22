# -*- coding: utf-8 -*-
{
    'name': 'Partner Statement of Account',
    'version': '19.0.1.9.0',
    'category': 'Accounting/Accounting',
    'summary': 'Customer / vendor statements: ledger with running balance, open items, ageing, '
               'PDF & Excel, e-mail sending, monthly auto-send, portal self-service',
    'description': """
Partner Statement of Account
============================
The account statement a business actually sends: one document per partner (customer,
vendor or both), per currency, that a clinic or supplier can reconcile against its own
books - and every way of getting it to them.

* **Ledger** - opening balance, every posted receivable / payable entry in the period
  with reference, journal, description, due date, debit, credit and running balance,
  closing amount due. Multi-currency: one block per currency.
* **Open items** - only what is still unpaid, with days overdue and the amount left.
* **Ageing summary** - Not due / 1-30 / 31-60 / 61-90 / 90+ days, per currency.
* **Outputs** - PDF (branded letterhead, bank details, payment QR, signature),
  **Excel** workbook (one sheet per partner), **e-mail** with the PDF attached and the
  statement logged in the partner's chatter.
* **Auto-send** - opt a partner in and a monthly job e-mails last month's statement.
* **Portal** - customers download their own statement from *My Account*.
* Period presets (this / last month, quarter, financial year, last 12 months, custom),
  batch from the partner list, one click from the partner form.

Plays well with lab / GST specifics when present: patient names on invoice lines,
GSTIN / PAN / DCI numbers, company payment QR and authorised-signatory signature.
""",
    'author': 'Ebin P G',
    'maintainer': 'Ebin P G',
    'website': 'https://www.arabiandentallab.com',
    'license': 'LGPL-3',
    'depends': ['account', 'mail', 'portal'],
    'data': [
        'security/statement_security.xml',
        'security/ir.model.access.csv',
        'data/mail_template_data.xml',
        'data/ir_cron_data.xml',
        'report/statement_report_templates.xml',
        'report/report_actions.xml',
        'wizard/statement_wizard_views.xml',
        'views/res_partner_views.xml',
        'views/res_config_settings_views.xml',
        'views/portal_templates.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.report_assets_common': [
            'epg_partner_statement/static/src/scss/statement_report.scss',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
}
