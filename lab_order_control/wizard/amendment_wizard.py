# -*- coding: utf-8 -*-
from odoo import fields, models


class LabAmendmentWizard(models.TransientModel):
    """Marking an amendment asks for the one thing that gets it done: what it is."""
    _name = 'lab.amendment.wizard'
    _description = 'Mark an Amendment on an Order'

    order_id = fields.Many2one('sale.order', required=True, readonly=True)
    note = fields.Text('What has to be amended', required=True)

    def action_confirm(self):
        self.ensure_one()
        self.order_id._mark_amendment(self.note)
        return {'type': 'ir.actions.act_window_close'}
