# -*- coding: utf-8 -*-
"""A reminder on the invoice template for invoices nobody opened. (client, 2026-09-17)

The template data is noupdate, so an existing database never receives it from the XML.
Set only where no reminder is configured yet: a lab that chose otherwise keeps its choice.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    template = env.ref('lab_whatsapp.tmpl_invoice', raise_if_not_found=False)
    if template and not template.nudge_after_hours and not template.nudge_text:
        template.write({
            'nudge_after_hours': 48,
            'nudge_text': "Dear Dr. {{partner_id.name}}, a gentle reminder: invoice "
                          "*{{name}}* ({{amount_total}} {{currency_id.name}}) is waiting "
                          "for you.",
        })
