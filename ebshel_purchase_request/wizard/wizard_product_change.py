# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class PrProductChangeWizard(models.TransientModel):
    """Approver workbench to swap request-line products for another product in
    the same category, or to return the request to the requester with a
    suggested product. Only lines the requester flagged "Allow Product Change"
    appear here."""
    _name = 'ebshel.pr.product.change.wizard'
    _description = 'Change Request Line Products'

    request_id = fields.Many2one(
        'ebshel.purchase.request', string='Purchase Request',
        required=True, ondelete='cascade')
    line_ids = fields.One2many(
        'ebshel.pr.product.change.line', 'wizard_id', string='Lines')

    def _collect_changes(self):
        """Return [(line, old_product, new_product)] for lines whose chosen
        product differs from the current one, validating the same-category rule."""
        self.ensure_one()
        changes = []
        for wl in self.line_ids:
            line = wl.request_line_id
            new = wl.new_product_id
            if not new or new == line.product_id:
                continue
            if new.categ_id != line.product_id.categ_id:
                raise UserError(_(
                    '"%(new)s" is not in the same category as "%(cur)s" — only '
                    'same-category products can be swapped.',
                    new=new.display_name, cur=line.product_id.display_name))
            changes.append((line, line.product_id, new))
        return changes

    def action_apply(self):
        """Approver swaps the product(s) in place; the request stays pending so
        the approver can then approve it."""
        self.ensure_one()
        if self.request_id.state != 'submitted':
            raise UserError(_('Only a submitted request can be edited here.'))
        changes = self._collect_changes()
        if not changes:
            raise UserError(_('Pick a different product on at least one line.'))
        for line, _old, new in changes:
            # Clear any stale suggestion; the swap is now applied.
            line.write({'product_id': new.id, 'suggested_product_id': False})
        self.request_id._apply_product_change_followup(changes)
        return {'type': 'ir.actions.act_window_close'}

    def action_send_to_requester(self):
        """Record the suggested product(s) and return the request to the
        requester (Draft) to make the change."""
        self.ensure_one()
        if self.request_id.state != 'submitted':
            raise UserError(_('Only a submitted request can be returned.'))
        changes = self._collect_changes()
        self.request_id._return_to_requester_for_product(changes)
        return {'type': 'ir.actions.act_window_close'}


class PrProductChangeLine(models.TransientModel):
    _name = 'ebshel.pr.product.change.line'
    _description = 'Product Change Line'
    _order = 'id'

    wizard_id = fields.Many2one(
        'ebshel.pr.product.change.wizard', required=True, ondelete='cascade')
    request_line_id = fields.Many2one(
        'ebshel.purchase.request.line', string='Request Line', required=True)
    current_product_id = fields.Many2one(
        related='request_line_id.product_id', string='Current Product', readonly=True)
    categ_id = fields.Many2one(
        related='request_line_id.product_id.categ_id', string='Category', readonly=True)
    qty = fields.Float(related='request_line_id.qty', string='Qty', readonly=True)
    new_product_id = fields.Many2one(
        'product.product', string='New / Suggested Product',
        domain="[('purchase_ok', '=', True), ('categ_id', '=', categ_id)]",
        help='A product in the same category as the current one.')
