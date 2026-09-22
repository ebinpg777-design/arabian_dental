# -*- coding: utf-8 -*-
{
    'name': 'Lab Management Dashboards',
    'version': '19.0.1.0.0',
    'summary': "Ready-made Spectrum dashboards for the lab, under the Management menu",
    'description': """
Lab Management Dashboards
===========================
Four boards under **Management**, built on Spectrum Dashboard and pointed at the lab's
own records — sales and cases, the field force, the production floor, and the money.

They are seeded, not hard-coded: every board is an ordinary `spectrum.dashboard.config`
record, so anyone with the Spectrum Administrator role can add a tile, change a chart or
re-point a measure from the builder without touching this module. Re-installing or
upgrading will not overwrite edits — the seed only creates what is missing.
    """,
    'author': 'Ebin P G',
    'maintainer': 'Ebin P G',
    'category': 'Productivity',
    'license': 'LGPL-3',
    'depends': ['lab_ceo_dashboard', 'spectrum_dashboard'],
    'data': [
        'security/groups.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
    'auto_install': False,
}
