# -*- coding: utf-8 -*-
{
    'name': 'Sticker & Label Printing',
    'version': '20.0.2.2.0',
    'category': 'Sales',
    'summary': 'Shipping / destination stickers for sale orders: customer, order or route labels, '
               'live preview, barcode or QR, roll and A4-sheet formats, copies',
    'description': """
Sticker & Label Printing
========================
The destination sticker that goes on every parcel - printed the way the counter needs it,
not the way a report happens to come out.

* **Three groupings** - one sticker per *customer* (all their orders on one label with a
  combined barcode), per *order* (with the work lines, U/L and patient) or per *sales
  route* (route address, for the courier bag).
* **Live preview** in the wizard: what you see is the first sticker, at label size,
  updating as you tick options.
* **Barcode or QR** of the order references (or none) - rendered inline, so it prints on
  any server without a network round-trip.
* **Label formats** you configure once: 100 x 60 mm roll (default), 65 x 35 and 50 x 30
  mm rolls, A4 sheets of N x M labels (3 x 8 of 65 x 35 mm for an ordinary printer) -
  each with its own margins and grid offsets; page breaks are handled for you.
* **The sheet in the tray**: for sheet formats the wizard draws the sheet, you mark the
  labels already peeled off (click, or drag over a run of them), and the stickers are
  laid on the free labels in order - numbered, so you see where each lands. What a print
  takes is remembered, so the next run starts from the sheet as it really is.
* **Copies**, sender block, phone / route / patient / lines toggles.
* One click from the sale order, from a delivery transfer, or in batch from the order list.
""",
    'author': 'Ebin P G',
    'maintainer': 'Ebin P G',
    'website': 'https://www.arabiandentallab.com',
    'license': 'LGPL-3',
    'depends': ['sale_stock', 'sales_team'],
    'data': [
        'data/sticker_format_data.xml',
        'report/sticker_report_templates.xml',
        'report/report_actions.xml',
        'wizard/sticker_wizard_views.xml',
        'views/sticker_format_views.xml',
        'views/sale_order_views.xml',
        'views/stock_picking_views.xml',
        'views/crm_team_views.xml',
        'security/ir.access.csv',
    ],
    'assets': {
        'web.assets_backend': [
            'epg_sticker_print/static/src/scss/sheet_grid.scss',
            'epg_sticker_print/static/src/js/sheet_grid.js',
            'epg_sticker_print/static/src/xml/sheet_grid.xml',
        ],
    },
    'installable': True,
    'application': False,
}
