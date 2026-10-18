# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.
from odoo import api, fields, models, _
from odoo.exceptions import UserError

class PrSelectWinnerWizard(models.TransientModel):
    _name = 'ebshel.pr.select.winner.wizard'
    _description = 'Select Winning Vendor for RFQ'

    rfq_id = fields.Many2one('ebshel.purchase.rfq', required=True, ondelete='cascade')
    quotation_ids = fields.Many2many(
        'ebshel.purchase.vendor.quotation', compute='_compute_quotation_ids',
        string='Vendor Quotations',
    )
    winner_quotation_id = fields.Many2one(
        'ebshel.purchase.vendor.quotation', string='Winning Quotation',
        required=True,
        domain="[('rfq_id', '=', rfq_id), ('state', '=', 'received')]",
    )
    justification = fields.Text('Selection Justification', required=True)
    auto_create_po = fields.Boolean(
        'Create Purchase Order Immediately', default=True,
    )

    @api.depends('rfq_id')
    def _compute_quotation_ids(self):
        for wiz in self:
            wiz.quotation_ids = wiz.rfq_id.vendor_quotation_ids.filtered(
                lambda q: q.state == 'received'
            )

    def action_confirm_winner(self):
        self.ensure_one()
        winner = self.winner_quotation_id

        # If the winning vendor is new and hasn't been registered yet,
        # redirect to the vendor creation wizard which will complete the award.
        if winner.is_new_vendor and not winner.vendor_id:
            return {
                'name': _('Create & Register Winning Vendor'),
                'type': 'ir.actions.act_window',
                'res_model': 'ebshel.pr.create.vendor.wizard',
                'view_mode': 'form',
                'target': 'new',
                'context': {
                    'default_quotation_id': winner.id,
                    'default_rfq_id': self.rfq_id.id,
                    'default_name': winner.new_vendor_name or '',
                    'default_payment_term_id': winner.payment_term_id.id if winner.payment_term_id else False,
                    'default_proceed_to_award': True,
                    'default_auto_create_po': self.auto_create_po,
                    'default_justification': self.justification,
                    'default_return_to_quotation': False,
                },
            }

        winner.winner_justification = self.justification
        winner.action_select_winner()
        if self.auto_create_po:
            # Route through approval when the company requires it.
            if self.rfq_id._award_needs_approval():
                self.rfq_id._propose_award_single(winner, self.justification)
                return self.rfq_id._award_pending_notification()
            return self._create_po()
        return {'type': 'ir.actions.act_window_close'}

    def _create_po(self):
        """Delegate to the RFQ's single PO-creation method and open the PO."""
        po = self.rfq_id._create_purchase_order_from_winner()
        return {
            'name': _('Purchase Order'),
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'view_mode': 'form',
            'res_id': po.id,
        }
