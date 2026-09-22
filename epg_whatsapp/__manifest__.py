# -*- coding: utf-8 -*-
{
    'name': 'EPG WhatsApp',
    'version': '19.0.2.13.0',
    'summary': 'WhatsApp from Odoo for free through your own WhatsApp, or through the '
               'Meta Cloud API - with chatter logging, read receipts and quick replies',
    'description': """
EPG WhatsApp
============
A self-contained WhatsApp channel for Odoo Community. Two ways to send, chosen per
sender:

* **WhatsApp app / Web (free)** - Odoo writes the message and puts it on the
  **WhatsApp Desk**. One tap opens it in WhatsApp Web, the desktop app, or on a phone
  by QR code, with the text already typed; the person presses Send and confirms, and
  the message is logged on the record's chatter. "Send all" walks the queue one
  message after another. No Meta account and no cost per message.

  - **Documents as private links** - a PDF cannot ride on a chat link, so it travels
    as a private link, and the doctor opening it is a real read receipt (blue ticks,
    logged on the chatter).
  - **Quick replies** - answers such as "✅ Received" become links under the message;
    the one tapped is logged on the record, no API needed.
  - **Log a reply** - paste an answer typed in WhatsApp onto the record.
  - Unsent messages expire, so stale news is never sent; a counter in the top bar
    shows what is waiting; the Desk shows what the Cloud API would have charged.

  - **One WhatsApp per doctor** - several messages waiting for the same doctor go
    out as one, with one tap.
  - **Best time and snooze** - learns when each doctor reads WhatsApp from the
    documents they open and the answers they tap; snooze to then, this evening or
    tomorrow.
  - **Reminders** - a document not opened after N hours gets one gentle reminder,
    which stands down by itself if the doctor opens it first.
  - **Campaigns** - news to many doctors, paced per day to keep the number safe, with
    a tracked link, quick replies and a stop link that previewers cannot trigger.
  - **Warnings and history** - "already sent for this invoice", "messaged 5 minutes
    ago", and the last messages with the doctor, in the send dialog.
  - **Marketing** - audiences (rules with a live count: city, engagement, "no case in
    45 days"), a gallery of ready-written ideas, a flyer page with a picture, a
    broadcast mode (one text for a WhatsApp broadcast list, logged to everyone),
    automations that send themselves ("we miss you"), "Interested" replies that
    become to-dos for the salesperson, follow-ups to the silent, and a message
    editor with emoji, formatting and placeholders.

* **Meta Cloud API (paid, automatic)** - senders with a token, approved templates,
  the 24-hour window, delivery and read receipts by webhook, and a simulation mode.

Templates, the composer, the message log, consent checks and retries are shared by
both. Business modules add their own events on top; this module knows nothing about
them.
""",
    'author': 'Ebin P G',
    'maintainer': 'Ebin P G',
    'category': 'Marketing',
    'license': 'LGPL-3',
    'depends': ['base', 'mail'],
    'data': [
        'security/epg_whatsapp_security.xml',
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'views/epg_whatsapp_account_views.xml',
        'views/epg_whatsapp_template_views.xml',
        'views/epg_whatsapp_message_views.xml',
        'views/epg_whatsapp_conversation_views.xml',
        'wizard/epg_whatsapp_composer_views.xml',
        'views/epg_whatsapp_menus.xml',
        'views/epg_whatsapp_extra_views.xml',
        'views/link_page.xml',
        'views/campaign_views.xml',
        'views/marketing_views.xml',
        'views/flyer_page.xml',
        'data/marketing_data.xml',
        'views/pay_page.xml',
        'views/snippet_views.xml',
        'views/chats_views.xml',
        'wizard/chat_import_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'epg_whatsapp/static/src/scss/preview.scss',
            'epg_whatsapp/static/src/js/preview.js',
            'epg_whatsapp/static/src/xml/preview.xml',
            'epg_whatsapp/static/src/desk/desk.scss',
            'epg_whatsapp/static/src/desk/format.js',
            'epg_whatsapp/static/src/desk/send_dialog.js',
            'epg_whatsapp/static/src/desk/send_dialog.xml',
            'epg_whatsapp/static/src/desk/desk.js',
            'epg_whatsapp/static/src/desk/desk.xml',
            'epg_whatsapp/static/src/desk/systray.js',
            'epg_whatsapp/static/src/desk/systray.xml',
            'epg_whatsapp/static/src/desk/chats.js',
            'epg_whatsapp/static/src/desk/chats.xml',
            'epg_whatsapp/static/src/desk/wa_editor.js',
            'epg_whatsapp/static/src/desk/wa_editor.xml',
        ],
    },
    'installable': True,
    'application': True,
}
