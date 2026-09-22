# -*- coding: utf-8 -*-
from odoo import fields, models


class CrmTeam(models.Model):
    _inherit = 'crm.team'

    # The route sticker prints these. (lab_reports declares them identically; Odoo
    # merges the definitions, so both modules can live with or without each other.)
    address = fields.Text('Address')
    phone = fields.Char('Phone')
