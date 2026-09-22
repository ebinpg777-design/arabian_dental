# -*- coding: utf-8 -*-
{
    'name': 'Product Label Builder Pro',
    'version': '19.0.1.0.0',
    'summary': "Design and print product, lot and address labels in PDF or ZPL",
    'description': """
Product Label Builder Pro
=========================
Build label templates through the Odoo UI - no code, no report XML - and print them
as PDF or send them to a Zebra printer as ZPL.

Designer
--------
* Free positioning of every element on the label (drag, resize, or type exact mm).
* Element types: static text, dynamic field, text template with ``{{ placeholders }}``,
  barcode, QR code, vCard QR, image, product/company logo, price, promotional price,
  price difference, price per unit of measure, pricelist rule details, product
  attributes, contact address, date, box, line and ellipse.
* Full typography and box styling: font family and size, weight, style, alignment,
  line height, letter spacing, case, colour, background, border, radius, padding,
  rotation and opacity.
* Barcodes with transparent or inverted (white-on-black) backgrounds.
* Static or dynamic label background images.

Output
------
* PDF, on label sheets (grid) or on roll/continuous stock, with an auto-maintained
  paper format per template.
* ZPL II for Zebra printers, with configurable print density, rotation and character
  encoding, set globally and overridable per template.
* Direct print through the IoT Box or any third-party print service, download, or
  in-browser preview.

Templates
---------
* Nine ready-made templates - seven for products, two for addresses.
* Duplicate, archive, import and export templates as XML to move them between
  databases.
* Restrict templates per user and set a default template per user.

Print wizard
------------
* Print from a product list selection, from a product form, from a contact, or from
  a lot/serial number.
* Number of copies, per-record quantities, and a *skip* setting that leaves N label
  slots empty at the start of the first sheet.
* Regular and promotional pricelist selection for price elements.
* Optionally replaces the standard Odoo product label wizard.
""",
    'author': 'Ebin P G',
    'maintainer': 'Ebin P G',
    'website': 'https://github.com/ebinpg',
    'category': 'Inventory/Inventory',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'mail',
        'product',
        'stock',
        'web',
    ],
    'external_dependencies': {'python': ['Pillow']},
    'data': [
        'security/label_security.xml',
        'security/ir.model.access.csv',
        'data/paperformat_data.xml',
        'report/label_reports.xml',
        'report/label_report_templates.xml',
        'views/label_element_views.xml',
        'views/label_stock_views.xml',
        'views/label_template_views.xml',
        'views/label_print_log_views.xml',
        'wizard/label_print_views.xml',
        'wizard/label_import_views.xml',
        'wizard/label_assistant_views.xml',
        'views/res_config_settings_views.xml',
        'views/res_users_views.xml',
        'views/label_menus.xml',
        'data/label_stock_data.xml',
        'data/label_template_product_data.xml',
        'data/label_template_partner_data.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'epg_product_label/static/src/scss/label_designer.scss',
            'epg_product_label/static/src/js/label_designer.js',
            'epg_product_label/static/src/js/label_widgets.js',
            'epg_product_label/static/src/xml/label_designer.xml',
            'epg_product_label/static/src/xml/label_widgets.xml',
        ],
        'web.report_assets_common': [
            'epg_product_label/static/src/scss/label_report.scss',
        ],
        'web.assets_tests': [
            'epg_product_label/static/tests/tours/**/*',
        ],
    },
    'installable': True,
    'application': True,
    'auto_install': False,
}
