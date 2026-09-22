# -*- coding: utf-8 -*-
{
    'name': 'Lab Rework',
    'version': '19.0.2.1.0',
    'summary': "Reworks as sale orders: tick Rework, give a reason, optionally point at the original job",
    'author': 'Ebin P G',
    'maintainer': 'Ebin P G',
    'category': 'Manufacturing',
    'license': 'LGPL-3',
    'depends': ['lab_fieldwork', 'lab_order_control', 'lab_delivery', 'account'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_sequence.xml',
        'data/rework_reason_data.xml',
        'views/rework_views.xml',
        'views/menus.xml',
        'data/filter_tiles.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'lab_rework/static/src/scss/rework_ribbon.scss',
        ],
    },
    'installable': True,
    'application': False,
}
