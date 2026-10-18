# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ChangeCustomerWizard(models.TransientModel):
    _name = 'sale.order.change.customer'
    _description = 'Change the customer of a confirmed order'

    order_id = fields.Many2one('sale.order', required=True, readonly=True, ondelete='cascade')
    current_partner_id = fields.Many2one(related='order_id.partner_id', string='Customer Now')
    partner_id = fields.Many2one('res.partner', string='New Customer', required=True,
                                 domain="[('id', '!=', current_partner_id)]")
    # blank means the customer itself (filled from the customer's own addresses)
    partner_invoice_id = fields.Many2one('res.partner', string='Invoice Address',
                                         compute='_compute_addresses', store=True, readonly=False)
    partner_shipping_id = fields.Many2one('res.partner', string='Delivery Address',
                                          compute='_compute_addresses', store=True, readonly=False)
    new_route_id = fields.Many2one('crm.team', string="New Clinic's Route", compute='_compute_new_route')
    move_route = fields.Boolean("Move the order to the new clinic's route", default=True,
                                help="The order's route and salesperson become the new clinic's route and its "
                                     "leader, as when the clinic is picked on a new order.")
    reason = fields.Char('Why', help="Kept on the order's chatter with the change.")

    @api.depends('partner_id')
    def _compute_addresses(self):
        for wiz in self:
            found = wiz.partner_id.address_get(['invoice', 'delivery']) if wiz.partner_id else {}
            wiz.partner_invoice_id = found.get('invoice') or wiz.partner_id
            wiz.partner_shipping_id = found.get('delivery') or wiz.partner_id

    @api.depends('partner_id')
    def _compute_new_route(self):
        for wiz in self:
            wiz.new_route_id = wiz.partner_id.team_id or wiz.partner_id.commercial_partner_id.team_id

    def action_apply(self):
        self.ensure_one()
        self.order_id._change_customer(self.partner_id, self.partner_invoice_id or self.partner_id,
                                       self.partner_shipping_id or self.partner_id,
                                       move_route=self.move_route, reason=self.reason)
        return {'type': 'ir.actions.act_window_close'}
