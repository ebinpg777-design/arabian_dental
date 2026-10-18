# -*- coding: utf-8 -*-
{
    'name': 'Compressed Web Assets',
    'version': '20.0.1.0.0',
    'category': 'Hidden/Tools',
    'summary': "Serve Odoo's JavaScript and CSS bundles gzip-compressed: a first open downloads a fraction of the bundle",
    'description': """
Odoo serves its asset bundles exactly as stored - several megabytes of JavaScript and
CSS, uncompressed - and a browser downloads all of it the first time a device opens
Odoo, and again after every deploy (the bundle's address changes). On a phone that is
the "first time" wait. With no compressing proxy in front of the server, nothing
shrinks it.

This module compresses each bundle once (gzip), keeps the result in memory, and sends
it to every browser that accepts gzip - which is every browser. Nothing else changes:
same addresses, same caching, and a proxy that compresses on its own simply passes
the already-compressed answer through.
""",
    'author': 'Ebin P G',
    'license': 'LGPL-3',
    'depends': ['web'],
    'data': [],
    'installable': True,
    'auto_install': False,
}
