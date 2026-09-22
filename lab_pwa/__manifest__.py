# -*- coding: utf-8 -*-
{
    'name': 'Lab Website App (PWA)',
    'version': '19.0.1.2.0',
    'summary': "Install the public site on a phone like an app",
    'description': """
Lab Website App
=================
Odoo already ships a Progressive Web App — for the **back office**. Its manifest is
scoped to ``/odoo``, so the people it makes an app for are the staff, and the doctors
who actually live on the public site get a browser tab with an address bar.

This module gives the public site its own manifest, its own service worker at the root
scope, and an install prompt, so a doctor can put Arabian Dental Lab on their home screen and
open straight into their case tracking.

* **Configurable** from Website settings: the app's name, the screen it opens on, and the
  colour of the phone's status bar while it is open.
* **The icon follows the site.** It is generated from the website's own favicon or the
  company logo at the sizes a phone asks for, rather than shipped as a picture that goes
  stale the day the branding changes.
* **A polite install prompt** that appears only where installing is possible, and stays
  dismissed once dismissed. iPhone users, whose browser refuses to offer this, are told
  the two taps that do it instead.
* **Offline** shows the site's own page saying so, not the browser's dinosaur.
""",
    'author': 'Ebin P G',
    'maintainer': 'Ebin P G',
    'category': 'Website',
    'license': 'OPL-1',
    'depends': ['website'],
    'data': [
        'views/layout.xml',
        'views/website_settings_views.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'lab_pwa/static/src/js/pwa.js',
            'lab_pwa/static/src/scss/pwa.scss',
        ],
    },
    'post_init_hook': 'set_default_icon',
    'installable': True,
    'application': False,
}
