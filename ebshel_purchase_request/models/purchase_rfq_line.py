# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

class PurchaseRFQLine(models.Model):
    _name = 'ebshel.purchase.rfq.line'
    _description = 'Purchase RFQ Line'
    _order = 'rfq_id, sequence, id'

    rfq_id = fields.Many2one(
        'ebshel.purchase.rfq', string='RFQ',
        required=True, ondelete='cascade', index=True,
    )
    sequence = fields.Integer('Seq.', default=10)
    request_line_id = fields.Many2one(
        'ebshel.purchase.request.line', string='PR Line',
        ondelete='set null', index=True,
    )

    # ── Product (mirrors PR line, overridable) ─────────────────────────────
    product_id = fields.Many2one(
        'product.product', string='Product',
        compute='_compute_from_request_line', store=True, readonly=False,
    )
    name = fields.Text(
        'Description',
        compute='_compute_from_request_line', store=True, readonly=False,
    )
    qty = fields.Float(
        'Quantity', digits='Product Unit',
        compute='_compute_from_request_line', store=True, readonly=False,
    )
    uom_id = fields.Many2one(
        'uom.uom', string='UOM',
        compute='_compute_from_request_line', store=True, readonly=False,
    )
    date_required = fields.Date(
        'Required By',
        compute='_compute_from_request_line', store=True, readonly=False,
    )
    remarks = fields.Char('Remarks')
    company_id = fields.Many2one(related='rfq_id.company_id', store=True, readonly=True)
    currency_id = fields.Many2one(related='rfq_id.currency_id', readonly=True)
    product_category_id = fields.Many2one(
        'product.category', string='Allowed Category',
        related='request_line_id.request_id.product_category_id', readonly=True)
    quotation_pdt_alter = fields.Boolean(
        related='company_id.quotation_pdt_alter', readonly=True)

    # ── Bargaining (target price + per-item negotiation note) ──────────────────
    target_price = fields.Monetary(
        'Target Price', currency_field='currency_id', copy=False,
        help='Unit price the buyer is aiming for when bargaining with vendors.')
    bargain_note = fields.Char(
        'Item Bargain Note', copy=False,
        help='Per-item message sent to vendors when requesting a revised price.')
    
    updated_qty = fields.Float(
        'Updated Quantity', digits='Product Unit',
        compute='_compute_updated_qty', store=False, copy=False, readonly=True,
    )

    # ── Compute ───────────────────────────────────────────────────────────────
    @api.depends('request_line_id')
    def _compute_from_request_line(self):
        for line in self:
            rl = line.request_line_id
            if rl:
                line.product_id = rl.product_id
                line.name = rl.name
                line.qty = rl.qty
                line.uom_id = rl.uom_id
                line.date_required = rl.date_required
            else:
                if not line.product_id:
                    line.product_id = False
                if not line.name:
                    line.name = ''
                if not line.qty:
                    line.qty = 1.0

    @api.depends('qty', 'request_line_id.qty')
    def _compute_updated_qty(self):
        for rec in self:
            base_qty = rec.request_line_id.qty if rec.request_line_id else 0.0
            rec.updated_qty = (rec.qty - base_qty) if rec.qty > base_qty else 0.0
            

    @api.constrains('qty', 'request_line_id')
    def _check_quantity_on_rfq(self):
        for rec in self:
            # Only cap at the requested quantity when the company does NOT allow
            # RFQ quantities above the request. When increases are allowed, the
            # quantity-approval workflow governs instead of a hard block.
            if (rec.request_line_id and rec.qty
                    and rec.qty > rec.request_line_id.qty
                    and not rec.company_id.rfq_allow_qty_increase):
                raise ValidationError(_(
                    'RFQ quantity (%(rfq)s) must not be greater than the '
                    'requested quantity (%(pr)s) for "%(p)s". Enable "Allow RFQ '
                    'Quantity Above Request" in Settings to permit this.',
                    rfq=rec.qty, pr=rec.request_line_id.qty,
                    p=rec.product_id.display_name or rec.name))

    def write(self, vals):
        res = super().write(vals)
        # Any quantity edit invalidates a prior quantity approval — force a fresh
        # approval cycle before the RFQ can be sent to vendors again.
        if 'qty' in vals:
            stale = self.rfq_id.filtered(lambda r: r.qty_approval_state != 'none')
            if stale:
                stale.write({'qty_approval_state': 'none'})
        return res

    def unlink(self):
        # RFQ lines are frozen once the RFQ leaves Draft (they have been sent to
        # vendors / quoted / awarded), so deletion is only allowed in Draft.
        if not self.env.context.get('ebshel_bypass_line_guard'):
            locked = self.filtered(lambda l: l.rfq_id and l.rfq_id.state != 'draft')
            if locked:
                raise UserError(_(
                    'RFQ lines can only be deleted while the RFQ is in Draft. '
                    'Affected RFQ(s): %s',
                    ', '.join(sorted(set(locked.mapped('rfq_id.name'))))))
        return super().unlink()
