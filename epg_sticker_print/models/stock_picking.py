# -*- coding: utf-8 -*-
from odoo import _, models
from odoo.exceptions import UserError


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def action_print_sticker(self):
        orders = self.sale_id
        if not orders:
            raise UserError(_("These transfers are not linked to a sale order, so there is no destination sticker to print."))
        return {
            'type': 'ir.actions.act_window', 'name': _('Print Stickers'),
            'res_model': 'epg.sticker.print.wizard', 'view_mode': 'form', 'target': 'new',
            'context': {'active_model': 'sale.order', 'active_ids': orders.ids,
                        'default_order_ids': [(6, 0, orders.ids)], 'default_mode': 'order'},
        }
