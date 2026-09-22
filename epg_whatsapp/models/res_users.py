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

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + ['whatsapp_open_with']

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + ['whatsapp_open_with']
