# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


class BudgetLine(models.Model):
    _inherit = 'budget.line'

    # Encumbrance surfaced from purchase.analytic.line for this budget's analytic
    # account and period. "Purchased" = open purchase requests, "Invoiced" = open
    # purchase orders (un-billed), "Committed" = their sum (what is reserved
    # against the budget before it is realised as a bill).
    purchased_amount = fields.Monetary(
        'Purchased', compute='_compute_commitment',
        currency_field='currency_id',
        help='Open purchase-request commitments booked to this budget.')
    invoiced_amount = fields.Monetary(
        'Invoiced', compute='_compute_commitment',
        currency_field='currency_id',
        help='Open purchase-order commitments booked to this budget.')
    total_amount = fields.Monetary(
        'Total', compute='_compute_commitment',
        currency_field='currency_id',
        help='Total reserved against this budget = Purchased + Invoiced.')

    @api.depends('account_id', 'company_id', 'date_from', 'date_to')
    def _compute_commitment(self):
        Line = self.env['purchase.analytic.line']
        # `auto_account_id` only resolves when an analytic plan is in the
        # context, so it reads as False here. Match on the actual analytic
        # account stored on each plan column instead (account_id for the default
        # plan, x_plan<id>_id for others).
        project_plan, other_plans = self.env['account.analytic.plan']._get_all_plans()
        columns = [p._column_name() for p in (project_plan + other_plans)]
        for line in self:
            purchased = invoiced = 0.0
            account_ids = {
                line[col].id for col in columns
                if col in line._fields and line[col]
            }
            for account_id in account_ids:
                amounts = Line._amounts_by_type(
                    account_id, line.company_id.id, line.date_from, line.date_to)
                purchased += amounts['request']
                invoiced += amounts['order']
            line.purchased_amount = purchased
            line.invoiced_amount = invoiced
            line.total_amount = purchased + invoiced
