# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import float_compare


class PrQtyEditWizard(models.TransientModel):
    """Reduce line quantities on an already-approved purchase request — e.g. to
    drop the quantity that is no longer needed after sourcing only part of it on
    an RFQ."""
    _name = 'ebshel.pr.qty.edit.wizard'
    _description = 'Edit Approved Request Quantities'

    request_id = fields.Many2one(
        'ebshel.purchase.request', string='Purchase Request',
        required=True, ondelete='cascade')
    reapproval = fields.Boolean(
        related='request_id.company_id.pr_qty_edit_reapproval', readonly=True)
    line_ids = fields.One2many(
        'ebshel.pr.qty.edit.line', 'wizard_id', string='Lines')

    def action_apply(self):
        self.ensure_one()
        request = self.request_id
        if request.state != 'approved':
            raise UserError(_('Only approved purchase requests can be edited here.'))

        changes = []
        for wl in self.line_ids:
            line = wl.request_line_id
            rounding = line.uom_id.rounding or 0.01
            new_qty = wl.new_qty
            # Unchanged → skip.
            if float_compare(new_qty, line.qty, precision_rounding=rounding) == 0:
                continue
            # Decrease-only.
            # if float_compare(new_qty, line.qty, precision_rounding=rounding) > 0:
            #     raise UserError(_(
            #         'Quantities can only be reduced. "%(p)s" is %(cur)s — it '
            #         'cannot be increased to %(new)s.',
            #         p=line.product_id.display_name or line.name,
            #         cur=line.qty, new=new_qty))
            # Never below what is already committed to an RFQ.
            if float_compare(new_qty, line.qty_in_rfq, precision_rounding=rounding) < 0:
                raise UserError(_(
                    '"%(p)s": %(rfq)s is already on an RFQ, so the quantity '
                    'cannot be reduced below that. To remove it entirely, cancel '
                    'the RFQ first.',
                    p=line.product_id.display_name or line.name,
                    rfq=line.qty_in_rfq))
            # Must stay positive — fully dropping a line is "Cancel Line".
            if float_compare(new_qty, 0.0, precision_rounding=rounding) <= 0:
                raise UserError(_(
                    '"%(p)s": quantity must stay above zero. Use "Cancel Line" '
                    'to drop the line completely.',
                    p=line.product_id.display_name or line.name))
            changes.append((line, line.qty, new_qty))

        if not changes:
            raise UserError(_('No quantity changes to apply.'))

        for line, _old, new in changes:
            line.write({'qty': new})

        request._apply_qty_edit_followup(changes)
        return {'type': 'ir.actions.act_window_close'}


class PrQtyEditLine(models.TransientModel):
    _name = 'ebshel.pr.qty.edit.line'
    _description = 'Edit Quantity Line'
    _order = 'id'

    wizard_id = fields.Many2one(
        'ebshel.pr.qty.edit.wizard', required=True, ondelete='cascade')
    request_line_id = fields.Many2one(
        'ebshel.purchase.request.line', string='Request Line', required=True)
    product_id = fields.Many2one(related='request_line_id.product_id', readonly=True)
    uom_id = fields.Many2one(related='request_line_id.uom_id', readonly=True)
    current_qty = fields.Float(
        related='request_line_id.qty', string='Current Qty', readonly=True,
        digits='Product Unit')
    qty_in_rfq = fields.Float(
        related='request_line_id.qty_in_rfq', string='On RFQ (min)', readonly=True,
        digits='Product Unit')
    new_qty = fields.Float(
        'New Quantity', digits='Product Unit')

