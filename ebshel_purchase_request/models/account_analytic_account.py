# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models, _

class AccountAnalyticAccount(models.Model):
    _inherit = 'account.analytic.account'

    is_cost_center = fields.Boolean(
        'Department / Cost Center',
        help='When enabled, this analytic account can be selected as a '
             'department / cost center on purchase requests and is tracked for '
             'budget commitments.')

    request_count = fields.Integer(
        'Purchase Requests', compute='_compute_request_count')

    def _compute_request_count(self):
        # One grouped query for the whole recordset instead of a search_count per
        # account (was N+1 when the field appears in list views).
        groups = self.env['ebshel.purchase.request']._read_group(
            [('analytic_account_id', 'in', self.ids)],
            ['analytic_account_id'], ['__count'])
        counts = {account.id: count for account, count in groups}
        for account in self:
            account.request_count = counts.get(account.id, 0)

    def action_view_requests(self):
        """Smart-button: purchase requests booked to this department / cost center."""
        self.ensure_one()
        return {
            'name': _('Purchase Requests'),
            'type': 'ir.actions.act_window',
            'res_model': 'ebshel.purchase.request',
            'view_mode': 'list,form',
            'domain': [('analytic_account_id', '=', self.id)],
            'context': {'default_analytic_account_id': self.id},
        }
