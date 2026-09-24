# -*- coding: utf-8 -*-
from odoo import fields, models


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    material_request_id = fields.Many2one(
        'material.request', string='Material Request', readonly=True, index=True, copy=False)

    def _action_done(self):
        res = super()._action_done()
        self.mapped('material_request_id')._check_delivered()
        return res


class StockMove(models.Model):
    _inherit = 'stock.move'

    material_request_line_id = fields.Many2one(
        'material.request.lines', string='Material Request Line', index=True, copy=False,
        ondelete='set null')
