# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.
from markupsafe import Markup
from odoo import api, fields, models, _
from odoo.exceptions import UserError

class PrCreateRfqWizard(models.TransientModel):
    """Select approved PR lines (or part of their remaining quantity) from one
    or more requests and create an RFQ. A line's quantity can be split across
    several RFQs over time."""
    _name = 'ebshel.pr.create.rfq.wizard'
    _description = 'Create RFQ from Purchase Request Lines'

    request_ids = fields.Many2many(
        'ebshel.purchase.request', string='Source Requests',
        domain=[('state', '=', 'approved')],
    )
    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company)
    wizard_line_ids = fields.One2many(
        'ebshel.pr.create.rfq.wizard.line', 'wizard_id', string='Lines to Source')
    deadline = fields.Date('Quotation Deadline')
    vendor_ids = fields.Many2many(
        'res.partner', string='Invite Vendors',
        domain=[('supplier_rank', '>', 0)],
        help='A draft vendor quotation (pre-loaded with the RFQ lines) is created '
             'for each vendor selected here, ready to capture their prices.')
    notes = fields.Html('RFQ Notes')
    currency_id = fields.Many2one(
        'res.currency', default=lambda self: self.env.company.currency_id,
    )
    line_count = fields.Integer('Lines', compute='_compute_totals')
    request_count = fields.Integer('Requests', compute='_compute_totals')
    est_total = fields.Monetary(
        'Estimated Total', compute='_compute_totals', currency_field='currency_id',
    )
    # ── Terms & Conditions ────────────────────────────────────────────────────
    terms_template_id = fields.Many2one(
        'ebshel.rfq.terms', string='T&C Template',
        default=lambda self: self.env.company.rfq_default_terms_id,
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",
        help='Pick a Terms & Conditions template to apply to the new RFQ.')
    terms_conditions = fields.Html(
        'Terms & Conditions', compute='_compute_terms_conditions',
        store=True, readonly=False,
        help='Carried to the RFQ; shown to vendors on the email and PDF.')

    @api.depends('terms_template_id')
    def _compute_terms_conditions(self):
        for wiz in self:
            if wiz.terms_template_id:
                wiz.terms_conditions = wiz.terms_template_id.terms

    @api.depends('wizard_line_ids.selected', 'wizard_line_ids.qty',
                 'wizard_line_ids.price_unit', 'request_ids')
    def _compute_totals(self):
        for wiz in self:
            sel = wiz.wizard_line_ids.filtered(lambda l: l.selected and l.qty > 0)
            wiz.line_count = len(sel)
            wiz.request_count = len(wiz.request_ids)
            wiz.est_total = sum(sel.mapped(lambda l: l.qty * l.price_unit))

    @api.onchange('request_ids')
    def _onchange_request_ids(self):
        """(Re)build the line grid from the eligible lines of the chosen
        requests — only lines with quantity still left to source."""
        eligible = self.request_ids.line_ids.filtered(
            lambda l: not l.cancelled and l.qty_to_rfq > 0)
        self.wizard_line_ids = [(5, 0, 0)] + [(0, 0, {
            'request_line_id': line.id,
            'qty': line.qty_to_rfq,
            'selected': True,
        }) for line in eligible]

    def action_create_rfq(self):
        self.ensure_one()
        sel = self.wizard_line_ids.filtered(lambda l: l.selected and l.qty > 0)
        if not sel:
            raise UserError(_('Select at least one line with a quantity to source.'))
        # Quantity guard — never RFQ more than what is left on the PR line.
        for wl in sel:
            if wl.qty > wl.remaining_qty + 1e-6:
                raise UserError(_(
                    'Line "%(p)s": quantity to RFQ (%(q)s) exceeds the remaining '
                    '%(r)s. Reduce it before creating the RFQ.',
                    p=wl.product_id.display_name, q=wl.qty, r=wl.remaining_qty))

        rfq = self.env['ebshel.purchase.rfq'].create({
            'procurement_officer_id': self.env.user.id,
            'deadline': self.deadline,
            'notes': self.notes,
            'terms_template_id': self.terms_template_id.id or False,
            'terms_conditions': self.terms_conditions or False,
            'rfq_line_ids': [(0, 0, {
                'request_line_id': wl.request_line_id.id,
                'product_id': wl.product_id.id,
                'name': wl.request_line_id.name,
                'qty': wl.qty,
                'uom_id': wl.uom_id.id,
                'date_required': wl.request_line_id.date_required,
            }) for wl in sel],
        })
        # Pre-create a draft vendor quotation per selected vendor, with the RFQ
        # lines loaded so the buyer just needs to fill in each vendor's prices.
        if self.vendor_ids:
            Quotation = self.env['ebshel.purchase.vendor.quotation']
            for vendor in self.vendor_ids:
                Quotation.create({
                    'rfq_id': rfq.id,
                    'vendor_id': vendor.id,
                    'line_ids': [(0, 0, {
                        'rfq_line_id': rl.id,
                        'product_id': rl.product_id.id,
                        'name': rl.name,
                        'qty': rl.qty,
                        'uom_id': rl.uom_id.id,
                    }) for rl in rfq.rfq_line_ids],
                })

        for request in sel.mapped('request_line_id.request_id'):
            request.message_post(
                body=Markup(_('RFQ <a href="#" data-oe-model="ebshel.purchase.rfq" data-oe-id="%d">%s</a> created from this request.')) % (rfq.id, rfq.name),
                subtype_xmlid='mail.mt_note',
            )
        return {
            'name': _('RFQ %s', rfq.name),
            'type': 'ir.actions.act_window',
            'res_model': 'ebshel.purchase.rfq',
            'view_mode': 'form',
            'res_id': rfq.id,
        }


class PrCreateRfqWizardLine(models.TransientModel):
    _name = 'ebshel.pr.create.rfq.wizard.line'
    _description = 'Create RFQ Wizard Line'

    wizard_id = fields.Many2one(
        'ebshel.pr.create.rfq.wizard', required=True, ondelete='cascade')
    request_line_id = fields.Many2one(
        'ebshel.purchase.request.line', string='Request Line', required=True)
    request_id = fields.Many2one(related='request_line_id.request_id', string='Request')
    product_id = fields.Many2one(related='request_line_id.product_id')
    uom_id = fields.Many2one(related='request_line_id.uom_id')
    currency_id = fields.Many2one(related='wizard_id.currency_id')
    requested_qty = fields.Float(related='request_line_id.qty', string='Requested')
    in_rfq_qty = fields.Float(related='request_line_id.qty_in_rfq', string='Already in RFQ')
    remaining_qty = fields.Float(related='request_line_id.qty_to_rfq', string='Remaining')
    price_unit = fields.Monetary(related='request_line_id.price_unit',
                                 string='Est. Unit Price', currency_field='currency_id')
    date_required = fields.Date(related='request_line_id.date_required')
    selected = fields.Boolean('Include', default=True)
    qty = fields.Float('Qty to RFQ', digits='Product Unit')

    @api.onchange('qty', 'remaining_qty')
    def _onchange_qty(self):
        for wl in self:
            if wl.qty > wl.remaining_qty:
                wl.qty = wl.remaining_qty
                return {'warning': {
                    'title': _('Quantity capped'),
                    'message': _('You can source at most the remaining %s.',
                                 wl.remaining_qty),
                }}
