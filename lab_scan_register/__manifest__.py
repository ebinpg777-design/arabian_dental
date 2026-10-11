# -*- coding: utf-8 -*-
{
    'name': 'Scan Register',
    'version': '20.0.1.2.0',
    'category': 'Sales/Sales',
    'summary': 'Every scan a doctor sends - by WhatsApp, scanner cloud or e-mail - '
               'registered, confirmed, and turned into an order with its files',
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
* **confirmed**, then turned into a sales order in one click, with the patient, notes,
  due date and the scan files carried across and the order pointing back at the scan;
* watched: a download link about to lapse with no files in yet raises an activity, and
  the same patient arriving twice in a week is flagged.
""",
    'author': 'Ebin P G',
        'license': 'LGPL-3',
    'depends': ['sale_custom', 'mail'],
    'data': [
        'security/security.xml',
        'security/ir.access.csv',
        'data/sequence.xml',
        'data/channels.xml',
        'data/mail_alias.xml',
        'data/cron.xml',
        'views/channel_views.xml',
        'views/scan_views.xml',
        'views/paste_views.xml',
        'views/sale_order_views.xml',
        'views/res_partner_views.xml',
        'views/menus.xml',
    ],
    'installable': True,
    'application': True,
}
