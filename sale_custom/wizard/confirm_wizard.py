# -*- coding: utf-8 -*-
from odoo import fields, models


class ConfirmWizard(models.TransientModel):
    _name = 'confirm.wizard'
    _description = 'Company Warning Confirmation'

    name = fields.Text()
    sale_id = fields.Many2one('sale.order', string='Sale Order')
    # Every flagged order of the selection, so one dialog confirms them all.
    sale_ids = fields.Many2many('sale.order', string='Sale Orders')

    def action_confirm(self):
        orders = self.sale_ids | self.sale_id
        # Seen only now, when the person has actually pressed Confirm.
        orders.write({'company_warning': True})
        return orders.action_confirm()
