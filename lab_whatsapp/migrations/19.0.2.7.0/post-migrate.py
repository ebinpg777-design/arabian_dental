# -*- coding: utf-8 -*-
"""Pay now on the invoice message. (client, 2026-09-17)

Harmless until a UPI ID is set on the sender - the link is only added when there is
one - so it is switched on for the existing template where nobody has touched it.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    template = env.ref('lab_whatsapp.tmpl_invoice', raise_if_not_found=False)
    if template and not template.add_payment_link:
        template.write({'add_payment_link': True,
                        'payment_amount_field': 'amount_total',
                        'payment_reference_field': 'name'})
