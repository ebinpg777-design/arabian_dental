# -*- coding: utf-8 -*-
from odoo import api, fields, models, _


class ResPartner(models.Model):
    """Credit limit enforcement (gap 18).

    v19 ships ``credit_limit`` plus a soft on-screen warning. What is missing is
    a decision: what should actually happen at confirmation. That decision is a
    per-clinic policy here, defaulting to the company setting, because a lab
    typically blocks a handful of chronic defaulters while only warning on the rest.
    """
    _inherit = 'res.partner'

    credit_limit_policy = fields.Selection(
        [('company', 'Company Default'), ('none', 'No Check'),
         ('warn', 'Warn Only'), ('block', 'Block Order')],
        default='company', string='Over-limit Action',
        company_dependent=True,
        help="What happens when confirming an order would take this clinic past its "
             "credit limit.")
    credit_exposure = fields.Monetary(
        'Credit Exposure', compute='_compute_credit_exposure',
        currency_field='currency_id',
        help="Posted receivables plus confirmed orders not yet invoiced.")
    credit_available = fields.Monetary(
        'Credit Available', compute='_compute_credit_exposure', currency_field='currency_id')
    credit_uncovered_order_amount = fields.Monetary(
        'Orders Not Yet Invoiced', compute='_compute_credit_exposure',
        currency_field='currency_id')

    @api.depends('credit', 'credit_limit')
    def _compute_credit_exposure(self):
        """Receivables alone understate the risk: a confirmed case sitting in the
        lab is work already committed against that clinic's limit."""
        uncovered = {}
        if self.ids:
            orders = self.env['sale.order'].search([
                ('partner_id', 'in', self.ids),
                ('state', '=', 'sale'),
                ('invoice_status', '!=', 'invoiced'),
            ])
            for order in orders:
                # Taxed against taxed: `amount_invoiced` is the posted invoices' total.
                # Subtracting qty_invoiced x price_unit (untaxed) counted the tax on
                # billed work twice. Not `amount_to_invoice`: on a delivery-invoiced
                # product - nearly all of this lab's - it leaves out work confirmed but
                # not yet delivered, which is exactly the commitment counted here.
                uncovered[order.partner_id.id] = uncovered.get(order.partner_id.id, 0.0) \
                    + max(0.0, order.amount_total - order.amount_invoiced)
        for partner in self:
            open_orders = uncovered.get(partner.id, 0.0)
            partner.credit_uncovered_order_amount = open_orders
            partner.credit_exposure = (partner.credit or 0.0) + open_orders
            partner.credit_available = (partner.credit_limit or 0.0) - partner.credit_exposure

    def _get_credit_limit_policy(self):
        """Resolve 'Company Default' to the company-wide setting."""
        self.ensure_one()
        policy = self.credit_limit_policy
        if policy and policy != 'company':
            return policy
        return self.env['ir.config_parameter'].sudo().get_param(
            'lab_order_control.credit_limit_policy', 'warn')
