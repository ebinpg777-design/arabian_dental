# -*- coding: utf-8 -*-
{
    'name': 'Lab Track a Work',
    'version': '19.0.1.3.0',
    'summary': "Type a case number, see everything about it on one screen (R18)",
    'description': """
Track a Work
============
One search box and one screen. Type a sale order, delivery, rework, invoice or patient
name and get the whole journey at once — verification, doctor calls, every
manufacturing order and its work orders, delivery with its outcome, invoices, reworks.

Built because the alternative is six screens and a dozen clicks: a manager on the phone
to a doctor cannot navigate a menu tree while being asked "where is my case".

No new application — it lives under Management.
    """,
    'author': 'Ebin P G',
    'maintainer': 'Ebin P G',
    'category': 'Sales',
    'license': 'LGPL-3',
    # lab_rework is the most downstream module and pulls in delivery, order control,
    # fieldwork and account with it — everything this screen has to read.
    # lab_workcenter_scan for the read-only Track Order on the Station Board.
    'depends': ['lab_rework', 'lab_workcenter_scan'],
    'data': [
        'security/ir.model.access.csv',
        'views/track_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'lab_track/static/src/js/track.js',
            'lab_track/static/src/xml/track.xml',
            'lab_track/static/src/scss/track.scss',
            'lab_track/static/src/xml/station_track.xml',
            'lab_track/static/src/scss/station_track.scss',
        ],
    },
    'installable': True,
    'application': True,
}
