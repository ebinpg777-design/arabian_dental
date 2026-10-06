# -*- coding: utf-8 -*-
from odoo import fields, models

OPEN_WITH = [
    ('auto', 'Automatic (app on a phone, WhatsApp Web on a computer)'),
    ('web', 'WhatsApp Web'),
    ('app', 'WhatsApp desktop app'),
    ('phone', 'My phone (scan a QR code)'),
]


class ResUsers(models.Model):
    _inherit = 'res.users'

    whatsapp_open_with = fields.Selection(
        OPEN_WITH, string='Open WhatsApp In', default='auto',
        help="Where the Send button opens a WhatsApp message prepared in Odoo.")
    # Odoo 20 dropped SELF_READABLE_FIELDS / SELF_WRITEABLE_FIELDS: a user may read
    # and update their own record through ir.access (res_users_rule_write_self),
    # so the preference needs no whitelist any more.
