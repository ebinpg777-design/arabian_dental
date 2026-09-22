# -*- coding: utf-8 -*-
"""The case message points at the portal instead of carrying the acknowledgement. (client, 2026-09-18)

WhatsApp draws no buttons for a message sent from the app or the Web, so a
message that carried three raw addresses now carries one that says what it
opens - and the document, the payment, the answers and the chat are real
buttons there.

The seed is noupdate. A template still carrying an earlier seed's words gets
the new ones; one the lab has reworded keeps them and only gains what it lacks.
"""
from odoo import SUPERUSER_ID, api

from odoo.addons.lab_whatsapp.models.epg_whatsapp_template import PREVIOUS_SEED


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['epg.whatsapp.template']._refresh_seeded_templates(PREVIOUS_SEED)
