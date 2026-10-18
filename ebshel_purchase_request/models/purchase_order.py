# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.

from markupsafe import Markup
from odoo import api, fields, models, _
from odoo.exceptions import UserError

class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    request_line_ids = fields.Many2many(
        'ebshel.purchase.request.line',
        'pr_line_po_line_rel', 'po_line_id', 'request_line_id',
        string='Request Lines', copy=False,
    )
    allocation_ids = fields.One2many(
        'ebshel.purchase.request.allocation', 'purchase_line_id',
        string='Request Allocations', copy=False,
    )

    def _prepare_stock_moves(self, picking):
        """Attach our request allocations to every generated inbound move so
        receipts can be traced back to the originating request line."""
        res = super()._prepare_stock_moves(picking)
        allocations = self.allocation_ids
        if allocations:
            alloc_commands = [(4, a.id) for a in allocations]
            for move_vals in res:
                move_vals['allocation_ids'] = list(alloc_commands)
        return res

    def _update_service_allocations(self, received_delta):
        """Services generate no stock move — allocate received qty directly."""
        self.ensure_one()
        remaining = received_delta
        for alloc in self.allocation_ids:
            if remaining <= 0:
                break
            take = min(alloc.open_product_qty, remaining)
            if take <= 0:
                continue
            alloc.allocated_product_qty += take
            alloc._notify_allocation(take)
            remaining -= take

    def write(self, vals):
        track_service = 'qty_received' in vals
        before = {}
        if track_service:
            for line in self:
                before[line.id] = line.qty_received
        res = super().write(vals)
        if track_service:
            for line in self:
                if line.product_id.type == 'service':
                    delta = line.qty_received - before.get(line.id, 0.0)
                    if delta > 0:
                        line._update_service_allocations(delta)
        return res

    def unlink(self):
        # A PO line sourced from a purchase request must not be deleted once the
        # order is confirmed — doing so would orphan the request allocation and
        # the fulfilment tracking. Cancel the order or reduce the quantity instead.
        if not self.env.context.get('ebshel_bypass_line_guard'):
            locked = self.filtered(
                lambda l: l.request_line_ids and l.order_id.state in ('purchase', 'done'))
            if locked:
                raise UserError(_(
                    'This purchase order line originates from a purchase request '
                    'and the order is confirmed, so it cannot be deleted (it would '
                    'orphan the request allocation). Cancel the order or reduce the '
                    'quantity instead. Affected order(s): %s',
                    ', '.join(sorted(set(locked.mapped('order_id.name'))))))
        return super().unlink()

    def action_open_request_lines(self):
        self.ensure_one()
        return {
            'name': _('Source Request Lines'),
            'type': 'ir.actions.act_window',
            'res_model': 'ebshel.purchase.request.line',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.request_line_ids.ids)],
        }

    # ── Budget encumbrance (see purchase.analytic.line) ───────────────────────
    def _budget_commitment_distribution(self):
        """{analytic_account_id: fraction} this PO line reserves against.

        Honours an explicit analytic distribution; otherwise falls back to the
        originating request line's department (PR-driven PO lines are created
        without a distribution on purpose, so standard budgeting ignores them
        and only this ledger tracks them).
        """
        self.ensure_one()
        dist = {}
        for acc_ids, pct in (self.analytic_distribution or {}).items():
            for acc in acc_ids.split(','):
                dist[int(acc)] = dist.get(int(acc), 0.0) + pct / 100.0
        if not dist:
            account = self.request_line_ids[:1].analytic_account_id
            if account:
                dist[account.id] = 1.0
        return dist

    def _budget_commitment_target(self):
        """Open (un-invoiced) value this PO line should currently reserve."""
        self.ensure_one()
        if self.order_id.state == 'cancel' or not self.product_qty:
            return 0.0
        ratio = max(0.0, (self.product_qty - self.qty_invoiced) / self.product_qty)
        return self.price_subtotal * ratio

    def _budget_commitment_vals(self, account_id, amount):
        self.ensure_one()
        order = self.order_id
        return {
            'commitment_type': 'order',
            'amount': amount,
            'analytic_account_id': account_id,
            'company_id': self.company_id.id,
            'date': (order.date_order.date() if order.date_order
                     else fields.Date.context_today(self)),
            'order_id': order.id,
            'order_line_id': self.id,
            'request_id': self.request_line_ids[:1].request_id.id,
            'note': order.name,
        }

    def _prepare_account_move_line(self, move=False):
        vals = super()._prepare_account_move_line(move=move)
        # PR-driven PO lines deliberately carry no analytic distribution (so they
        # stay out of standard "committed" and only this ledger tracks them). Tag
        # the *bill* line with the request's department so the realized spend is
        # picked up as standard "achieved" once the bill posts.
        if self.request_line_ids and not vals.get('analytic_distribution'):
            account = self.request_line_ids[:1].analytic_account_id
            if account:
                vals['analytic_distribution'] = {str(account.id): 100}
        return vals


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    request_ids = fields.Many2many(
        'ebshel.purchase.request', compute='_compute_links',
        string='Source Purchase Requests',
    )
    request_count = fields.Integer(
        'Source Requests', compute='_compute_links')
    rfq_id = fields.Many2one(
        'ebshel.purchase.rfq', compute='_compute_links',
        string='Source RFQ',
    )
    is_direct = fields.Boolean(
        string='Off-Process Order', compute='_compute_is_direct',
        store=True, copy=False,
        help='Set when this purchase order was raised directly in the Purchase '
             'app, outside the request → RFQ → award workflow.')

    @api.depends('order_line.request_line_ids')
    def _compute_is_direct(self):
        """A PO is 'direct' (off-process) unless it was produced by our flow:
        an order line links back to a request line, or an RFQ / vendor quotation
        points at it. Batched to avoid a per-order query storm."""
        stored = self.filtered('id')
        linked = set(stored.order_line.filtered('request_line_ids').order_id.ids)
        if stored:
            Quotation = self.env['ebshel.purchase.vendor.quotation'].sudo()
            Rfq = self.env['ebshel.purchase.rfq'].sudo()
            linked |= set(Quotation.search(
                [('purchase_order_id', 'in', stored.ids)]).purchase_order_id.ids)
            linked |= set(Rfq.search(
                [('purchase_order_id', 'in', stored.ids)]).purchase_order_id.ids)
        for order in self:
            order.is_direct = bool(order.id) and order.id not in linked

    def _is_direct_po(self):
        """Authoritative off-process check for a single order, used at
        confirmation time (before the stored flag has necessarily settled)."""
        self.ensure_one()
        if self.order_line.mapped('request_line_ids'):
            return False
        if self.env['ebshel.purchase.vendor.quotation'].sudo().search_count(
                [('purchase_order_id', '=', self.id)]):
            return False
        if self.env['ebshel.purchase.rfq'].sudo().search_count(
                [('purchase_order_id', '=', self.id)]):
            return False
        return True

    def _ebshel_enforce_po_policy(self):
        """Block confirmation of off-process orders under the company's 'strict'
        Direct-PO policy. Purchase Managers may always override."""
        can_override = self.env.user.has_group('purchase.group_purchase_manager')
        for order in self:
            if order.company_id.pr_po_enforcement != 'strict':
                continue
            if can_override or not order._is_direct_po():
                continue
            raise UserError(_(
                'Purchase Order %(po)s was created directly in the Purchase app, '
                'outside the purchase-request workflow. Company policy requires '
                'purchases to originate from an approved request and awarded RFQ.\n\n'
                'Raise a purchase request first — or ask a Purchase Manager to '
                'confirm this order.',
                po=order.name or _('(new)')))

    def _ebshel_flag_off_process(self):
        """Under the 'warn' policy, notify the Purchase-Request administrators
        (via chatter) when an off-process order is confirmed."""
        to_flag = self.filtered(
            lambda o: o.company_id.pr_po_enforcement == 'warn'
            and o._is_direct_po())
        if not to_flag:
            return
        admin_group = self.env.ref(
            'ebshel_purchase_request.group_pr_admin', raise_if_not_found=False)
        partners = (admin_group.all_user_ids.partner_id
                    if admin_group else self.env['res.partner'])
        for order in to_flag:
            order.message_post(
                body=Markup(_(
                    '<p>⚠️ <b>Off-process purchase order.</b></p>'
                    '<p>This order was confirmed directly in the Purchase app, '
                    'outside the request → RFQ → award workflow — no purchase '
                    'request backs it.</p>')),
                partner_ids=partners.ids,
                subtype_xmlid='mail.mt_comment',
            )

    @api.depends('order_line.request_line_ids', 'order_line.request_line_ids.request_id')
    def _compute_links(self):
        Rfq = self.env['ebshel.purchase.rfq']
        Quotation = self.env['ebshel.purchase.vendor.quotation']
        # Batched: resolve the source RFQ for every order in two searches, not
        # two searches per order.
        quo_by_po, rfq_by_po = {}, {}
        if self.ids:
            for q in Quotation.search([('purchase_order_id', 'in', self.ids)]):
                quo_by_po.setdefault(q.purchase_order_id.id, q)
            for r in Rfq.search([('purchase_order_id', 'in', self.ids)]):
                rfq_by_po.setdefault(r.purchase_order_id.id, r)
        for order in self:
            requests = order.order_line.mapped('request_line_ids.request_id')
            order.request_ids = requests
            order.request_count = len(requests)
            # The RFQ is the one whose award produced this PO (split or single).
            quotation = quo_by_po.get(order.id)
            order.rfq_id = (quotation.rfq_id if quotation else rfq_by_po.get(order.id)) or Rfq

    def action_view_requests(self):
        self.ensure_one()
        requests = self.request_ids
        action = {
            'name': _('Source Requests'),
            'type': 'ir.actions.act_window',
            'res_model': 'ebshel.purchase.request',
            'view_mode': 'list,form',
            'domain': [('id', 'in', requests.ids)],
        }
        if len(requests) == 1:
            action.update(view_mode='form', res_id=requests.id)
        return action

    def action_view_rfq(self):
        self.ensure_one()
        if not self.rfq_id:
            return
        return {
            'name': _('Source RFQ'),
            'type': 'ir.actions.act_window',
            'res_model': 'ebshel.purchase.rfq',
            'view_mode': 'form',
            'res_id': self.rfq_id.id,
        }

    # ── Budget encumbrance ────────────────────────────────────────────────────
    def _apply_budget_commitment(self):
        """Record this PO's encumbrance (PR reservations were already released
        when their lines were linked) and gate it against the budget."""
        Line = self.env['purchase.analytic.line']
        for order in self:
            po_lines = order.order_line.filtered('request_line_ids')
            if not po_lines:
                continue
            Line._sync_order_lines(po_lines)
            order._check_budget_commitment(po_lines)

    def _check_budget_commitment(self, po_lines):
        """Block the order when its department(s) go over budget, unless the
        company explicitly allows over-budget purchasing."""
        self.ensure_one()
        if self.company_id.pr_for_amount_exceed_budget:
            return
        Line = self.env['purchase.analytic.line']
        ref_date = (self.date_order.date() if self.date_order
                    else fields.Date.context_today(self))
        account_ids = set()
        for line in po_lines:
            account_ids.update(line._budget_commitment_distribution())
        for account_id in account_ids:
            fig = Line._budget_figures(account_id, self.company_id.id, ref_date)
            if fig['has_budget'] and fig['budget'] and fig['available'] < 0:
                account = self.env['account.analytic.account'].browse(account_id)
                raise UserError(_(
                    'This purchase order pushes %(dept)s over budget by %(over)s. '
                    'Open commitments (requests + orders) and bills exceed the '
                    'budget available for the period. Over-budget purchasing is '
                    'not allowed — reduce the order or free up commitments.',
                    dept=account.display_name,
                    over=self.currency_id.round(-fig['available'])))

    def button_cancel(self):
        res = super().button_cancel()
        # Cancelled lines reserve nothing — rebalance to release.
        po_lines = self.order_line.filtered('request_line_ids')
        if po_lines:
            self.env['purchase.analytic.line']._sync_order_lines(po_lines)
        return res

    def button_confirm(self):
        # Governance: the module's own award flow confirms POs with
        # ebshel_skip_po_policy=True (it links request lines only AFTER confirming).
        skip_policy = self.env.context.get('ebshel_skip_po_policy')
        if not skip_policy:
            self._ebshel_enforce_po_policy()  # raises in 'strict' mode
        res = super().button_confirm()
        if not skip_policy:
            self._ebshel_flag_off_process()   # notifies in 'warn' mode
        for order in self:
            request_lines = order.order_line.mapped('request_line_ids')
            requests = request_lines.mapped('request_id')
            for request in requests:
                request.message_post(
                    body=Markup(_(
                        'Purchase order <a href="#" data-oe-model="purchase.order" '
                        'data-oe-id="%(id)d">%(name)s</a> confirmed for this request.'
                    )) % {'id': order.id, 'name': order.name},
                    subtype_xmlid='mail.mt_note',
                )
        return res
