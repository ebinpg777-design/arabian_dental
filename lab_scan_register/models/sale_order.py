# -*- coding: utf-8 -*-
from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    scan_id = fields.Many2one(
        'lab.scan', string='Scan', readonly=True, copy=False, index='btree_not_null',
        help="The registered scan this order was made from.")

    def action_view_scan(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'lab.scan',
                'res_id': self.scan_id.id, 'view_mode': 'form'}
