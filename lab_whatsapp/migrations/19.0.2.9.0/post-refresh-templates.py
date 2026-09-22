# -*- coding: utf-8 -*-
"""The lab's messages with placeholders that read well: "Dr." said once, dates as
words, money with its currency. (client, 2026-09-18)

The seed is noupdate. A template still carrying an earlier seed's words gets the
new ones; one the lab has reworded keeps them.
"""
from odoo import SUPERUSER_ID, api

from odoo.addons.lab_whatsapp.models.epg_whatsapp_template import PREVIOUS_SEED


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['epg.whatsapp.template']._refresh_seeded_templates(PREVIOUS_SEED)
