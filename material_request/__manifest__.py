# -*- coding: utf-8 -*-
{
    'name': 'Material Requests',
    'version': '19.0.2.0.0',
    'summary': 'Department stores ask the main store for consumables; the store approves, '
               'picks and delivers, and everyone can see what is short and what has arrived.',
    'description': """
Material Requests
=================
A department (ceramic, acrylic, CAD/CAM, orthodontic, marketing, office...) raises a
request for consumables from the main store. The storekeeper sees at once what is on
hand and what is short, approves the quantities that can go, and the internal transfer
is created and followed through to delivery.

* Kanban board by stage with delivery progress, deadlines and shortage flags
* Availability at the source store on every line, before anything is approved
* Approve less than asked, reject with a reason, reorder a past request in one click
* Consumption analysis by department, product and month
* Printable requisition slip
""",
    'author': 'Arabian Dental Lab',
    'category': 'Inventory/Inventory',
    'license': 'LGPL-3',
    'depends': ['stock', 'mail'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/data.xml',
        'wizard/reject_wizard_views.xml',
        'views/material_request_views.xml',
        'views/res_config_settings_views.xml',
        'report/material_request_report.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'material_request/static/src/scss/material_request.scss',
        ],
    },
    'installable': True,
    'application': True,
}
