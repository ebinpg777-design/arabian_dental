# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


class PurchaseAnalyticLine(models.Model):
    """Purchase encumbrance ledger — the purchasing analogue of
    ``account.analytic.line``.

    Standard Odoo budgeting only sees *confirmed* purchase orders (committed)
    and *posted* bills (achieved). Purchase requests, and the gap between
    parallel in-flight documents, are invisible — so two requests can each pass
    the budget check yet together overshoot.

    This append-only, signed ledger closes that gap. Each lifecycle event writes
    a balancing *delta* so the running total for an analytic account is always
    ``SUM(amount)`` — a single aggregate query, regardless of history:

      * PR submitted        -> ``+amount`` per analytic account (type ``request``)
      * PR line -> PO        -> the request entries rebalance to 0 (go negative)
      * PO created / linked  -> ``+amount`` per analytic account (type ``order``)
      * Vendor bill posted   -> the order entries rebalance down (go negative)

    The split feeds the budget line columns: net ``request`` = *Purchased*,
    net ``order`` = *Invoiced*, and their sum = *Committed*.
    """
    _name = 'purchase.analytic.line'
    _description = 'Purchase Analytic Line (Encumbrance)'
    _order = 'date desc, id desc'

    name = fields.Char('Reference', default='/', copy=False)
    date = fields.Date('Date', required=True, index=True)
    company_id = fields.Many2one(
        'res.company', 'Company', required=True, index=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True)
    analytic_account_id = fields.Many2one(
        'account.analytic.account', 'Department / Cost Center',
        required=True, index=True, ondelete='cascade')
    amount = fields.Monetary(
        'Amount', currency_field='currency_id',
        help='Signed commitment delta. Positive reserves budget, negative '
             'releases it. The net of all entries is the live encumbrance.')
    commitment_type = fields.Selection([
        ('request', 'Purchase Request'),
        ('order', 'Purchase Order'),
    ], string='Source', required=True, index=True)

    request_id = fields.Many2one(
        'ebshel.purchase.request', 'Purchase Request', index=True, ondelete='cascade')
    request_line_id = fields.Many2one(
        'ebshel.purchase.request.line', 'Request Line', index=True, ondelete='cascade')
    order_id = fields.Many2one(
        'purchase.order', 'Purchase Order', index=True, ondelete='cascade')
    order_line_id = fields.Many2one(
        'purchase.order.line', 'Order Line', index=True, ondelete='cascade')
    note = fields.Char('Note')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', '/') == '/':
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'purchase.analytic.line') or '/'
        return super().create(vals_list)

    # ── Aggregation ───────────────────────────────────────────────────────────
    @api.model
    def _open_amount(self, analytic_account_id, company_id, date_from, date_to,
                     exclude_request_ids=()):
        """Net open encumbrance for an analytic account within a budget period.

        One aggregate query — O(1) in the number of ledger rows. Reversed
        (rebalanced) entries net to zero, so the sum is the live commitment.
        ``exclude_request_ids`` drops a request's own request-type entries so a
        record never blocks itself (e.g. on re-submit / for available-after).
        """
        amounts = self._amounts_by_type(
            analytic_account_id, company_id, date_from, date_to,
            exclude_request_ids=exclude_request_ids)
        return amounts['request'] + amounts['order']

    @api.model
    def _amounts_by_type(self, analytic_account_id, company_id, date_from, date_to,
                         exclude_request_ids=()):
        """{'request': net, 'order': net} for an analytic account and period."""
        res = {'request': 0.0, 'order': 0.0}
        if not analytic_account_id or not date_from or not date_to:
            return res
        domain = [
            ('analytic_account_id', '=', analytic_account_id),
            ('company_id', '=', company_id),
            ('date', '>=', date_from),
            ('date', '<=', date_to),
        ]
        if exclude_request_ids:
            domain += ['|', ('commitment_type', '!=', 'request'),
                       ('request_id', 'not in', list(exclude_request_ids))]
        for ctype, total in self._read_group(
                domain, ['commitment_type'], ['amount:sum']):
            res[ctype] = total or 0.0
        return res

    @api.model
    def _budget_figures(self, analytic_account_id, company_id, ref_date,
                        exclude_request_ids=()):
        """Budget snapshot for an analytic account on ``ref_date``.

        Availability is driven by this ledger: ``budget - committed``, where
        committed = net purchased (PRs) + net invoiced (POs).
        """
        res = {
            'has_budget': False, 'budget': 0.0, 'purchased': 0.0,
            'invoiced': 0.0, 'committed': 0.0, 'achieved': 0.0,
            'available': 0.0, 'date_from': None, 'date_to': None,
        }
        if 'budget.line' not in self.env or not analytic_account_id:
            return res
        lines = self.env['budget.line'].sudo().search([
            ('auto_account_id', '=', analytic_account_id),
            ('company_id', '=', company_id),
            ('budget_analytic_state', '=', 'confirmed'),
            ('date_from', '<=', ref_date),
            ('date_to', '>=', ref_date),
        ])
        if not lines:
            return res
        budget = sum(lines.mapped('budget_amount'))
        # Realized spend from posted bills (standard budgeting). Order
        # commitments release as they are billed, so this picks up where the
        # ledger leaves off — together they never double count.
        achieved = sum(lines.mapped('achieved_amount'))
        date_from = min(lines.mapped('date_from'))
        date_to = max(lines.mapped('date_to'))
        amounts = self._amounts_by_type(
            analytic_account_id, company_id, date_from, date_to,
            exclude_request_ids=exclude_request_ids)
        purchased, invoiced = amounts['request'], amounts['order']
        committed = purchased + invoiced
        res.update(
            has_budget=bool(budget or committed or achieved),
            budget=budget, purchased=purchased, invoiced=invoiced,
            committed=committed, achieved=achieved,
            available=budget - achieved - committed,
            date_from=date_from, date_to=date_to,
        )
        return res

    # ── Reconcile-to-target writers ───────────────────────────────────────────
    @api.model
    def _sync_request_lines(self, pr_lines):
        """Rebalance request-type encumbrance for the given PR lines.

        One ledger entry per analytic account in the line's distribution — the
        purchasing analogue of how ``account.analytic.line`` records split a
        move line across analytic accounts.
        """
        vals_list = []
        # Pre-fetch all existing request-type entries for these lines in ONE
        # query, then look them up in memory (was one search per line → N+1).
        existing_by_line = {}
        for entry in self.search([
            ('request_line_id', 'in', pr_lines.ids),
            ('commitment_type', '=', 'request'),
        ]):
            existing_by_line.setdefault(entry.request_line_id.id, []).append(entry)
        for line in pr_lines:
            distribution = line._budget_commitment_distribution()
            total = line._budget_commitment_target()
            current = {}
            for entry in existing_by_line.get(line.id, []):
                acc = entry.analytic_account_id.id
                current[acc] = current.get(acc, 0.0) + entry.amount
            for acc_id in set(distribution) | set(current):
                delta = total * distribution.get(acc_id, 0.0) - current.get(acc_id, 0.0)
                if line.company_id.currency_id.is_zero(delta):
                    continue
                vals_list.append(line._budget_commitment_vals(acc_id, delta))
        if vals_list:
            self.create(vals_list)

    @api.model
    def _sync_order_lines(self, po_lines):
        """Rebalance order-type encumbrance for the given PO lines.

        Honours the analytic distribution (falling back to the originating PR
        line's department) and the un-invoiced portion, so partial bills release
        the commitment proportionally.
        """
        vals_list = []
        # Pre-fetch all existing order-type entries for these lines in ONE
        # query, then look them up in memory (was one search per line → N+1).
        existing_by_line = {}
        for entry in self.search([
            ('order_line_id', 'in', po_lines.ids),
            ('commitment_type', '=', 'order'),
        ]):
            existing_by_line.setdefault(entry.order_line_id.id, []).append(entry)
        for line in po_lines:
            distribution = line._budget_commitment_distribution()
            total = line._budget_commitment_target()
            current = {}
            for entry in existing_by_line.get(line.id, []):
                acc = entry.analytic_account_id.id
                current[acc] = current.get(acc, 0.0) + entry.amount
            for acc_id in set(distribution) | set(current):
                delta = total * distribution.get(acc_id, 0.0) - current.get(acc_id, 0.0)
                if line.company_id.currency_id.is_zero(delta):
                    continue
                vals_list.append(line._budget_commitment_vals(acc_id, delta))
        if vals_list:
            self.create(vals_list)
