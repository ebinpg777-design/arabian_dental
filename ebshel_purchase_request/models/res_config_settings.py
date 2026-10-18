# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.
from odoo import fields, models

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    rfq_default_terms_id = fields.Many2one(
        related='company_id.rfq_default_terms_id', readonly=False)
    pr_auto_approve_enabled = fields.Boolean(
        related='company_id.pr_auto_approve_enabled', readonly=False)
    pr_auto_approve_amount = fields.Monetary(
        related='company_id.pr_auto_approve_amount', readonly=False,
        currency_field='company_currency_id')
    pr_allow_self_approval = fields.Boolean(
        related='company_id.pr_allow_self_approval', readonly=False)
    pr_escalation_enabled = fields.Boolean(
        related='company_id.pr_escalation_enabled', readonly=False)
    pr_escalation_days = fields.Integer(
        related='company_id.pr_escalation_days', readonly=False)
    pr_po_enforcement = fields.Selection(
        related='company_id.pr_po_enforcement', readonly=False)
    pr_require_po_approval = fields.Boolean(
        related='company_id.pr_require_po_approval', readonly=False)
    pr_allow_approved_qty_edit = fields.Boolean(
        related='company_id.pr_allow_approved_qty_edit', readonly=False)
    pr_qty_edit_reapproval = fields.Boolean(
        related='company_id.pr_qty_edit_reapproval', readonly=False)
    pr_allow_product_change = fields.Boolean(
        related='company_id.pr_allow_product_change', readonly=False)
    rfq_allow_qty_increase = fields.Boolean(
        related='company_id.rfq_allow_qty_increase', readonly=False)
    rfq_qty_increase_approval = fields.Boolean(
        related='company_id.rfq_qty_increase_approval', readonly=False)
    quotation_pdt_alter = fields.Boolean(
        related="company_id.quotation_pdt_alter", readonly=False)
    pr_for_amount_exceed_budget = fields.Boolean(related="company_id.pr_for_amount_exceed_budget", readonly=False)
    company_currency_id = fields.Many2one(
        related='company_id.currency_id', readonly=True)

    def action_open_rfq_terms(self):
        return {
            'name': 'RFQ Terms & Conditions',
            'type': 'ir.actions.act_window',
            'res_model': 'ebshel.rfq.terms',
            'view_mode': 'list,form',
        }
