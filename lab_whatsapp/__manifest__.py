# -*- coding: utf-8 -*-
{
    'name': 'Lab WhatsApp Notifications',
    'version': '19.0.2.18.0',
    'category': 'Marketing/WhatsApp',
    'summary': "Dental-lab WhatsApp notifications - case registered, dispatched, invoice, "
               "payment, reminders - sent free from the lab's own WhatsApp or by Cloud API",
    'description': """
Lab WhatsApp Notifications
============================
Built on `epg_whatsapp`, the community Meta Cloud API engine — senders, templates, the
message log, the HMAC-verified webhook, delivery and read receipts, the 24-hour
customer-service window and STOP opt-out all live there rather than being reimplemented
here. (This module previously sat on Odoo's Enterprise `whatsapp` addons; it no longer
depends on anything from Enterprise.)

Sent free through the lab's own WhatsApp (the WhatsApp Desk) or automatically through
the paid Cloud API, whichever the sender is set to. Dispatch notices carry "Received /
Something is missing" and feedback requests carry star ratings as quick replies.

This module adds only the dental-lab-specific glue on top:

* A green WhatsApp button on every order, invoice, delivery and payment that opens
  the composer with the record's own moment filled in (invoice, reminder once
  overdue, case registered, dispatched, payment received), an "Add WhatsApp number"
  button when the doctor has none, a stat button that tells the record's WhatsApp
  story and opens the chat, a WhatsApp icon on list rows, and a list action that
  puts a whole selection on the Desk.

* Automatic, event-driven notifications: case registered, dispatched (with courier +
  consignment number), invoice ready, payment received, feedback request, and an
  escalating overdue-payment reminder cron.
* An `event` tag on WhatsApp templates (native has no such concept) so each business
  event resolves its own template. No fallback: an event whose template is archived
  sends nothing; the per-model generic templates are for manual sends.
* A Simulation Mode toggle per WhatsApp sender account so the whole flow is demoable
  without a live Meta account — real accounts go through the native send pipeline
  unmodified.
* Per-doctor consent (opt-in/opt-out), synced with the native phone.blacklist; both
  are checked before every send, and a refused message is logged as cancelled.
""",
    'author': 'Ebin P G',
    'website': 'https://www.arabiandentallab.com',
    # phone_validation: owns phone.blacklist, which the opt-in writes and every send reads.
    'depends': ['epg_whatsapp', 'sale_custom', 'account', 'stock', 'mail',
                'phone_validation'],
    'data': [
        'security/ir.model.access.csv',
        'data/whatsapp_data.xml',
        'data/whatsapp_template_data.xml',
        'data/ir_cron_data.xml',
        'views/res_partner_views.xml',
        'views/res_config_settings_views.xml',
        'views/epg_whatsapp_template_views.xml',
        'views/record_views.xml',
        'wizard/number_wizard_views.xml',
        'wizard/enquiry_wizard_views.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
