# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    material_request_type_id = fields.Many2one(
        'stock.picking.type', string='Material Request Operation Type',
        domain="[('code', '=', 'internal')]",
        help="The internal transfer type an approved request is raised with; its source "
             "location is the store the material comes from.")
    material_request_approver_id = fields.Many2one(
        'res.users', string='Material Request Approver',
        help="Who gets an activity when a request is confirmed. Leave empty to notify nobody.")
