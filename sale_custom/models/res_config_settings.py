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

    # Odoo's "Multi Teams" switch lives in the CRM app's settings, and the lab has
    # no CRM: without it, adding a person to a second Sales Route archived them
    # from the first. Same parameter, shown in Sales. (client, 2026-11-27)
    lab_route_membership_multi = fields.Boolean(
        string='Same Person on Several Sales Routes',
        config_parameter='sales_team.membership_multi')
