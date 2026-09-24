# -*- coding: utf-8 -*-
from odoo import fields, models


class MaterialRequestReject(models.TransientModel):
    _name = 'material.request.reject'
    _description = 'Reject a Material Request'

    request_id = fields.Many2one('material.request', required=True)
    reason = fields.Text(required=True,
                         help="Told to the requester on the request itself.")

    def action_reject(self):
        self.ensure_one()
        self.request_id._do_reject(self.reason)
        return {'type': 'ir.actions.act_window_close'}
