# -*- coding: utf-8 -*-
from odoo import models


class StockPicking(models.Model):
    """The message a doctor actually waits for: it has left the lab."""
    _name = 'stock.picking'
    _inherit = ['stock.picking', 'lab.whatsapp.mixin']

    def _whatsapp_suggested_event(self):
        return 'dispatch' if self.state == 'done' else 'manual'

    def button_validate(self):
        res = super().button_validate()
        self.filtered(lambda p: p.state == 'done'
                      and p.picking_type_id.code == 'outgoing') \
            ._whatsapp_send_event('dispatch')
        return res
