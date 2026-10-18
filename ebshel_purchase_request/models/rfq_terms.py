# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.
from odoo import api, fields, models

class RfqTerms(models.Model):
    """Reusable master of RFQ / purchase Terms & Conditions templates."""
    _name = 'ebshel.rfq.terms'
    _description = 'RFQ Terms & Conditions Template'
    _order = 'sequence, name'

    name = fields.Char('Title', required=True, translate=True)
    code = fields.Char('Reference Code', copy=False,
                       help='Short internal code, e.g. STD-2026.')
    sequence = fields.Integer('Sequence', default=10)
    active = fields.Boolean('Active', default=True)
    company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company,
        help='Leave empty to make this template available to all companies.')
    currency_id = fields.Many2one(related='company_id.currency_id', readonly=True)

    # ── Commercial terms (structured) ─────────────────────────────────────────
    payment_term_id = fields.Many2one('account.payment.term', string='Payment Terms')
    incoterm_id = fields.Many2one('account.incoterms', string='Incoterm')
    validity_days = fields.Integer(
        'Quotation Validity (days)', default=30,
        help='How long vendors should keep their quoted prices valid.')
    delivery_lead_days = fields.Integer('Expected Delivery (days)')
    warranty = fields.Char('Warranty', help='e.g. 12 months parts & labour.')
    penalty = fields.Char('Late-Delivery Penalty',
                          help='e.g. 0.5% of order value per week, capped at 5%.')

    # ── Narrative ─────────────────────────────────────────────────────────────
    description = fields.Char('Summary', help='One-line description for the list.')
    terms = fields.Html('Terms & Conditions', translate=True, sanitize=False,
                        help='Full T&C body shown to vendors on the RFQ.')
    note = fields.Text('Internal Note', help='Not shared with vendors.')

    # ── Usage stat ────────────────────────────────────────────────────────────
    rfq_count = fields.Integer('RFQs Using This', compute='_compute_rfq_count')

    def _compute_rfq_count(self):
        groups = self.env['ebshel.purchase.rfq']._read_group(
            [('terms_template_id', 'in', self.ids)],
            ['terms_template_id'], ['__count'])
        counts = {t.id: n for t, n in groups}
        for rec in self:
            rec.rfq_count = counts.get(rec.id, 0)

    @api.depends('name', 'code')
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f'[{rec.code}] {rec.name}' if rec.code else rec.name

    def action_view_rfqs(self):
        self.ensure_one()
        return {
            'name': 'RFQs',
            'type': 'ir.actions.act_window',
            'res_model': 'ebshel.purchase.rfq',
            'view_mode': 'list,form',
            'domain': [('terms_template_id', '=', self.id)],
        }
