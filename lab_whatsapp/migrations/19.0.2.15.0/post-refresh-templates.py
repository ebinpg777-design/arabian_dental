# -*- coding: utf-8 -*-
"""The enquiry a doctor answers in the chat carries no link, and the questions
line up in the order they are asked. (client, 2026-09-18)

The seed is noupdate, so a template that already exists keeps what it has: the
refresh brings the words up to date where the lab has not rewritten them, and the
order is set here because `sequence` is not something anybody chose - every
template was created with the same default.
"""
from odoo import SUPERUSER_ID, api

from odoo.addons.lab_whatsapp.models.epg_whatsapp_template import PREVIOUS_SEED

# The order the questions come up at the counter.
ENQUIRY_ORDER = [
    ('lab_whatsapp.tmpl_more_info', 10),
    ('lab_whatsapp.tmpl_ask_impression', 20),
    ('lab_whatsapp.tmpl_ask_shade', 30),
    ('lab_whatsapp.tmpl_ask_pickup', 40),
    ('lab_whatsapp.tmpl_ask_approval', 50),
]


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['epg.whatsapp.template']._refresh_seeded_templates(PREVIOUS_SEED)
    for xmlid, sequence in ENQUIRY_ORDER:
        template = env.ref(xmlid, raise_if_not_found=False)
        # Only while it is still the default nobody chose.
        if template and template.sequence == 10 and sequence != 10:
            template.sequence = sequence
