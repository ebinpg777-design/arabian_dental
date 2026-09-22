# -*- coding: utf-8 -*-
from odoo import models


class StockLot(models.Model):
    _inherit = 'stock.lot'

    def action_epg_print_labels(self):
        """Open the print wizard on the selected lot or serial numbers."""
        action = self.env['ir.actions.act_window']._for_xml_id(
            'epg_product_label.action_epg_label_print')
        action['context'] = {
            'default_lot_ids': self.ids,
            'default_label_type': 'lot',
        }
        return action
