# -*- coding: utf-8 -*-
{
    'name': 'Lab Doctor Call Desk',
    'version': '19.0.1.0.0',
    'category': 'Sales/Sales',
    'summary': 'A case that needs the doctor raises a ticket; a desk of its own takes it, '
               'calls, records the answer - and the order waits until it is closed',
    'description': """
Doctor Call Desk
================
A case slip marked "call the doctor" used to become a flag on the order and a note in
somebody's head. Here it becomes a **ticket**:

* raised by itself when an order needs the doctor, with the question, the clinic, the
  patient and how soon an answer is needed;
* taken at the **desk** by the people whose job it is (their own role), one ticket or
  every open question for the same doctor in a single call;
* every attempt is logged - answered, no answer, busy, call back at four - and a ticket
  waiting for a callback comes back to the top when its time arrives;
* the doctor's answer closes it, is written on the order, reaches the job card and is
  sent back to whoever raised it;
* an order with an open ticket **cannot be confirmed**.

Switched on per company. Off, nothing changes: no ticket is raised and no order waits.
""",
    'author': 'Ebin P G',
    'website': 'https://www.arabiandentallab.com',
    'depends': ['lab_order_control', 'lab_fieldwork', 'mail'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/sequence.xml',
        'data/cron.xml',
        'views/ticket_views.xml',
        'views/sale_order_views.xml',
        'views/case_views.xml',
        'views/res_config_settings_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'lab_doctor_desk/static/src/desk/desk.scss',
            'lab_doctor_desk/static/src/desk/desk.js',
            'lab_doctor_desk/static/src/desk/desk.xml',
        ],
    },
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
