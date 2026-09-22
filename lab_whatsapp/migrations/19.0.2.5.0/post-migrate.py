# -*- coding: utf-8 -*-
"""Quick replies on the dispatch and feedback templates. (client, 2026-09-17)

The template data is noupdate, so an existing database never receives them from the
XML. Written only where the template has none: a lab that already wrote its own
answers keeps them.
"""
from odoo import SUPERUSER_ID, api

ANSWERS = {
    'lab_whatsapp.tmpl_dispatch': "✅ Received, all fine\n⚠️ Something is missing",
    'lab_whatsapp.tmpl_feedback': "⭐⭐⭐⭐⭐ Excellent\n⭐⭐⭐⭐ Good\n⭐⭐⭐ Average\n"
                                    "⭐⭐ Needs improvement",
}


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    for xmlid, answers in ANSWERS.items():
        template = env.ref(xmlid, raise_if_not_found=False)
        if template and not template.quick_replies:
            template.quick_replies = answers
