# -*- coding: utf-8 -*-
from odoo import fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    # Read by the partner rule that lets a carrier open the clinics they carry
    # work to - deliveries cross routes, the route rule alone does not.
    delivery_ids = fields.One2many('lab.delivery', 'partner_id', string='Deliveries')
