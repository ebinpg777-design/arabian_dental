# -*- coding: utf-8 -*-
from odoo import _, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_print_sticker(self):
        return {
            'type': 'ir.actions.act_window', 'name': _('Print Stickers'),
            'res_model': 'epg.sticker.print.wizard', 'view_mode': 'form', 'target': 'new',
            'context': {'active_model': 'sale.order', 'active_ids': self.ids,
                        # one sticker per order, however many are picked (client, 2026-10-20)
                        'default_order_ids': [(6, 0, self.ids)], 'default_mode': 'order'},
        }
