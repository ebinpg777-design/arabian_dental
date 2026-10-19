# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class PrSplitAwardWizard(models.TransientModel):
    """Assign each RFQ line to a vendor and generate one PO per vendor in a
    single confirmation — the split-award workbench."""
    _name = 'ebshel.pr.split.award.wizard'
    _description = 'Split Award – Generate Multiple POs'

    rfq_id = fields.Many2one(
        'ebshel.purchase.rfq', string='RFQ', required=True, ondelete='cascade')
    currency_id = fields.Many2one(related='rfq_id.currency_id', readonly=True)
    line_ids = fields.One2many(
        'ebshel.pr.split.award.line', 'wizard_id', string='Award Lines')

    # ── Live summary ──────────────────────────────────────────────────────────
    po_count = fields.Integer('Purchase Orders', compute='_compute_summary')
    awarded_count = fields.Integer('Lines Awarded', compute='_compute_summary')
    skipped_count = fields.Integer('Lines Skipped', compute='_compute_summary')
    est_total = fields.Monetary(
        'Estimated Total', compute='_compute_summary', currency_field='currency_id')
    summary_html = fields.Html('Summary', compute='_compute_summary', sanitize=False)

    @api.depends('line_ids.award', 'line_ids.quotation_id', 'line_ids.subtotal',
                 'line_ids.awarded_qty')
    def _compute_summary(self):
        for wiz in self:
            awarded = wiz.line_ids.filtered(lambda l: l.award and l.quotation_id)
            vendors = awarded.mapped('quotation_id')
            wiz.awarded_count = len(awarded)
            wiz.skipped_count = len(wiz.line_ids) - len(awarded)
            wiz.po_count = len(vendors)
            wiz.est_total = sum(awarded.mapped('subtotal'))
            # Per-vendor mini breakdown
            rows = []
            for q in vendors:
                v_lines = awarded.filtered(lambda l: l.quotation_id == q)
                total = sum(v_lines.mapped('subtotal'))
                rows.append(
                    '<li><b>%s</b> — %d line(s), %s</li>' % (
                        q.display_name, len(v_lines),
                        '{:,.2f}'.format(total)))
            if rows:
                wiz.summary_html = (
                    '<div>This award will create <b>%d purchase order(s)</b> '
                    'for <b>%d vendor(s)</b>:</div><ul>%s</ul>' % (
                        len(vendors), len(vendors), ''.join(rows)))
            else:
                wiz.summary_html = (
                    '<div class="text-muted">No lines awarded yet — pick a '
                    'vendor on each line you want to order.</div>')

    def action_generate_pos(self):
        self.ensure_one()
        awarded = self.line_ids.filtered(
            lambda l: l.award and l.quotation_id and l.awarded_qty > 0)
        if not awarded:
            raise UserError(_('Award at least one line to a vendor before generating POs.'))
        for l in awarded:
            if l.awarded_qty > l.requested_qty + 1e-6:
                raise UserError(_(
                    'Line "%(p)s": awarded qty %(a)s exceeds the requested %(r)s.',
                    p=l.product_id.display_name, a=l.awarded_qty, r=l.requested_qty))
            if l.quotation_id not in l.available_quotation_ids:
                raise UserError(_(
                    'Line "%(p)s": %(v)s did not quote this line — pick a vendor '
                    'who did.', p=l.product_id.display_name,
                    v=l.quotation_id.display_name))
        awards = [{
            'quotation': l.quotation_id,
            'rfq_line': l.rfq_line_id,
            'qty': l.awarded_qty,
        } for l in awarded]
        # Route through approval when the company requires it.
        if self.rfq_id._award_needs_approval():
            self.rfq_id._propose_award_split(awards)
            return self.rfq_id._award_pending_notification()
        pos = self.rfq_id._create_split_purchase_orders(awards)
        return {
            'name': _('Purchase Orders – %s', self.rfq_id.name),
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'view_mode': 'list,form' if len(pos) > 1 else 'form',
            'res_id': pos.id if len(pos) == 1 else False,
            'domain': [('id', 'in', pos.ids)],
        }


class PrSplitAwardLine(models.TransientModel):
    _name = 'ebshel.pr.split.award.line'
    _description = 'Split Award Line'
    _order = 'id'

    wizard_id = fields.Many2one(
        'ebshel.pr.split.award.wizard', required=True, ondelete='cascade')
    rfq_line_id = fields.Many2one(
        'ebshel.purchase.rfq.line', string='RFQ Line', required=True)
    product_id = fields.Many2one(related='rfq_line_id.product_id', readonly=True)
    requested_qty = fields.Float(
        related='rfq_line_id.qty', string='Requested', readonly=True)
    uom_id = fields.Many2one(related='rfq_line_id.uom_id', readonly=True)
    currency_id = fields.Many2one(related='wizard_id.currency_id', readonly=True)

    award = fields.Boolean('Award', default=True)
    available_quotation_ids = fields.Many2many(
        'ebshel.purchase.vendor.quotation', compute='_compute_available')
    quotation_id = fields.Many2one(
        'ebshel.purchase.vendor.quotation', string='Awarded Vendor')
    awarded_qty = fields.Float('Award Qty', digits='Product Unit')
    price_unit = fields.Float(
        'Unit Price', compute='_compute_price', digits='Product Price')
    delivery_days = fields.Integer('Lead (days)', compute='_compute_price')
    is_best = fields.Boolean('Best Price', compute='_compute_price')
    subtotal = fields.Monetary(
        'Subtotal', compute='_compute_price', currency_field='currency_id')

    @api.depends('rfq_line_id')
    def _compute_available(self):
        QL = self.env['ebshel.purchase.vendor.quotation.line']
        Quotation = self.env['ebshel.purchase.vendor.quotation']
        # One search for every wizard line instead of one per line (N+1).
        quos_by_line = {}
        rfq_line_ids = self.rfq_line_id.ids
        if rfq_line_ids:
            qls = QL.search([
                ('rfq_line_id', 'in', rfq_line_ids),
                ('price_unit', '>', 0),
                ('quotation_id.state', 'in', ('received', 'selected')),
            ])
            for ql in qls:
                quos_by_line.setdefault(ql.rfq_line_id.id, Quotation)
                quos_by_line[ql.rfq_line_id.id] |= ql.quotation_id
        for line in self:
            line.available_quotation_ids = quos_by_line.get(line.rfq_line_id.id, Quotation)

    @api.depends('quotation_id', 'rfq_line_id', 'awarded_qty')
    def _compute_price(self):
        for line in self:
            ql = line.quotation_id.line_ids.filtered(
                lambda x: x.rfq_line_id == line.rfq_line_id)[:1]
            net = ql.price_unit * (1 - (ql.discount or 0) / 100) if ql else 0.0
            line.price_unit = net
            line.delivery_days = line.quotation_id.delivery_days or 0
            line.subtotal = net * (line.awarded_qty or 0)
            # Best price across candidates for this RFQ line
            prices = line.available_quotation_ids.mapped('line_ids').filtered(
                lambda x: x.rfq_line_id == line.rfq_line_id and x.price_unit > 0
            ).mapped('price_unit')
            line.is_best = bool(ql) and prices and net <= min(prices) + 1e-6

    @api.onchange('quotation_id')
    def _onchange_quotation(self):
        for line in self:
            line.award = bool(line.quotation_id)

    @api.onchange('awarded_qty', 'requested_qty')
    def _onchange_awarded_qty(self):
        for line in self:
            if line.awarded_qty > line.requested_qty:
                line.awarded_qty = line.requested_qty
                return {'warning': {
                    'title': _('Quantity capped'),
                    'message': _('You cannot award more than the requested %s.',
                                 line.requested_qty),
                }}
