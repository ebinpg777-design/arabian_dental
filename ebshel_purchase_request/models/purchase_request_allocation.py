# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.
from markupsafe import Markup
from odoo import api, fields, models, _

class PurchaseRequestAllocation(models.Model):
    """Links a purchase-request line to the purchase-order line and/or the
    inbound stock move that fulfils it, and tracks how much has been received."""
    _name = 'ebshel.purchase.request.allocation'
    _description = 'Purchase Request Allocation'

    request_line_id = fields.Many2one(
        'ebshel.purchase.request.line', string='Request Line',
        required=True, ondelete='cascade', index=True,
    )
    request_id = fields.Many2one(
        related='request_line_id.request_id', store=True, index=True,
        string='Purchase Request',
    )
    purchase_line_id = fields.Many2one(
        'purchase.order.line', string='Purchase Order Line',
        ondelete='cascade', index=True,
    )
    purchase_order_id = fields.Many2one(
        related='purchase_line_id.order_id', store=True, string='Purchase Order',
    )
    stock_move_id = fields.Many2one(
        'stock.move', string='Stock Move',
        ondelete='cascade', index=True,
    )
    company_id = fields.Many2one(
        related='request_line_id.company_id', store=True, index=True,
    )
    product_id = fields.Many2one(
        related='request_line_id.product_id', store=True, string='Product',
    )
    product_uom_id = fields.Many2one(
        related='request_line_id.uom_id', string='UoM',
    )

    requested_product_uom_qty = fields.Float(
        'Allocated Qty', digits='Product Unit',
        help='Quantity of the request line allocated to this PO line / move.',
    )
    allocated_product_qty = fields.Float(
        'Received Qty', digits='Product Unit', copy=False,
        help='Quantity actually received against this allocation.',
    )
    open_product_qty = fields.Float(
        'Open Qty', compute='_compute_open_product_qty', store=True,
        digits='Product Unit',
        help='Quantity still expected (allocated − received).',
    )
    purchase_state = fields.Selection(
        related='purchase_line_id.state', string='PO Status', store=True,
    )

    @api.depends('requested_product_uom_qty', 'allocated_product_qty')
    def _compute_open_product_qty(self):
        for alloc in self:
            alloc.open_product_qty = max(
                alloc.requested_product_uom_qty - alloc.allocated_product_qty, 0.0
            )

    # ── Smart-button navigation ───────────────────────────────────────────────
    def action_view_request(self):
        self.ensure_one()
        return {
            'name': _('Purchase Request'),
            'type': 'ir.actions.act_window',
            'res_model': 'ebshel.purchase.request',
            'view_mode': 'form',
            'res_id': self.request_id.id,
        }

    def action_view_purchase_order(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('purchase.purchase_form_action')
        action['context'] = {}
        action['views'] = [(self.env.ref('purchase.purchase_order_form').id, 'form')]
        action['res_id'] = self.purchase_order_id.id
        action['domain'] = [('id', '=', self.purchase_order_id.id)]
        return action

    def action_view_receipt(self):
        self.ensure_one()
        return {
            'name': _('Receipt'),
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'view_mode': 'form',
            'res_id': self.stock_move_id.picking_id.id,
        }

    def _notify_allocation(self, received_qty):
        """Post a chatter note on the parent request when stock is received."""
        for alloc in self:
            request = alloc.request_line_id.request_id
            if not request:
                continue
            request.message_post(
                body=Markup(_(
                    'Received <b>%(qty)s %(uom)s</b> of <b>%(product)s</b> '
                    '(request line %(line)s).'
                )) % {
                    'qty': received_qty,
                    'uom': alloc.product_uom_id.name or '',
                    'product': alloc.product_id.display_name or '',
                    'line': alloc.request_line_id.name or '',
                },
                subtype_xmlid='mail.mt_note',
            )
