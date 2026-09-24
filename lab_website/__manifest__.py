# -*- coding: utf-8 -*-
{
    'name': 'Arabian Dental Lab Website',
    'version': '19.0.3.4.0',
    'summary': "The lab's public site — services, technology, academy, gallery, doctor login",
    'description': """
Arabian Dental Lab Website
==========================
The public site, built on Odoo's website builder so every page stays editable in the
front-end editor.

* **Home** — a photo slider of the lab's own work, what it makes, the numbers, why
  clinics stay, the five case stages, a gallery rail, the academy, reviews and a map.
* **Services** — an overview and a page per line: crown & bridge, implant prosthetics,
  veneers & aesthetics, removable prosthetics, digital dentistry, orthodontic &
  speciality — each with what the lab offers, what to send, and live examples from
  the catalogue.
* **Technology**, **Gallery** (with lightbox and filters), **Academy** (programmes,
  the Crown Conference Hall, enquiry form), **About**, **Send a case** (three ways, a
  checklist, a call-back form), **Careers**, **FAQ**, and the contact page with a map.

Enquiry forms use Odoo's own website form mechanics and land in the lab's inbox; no
extra app is needed. The lab's facts live in one place (`models/website.py`), and the
service counts are read from the product catalogue at request time.

On install the hook names the website after the company, points "/" at the new
homepage, and fills in the lab's address, phone, email and Instagram wherever those
fields are still blank — never over something typed in by hand.
    """,
    'author': 'Ebin P G',
    'maintainer': 'Ebin P G',
    'category': 'Website',
    'license': 'LGPL-3',
    'depends': ['website', 'product'],
    'data': [
        'views/layout.xml',
        'views/snippets.xml',
        'views/home.xml',
        'views/services.xml',
        'views/company.xml',
        'views/academy.xml',
        'views/contact.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'lab_website/static/src/scss/site.scss',
            'lab_website/static/src/js/site.js',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
}
