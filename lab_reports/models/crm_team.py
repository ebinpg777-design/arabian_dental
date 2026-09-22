# -*- coding: utf-8 -*-
from odoo import fields, models


class CrmTeam(models.Model):
    _inherit = 'crm.team'

    # Also declared by print_sticker (identical definition) — Odoo merges them.
    address = fields.Text('Address')
    phone = fields.Char('Phone')
