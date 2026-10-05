# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
from odoo import models


class StockQuant(models.Model):
    _inherit = 'stock.quant'

    def _get_inventory_move_values(self, qty, location_id, location_dest_id, package_id=False, package_dest_id=False):
        """Copy the system and counted quantities onto the adjustment move while
        they are still on the quant (DESIGN.md §1.9), and the reason asked by the wizard."""
        vals = super()._get_inventory_move_values(qty, location_id, location_dest_id, package_id, package_dest_id)
        vals['asr_qty_before'] = self.quantity
        vals['asr_qty_counted'] = self.inventory_quantity
        reason_id = self.env.context.get('asr_reason_id')
        if reason_id:
            vals['asr_reason_id'] = reason_id
        return vals
