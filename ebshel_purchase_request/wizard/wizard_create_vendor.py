# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.
from markupsafe import Markup
from odoo import api, fields, models, _
from odoo.exceptions import UserError

class PrCreateVendorWizard(models.TransientModel):
    """Create a new vendor (res.partner) directly from an RFQ / vendor quotation.

    When `proceed_to_award=True` this wizard also marks the linked quotation as
    winner and optionally creates the Purchase Order in one shot, avoiding the
    need to re-open the select-winner wizard after vendor creation.
    """
    _name = 'ebshel.pr.create.vendor.wizard'
    _description = 'Create New Vendor from RFQ'

    quotation_id = fields.Many2one(
        'ebshel.purchase.vendor.quotation', string='Quotation',
        ondelete='cascade',
    )
    rfq_id = fields.Many2one('ebshel.purchase.rfq', ondelete='cascade')

    # ── Vendor fields ─────────────────────────────────────────────────────────
    name = fields.Char('Vendor Name', required=True)
    company_name = fields.Char('Company Name')
    email = fields.Char('Email')
    phone = fields.Char('Phone')
    street = fields.Char('Street')
    street2 = fields.Char('Street2')
    city = fields.Char('City')
    state_id = fields.Many2one('res.country.state', string='State')
    country_id = fields.Many2one('res.country', string='Country')
    zip = fields.Char('ZIP')
    vat = fields.Char('Tax ID (VAT)')
    payment_term_id = fields.Many2one('account.payment.term', string='Payment Terms')
    website = fields.Char('Website')
    is_company = fields.Boolean('Is Company', default=True)

    # ── Post-creation actions ─────────────────────────────────────────────────
    return_to_quotation = fields.Boolean(
        'Return to Quotation', default=False,
        help='After creating the vendor, open the vendor quotation form.',
    )
    proceed_to_award = fields.Boolean(
        'Proceed to Award After Creation', default=False,
        help='After creating and linking the vendor, immediately mark this '
             'quotation as the winner and create the Purchase Order.',
    )
    justification = fields.Text(
        'Award Justification',
        help='Required when proceeding to award.',
    )
    auto_create_po = fields.Boolean(
        'Create Purchase Order', default=True,
        help='Visible only when "Proceed to Award" is enabled.',
    )

    @api.onchange('proceed_to_award')
    def _onchange_proceed_to_award(self):
        if not self.proceed_to_award:
            self.justification = False
            self.auto_create_po = False

    def action_create_vendor(self):
        self.ensure_one()
        if self.proceed_to_award and self.quotation_id and not self.justification:
            raise UserError(_('Please provide a justification for awarding this vendor.'))

        vendor = self.env['res.partner'].create({
            'name': self.name,
            'company_name': self.company_name,
            'is_company': self.is_company,
            'supplier_rank': 1,
            'email': self.email,
            'phone': self.phone,
            'street': self.street,
            'street2': self.street2,
            'city': self.city,
            'state_id': self.state_id.id,
            'country_id': self.country_id.id,
            'zip': self.zip,
            'vat': self.vat,
            'property_payment_term_id': self.payment_term_id.id if self.payment_term_id else False,
            'website': self.website,
        })

        # Link to quotation when opened from a specific vendor quotation record
        if self.quotation_id:
            self.quotation_id.write({
                'vendor_id': vendor.id,
                'is_new_vendor': True,    # keep the flag so history shows it was a new vendor
                'vendor_registered': True,
            })
            self.quotation_id.message_post(
                body=Markup(_('Vendor <a href="#" data-oe-model="res.partner" data-oe-id="%d">%s</a> created and linked to this quotation.')) % (vendor.id, vendor.name),
                subtype_xmlid='mail.mt_note',
            )

        # Award the winner and optionally create PO in one shot
        if self.proceed_to_award and self.quotation_id:
            self.quotation_id.winner_justification = self.justification
            # Call the internal award logic directly (vendor_id is now set)
            quotation = self.quotation_id
            siblings = quotation.rfq_id.vendor_quotation_ids
            siblings.filtered(lambda q: q.id != quotation.id).write({
                'is_winner': False,
                'state': 'rejected',
            })
            quotation.write({'is_winner': True, 'state': 'selected'})
            quotation.rfq_id.write({'state': 'awarded'})
            quotation.message_post(
                body=Markup(_('<b>%s</b> selected as the winning vendor after registration.')) % (vendor.name,),
            )
            if self.auto_create_po:
                # Route through approval when the company requires it.
                if quotation.rfq_id._award_needs_approval():
                    quotation.rfq_id._propose_award_single(quotation, self.justification)
                    return quotation.rfq_id._award_pending_notification()
                return self._create_po(quotation, vendor)
            # Return to quotation form
            return {
                'name': _('Vendor Quotation'),
                'type': 'ir.actions.act_window',
                'res_model': 'ebshel.purchase.vendor.quotation',
                'view_mode': 'form',
                'res_id': quotation.id,
            }

        # Return to quotation if requested
        if self.return_to_quotation and self.quotation_id:
            return {
                'name': _('Vendor Quotation'),
                'type': 'ir.actions.act_window',
                'res_model': 'ebshel.purchase.vendor.quotation',
                'view_mode': 'form',
                'res_id': self.quotation_id.id,
            }

        # Default: open the newly created vendor record
        return {
            'name': _('Vendor: %s', vendor.name),
            'type': 'ir.actions.act_window',
            'res_model': 'res.partner',
            'view_mode': 'form',
            'res_id': vendor.id,
        }

    def _create_po(self, quotation, vendor):
        """Delegate to the RFQ's single PO-creation method and open the PO."""
        po = quotation.rfq_id._create_purchase_order_from_winner()
        return {
            'name': _('Purchase Order'),
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'view_mode': 'form',
            'res_id': po.id,
        }
