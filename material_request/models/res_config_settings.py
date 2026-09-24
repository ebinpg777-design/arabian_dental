# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    material_request_type_id = fields.Many2one(
        related='company_id.material_request_type_id', readonly=False)
    material_request_approver_id = fields.Many2one(
        related='company_id.material_request_approver_id', readonly=False)
