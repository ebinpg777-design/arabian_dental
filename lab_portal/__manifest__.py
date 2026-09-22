# -*- coding: utf-8 -*-
{
    'name': 'Lab Customer Portal',
    'version': '19.0.1.1.0',
    'summary': 'Doctors track their cases and see where each one is in the lab',
    'description': """
Customer Portal
===============
A doctor logs in and sees their cases the way they think about them — by patient — and
can tell at a glance where each one is without ringing the lab.

* **My Cases** — every case, its patient, and one honest stage: Registered, In the lab,
  Ready, On its way, Delivered.
* **Case detail** — the appliances with their arch and colour, a progress track, and the
  operation the job is actually on.
* Written mobile-first: a doctor checks this between patients, on a phone, in a surgery.
""",
    'author': 'Ebin P G',
    'maintainer': 'Ebin P G',
    'category': 'Website/Portal',
    'license': 'OPL-1',
    # lab_fieldwork: the case-request ACLs grant its Field Manager group. It does
    # not depend on this module (directly or through its own dependencies), so no cycle.
    'depends': ['portal', 'sale_custom', 'mrp', 'lab_order_control',
                'dynamic_filter_tiles', 'lab_fieldwork'],
    'data': [
        'security/ir.model.access.csv',
        'security/case_request_security.xml',
        'data/ir_sequence.xml',
        'views/case_request_views.xml',
        'views/portal_templates.xml',
        'data/filter_tiles.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'lab_portal/static/src/scss/portal.scss',
            'lab_portal/static/src/js/portal_filter.js',
        ],
    },
    'installable': True,
    'application': False,
}
