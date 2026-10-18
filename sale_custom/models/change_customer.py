# -*- coding: utf-8 -*-
"""Changing the customer of a confirmed order, without taking it back to a quotation.

A case booked against the wrong clinic was put right by cancelling the order, setting
it to quotation, changing the customer and confirming again - which cancels its
manufacturing orders and deliveries on the way. (Ortho client, 2026-10-15: "provide a button
to change partner in confirmed sale orders without changing the SO status")

What moves with the customer: the invoice and delivery addresses, the open delivery
transfers, the payment terms and fiscal position (core recomputes both from the
customer), and - unless asked not to - the order's route and its leader, the way
picking a clinic sets them on a new order. What does not: the status, the lines, the
prices and the pricelist, and transfers already done. An order that has an invoice is
refused: the invoice is the customer's document, and it has to be cancelled or
credited first.
"""
from markupsafe import Markup

from odoo import _, models
from odoo.exceptions import UserError


class SaleOrderChangeCustomer(models.Model):
    _inherit = 'sale.order'

    def _check_change_customer(self):
        for order in self:
            if order.state != 'sale':
                raise UserError(_("%s is not a confirmed order - change its customer on the order itself.",
                                  order.name))
            invoices = order.invoice_ids.filtered(lambda m: m.state != 'cancel')
            if invoices:
                raise UserError(_(
                    "%(order)s is invoiced (%(invoices)s). Cancel or credit the invoice first - it is made out "
                    "to the customer the order has now.",
                    order=order.name, invoices=', '.join(invoices.mapped('display_name'))))

    def action_open_change_customer(self):
        self.ensure_one()
        self._check_change_customer()
        return {
            'type': 'ir.actions.act_window', 'name': _('Change Customer'),
            'res_model': 'sale.order.change.customer', 'view_mode': 'form', 'target': 'new',
            'context': {'default_order_id': self.id},
        }

    def _change_customer(self, partner, invoice=None, shipping=None, move_route=True, reason=None):
        """The order, and what goes with it, made out to `partner`; the status as it was."""
        self._check_change_customer()
        invoice, shipping = invoice or partner, shipping or partner
        for order in self:
            old, old_fpos, old_route = order.partner_id, order.fiscal_position_id, order.team_id
            if partner == old and invoice == order.partner_invoice_id and shipping == order.partner_shipping_id:
                continue
            vals = {'partner_id': partner.id, 'partner_invoice_id': invoice.id,
                    'partner_shipping_id': shipping.id}
            route = partner.team_id or partner.commercial_partner_id.team_id
            if move_route and route and route != order.team_id:
                vals['team_id'] = route.id
                if route.user_id:
                    vals['user_id'] = route.user_id.id
            order.write(vals)
            open_transfers = order.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel'))
            if open_transfers:
                open_transfers.write({'partner_id': shipping.id})
            lines = [_("Customer changed from %(old)s to %(new)s on the confirmed order.",
                       old=old.display_name, new=partner.display_name)]
            if reason:
                lines.append(_("Why: %s", reason))
            if order.team_id != old_route:
                lines.append(_("Route: %(old)s → %(new)s.", old=old_route.name or '-', new=order.team_id.name or '-'))
            if open_transfers:
                lines.append(_("%s open delivery transfer(s) now go to the new address.", len(open_transfers)))
            if order.fiscal_position_id != old_fpos:
                lines.append(_("The fiscal position is now %(new)s (was %(old)s): the taxes on the lines were not "
                               "changed - check them before invoicing.",
                               new=order.fiscal_position_id.name or _('none'), old=old_fpos.name or _('none')))
            order.message_post(body=Markup('<br/>').join(lines))
        return True
