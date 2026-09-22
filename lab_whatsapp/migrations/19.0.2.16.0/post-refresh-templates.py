# -*- coding: utf-8 -*-
"""The generic messages read like messages. (client, 2026-09-18)

"SO284978: JUMANATH" is what a doctor used to receive when somebody pressed
WhatsApp on a quotation - the template behind that button was a debug line. They
are now a frame: the greeting, what the doctor recognises the case by, and room
for the line the person sending wants to write.

The seed is noupdate. A template still carrying the old one-liner gets the frame;
one the lab has reworded keeps its words.
"""
from odoo import SUPERUSER_ID, api

from odoo.addons.lab_whatsapp.models.epg_whatsapp_template import PREVIOUS_SEED


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['epg.whatsapp.template']._refresh_seeded_templates(PREVIOUS_SEED)
