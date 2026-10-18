# -*- coding: utf-8 -*-
{
    'name': 'Scan Register',
    'version': '20.0.2.1.0',
    'category': 'Sales/Sales',
    'summary': 'Every scan a doctor sends - by WhatsApp, scanner cloud or e-mail - '
               'registered, confirmed, printed plate by plate as print orders, and turned '
               'into an order with its files',
    'description': """
Scan Register
=============
Scans reach the lab a dozen ways: an STL dropped in a clinic's WhatsApp group with
"Patient name - X / Delivery - date" typed above it, a WeTransfer link that dies in
three days, a case notification from the scanner's own cloud (Allied Star, Freqty,
DEXIS), a photo of a QR code. None of that was anywhere until somebody typed an order.

Here each one becomes a **scan**:

* registered by hand from WhatsApp - paste the message and the patient, appliance and
  delivery date fill themselves; pick the group and the clinic fills itself;
* registered **by itself from e-mail**: the scanner-cloud notifications are read for the
  patient, doctor, clinic, case number, download link and when that link expires;
* **confirmed** and sent to the printing room, where patients from several registers
  go on one plate as a **print order** - PRINT/2026/0012 on machine 3DP-02: the files
  zipped per patient for slicing, the ChituBox file's own figures (layers, time, resin,
  plate picture) read from the file the plate keeps, the clock and the expected off time, a plate
  ticket with a QR code for the wash basket, and at Plate Off each model marked printed
  or failed with its reason - the failed ones re-plated in one step;
* each 3D printer a record with its own numbered sticker, so every plate is some
  machine's: one plate at a time, a queue of plates to start next, how long each ran,
  which models failed and why;
* then turned into a sales order in one click, with the patient, notes, due date and
  the scan files carried across, the order pointing back at the scan and at the plate
  and machine that printed it;
* watched: a download link about to lapse with no files in yet raises an activity, a
  plate past its expected off time raises one for the machine's in-charge, and the
  same patient arriving twice in a week is flagged.

The machine stickers and plate tickets need ``qrcode`` and ``python-barcode`` in the
server's Python environment (the server's venv must have both; the imports are guarded,
so without them the labels print without their codes).
""",
    'author': 'Ebin P G',
    'license': 'LGPL-3',
    'depends': ['sale_custom', 'mail', 'ebshel_dynamic_filter'],
    'data': [
        'security/security.xml',
        'security/ir.access.csv',
        'data/sequence.xml',
        'data/printer_sequence.xml',
        'data/print_order_sequence.xml',
        'data/channels.xml',
        'data/mail_alias.xml',
        'data/cron.xml',
        'data/message_templates.xml',
        'views/channel_views.xml',
        'views/printer_views.xml',
        'views/print_order_views.xml',
        'report/plate_ticket.xml',
        'views/print_wizard_views.xml',
        'report/printer_sticker.xml',
        'views/scan_views.xml',
        'views/link_orders_views.xml',
        'views/paste_views.xml',
        'views/whatsapp_views.xml',
        'views/sale_order_views.xml',
        'views/res_partner_views.xml',
        'views/fetchmail_views.xml',
        'views/menus.xml',
        'views/settings_views.xml',
        'data/filter_tiles.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'lab_scan_register/static/src/dashboard/scan_dashboard.js',
            'lab_scan_register/static/src/dashboard/scan_dashboard.xml',
            'lab_scan_register/static/src/dashboard/scan_dashboard.scss',
            'lab_scan_register/static/src/whatsapp/whatsapp_open.js',
            'lab_scan_register/static/src/timer/scan_timer.js',
            'lab_scan_register/static/src/timer/scan_timer.xml',
            'lab_scan_register/static/src/timer/scan_timer.scss',
            'lab_scan_register/static/src/slice/slice_upload_field.js',
            'lab_scan_register/static/src/slice/slice_upload_field.xml',
        ],
    },
    'installable': True,
    'application': True,
}
