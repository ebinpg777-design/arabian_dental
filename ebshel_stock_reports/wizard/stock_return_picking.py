# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
from odoo import fields, models


class StockReturnPicking(models.TransientModel):
    _inherit = 'stock.return.picking'

    asr_reason_id = fields.Many2one(
        'stock.scrap.reason.tag', string='Return Reason',
        domain="[('asr_usage', 'in', ('return', 'any'))]",
        help="Stored on every return move; used by the scrap and returns report.")

    def _create_return(self):
        new_picking = super()._create_return()
        if self.asr_reason_id and new_picking:
            new_picking.move_ids.write({'asr_reason_id': self.asr_reason_id.id})
        return new_picking
