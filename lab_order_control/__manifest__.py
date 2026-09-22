# -*- coding: utf-8 -*-
{
    'name': 'Lab Order Control — Maker/Checker & Credit Limit',
    'version': '19.0.1.9.0',
    'category': 'Sales/Sales',
    'summary': 'Second-person verification of registered cases before processing, plus '
               'enforced customer credit limits',
    'description': """
Lab Order Control (proposal gaps 16, 17, 18)
================================================
Two controls the Odoo 19 scope did not name:

* **Maker-checker order verification (gaps 16 & 17)** — an order registered at the
  counter or submitted from the field goes to *To Verify*; a second person holding the
  Order Checker role verifies it (or sends it back with a reason) before it can be
  confirmed into production. Same-person verification is refused by default, which is
  the entire point of a maker-checker control.
* **Credit limit enforcement (gap 18)** — v19 already stores ``credit_limit`` on the
  partner and shows a soft warning. This adds the missing half: a per-clinic policy of
  *ignore / warn / block*, live exposure (posted receivables + orders not yet invoiced)
  and a hard stop on confirmation, releasable only by someone holding the Credit
  Override role, with the release written to the chatter.

Urgent-order flagging (gap 6) needs no development — ``sale.order.priority`` in
``sale_custom`` already carries Low/Normal/Urgent and drives the emergency surcharge.
This module only surfaces it on the verification screens.
""",
    'author': 'Ebin P G',
    'website': 'https://www.arabiandentallab.com',
    'depends': ['lab_access_control', 'sale_custom', 'account',
                'dynamic_filter_tiles'],
    'data': [
        'security/lab_order_control_security.xml',
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'views/doctor_call_views.xml',
        'views/hold_views.xml',
        'views/sale_order_views.xml',
        'views/urgent_views.xml',
        'data/filter_tiles.xml',
        'views/res_partner_views.xml',
        'views/res_config_settings_views.xml',
        'views/lab_order_control_menus.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
