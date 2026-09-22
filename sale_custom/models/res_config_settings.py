# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    emergency_service_id = fields.Many2one(
        related='company_id.emergency_service_id', readonly=False,
        string='Emergency Service Product')
    emergency_service_perc = fields.Float(
        related='company_id.emergency_service_perc', readonly=False,
        string="Emergency Service Percentage")
