# -*- coding: utf-8 -*-
from odoo import models


class AccountPayment(models.Model):
    """Money received is confirmed back, so the doctor is never left wondering."""
    _name = 'account.payment'
    _inherit = ['account.payment', 'lab.whatsapp.mixin']

    def _whatsapp_suggested_event(self):
        return 'payment' if self.state in ('in_process', 'paid') else 'manual'

    def action_post(self):
        res = super().action_post()
        self.filtered(lambda p: p.payment_type == 'inbound') \
            ._whatsapp_send_event('payment')
        return res
