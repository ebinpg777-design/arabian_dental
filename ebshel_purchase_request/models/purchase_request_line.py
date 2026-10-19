# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

class PurchaseRequestLine(models.Model):
    _name = 'ebshel.purchase.request.line'
    _description = 'Purchase Request Line'
    _inherit = ['analytic.mixin']
    _order = 'request_id, sequence, id'

    # ── Relations ─────────────────────────────────────────────────────────────
    request_id = fields.Many2one(
        'ebshel.purchase.request', string='Purchase Request',
        required=True, ondelete='cascade', index=True,
    )
    sequence = fields.Integer('Seq.', default=10)
    company_id = fields.Many2one(
        related='request_id.company_id', store=True, index=True, readonly=True,
    )
    request_state = fields.Selection(
        related='request_id.state', string='Request Status', store=True,
    )

    # ── Product ───────────────────────────────────────────────────────────────
    product_id = fields.Many2one(
        'product.product', string='Product',
        domain=[('purchase_ok', '=', True)],
        change_default=True, index=True,
    )
    name = fields.Text(
        'Description', required=True,
        compute='_compute_name', store=True, readonly=False,
    )
    qty = fields.Float(
        'Quantity', required=True, default=1.0,
        digits='Product Unit',
    )
    uom_id = fields.Many2one(
        'uom.uom', string='UOM',
        compute='_compute_uom_id', store=True, readonly=False,
    )
    specifications = fields.Text('Specifications')

    # ── Estimates ─────────────────────────────────────────────────────────────
    price_unit = fields.Monetary(
        'Est. Unit Price', default=0.0,
        currency_field='currency_id',
    )
    estimated_total = fields.Monetary(
        'Est. Total', compute='_compute_estimated_total', store=True,
        currency_field='currency_id',
    )
    currency_id = fields.Many2one(
        related='request_id.currency_id', store=True, readonly=True,
    )

    # ── Organisational (analytic account == department / cost center) ─────────
    date_required = fields.Date('Required By')
    analytic_account_id = fields.Many2one(
        'account.analytic.account', string='Department / Cost Center',
        compute='_compute_analytic_account', store=True, readonly=False,
        index=True,
        domain="[('is_cost_center', '=', True)]",
        help='Department / cost center for this line. Defaults to the request '
             'header and seeds the analytic distribution.',
    )
    remarks = fields.Char('Remarks')

    # ── Product change (approver swap / return-to-requester) ──────────────────
    allow_product_change = fields.Boolean(
        'Allow Product Change', default=False,
        help='Set by the requester before submitting. When enabled, an approver '
             'may swap this line\'s product for another in the same category '
             '(or return the request to you to do so) during approval.')
    suggested_product_id = fields.Many2one(
        'product.product', string='Suggested Product', copy=False,
        domain="[('purchase_ok', '=', True)]",
        help='Product proposed by the approver when the request was returned for '
             'a product change. The chosen product must stay in the same '
             'category as this suggestion.')

    @api.constrains('product_id', 'suggested_product_id')
    def _check_suggested_product_category(self):
        for line in self:
            if (line.suggested_product_id and line.product_id
                    and line.product_id.categ_id != line.suggested_product_id.categ_id):
                raise ValidationError(_(
                    'Line "%(l)s": the product must stay in the same category '
                    '(%(c)s) as the suggested product.',
                    l=line.name,
                    c=line.suggested_product_id.categ_id.display_name))

    # ── State & Traceability ──────────────────────────────────────────────────
    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('rfq_created', 'RFQ Created'),
        ('po_created', 'PO Created'),
        ('cancelled', 'Cancelled'),
    ], string='Status', default='draft', compute='_compute_line_state',
        store=True,
    )
    cancelled = fields.Boolean(
        'Cancelled', default=False, copy=False, readonly=True,
        help='A cancelled line is excluded from RFQs, POs and approval totals.',
    )
    is_editable = fields.Boolean(
        'Is Editable', compute='_compute_is_editable',
        help='False once the request is locked or the line is committed to a PO.',
    )
    rfq_line_ids = fields.One2many(
        'ebshel.purchase.rfq.line', 'request_line_id',
        string='RFQ Lines', copy=False,
    )
    po_line_id = fields.Many2one(
        'purchase.order.line', string='Primary PO Line',
        copy=False, readonly=True,
    )
    purchase_line_ids = fields.Many2many(
        'purchase.order.line',
        'pr_line_po_line_rel', 'request_line_id', 'po_line_id',
        string='Purchase Order Lines', copy=False, readonly=True,
    )
    allocation_ids = fields.One2many(
        'ebshel.purchase.request.allocation', 'request_line_id',
        string='Allocations', copy=False,
    )
    rfq_count = fields.Integer('RFQ Count', compute='_compute_rfq_count')
    po_count = fields.Integer('PO Count', compute='_compute_line_po_count')
    allocation_count = fields.Integer('Allocation Count', compute='_compute_line_alloc_count')

    # ── RFQ sourcing quantities (supports partial / split RFQs) ───────────────
    qty_in_rfq = fields.Float(
        'In RFQ', compute='_compute_rfq_qty', store=True,
        digits='Product Unit',
        help='Quantity already placed on active (non-cancelled) RFQs.',
    )
    qty_to_rfq = fields.Float(
        'To RFQ', compute='_compute_rfq_qty', store=True,
        digits='Product Unit',
        help='Requested quantity still available to source on a new RFQ.',
    )

    @api.depends('qty', 'rfq_line_ids.qty', 'rfq_line_ids.rfq_id.state', 'cancelled')
    def _compute_rfq_qty(self):
        for line in self:
            in_rfq = sum(
                rl.qty for rl in line.rfq_line_ids
                if rl.rfq_id.state != 'cancelled'
            )
            line.qty_in_rfq = in_rfq
            line.qty_to_rfq = 0.0 if line.cancelled else max(line.qty - in_rfq, 0.0)

    # ── Fulfilment quantities (OCA-style procurement tracking) ────────────────
    purchased_qty = fields.Float(
        'Ordered Qty', compute='_compute_purchased_qty', store=True,
        digits='Product Unit',
        help='Total quantity committed across linked purchase order lines.',
    )
    qty_done = fields.Float(
        'Received Qty', compute='_compute_allocation_qty', store=True,
        digits='Product Unit',
        help='Quantity fully received against this line.',
    )
    qty_in_progress = fields.Float(
        'In Progress Qty', compute='_compute_allocation_qty', store=True,
        digits='Product Unit',
        help='Quantity ordered but not yet received.',
    )
    pending_qty_to_receive = fields.Float(
        'Pending Qty', compute='_compute_pending_qty', store=True,
        digits='Product Unit',
        help='Requested quantity still to be received (requested − received).',
    )
    qty_to_buy = fields.Boolean(
        'Needs Buying', compute='_compute_pending_qty', store=True,
        help='True while there is still pending quantity to receive.',
    )
    purchase_state = fields.Selection([
        ('draft', 'RFQ'),
        ('sent', 'RFQ Sent'),
        ('to approve', 'To Approve'),
        ('purchase', 'Purchase Order'),
        ('done', 'Done'),
        ('cancel', 'Cancelled'),
    ], string='PO Status', compute='_compute_purchase_state', store=True,
        help='Roll-up status of the linked purchase order(s).',
    )
    # ── Reception & billing tracking (full lifecycle visibility) ──────────────
    qty_invoiced = fields.Float(
        'Billed Qty', compute='_compute_qty_invoiced', store=True,
        digits='Product Unit',
        help='Quantity already invoiced/billed across linked purchase order lines.',
    )
    reception_status = fields.Selection([
        ('nothing', 'Not Received'),
        ('partial', 'Partially Received'),
        ('full', 'Fully Received'),
    ], string='Reception', compute='_compute_fulfilment_status', store=True)
    billing_status = fields.Selection([
        ('nothing', 'Not Billed'),
        ('partial', 'Partially Billed'),
        ('full', 'Fully Billed'),
    ], string='Billing', compute='_compute_fulfilment_status', store=True)

    # ── Compute ───────────────────────────────────────────────────────────────
    @api.depends('product_id')
    def _compute_name(self):
        for line in self:
            if line.product_id:
                line.name = line.product_id._get_purchase_description()
            elif not line.name:
                line.name = ''

    @api.depends('product_id')
    def _compute_uom_id(self):
        for line in self:
            if line.product_id and not line.uom_id:
                line.uom_id = line.product_id.uom_id

    @api.depends('request_id.analytic_account_id')
    def _compute_analytic_account(self):
        for line in self:
            if not line.analytic_account_id:
                line.analytic_account_id = line.request_id.analytic_account_id

    @api.depends('product_id', 'analytic_account_id', 'company_id')
    def _compute_analytic_distribution(self):
        """Auto-build the analytic distribution like a journal/analytic line.

        Prefer the product/category distribution rules (so a product split
        across cost centers flows through), and fall back to a 100% allocation
        on the line's department. Stays editable so a user can override.
        """
        for line in self:
            distribution = False
            if line.product_id:
                distribution = self.env['account.analytic.distribution.model']._get_distribution({
                    'product_id': line.product_id.id,
                    'product_categ_id': line.product_id.categ_id.id,
                    'company_id': line.company_id.id,
                })
            if not distribution and line.analytic_account_id:
                distribution = {str(line.analytic_account_id.id): 100}
            line.analytic_distribution = distribution or line.analytic_distribution

    @api.depends('qty', 'price_unit')
    def _compute_estimated_total(self):
        for line in self:
            line.estimated_total = line.qty * line.price_unit

    # ── Budget encumbrance (see purchase.analytic.line) ───────────────────────
    def _budget_commitment_distribution(self):
        """{analytic_account_id: fraction} this request line reserves against.

        Mirrors how ``account.analytic.line`` records are split per analytic
        account; falls back to the line's department when no distribution set.
        """
        self.ensure_one()
        dist = {}
        for acc_ids, pct in (self.analytic_distribution or {}).items():
            for acc in str(acc_ids).split(','):
                dist[int(acc)] = dist.get(int(acc), 0.0) + pct / 100.0
        if not dist and self.analytic_account_id:
            dist[self.analytic_account_id.id] = 1.0
        return dist

    def _budget_commitment_target(self):
        """Open request encumbrance this line should currently hold.

        A line reserves its estimated total once the request is *approved* and
        while the line is still live and not yet on a PO. Anything else (draft,
        submitted, cancelled, rejected, converted, closed) releases.
        """
        self.ensure_one()
        active = (
            not self.cancelled
            and not self.po_line_id
            and not self.purchase_line_ids
            and self.request_id.state == 'approved'
        )
        return self.estimated_total if active else 0.0

    def _budget_commitment_vals(self, account_id, amount):
        self.ensure_one()
        return {
            'commitment_type': 'request',
            'amount': amount,
            'analytic_account_id': account_id,
            'company_id': self.company_id.id,
            'date': self.request_id.date_request or fields.Date.context_today(self),
            'request_id': self.request_id.id,
            'request_line_id': self.id,
            'note': self.request_id.name,
        }

    def write(self, vals):
        res = super().write(vals)
        # Conversion to a PO line (or an edit to the committed amount) changes how
        # much budget this line reserves — rebalance its encumbrance.
        if {'po_line_id', 'purchase_line_ids', 'cancelled', 'qty', 'price_unit',
                'analytic_account_id', 'analytic_distribution'} & vals.keys():
            self.env['purchase.analytic.line']._sync_request_lines(self)
        return res

    @api.depends('request_id.state', 'rfq_line_ids', 'po_line_id',
                 'purchase_line_ids', 'cancelled')
    def _compute_line_state(self):
        for line in self:
            if line.cancelled:
                line.state = 'cancelled'
            elif line.po_line_id or line.purchase_line_ids:
                line.state = 'po_created'
            elif line.rfq_line_ids:
                line.state = 'rfq_created'
            else:
                pr_state = line.request_id.state
                line.state = pr_state if pr_state in (
                    'draft', 'submitted', 'approved', 'rejected', 'cancelled'
                ) else 'approved'

    @api.depends('request_id.state', 'purchase_line_ids', 'cancelled')
    def _compute_is_editable(self):
        for line in self:
            locked_states = ('approved', 'rejected', 'cancelled', 'closed')
            line.is_editable = (
                not line.cancelled
                and line.request_id.state not in locked_states
                and not line.purchase_line_ids
            )

    @api.depends('purchase_line_ids.product_qty', 'purchase_line_ids.state', 'uom_id')
    def _compute_purchased_qty(self):
        for line in self:
            total = 0.0
            for pol in line.purchase_line_ids:
                if pol.state == 'cancel':
                    continue
                if pol.uom_id and line.uom_id:
                    total += pol.uom_id._compute_quantity(pol.product_qty, line.uom_id)
                else:
                    total += pol.product_qty
            line.purchased_qty = total

    @api.depends('allocation_ids.allocated_product_qty',
                 'allocation_ids.open_product_qty')
    def _compute_allocation_qty(self):
        for line in self:
            line.qty_done = sum(line.allocation_ids.mapped('allocated_product_qty'))
            line.qty_in_progress = sum(line.allocation_ids.mapped('open_product_qty'))

    @api.depends('qty', 'qty_done')
    def _compute_pending_qty(self):
        for line in self:
            pending = max(line.qty - line.qty_done, 0.0)
            line.pending_qty_to_receive = pending
            line.qty_to_buy = pending > 0

    @api.depends('purchase_line_ids.state', 'purchase_line_ids.qty_received',
                 'purchase_line_ids.product_qty')
    def _compute_purchase_state(self):
        for line in self:
            states = line.purchase_line_ids.mapped('state')
            if not states:
                line.purchase_state = False
            elif all(s == 'cancel' for s in states):
                line.purchase_state = 'cancel'
            elif any(
                pol.state == 'purchase' and pol.qty_received >= pol.product_qty
                for pol in line.purchase_line_ids
            ) and line.pending_qty_to_receive <= 0:
                line.purchase_state = 'done'
            elif 'purchase' in states or 'done' in states:
                line.purchase_state = 'purchase'
            elif 'to approve' in states:
                line.purchase_state = 'to approve'
            elif 'sent' in states:
                line.purchase_state = 'sent'
            else:
                line.purchase_state = 'draft'

    @api.depends('purchase_line_ids.qty_invoiced', 'purchase_line_ids.state', 'uom_id')
    def _compute_qty_invoiced(self):
        for line in self:
            total = 0.0
            for pol in line.purchase_line_ids:
                if pol.state == 'cancel':
                    continue
                if pol.uom_id and line.uom_id:
                    total += pol.uom_id._compute_quantity(pol.qty_invoiced, line.uom_id)
                else:
                    total += pol.qty_invoiced
            line.qty_invoiced = total

    @api.depends('purchased_qty', 'qty_done', 'qty_invoiced')
    def _compute_fulfilment_status(self):
        prec = 0.001
        for line in self:
            base = line.purchased_qty or line.qty
            # Reception
            if line.qty_done <= prec:
                line.reception_status = 'nothing'
            elif base and line.qty_done + prec >= base:
                line.reception_status = 'full'
            else:
                line.reception_status = 'partial'
            # Billing
            if line.qty_invoiced <= prec:
                line.billing_status = 'nothing'
            elif base and line.qty_invoiced + prec >= base:
                line.billing_status = 'full'
            else:
                line.billing_status = 'partial'

    @api.depends('rfq_line_ids.rfq_id')
    def _compute_rfq_count(self):
        for line in self:
            line.rfq_count = len(line.rfq_line_ids.mapped('rfq_id'))

    @api.depends('purchase_line_ids', 'po_line_id')
    def _compute_line_po_count(self):
        for line in self:
            pos = (line.purchase_line_ids | line.po_line_id).mapped('order_id')
            line.po_count = len(pos)

    @api.depends('allocation_ids')
    def _compute_line_alloc_count(self):
        for line in self:
            line.allocation_count = len(line.allocation_ids)

    # ── Smart-button navigation ───────────────────────────────────────────────
    def action_view_rfqs(self):
        """Open the RFQ(s) this request line was sourced on."""
        self.ensure_one()
        rfqs = self.rfq_line_ids.mapped('rfq_id')
        action = {
            'name': _('RFQs'),
            'type': 'ir.actions.act_window',
            'res_model': 'ebshel.purchase.rfq',
            'view_mode': 'list,form',
            'domain': [('id', 'in', rfqs.ids)],
        }
        if len(rfqs) == 1:
            action.update(view_mode='form', res_id=rfqs.id)
        return action

    def action_view_purchase_orders(self):
        """Open the purchase order(s) carrying this line's PO line(s)."""
        self.ensure_one()
        pos = (self.purchase_line_ids | self.po_line_id).mapped('order_id')
        action = self.env['ir.actions.act_window']._for_xml_id('purchase.purchase_form_action')
        action['context'] = {}
        action['domain'] = [('id', 'in', pos.ids)]
        if len(pos) == 1:
            action['views'] = [(self.env.ref('purchase.purchase_order_form').id, 'form')]
            action['res_id'] = pos.id
        return action

    def action_view_allocations(self):
        """Open the receipt allocations for this request line."""
        self.ensure_one()
        return {
            'name': _('Allocations'),
            'type': 'ir.actions.act_window',
            'res_model': 'ebshel.purchase.request.allocation',
            'view_mode': 'list,form',
            'domain': [('request_line_id', '=', self.id)],
            'context': {'default_request_line_id': self.id},
        }

    # ── Line cancellation ─────────────────────────────────────────────────────
    def do_cancel(self):
        # Lines may only be cancelled while the request is still being prepared
        # or is under approval — never once it has been approved/closed.
        blocked = self.filtered(
            lambda l: l.request_id.state in ('approved', 'closed'))
        if blocked:
            raise UserError(_(
                'Lines of an approved purchase request cannot be cancelled. '
                'Cancel a line only while the request is in Draft or under '
                'approval. (Affected: %s)',
                ', '.join(blocked.mapped('request_id.name'))))
        self.write({'cancelled': True})
        # One check per affected request (lines often share a request).
        self.mapped('request_id')._check_auto_reject()

    def do_uncancel(self):
        self.write({'cancelled': False})

    def unlink(self):
        # Lines may only be *deleted* while the request is still in Draft. Once
        # submitted/approved they are part of the audit trail — cancel the line
        # (do_cancel) instead of removing it.
        if not self.env.context.get('ebshel_bypass_line_guard'):
            locked = self.filtered(
                lambda l: l.request_id and l.request_id.state != 'draft')
            if locked:
                raise UserError(_(
                    'Purchase request lines can only be deleted while the request '
                    'is in Draft. Cancel the line instead — it stays on record for '
                    'traceability. Affected request(s): %s',
                    ', '.join(sorted(set(locked.mapped('request_id.name'))))))
        return super().unlink()

    # ── onchange ──────────────────────────────────────────────────────────────
    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            self.uom_id = self.product_id.uom_id
            # Fetch standard price as initial estimate
            if not self.price_unit:
                self.price_unit = self.product_id.standard_price

    def action_show_details(self):
        """Open the full detail form of a single request line."""
        self.ensure_one()
        return {
            'name': _('Request Line'),
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
