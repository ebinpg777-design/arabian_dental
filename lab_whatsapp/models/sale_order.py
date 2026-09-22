# -*- coding: utf-8 -*-
from odoo import models


class SaleOrder(models.Model):
    """Registering a case tells the doctor it arrived; holding one asks them why."""
    _name = 'sale.order'
    _inherit = ['sale.order', 'lab.whatsapp.mixin']

    def _whatsapp_suggested_event(self):
        # A held case is waiting on the DOCTOR, so that is the message the button
        # should open with - not "case registered", which they have already had.
        # `verification_state` belongs to lab_order_control: checked for rather
        # than assumed, so this module still installs without it.
        # (client, 2026-09-19)
        if 'verification_state' in self._fields and self.verification_state == 'on_hold':
            return 'hold_ask'
        return 'order_confirm' if self.state == 'sale' else 'manual'

    def action_confirm(self):
        res = super().action_confirm()
        self._whatsapp_send_event('order_confirm')
        return res
