# -*- coding: utf-8 -*-
{
    'name': 'Sticker & Label Printing',
    'version': '19.0.1.5.1',
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
* **Label formats** you configure once: 100 x 60 mm roll (default), 50 x 30 mm roll, A4
  sheets of N x M labels - each with its own margins; page breaks are handled for you.
* **Copies**, sender block, phone / route / patient / lines toggles.
* One click from the sale order, from a delivery transfer, or in batch from the order list.
""",
    'author': 'Ebin P G',
    'maintainer': 'Ebin P G',
    'website': 'https://www.arabiandentallab.com',
    'license': 'LGPL-3',
    'depends': ['sale_stock', 'sales_team'],
    'data': [
        'security/ir.model.access.csv',
        'data/sticker_format_data.xml',
        'report/sticker_report_templates.xml',
        'report/report_actions.xml',
        'wizard/sticker_wizard_views.xml',
        'views/sticker_format_views.xml',
        'views/sale_order_views.xml',
        'views/stock_picking_views.xml',
        'views/crm_team_views.xml',
    ],
    'installable': True,
    'application': False,
}
