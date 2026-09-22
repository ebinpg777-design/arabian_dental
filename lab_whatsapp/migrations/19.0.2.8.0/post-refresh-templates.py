# -*- coding: utf-8 -*-
"""The redesigned lab messages: answers to tap, an escalating reminder with a Pay
now link, a receipt on the payment thank-you, feedback the day after. (client,
2026-09-17)

The seed is noupdate. A template still carrying the 19.0.2.7.0 words gets the new
ones; one the lab has reworded keeps them and only gains what it lacks.
"""
from odoo import SUPERUSER_ID, api

from odoo.addons.lab_whatsapp.models.epg_whatsapp_template import PREVIOUS_SEED


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['epg.whatsapp.template']._refresh_seeded_templates(PREVIOUS_SEED)
