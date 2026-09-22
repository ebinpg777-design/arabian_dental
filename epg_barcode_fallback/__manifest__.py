# -*- coding: utf-8 -*-
{
    'name': 'Barcode Rendering Fallback',
    'version': '19.0.1.2.0',
    'summary': "Draw barcodes and QR codes without reportlab's cairo backend",
    'description': """
Barcode Rendering Fallback
==========================
reportlab 4.x rasterises through **rlPyCairo**, which needs system cairo headers. Where
those are absent every barcode in every report fails — one broken call takes down job
cards, product labels, delivery slips and stickers together, in core and custom modules
alike.

This renders them with `qrcode` and `python-barcode` through Pillow instead, which needs
nothing from the system. It is a fallback: when reportlab can rasterise, reportlab is
used, so installing cairo later restores the native output on its own.
    """,
    'author': 'Ebin P G',
    'maintainer': 'Ebin P G',
    'category': 'Technical',
    'license': 'LGPL-3',
    'depends': ['base'],
    # Distribution names, not module names: Odoo resolves these through
    # importlib.metadata, so `barcode` (the import) is `python-barcode` here.
    'external_dependencies': {'python': ['qrcode', 'python-barcode']},
    'data': [],
    'installable': True,
    'application': False,
    'auto_install': True,
}
