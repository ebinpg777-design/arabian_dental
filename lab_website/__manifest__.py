# -*- coding: utf-8 -*-
{
    'name': 'Arabian Dental Lab Website',
    'version': '19.0.2.1.0',
    'summary': "Public website for the lab — restorations, the academy, and doctor login",
    'description': """
Arabian Dental Lab Website
==========================
The public site, built on Odoo's website builder so every page stays editable in the
front-end editor.

* **Home** — what the lab makes, how a case flows, why send it here, where to find us.
* **What We Make** — the restoration families, with live examples from the catalogue.
* **Academy** — ADL Dental Academy and the Crown Conference Hall.
* **About** — the lab, its hours and its address.

The restoration families and their counts are read from the lab's own product
catalogue at request time rather than typed into the page, so the site cannot drift
from what the lab actually makes. The five case stages shown to visitors are the same
five the doctor sees in the portal, so the promise made here and the status shown later
are one thing.

On install the hook names the website after the company, points "/" at the new
homepage, and fills in the lab's address, phone and Instagram wherever those fields
are still blank — never over something typed in by hand.
    """,
    'author': 'Ebin P G',
    'maintainer': 'Ebin P G',
    'category': 'Website',
    'license': 'LGPL-3',
    'depends': ['website', 'product'],
    'data': [
        'views/pages.xml',
        'views/footer.xml',
        'views/contact.xml',
        'views/header.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'lab_website/static/src/scss/arabiandentallab.scss',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
}
