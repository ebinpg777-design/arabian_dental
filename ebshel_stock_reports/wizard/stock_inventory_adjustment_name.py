# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
from odoo import fields, models


class StockInventoryAdjustmentName(models.TransientModel):
    _inherit = 'stock.inventory.adjustment.name'

    asr_reason_id = fields.Many2one(
        'stock.scrap.reason.tag', string='Count Reason',
        domain="[('asr_usage', 'in', ('count', 'any'))]",
        help="Stored on every adjustment move; used by the count variance and GST reports.")

    def _get_quants_context(self):
        ctx = super()._get_quants_context()
        if self.asr_reason_id:
            ctx['asr_reason_id'] = self.asr_reason_id.id
        return ctx
