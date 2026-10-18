# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.
from odoo import fields, models

class ResCompany(models.Model):
    _inherit = 'res.company'

    # ── RFQ Terms & Conditions ────────────────────────────────────────────────
    rfq_default_terms_id = fields.Many2one(
        'ebshel.rfq.terms', string='Default RFQ Terms & Conditions',
        domain="['|', ('company_id', '=', False), ('company_id', '=', id)]",
        help='Terms & Conditions template pre-filled on every new RFQ for this '
             'company. Pick one from the Terms & Conditions master.')

    # ── Purchase-Request approval policy ──────────────────────────────────────
    pr_auto_approve_enabled = fields.Boolean(
        'Enable Auto-Approval', default=False,
        help='When enabled, purchase requests at or below the threshold are '
             'approved automatically on submission.')
    pr_auto_approve_amount = fields.Monetary(
        'Auto-Approve Below', currency_field='currency_id', default=0.0)

    pr_allow_self_approval = fields.Boolean(
        'Allow Self-Approval', default=False,
        help='If enabled, a requester listed as an approver may approve their '
             'own purchase request.')

    pr_escalation_enabled = fields.Boolean(
        'Enable Approval Escalation', default=False,
        help='When enabled, each approval to-do gets a due date so reminders / '
             'escalation can fire.')
    pr_escalation_days = fields.Integer(
        'Approval Escalation (days)', default=2)

    # ── Direct-PO governance (native Purchase "back door") ────────────────────
    pr_po_enforcement = fields.Selection(
        selection=[
            ('open', 'Open — allow direct purchase orders'),
            ('warn', 'Warn — flag & notify on off-process orders'),
            ('strict', 'Strict — block off-process orders'),
        ],
        string='Direct Purchase Order Policy', default='warn', required=True,
        help='Governs purchase orders raised straight in the Purchase app, '
             'outside the request → RFQ → award flow:\n'
             '• Open: no restriction (standard Odoo behaviour).\n'
             '• Warn: such orders are allowed but flagged as "Off-process" and '
             'the Purchase-Request administrators are notified on confirmation.\n'
             '• Strict: confirming such an order is blocked — only a Purchase '
             'Manager may override.')

    # ── PO award approval (higher authority signs off before PO creation) ──────
    pr_require_po_approval = fields.Boolean(
        'Require PO Award Approval', default=True,
        help='When enabled, awarding a vendor from an RFQ creates an award '
             'proposal that a higher authority (Administrator) must approve '
             'before the purchase order(s) are created.')

    # ── Quantity editing on approved requests ─────────────────────────────────
    pr_allow_approved_qty_edit = fields.Boolean(
        'Allow Quantity Update on Approved Requests', default=False,
        help='When enabled, an "Update Quantities" action lets users edit line '
             'quantities on an already-approved purchase request — e.g. to drop '
             'the quantity that is no longer needed after a partial RFQ. ')
    pr_qty_edit_reapproval = fields.Boolean(
        'Re-Approve After Quantity Change', default=False,
        help='When enabled, editing the quantities of an approved request sends '
             'it back through the approval workflow from the first applicable '
             'level. When disabled, the edit is applied in place and the request '
             'stays approved.')

    # ── RFQ line quantity increase (over the requested amount) ────────────────
    rfq_allow_qty_increase = fields.Boolean(
        'Allow RFQ Quantity Above Request', default=False,
        help='When enabled, an RFQ line quantity may exceed the quantity on its '
             'source purchase-request line. When disabled, the quantity is '
             'capped at the requested amount.')
    rfq_qty_increase_approval = fields.Boolean(
        'Require Approval for RFQ Quantity Increase', default=False,
        help='When enabled, an RFQ whose line quantity exceeds the requested '
             'amount must be approved by an Administrator before it can be sent '
             'to vendors.')

    # ── Product change during approval ─────────────────────────────────────────
    pr_allow_product_change = fields.Boolean(
        'Allow Product Change During Approval', default=False,
        help='When enabled, an approver reviewing a submitted request may, on '
             'lines the requester flagged "Allow Product Change", swap the '
             'product for another in the same category — or return the request '
             'to the requester with a suggested product. Disabled = no product '
             'changes are possible.')
    

    quotation_pdt_alter = fields.Boolean(
        'Allow Product Change at Quotation and RFQ', default=False,
        help='When Enabled, Allow to alter the product at quotation,'
             'But the product must belong to the same category, '
             'accordingly allow to alter the product at RFQ also.' )
    
    pr_for_amount_exceed_budget = fields.Boolean(
        'Allow to create even if the department budget exceeded', default=False,
        help='When enabled, Allow to create PR for a department, '
             'even if the department budget exceeded.')
