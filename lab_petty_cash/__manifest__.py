# -*- coding: utf-8 -*-
{
    'name': 'Lab Petty Cash',
    'version': '20.0.1.0.0',
    'summary': "The lab's field work and management screens on Ebshel Petty Cash",
    'description': """
The lab's customisations of petty cash, in one place (client, 2026-10-22).

The lab's petty cash is ebshel_petty_cash. Field Work keeps the executives' field
cash itself (collected at clinics, counted back in by the office - a custody record,
never petty cash); this module joins the two:

* whoever keeps the petty cash box (an Approver or Administrator of Petty Cash)
  counts executives' cash handovers in, as the operational desk does;
* the Petty Cash app gets a Field Cash menu: cash with the executives, the
  handovers, and receiving one by its code;
* the management board's cash reading adds the petty cash funds' balances.

It replaces the old petty_cash module, which Field Work and the management board
were built on.
""",
    'category': 'Accounting',
    'author': 'Ebin P G',
    'license': 'OPL-1',
    'depends': ['lab_fieldwork', 'lab_ceo_dashboard', 'ebshel_petty_cash'],
    'data': [
        'security/ir.access.csv',
        'views/menus.xml',
    ],
    'installable': True,
    'auto_install': True,
    'application': False,
}
