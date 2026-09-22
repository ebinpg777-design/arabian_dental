# -*- coding: utf-8 -*-
from odoo import fields, models

from ..models.sale_order_hold import HOLD_REASONS


class LabHoldWizard(models.TransientModel):
    """Putting an order on hold asks for the two things that get it moving again:
    why it stopped, and when somebody will look at it next."""
    _name = 'lab.hold.wizard'
    _description = 'Put Order On Hold'

    order_id = fields.Many2one('sale.order', required=True, readonly=True)
    hold_reason = fields.Selection(HOLD_REASONS, required=True)
    hold_note = fields.Text('Details')
    next_followup_date = fields.Date(
        required=True, default=lambda s: fields.Date.add(
            fields.Date.context_today(s), days=1),
        help="A hold with no follow-up date is how an order gets forgotten.")

    def action_confirm(self):
        self.ensure_one()
        self.order_id._put_on_hold(
            reason=self.hold_reason, note=self.hold_note,
            followup=self.next_followup_date)
        return {'type': 'ir.actions.act_window_close'}
