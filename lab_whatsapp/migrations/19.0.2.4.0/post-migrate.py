# -*- coding: utf-8 -*-
"""Templates send to the contact's WhatsApp Number, not its phone.

The seeded templates are noupdate, so their phone field is moved here. Only the
contact paths change: a template someone pointed at another field keeps it.
(client, 2026-09-15)
"""


def migrate(cr, version):
    cr.execute("""
        UPDATE epg_whatsapp_template
           SET phone_field = 'partner_id.whatsapp_number'
         WHERE phone_field IN ('partner_id.phone', 'partner_id.mobile')
    """)
    cr.execute("""
        UPDATE epg_whatsapp_template
           SET phone_field = 'whatsapp_number'
         WHERE phone_field IN ('phone', 'mobile') AND model = 'res.partner'
    """)
