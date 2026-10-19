# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.
from markupsafe import Markup, escape
from odoo import api, fields, models, _
from odoo.exceptions import UserError

class PurchaseRFQ(models.Model):
    """Custom RFQ that aggregates lines from one or more approved purchase
    requests and tracks vendor quotations through to PO creation."""
    _name = 'ebshel.purchase.rfq'
    _description = 'Purchase RFQ (from Request)'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_rfq desc, id desc'
    _rec_name = 'name'

    # ── Identity ──────────────────────────────────────────────────────────────
    name = fields.Char(
        'RFQ Reference', required=True, copy=False, readonly=True,
        default=lambda self: _('New'), tracking=True,
    )
    state = fields.Selection([
        ('draft', 'Draft'),
        ('sent', 'Sent to Vendors'),
        ('comparing', 'Comparing Quotes'),
        ('awarded', 'Vendor Awarded'),
        ('po_created', 'PO Created'),
        ('cancelled', 'Cancelled'),
    ], string='Status', default='draft', copy=False,
        tracking=True, index=True,
    )

    # ── Responsibility ────────────────────────────────────────────────────────
    procurement_officer_id = fields.Many2one(
        'res.users', string='Procurement Officer',
        default=lambda self: self.env.user, tracking=True,
    )
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, index=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id',
        string='Currency', readonly=True, store=True,
    )

    # ── Dates ─────────────────────────────────────────────────────────────────
    date_rfq = fields.Date(
        'RFQ Date', default=fields.Date.context_today, required=True,
    )
    deadline = fields.Date(
        'Quotation Deadline', index=True,
        help='Date by which vendors must submit their quotations.',
    )

    # ── Lines & Quotations ────────────────────────────────────────────────────
    rfq_line_ids = fields.One2many(
        'ebshel.purchase.rfq.line', 'rfq_id',
        string='RFQ Lines', copy=True,
    )
    vendor_quotation_ids = fields.One2many(
        'ebshel.purchase.vendor.quotation', 'rfq_id',
        string='Vendor Quotations', copy=False,
    )
    purchase_order_id = fields.Many2one(
        'purchase.order', string='Purchase Order',
        copy=False, readonly=True, tracking=True, index='btree_not_null',
    )
    notes = fields.Html('Internal Notes')
    terms_template_id = fields.Many2one(
        'ebshel.rfq.terms', string='T&C Template',
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",
        help='Pick a Terms & Conditions template to fill the text below.')
    terms_conditions = fields.Html(
        'Terms & Conditions',
        help='Shown to vendors on the RFQ email and the comparison PDF.')

    # ── Award approval (lower user proposes → higher authority approves) ───────
    award_state = fields.Selection([
        ('none', 'Not Proposed'),
        ('pending', 'Pending Approval'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ], string='Award Approval', default='none', copy=False, tracking=True, index=True)
    award_mode = fields.Selection([
        ('single', 'Single Winner'),
        ('split', 'Split Award'),
    ], string='Award Type', copy=False)
    award_winner_id = fields.Many2one(
        'ebshel.purchase.vendor.quotation', string='Proposed Winner', copy=False)
    award_line_ids = fields.One2many(
        'ebshel.rfq.award.line', 'rfq_id', string='Proposed Award Lines', copy=False)
    award_note = fields.Char('Award Justification', copy=False)
    award_proposed_by_id = fields.Many2one('res.users', string='Proposed By', copy=False)
    award_approved_by_id = fields.Many2one('res.users', string='Approved By', copy=False)
    award_rejection_reason = fields.Char('Rejection Reason', copy=False)

    # ── Quantity-increase approval (line qty above the requested amount) ───────
    qty_approval_state = fields.Selection([
        ('none', 'Not Required'),
        ('pending', 'Pending Approval'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ], string='Quantity Approval', default='none', copy=False, tracking=True, index=True)
    qty_proposed_by_id = fields.Many2one('res.users', string='Qty Requested By', copy=False)
    qty_approved_by_id = fields.Many2one('res.users', string='Qty Approved By', copy=False)
    qty_approval_reason = fields.Char('Quantity Rejection Reason', copy=False)
    has_qty_increase = fields.Boolean(
        'Has Quantity Increase', compute='_compute_has_qty_increase', store=True,
        help='True when any RFQ line quantity exceeds its source request line.')
    qty_increase_needs_approval = fields.Boolean(
        'Quantity Approval Required',
        compute='_compute_qty_increase_needs_approval',
        help='True while a quantity increase still needs Administrator approval '
             'before the RFQ can be sent to vendors.')

    @api.depends('rfq_line_ids.qty', 'rfq_line_ids.request_line_id.qty')
    def _compute_has_qty_increase(self):
        for rfq in self:
            rfq.has_qty_increase = any(
                line.request_line_id and line.qty > line.request_line_id.qty
                for line in rfq.rfq_line_ids)

    @api.depends('has_qty_increase', 'qty_approval_state',
                 'company_id.rfq_qty_increase_approval')
    def _compute_qty_increase_needs_approval(self):
        for rfq in self:
            rfq.qty_increase_needs_approval = bool(
                rfq.has_qty_increase
                and rfq.company_id.rfq_qty_increase_approval
                and rfq.qty_approval_state != 'approved')

    # ── Computed ──────────────────────────────────────────────────────────────
    request_ids = fields.Many2many(
        'ebshel.purchase.request', compute='_compute_request_ids',
        string='Source Requests',
    )
    vendor_count = fields.Integer('Vendors', compute='_compute_vendor_count')
    quotation_count = fields.Integer('Quotations', compute='_compute_quotation_count')
    received_count = fields.Integer('Priced Quotations', compute='_compute_received_count',
        help='Quotations for which the vendor has submitted prices (received).')
    line_count = fields.Integer('Lines', compute='_compute_line_count')
    request_count = fields.Integer('Source Request Count', compute='_compute_request_ids')
    purchase_order_ids = fields.Many2many(
        'purchase.order', compute='_compute_purchase_orders',
        string='Purchase Orders',
    )
    po_count = fields.Integer('Purchase Order Count', compute='_compute_purchase_orders')
    winner_quotation_id = fields.Many2one(
        'ebshel.purchase.vendor.quotation', compute='_compute_winner',
        string='Winning Quotation', store=True,
    )

    # ── Cockpit insights (power the form dashboard) ────────────────────────────
    days_to_deadline = fields.Integer(
        'Days to Deadline', compute='_compute_deadline_state')
    deadline_state = fields.Selection([
        ('none', 'No deadline'),
        ('overdue', 'Overdue'),
        ('today', 'Due today'),
        ('soon', 'Due soon'),
        ('ok', 'On track'),
    ], string='Deadline Status', compute='_compute_deadline_state')
    response_rate = fields.Integer(
        'Response Rate', compute='_compute_insights',
        help='Percentage of invited vendors that have submitted prices.')
    best_quote_total = fields.Monetary(
        'Best Quote', compute='_compute_insights', currency_field='currency_id')
    highest_quote_total = fields.Monetary(
        'Highest Quote', compute='_compute_insights', currency_field='currency_id')
    potential_savings = fields.Monetary(
        'Potential Savings', compute='_compute_insights', currency_field='currency_id',
        help='Difference between the highest and lowest received quote.')
    best_vendor_id = fields.Many2one(
        'res.partner', string='Best-priced Vendor', compute='_compute_insights')
    progress_pct = fields.Integer('Progress', compute='_compute_progress')

    # ── Compute ───────────────────────────────────────────────────────────────
    @api.depends('deadline', 'state')
    def _compute_deadline_state(self):
        today = fields.Date.context_today(self)
        for rfq in self:
            if not rfq.deadline or rfq.state in ('awarded', 'po_created', 'cancelled'):
                rfq.days_to_deadline = 0
                rfq.deadline_state = 'none'
                continue
            delta = (rfq.deadline - today).days
            rfq.days_to_deadline = delta
            if delta < 0:
                rfq.deadline_state = 'overdue'
            elif delta == 0:
                rfq.deadline_state = 'today'
            elif delta <= 3:
                rfq.deadline_state = 'soon'
            else:
                rfq.deadline_state = 'ok'

    @api.depends('vendor_quotation_ids.amount_total',
                 'vendor_quotation_ids.state',
                 'vendor_quotation_ids.vendor_id')
    def _compute_insights(self):
        for rfq in self:
            quotes = rfq.vendor_quotation_ids
            priced = quotes.filtered(lambda q: q.amount_total > 0)
            rfq.response_rate = round(len(priced) / len(quotes) * 100) if quotes else 0
            totals = priced.mapped('amount_total')
            if totals:
                rfq.best_quote_total = min(totals)
                rfq.highest_quote_total = max(totals)
                rfq.potential_savings = max(totals) - min(totals)
                rfq.best_vendor_id = priced.sorted('amount_total')[:1].vendor_id
            else:
                rfq.best_quote_total = 0.0
                rfq.highest_quote_total = 0.0
                rfq.potential_savings = 0.0
                rfq.best_vendor_id = False

    @api.depends('state')
    def _compute_progress(self):
        steps = {'draft': 10, 'sent': 35, 'comparing': 60,
                 'awarded': 85, 'po_created': 100, 'cancelled': 0}
        for rfq in self:
            rfq.progress_pct = steps.get(rfq.state, 0)

    @api.depends('rfq_line_ids.request_line_id.request_id')
    def _compute_request_ids(self):
        for rfq in self:
            rfq.request_ids = rfq.rfq_line_ids.mapped('request_line_id.request_id')
            rfq.request_count = len(rfq.request_ids)

    @api.depends('purchase_order_id', 'vendor_quotation_ids.purchase_order_id')
    def _compute_purchase_orders(self):
        for rfq in self:
            pos = rfq.purchase_order_id | rfq.vendor_quotation_ids.mapped('purchase_order_id')
            rfq.purchase_order_ids = pos
            rfq.po_count = len(pos)

    @api.depends('vendor_quotation_ids.vendor_id')
    def _compute_vendor_count(self):
        for rfq in self:
            rfq.vendor_count = len(rfq.vendor_quotation_ids.mapped('vendor_id'))

    @api.depends('vendor_quotation_ids')
    def _compute_quotation_count(self):
        for rfq in self:
            rfq.quotation_count = len(rfq.vendor_quotation_ids)

    @api.depends('vendor_quotation_ids.state', 'vendor_quotation_ids.amount_total')
    def _compute_received_count(self):
        # A quotation counts as "priced/received" once the vendor's prices are in.
        for rfq in self:
            rfq.received_count = len(rfq.vendor_quotation_ids.filtered(
                lambda q: q.state in ('received', 'selected')))

    @api.depends('rfq_line_ids')
    def _compute_line_count(self):
        for rfq in self:
            rfq.line_count = len(rfq.rfq_line_ids)

    @api.depends('vendor_quotation_ids.is_winner')
    def _compute_winner(self):
        for rfq in self:
            rfq.winner_quotation_id = rfq.vendor_quotation_ids.filtered('is_winner')[:1]

    # ── ORM ───────────────────────────────────────────────────────────────────
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('ebshel.purchase.rfq') or _('New')
            # Seed Terms & Conditions from the company default template.
            company = self.env['res.company'].browse(
                vals.get('company_id')) or self.env.company
            default_terms = company.rfq_default_terms_id
            if default_terms:
                vals.setdefault('terms_template_id', default_terms.id)
                if not vals.get('terms_conditions'):
                    vals['terms_conditions'] = default_terms.terms
        return super().create(vals_list)

    @api.onchange('terms_template_id')
    def _onchange_terms_template_id(self):
        if self.terms_template_id:
            self.terms_conditions = self.terms_template_id.terms

    def unlink(self):
        if any(r.state != 'draft' for r in self):
            raise UserError(_('Only Draft RFQs can be deleted. Cancel the RFQ instead.'))
        return super().unlink()

    # ── Workflow ──────────────────────────────────────────────────────────────
    def action_send_to_vendors(self):
        for rfq in self:
            if not rfq.vendor_quotation_ids:
                raise UserError(_('Please add at least one vendor before sending.'))
            if rfq.qty_increase_needs_approval:
                raise UserError(_(
                    'This RFQ has a line quantity above the requested amount. '
                    'Submit it for quantity approval and have an Administrator '
                    'approve it before sending to vendors.'))
            rfq.write({'state': 'sent'})
            for quotation in rfq.vendor_quotation_ids:
                quotation._send_rfq_email()
            rfq.message_post(
                body=_('RFQ sent to %d vendor(s).', len(rfq.vendor_quotation_ids)),
                subtype_xmlid='mail.mt_note',
            )

    def action_start_comparison(self):
        for rfq in self:
            received = rfq.vendor_quotation_ids.filtered(
                lambda q: q.state == 'received'
            )
            if not received:
                raise UserError(_('No vendor quotations have been received yet.'))
            rfq.write({'state': 'comparing'})

    def action_revert_comparison(self):
        for rfq in self:
            if rfq.state != 'comparing':
                raise UserError(_('Only RFQs in "Comparing Quotes" can be reverted to "Sent".'))
            rfq.vendor_quotation_ids.filtered(
                lambda q: q.state == 'received'
            ).write({'state': 'sent'})
            rfq.write({'state': 'sent'})


    def action_cancel(self):
        for rfq in self:
            if rfq.state == 'po_created':
                raise UserError(_('Cannot cancel an RFQ with a created Purchase Order.'))
            rfq.write({'state': 'cancelled'})
            rfq.message_post(body=_('RFQ cancelled.'), subtype_xmlid='mail.mt_note')

    def action_reset_to_draft(self):
        for rfq in self:
            # A draft RFQ has not been sent, so its quotations go back to
            # "Pending" — re-sending the RFQ will mark them "Sent" again.
            # (Related fields cannot be set through a dotted write key.)
            rfq.vendor_quotation_ids.write({'state': 'draft'})
            rfq.write({'state': 'draft'})
            rfq.message_post(body=_('RFQ reset to draft.'), subtype_xmlid='mail.mt_note')

    def action_add_vendor(self):
        self.ensure_one()
        return {
            'name': _('Add Vendor Quotation'),
            'type': 'ir.actions.act_window',
            'res_model': 'ebshel.purchase.vendor.quotation',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_rfq_id': self.id,
                'default_company_id': self.company_id.id,
            },
        }

    def action_select_winner(self):
        self.ensure_one()
        if self.state not in ('sent', 'comparing'):
            raise UserError(_('Please put the RFQ in "Comparing Quotes" state first.'))
        received = self.vendor_quotation_ids.filtered(lambda q: q.state == 'received')
        if not received:
            raise UserError(_('No quotations received to compare.'))
        return {
            'name': _('Select Winning Vendor'),
            'type': 'ir.actions.act_window',
            'res_model': 'ebshel.pr.select.winner.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_rfq_id': self.id},
        }

    def action_create_purchase_order(self):
        """Create the PO from the winning quotation and open it."""
        self.ensure_one()
        if not self.winner_quotation_id:
            raise UserError(_('Please select a winning vendor quotation first.'))
        if self.purchase_order_id:
            return self.action_view_purchase_order()
        # Route through approval when required.
        if self._award_needs_approval():
            self._propose_award_single(self.winner_quotation_id,
                                       self.winner_quotation_id.winner_justification)
            return self._award_pending_notification()
        po = self._create_purchase_order_from_winner()
        return {
            'name': _('Purchase Order'),
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'view_mode': 'form',
            'views': [[False, 'form']],
            'res_id': po.id,
        }

    def _guard_award_approved(self):
        """Defence-in-depth: no path may create a PO while the award still needs
        a higher authority's approval (approval sets award_state='approved' first)."""
        self.ensure_one()
        if self._award_needs_approval():
            raise UserError(_(
                'This award must be approved by a higher authority before the '
                'purchase order is created. Submit it from the comparison / award '
                'flow, then approve it from “Approvals → PO Awards to Approve”.'))

    def _po_picking_type(self):
        """Incoming operation type ("Deliver To") for this RFQ's company.

        Set explicitly on every PO we create so creation does not fall back to
        the *active* company's default — which may differ from the RFQ's company
        and leave the required ``picking_type_id`` empty (ORM "Missing required
        value for the field 'Deliver To'")."""
        self.ensure_one()
        picking_type = self.env['purchase.order']._get_picking_type(self.company_id.id)
        if not picking_type:
            raise UserError(_(
                'No incoming operation type (receipts) is configured for company '
                '%(company)s. Set up a warehouse / receipt operation for that '
                'company before generating purchase orders from this RFQ.',
                company=self.company_id.display_name))
        return picking_type

    def _create_purchase_order_from_winner(self):
        """Single source of truth for turning the winning quotation into a PO.
        Idempotent: returns the existing PO if one was already created."""
        self.ensure_one()
        if self.purchase_order_id:
            return self.purchase_order_id
        self._guard_award_approved()
        winner = self.winner_quotation_id
        if not winner:
            raise UserError(_('No winning vendor has been selected for this RFQ.'))
        if winner.is_new_vendor and not winner.vendor_id:
            raise UserError(_(
                'The winning vendor "%s" is not yet registered. Register the '
                'vendor before creating the purchase order.',
                winner.new_vendor_name or winner.display_name,
            ))

        # Build PO lines while keeping each quotation line alongside, so we can
        # link back by position (matching on product alone double-links when two
        # lines share the same product).
        qlines = list(winner.line_ids)
        po_lines = [(0, 0, {
            'product_id': qline.product_id.id,
            'name': qline.name,
            # Never order more than the RFQ line requested.
            'product_qty': (min(qline.qty, qline.rfq_line_id.qty)
                            if qline.rfq_line_id else qline.qty),
            'uom_id': qline.uom_id.id,
            'price_unit': qline.price_unit * (1 - (qline.discount or 0) / 100),
            'tax_ids': [(6, 0, qline.tax_ids.ids)],
            'date_planned': fields.Datetime.to_datetime(qline.delivery_date) or fields.Datetime.now(),
        }) for qline in qlines]

        po = self.env['purchase.order'].with_company(self.company_id).create({
            'company_id': self.company_id.id,
            'picking_type_id': self._po_picking_type().id,
            'partner_id': winner.vendor_id.id,
            'currency_id': self.currency_id.id,
            'payment_term_id': winner.payment_term_id.id,
            'origin': self.name,
            'order_line': po_lines,
            'note': self.terms_conditions or self.notes,
        })
        # Confirm immediately so the PO lands in "Purchase Order" status.
        # Skip the direct-PO policy: this order IS on-process, but its request
        # links are written just below (after confirmation).
        po.with_context(ebshel_skip_po_policy=True).button_confirm()

        winner.write({'purchase_order_id': po.id})
        self.write({'state': 'po_created', 'purchase_order_id': po.id})

        # Link PR lines ↔ PO lines (by position) and batch-create receipt allocations.
        Allocation = self.env['ebshel.purchase.request.allocation']
        alloc_vals = []
        for qline, po_line in zip(qlines, po.order_line):
            pr_line = qline.rfq_line_id.request_line_id
            if pr_line and po_line:
                pr_line.write({
                    'po_line_id': po_line.id,
                    'purchase_line_ids': [(4, po_line.id)],
                })
                alloc_vals.append({
                    'request_line_id': pr_line.id,
                    'purchase_line_id': po_line.id,
                    'requested_product_uom_qty': pr_line.qty,
                })
        if alloc_vals:
            Allocation.create(alloc_vals)

        # Record the PO encumbrance (PR reservations released as lines linked
        # above) and gate it against the department budget.
        po._apply_budget_commitment()

        # Close fully-ordered source requests
        for request in self.request_ids:
            if request.line_ids and all(
                l.state in ('po_created', 'cancelled') for l in request.line_ids
            ):
                request.write({'state': 'closed'})
                request.message_post(
                    body=_('All lines have been ordered. Request closed.'),
                    subtype_xmlid='mail.mt_note',
                )

        self.message_post(
            body=Markup(_(
                'Purchase Order <a href="#" data-oe-model="purchase.order" '
                'data-oe-id="%(id)d">%(name)s</a> created from winning vendor %(vendor)s.'
            )) % {'id': po.id, 'name': po.name, 'vendor': winner.display_name},
            subtype_xmlid='mail.mt_comment',
        )
        return po

    # ── Split award (multiple vendors → multiple POs) ─────────────────────────
    def action_open_split_award(self):
        """Open the split-award workbench: assign each RFQ line to a vendor and
        generate one PO per vendor in a single step."""
        self.ensure_one()
        if self.state not in ('sent', 'comparing', 'awarded'):
            raise UserError(_('Put the RFQ in "Sent" or "Comparing" state before awarding.'))
        received = self.vendor_quotation_ids.filtered(
            lambda q: q.state in ('received', 'selected'))
        if not received:
            raise UserError(_(
                'No received quotations to award. Mark at least one vendor '
                'quotation as "Received" first.'))

        QL = self.env['ebshel.purchase.vendor.quotation.line']
        # One query for all lines, then pick the cheapest candidate per line in
        # memory (was one search per RFQ line — N+1).
        cands = QL.search([
            ('rfq_line_id', 'in', self.rfq_line_ids.ids),
            ('price_unit', '>', 0),
            ('quotation_id.state', 'in', ('received', 'selected')),
        ])
        best_by_line = {}
        for cand in cands:
            current = best_by_line.get(cand.rfq_line_id.id)
            if current is None or cand.price_unit < current.price_unit:
                best_by_line[cand.rfq_line_id.id] = cand
        wiz_lines = []
        for rfq_line in self.rfq_line_ids:
            best = best_by_line.get(rfq_line.id, False)
            wiz_lines.append((0, 0, {
                'rfq_line_id': rfq_line.id,
                'quotation_id': best.quotation_id.id if best else False,
                'award': bool(best),
                'awarded_qty': rfq_line.qty,
            }))
        # NOTE: opening the wizard is only a preview/selection step — it must not
        # change the RFQ state. The move to "Vendor Awarded" happens when the
        # award is actually generated/proposed (action_generate_pos →
        # _propose_award_split / _create_split_purchase_orders).
        return {
            'name': _('Split Award – %s', self.name),
            'type': 'ir.actions.act_window',
            'res_model': 'ebshel.pr.split.award.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_rfq_id': self.id,
                'default_line_ids': wiz_lines,
            },
        }

    def _create_split_purchase_orders(self, awards):
        """Create one PO per distinct vendor from `awards`, capping every PO
        line at the RFQ line's requested quantity.

        :param awards: list of dicts {'quotation', 'rfq_line', 'qty'}
        :return: the created purchase.order recordset
        """
        self.ensure_one()
        self._guard_award_approved()
        Allocation = self.env['ebshel.purchase.request.allocation']

        # Guard: total awarded qty per RFQ line may never exceed the request.
        by_line = {}
        for a in awards:
            by_line.setdefault(a['rfq_line'], 0.0)
            by_line[a['rfq_line']] += a['qty']
        for rfq_line, total in by_line.items():
            if total > rfq_line.qty + 1e-6:
                raise UserError(_(
                    'Awarded quantity for "%(p)s" (%(t)s) exceeds the requested '
                    '%(r)s. Reduce the split before generating POs.',
                    p=rfq_line.product_id.display_name or rfq_line.name,
                    t=total, r=rfq_line.qty))

        # Group awards by quotation (= vendor).
        grouped = {}
        for a in awards:
            grouped.setdefault(a['quotation'], []).append(a)

        picking_type = self._po_picking_type()
        created = self.env['purchase.order']
        for quotation, items in grouped.items():
            # Auto-register prospective vendors so partial allocation works for
            # new vendors too — no manual "award first" step required.
            vendor = quotation._ensure_vendor_registered()

            po_lines, valid_items = [], []
            for it in items:
                rfq_line = it['rfq_line']
                qty = min(it['qty'], rfq_line.qty)
                if qty <= 0:
                    continue
                qline = quotation.line_ids.filtered(
                    lambda l: l.rfq_line_id == rfq_line)[:1]
                price = (qline.price_unit * (1 - (qline.discount or 0) / 100)
                         if qline else 0.0)
                planned = (fields.Datetime.to_datetime(qline.delivery_date)
                           if qline and qline.delivery_date else fields.Datetime.now())
                po_lines.append((0, 0, {
                    'product_id': rfq_line.product_id.id,
                    'name': qline.name if qline else rfq_line.name,
                    'product_qty': qty,
                    'uom_id': rfq_line.uom_id.id,
                    'price_unit': price,
                    'tax_ids': [(6, 0, qline.tax_ids.ids)] if qline else False,
                    'date_planned': planned,
                }))
                valid_items.append((rfq_line, qty))
            if not po_lines:
                continue

            po = self.env['purchase.order'].with_company(self.company_id).create({
                'company_id': self.company_id.id,
                'picking_type_id': picking_type.id,
                'partner_id': vendor.id,
                'currency_id': self.currency_id.id,
                'payment_term_id': quotation.payment_term_id.id,
                'origin': self.name,
                'order_line': po_lines,
                'note': self.terms_conditions or self.notes,
            })
            # Confirm immediately so each PO lands in "Purchase Order" status.
            # Skip the direct-PO policy: on-process order, links written below.
            po.with_context(ebshel_skip_po_policy=True).button_confirm()
            quotation.write({'purchase_order_id': po.id, 'state': 'selected'})
            created |= po

            # Link PR lines + allocations (order of order_line matches valid_items)
            for (rfq_line, qty), po_line in zip(valid_items, po.order_line):
                pr_line = rfq_line.request_line_id
                if pr_line:
                    pr_line.write({
                        'po_line_id': po_line.id,
                        'purchase_line_ids': [(4, po_line.id)],
                    })
                    Allocation.create({
                        'request_line_id': pr_line.id,
                        'purchase_line_id': po_line.id,
                        'requested_product_uom_qty': qty,
                    })

        if not created:
            raise UserError(_('Nothing to order — no awarded lines with a positive quantity.'))

        # Record each PO's encumbrance and gate against the department budget.
        created._apply_budget_commitment()

        # Reject quotations that won nothing; keep the RFQ pointing at a PO.
        self.vendor_quotation_ids.filtered(
            lambda q: q not in grouped and q.state not in ('rejected',)
        ).write({'state': 'rejected'})
        self.write({'state': 'po_created', 'purchase_order_id': created[:1].id})

        # Close fully-ordered requests.
        for request in self.request_ids:
            if request.line_ids and all(
                l.state in ('po_created', 'cancelled') for l in request.line_ids
            ):
                request.write({'state': 'closed'})
                request.message_post(
                    body=_('All lines have been ordered. Request closed.'),
                    subtype_xmlid='mail.mt_note')

        po_links = Markup(', ').join(
            Markup('<a href="#" data-oe-model="purchase.order" data-oe-id="%d">%s</a>')
            % (po.id, po.name) for po in created)
        self.message_post(
            body=Markup(_('Split award generated %(n)s purchase order(s): %(links)s')) % {
                'n': len(created), 'links': po_links,
            },
            subtype_xmlid='mail.mt_comment')
        return created

    def action_generate_split_pos(self, allocations):
        """Called from the OWL comparison matrix. `allocations` is a list of
        {'line_id', 'vendor_id', 'qty'} where vendor_id is a vendor-quotation id.
        One RFQ line may appear under several vendors (split quantity)."""
        self.ensure_one()
        if self.state not in ('sent', 'comparing', 'awarded'):
            raise UserError(_('This RFQ can no longer be ordered.'))
        Quotation = self.env['ebshel.purchase.vendor.quotation']
        RfqLine = self.env['ebshel.purchase.rfq.line']
        awards = []
        for a in allocations or []:
            qty = a.get('qty') or 0
            if qty <= 0:
                continue
            quotation = Quotation.browse(int(a['vendor_id']))
            rfq_line = RfqLine.browse(int(a['line_id']))
            if quotation.rfq_id != self or rfq_line.rfq_id != self:
                raise UserError(_('Allocation references data outside this RFQ.'))
            # The vendor must actually have quoted this line.
            if not quotation.line_ids.filtered(
                    lambda l: l.rfq_line_id == rfq_line and l.price_unit > 0):
                raise UserError(_(
                    '%(v)s did not quote "%(p)s" — remove that allocation.',
                    v=quotation.display_name,
                    p=rfq_line.product_id.display_name or rfq_line.name))
            awards.append({'quotation': quotation, 'rfq_line': rfq_line, 'qty': qty})
        if not awards:
            raise UserError(_('Set a quantity on at least one vendor before generating POs.'))
        # Validate quantities up-front (also enforced again at PO creation) so an
        # over-allocated proposal is rejected immediately, not only on approval.
        by_line = {}
        for a in awards:
            by_line.setdefault(a['rfq_line'], 0.0)
            by_line[a['rfq_line']] += a['qty']
        for rfq_line, total in by_line.items():
            if total > rfq_line.qty + 1e-6:
                raise UserError(_(
                    'Awarded quantity for "%(p)s" (%(t)s) exceeds the requested '
                    '%(r)s. Reduce the split first.',
                    p=rfq_line.product_id.display_name or rfq_line.name,
                    t=total, r=rfq_line.qty))
        # Lower-level users propose; a higher authority approves the PO.
        if self._award_needs_approval():
            self._propose_award_split(awards)
            return {'status': 'pending_approval',
                    'message': _('Split PO award submitted for manager approval.')}
        pos = self._create_split_purchase_orders(awards)
        return {'status': 'po_created', 'po_ids': pos.ids, 'po_names': pos.mapped('name')}

    def action_open_bargain_wizard(self):
        """Open the bargaining wizard pre-loaded with this RFQ's lines and the
        vendors whose quotes can still be revised."""
        self.ensure_one()
        quotations = self.vendor_quotation_ids.filtered(
            lambda q: q.state in ('sent', 'received'))
        if not quotations:
            raise UserError(_(
                'There are no vendors to bargain with yet — send the RFQ to '
                'vendors (or capture their quotes) first.'))
        return {
            'name': _('Request Better Price'),
            'type': 'ir.actions.act_window',
            'res_model': 'ebshel.pr.bargain.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_rfq_id': self.id,
                'default_quotation_ids': [(6, 0, quotations.ids)],
                'default_line_ids': [(0, 0, {
                    'rfq_line_id': line.id,
                    'target_price': line.target_price,
                    'item_message': line.bargain_note,
                }) for line in self.rfq_line_ids],
            },
        }

    def action_print_comparison(self):
        """Return the professional B&W vendor-comparison PDF report action."""
        self.ensure_one()
        return self.env.ref(
            'ebshel_purchase_request.action_report_rfq_comparison').report_action(self)

    def action_view_comparison(self):
        """Legacy list-based comparison (kept for backward compat)."""
        self.ensure_one()
        return {
            'name': _('Vendor Comparison – %s', self.name),
            'type': 'ir.actions.act_window',
            'res_model': 'ebshel.purchase.vendor.quotation',
            'view_mode': 'list,form',
            'domain': [('rfq_id', '=', self.id)],
            'context': {'default_rfq_id': self.id},
        }

    def action_open_comparison_view(self):
        """Open the advanced OWL-based vendor comparison dashboard."""
        self.ensure_one()
        if not self.received_count:
            raise UserError(_(
                'There are no vendor prices to compare yet. Capture the vendors\' '
                'quotations (mark them as received) before opening the comparison.'))
        return {
            'type': 'ir.actions.client',
            'tag': 'ebshel_rfq_comparison',
            'name': _('Vendor Comparison – %s', self.name),
            # params survive breadcrumb / URL restoration (context does not), so
            # the RFQ id is still available when the user clicks back into this
            # action from the breadcrumb. Keep it in context too for safety.
            'params': {'rfq_id': self.id},
            'context': {'rfq_id': self.id},
            'target': 'current',
        }

    # ── List KPI dashboard ────────────────────────────────────────────────────
    @api.model
    def retrieve_dashboard(self):
        """Counts powering the KPI tiles above the RFQ list (global + 'my')."""
        uid = self.env.uid
        today = fields.Date.context_today(self)
        month_start = today.replace(day=1)
        base = [('company_id', '=', self.env.company.id)]

        def count(extra):
            return self.search_count(base + extra)

        overdue_dom = [('deadline', '<', today),
                       ('state', 'not in', ('po_created', 'cancelled'))]
        month_dom = [('date_rfq', '>=', month_start)]

        def bucket(extra):
            return {
                'draft': count([('state', '=', 'draft')] + extra),
                'sent': count([('state', '=', 'sent')] + extra),
                'comparing': count([('state', '=', 'comparing')] + extra),
                'awarded': count([('state', '=', 'awarded')] + extra),
                'po_created': count([('state', '=', 'po_created')] + extra),
                'overdue': count(overdue_dom + extra),
                'this_month': count(month_dom + extra),
                'active': count([('state', 'in', ('sent', 'comparing'))] + extra),
            }

        return {
            'global': bucket([]),
            'my': bucket([('procurement_officer_id', '=', uid)]),
        }

    # ── OWL comparison data API ───────────────────────────────────────────────

    def get_comparison_data(self):
        """Return full comparison payload consumed by the OWL comparison view."""
        self.ensure_one()
        rfq = self
        currency = rfq.currency_id
        today = fields.Date.context_today(self)
        state_labels = dict(self._fields['state'].selection)

        lines_data = [
            {
                'id': line.id,
                'product_name': line.product_id.name or line.name or '',
                'full_name': line.name or '',
                'qty': line.qty,
                'uom': line.uom_id.name or '',
                'date_required': line.date_required.strftime('%d %b %Y') if line.date_required else '',
            }
            for line in rfq.rfq_line_ids
        ]

        line_count = len(lines_data)
        line_qty = {line['id']: line['qty'] for line in lines_data}

        vendors_data = []
        for q in rfq.vendor_quotation_ids:
            line_prices = {}
            for ql in q.line_ids:
                if ql.rfq_line_id:
                    line_prices[str(ql.rfq_line_id.id)] = {
                        'price_unit': ql.price_unit,
                        'discount': ql.discount,
                        'subtotal': ql.price_subtotal,
                        'total': ql.price_total,
                        'delivery_date': ql.delivery_date.strftime('%d %b %Y') if ql.delivery_date else '',
                    }
            quoted = sum(1 for ld in line_prices.values() if ld['price_unit'] > 0)
            vendors_data.append({
                'id': q.id,
                'vendor_id': q.vendor_id.id if q.vendor_id else False,
                'vendor_name': q.vendor_id.name if q.vendor_id else (q.new_vendor_name or _('New Vendor')),
                'is_new_vendor': q.is_new_vendor and not q.vendor_id,
                'total': q.amount_total,
                'untaxed': q.amount_untaxed,
                'tax': q.amount_tax,
                'delivery_days': q.delivery_days or 0,
                'payment_terms': q.payment_term_id.name if q.payment_term_id else '',
                'incoterm': q.incoterm_id.code if q.incoterm_id else '',
                'vendor_reference': q.vendor_reference or '',
                'date_quotation': q.date_quotation.strftime('%d %b %Y') if q.date_quotation else '',
                'validity_date': q.validity_date.strftime('%d %b %Y') if q.validity_date else '',
                'score': q.score,
                'rank': q.rank,
                'state': q.state,
                'is_winner': q.is_winner,
                'winner_justification': q.winner_justification or '',
                'lines': line_prices,
                'lines_quoted': quoted,
                'coverage': round(quoted / line_count * 100) if line_count else 0,
                'is_full_quote': line_count > 0 and quoted == line_count,
                'validity_expired': bool(q.validity_date and q.validity_date < today),
                'flags': [],
            })

        totals = [v['total'] for v in vendors_data if v['total'] > 0]
        best_total = min(totals) if totals else 0.0
        worst_total = max(totals) if totals else 0.0
        max_delivery = max((v['delivery_days'] for v in vendors_data if v['delivery_days']), default=0)

        # ── Auto risk & quality flags (procurement intelligence) ──────────────────
        for v in vendors_data:
            flags = []
            if v['validity_expired']:
                flags.append({'icon': '⌛', 'label': 'Quote expired', 'level': 'danger'})
            if v['total'] > 0 and not v['is_full_quote']:
                flags.append({'icon': '⚠', 'label': 'Partial quote (%s/%s)' % (v['lines_quoted'], line_count),
                              'level': 'warn'})
            if len(totals) >= 2 and v['total'] > 0 and v['total'] == worst_total:
                flags.append({'icon': '💸', 'label': 'Highest price', 'level': 'warn'})
            if max_delivery and v['delivery_days'] and v['delivery_days'] == max_delivery \
                    and len([x for x in vendors_data if x['delivery_days']]) >= 2:
                flags.append({'icon': '🐢', 'label': 'Slowest delivery', 'level': 'warn'})
            if v['is_new_vendor']:
                flags.append({'icon': '🆕', 'label': 'New vendor', 'level': 'info'})
            v['flags'] = flags
        avg_total = round(sum(totals) / len(totals), 2) if totals else 0.0
        savings = round(worst_total - best_total, 2) if len(totals) >= 2 else 0.0
        winner = next((v for v in vendors_data if v['is_winner']), None)

        # ── Optimal split basket: cheapest vendor per line (new vendors included,
        #    they are auto-registered on PO generation) ────────────────────────────
        split_total = 0.0
        split_lines = {}
        for line in lines_data:
            lid = str(line['id'])
            best_price, best_vid = None, False
            for v in vendors_data:
                ld = v['lines'].get(lid)
                if not ld or not ld['price_unit']:
                    continue
                net = ld['price_unit'] * (1 - (ld['discount'] or 0) / 100.0)
                if best_price is None or net < best_price:
                    best_price, best_vid = net, v['id']
            if best_price is not None:
                amount = round(best_price * line_qty[line['id']], 2)
                split_total += amount
                split_lines[lid] = {'vendor_id': best_vid, 'unit': round(best_price, 2), 'amount': amount}
        split_total = round(split_total, 2)
        split_savings = round(best_total - split_total, 2) if best_total and split_total else 0.0

        # ── Recommendation: rank-1 vendor with human-readable reasons ────────────
        recommendation = None
        ranked = sorted([v for v in vendors_data if v['total'] > 0], key=lambda v: v['rank'] or 999)
        if ranked:
            top = ranked[0]
            reasons = []
            if top['total'] == best_total:
                reasons.append(_('Lowest total cost'))
            if top['is_full_quote']:
                reasons.append(_('Quoted all %s lines', line_count))
            else:
                reasons.append(_('Quoted %(q)s of %(t)s lines', q=top['lines_quoted'], t=line_count))
            fastest = min((v['delivery_days'] for v in vendors_data if v['delivery_days']), default=0)
            if top['delivery_days'] and top['delivery_days'] == fastest:
                reasons.append(_('Fastest delivery (%s days)', top['delivery_days']))
            recommendation = {
                'vendor_id': top['id'],
                'vendor_name': top['vendor_name'],
                'score': top['score'],
                'total': top['total'],
                'reasons': reasons,
            }

        return {
            'rfq_id': rfq.id,
            'rfq_name': rfq.name,
            'state': rfq.state,
            'state_label': state_labels.get(rfq.state, rfq.state),
            'date_rfq': rfq.date_rfq.strftime('%d %b %Y') if rfq.date_rfq else '',
            'deadline': rfq.deadline.strftime('%d %b %Y') if rfq.deadline else '',
            'procurement_officer': rfq.procurement_officer_id.name or '',
            'currency_symbol': currency.symbol or currency.name or '$',
            'currency_position': currency.position or 'before',
            'lines': lines_data,
            'line_count': line_count,
            'vendors': vendors_data,
            'vendor_count': len(vendors_data),
            'savings': savings,
            'best_total': best_total,
            'worst_total': worst_total,
            'avg_total': avg_total,
            'split_total': split_total,
            'split_savings': split_savings,
            'split_lines': split_lines,
            'recommendation': recommendation,
            'winner': winner,
            'has_winner': bool(winner),
            'has_po': bool(rfq.po_count),
            'po_count': rfq.po_count,
            'po_id': rfq.purchase_order_id.id if rfq.purchase_order_id else False,
            'po_name': rfq.purchase_order_id.name if rfq.purchase_order_id else '',
            # ── Award approval workflow ──
            'award_state': rfq.award_state,
            'award_mode': rfq.award_mode or '',
            'award_note': rfq.award_note or '',
            'award_rejection_reason': rfq.award_rejection_reason or '',
            'award_proposed_by': rfq.award_proposed_by_id.name or '',
            'award_approved_by': rfq.award_approved_by_id.name or '',
            'can_approve_award': self.env.user.has_group(
                'ebshel_purchase_request.group_pr_admin'),
        }

    def action_award_winner(self, quotation_id, justification, create_po=False):
        """Award a vendor from the OWL comparison view / inline widget.

        Returns a dict the caller can act on:
          - {'status': 'awarded'}                          → winner crowned
          - {'status': 'po_created', 'po_id', 'po_name'}   → winner crowned + PO made
          - {'status': 'need_vendor', 'action': {...}}     → new vendor must be
            registered first; `action` is the vendor-creation wizard to open.
        """
        self.ensure_one()
        quotation = self.env['ebshel.purchase.vendor.quotation'].browse(quotation_id)
        if quotation.rfq_id != self:
            raise UserError(_('Quotation does not belong to this RFQ.'))
        if self.state not in ('draft', 'sent', 'comparing', 'awarded'):
            raise UserError(_('This RFQ can no longer be awarded.'))

        quotation.winner_justification = justification

        # New, unregistered vendor → hand back the registration wizard action.
        if quotation.is_new_vendor and not quotation.vendor_id:
            wizard_action = quotation.action_select_winner()
            if isinstance(wizard_action, dict):
                wizard_action.setdefault('context', {})
                wizard_action['context'].update({
                    'default_proceed_to_award': True,
                    'default_auto_create_po': create_po,
                    'default_justification': justification,
                })
                return {'status': 'need_vendor', 'action': wizard_action}

        quotation.action_select_winner()

        if create_po:
            # Lower-level users propose; a higher authority approves the PO.
            if self._award_needs_approval():
                self._propose_award_single(quotation, justification)
                return {'status': 'pending_approval',
                        'message': _('PO award submitted for manager approval.')}
            po = self._create_purchase_order_from_winner()
            return {'status': 'po_created', 'po_id': po.id, 'po_name': po.name}
        return {'status': 'awarded'}

    # ── Award approval gate ───────────────────────────────────────────────────
    def _award_needs_approval(self):
        """When the company requires PO-award approval, awarding creates a
        proposal that a higher authority (Administrator) must approve before the
        purchase order(s) are created. An approver who awards still routes through
        the proposal, but can immediately approve it."""
        self.ensure_one()
        if not self.company_id.pr_require_po_approval:
            return False
        # Already approved (e.g. just approved by a manager) → go straight to PO.
        return self.award_state != 'approved'

    def _notify_award_approvers(self):
        """Post a chatter note + activity for the managers who must approve."""
        self.ensure_one()
        managers = self.env.ref('ebshel_purchase_request.group_pr_admin').all_user_ids
        self.message_post(
            body=Markup(_(
                "<p><b>Purchase Award Approval Required</b></p>"
                "<p><b>%(user)s</b> has submitted an award recommendation for review and approval.</p>"
                "<ul>"
                "<li><b>Award Method:</b> %(mode)s</li>"
                "<li><b>Request Reference:</b> %(pr)s</li>"
                "</ul>"
                "<p>Please review the recommendation and approve or reject it at your earliest convenience.</p>"
            )) % {
                'user': self.env.user.name,
                'mode': dict(self._fields['award_mode'].selection).get(self.award_mode, ''),
                'pr': self.name,
            },
            partner_ids=managers.partner_id.ids,
            subtype_xmlid='mail.mt_comment',
        )

    # ── Quantity-increase approval (mirrors award approval) ────────────────────
    def _notify_qty_approvers(self):
        """Post a chatter note to the Administrators who must approve a line
        quantity that exceeds the requested amount."""
        self.ensure_one()
        managers = self.env.ref('ebshel_purchase_request.group_pr_admin').all_user_ids
        over = self.rfq_line_ids.filtered(
            lambda l: l.request_line_id and l.qty > l.request_line_id.qty)
        rows = Markup('').join(
            Markup('<li>%(p)s: requested %(req)s → RFQ %(rfq)s</li>') % {
                'p': escape(l.product_id.display_name or l.name),
                'req': l.request_line_id.qty, 'rfq': l.qty,
            } for l in over)
        self.message_post(
            body=Markup(_(
                "<p><b>RFQ Quantity Approval Required</b></p>"
                "<p><b>%(user)s</b> set an RFQ line quantity above the requested "
                "amount on <b>%(rfq)s</b>:</p><ul>%(rows)s</ul>"
                "<p>Please approve or reject before this RFQ is sent to vendors.</p>"
            )) % {'user': self.env.user.name, 'rfq': self.name, 'rows': rows},
            partner_ids=managers.partner_id.ids,
            subtype_xmlid='mail.mt_comment',
        )

    def action_request_qty_approval(self):
        """Submit the RFQ's quantity increase for Administrator approval."""
        self.ensure_one()
        if not self.company_id.rfq_qty_increase_approval:
            raise UserError(_('Quantity-increase approval is not enabled.'))
        if not self.has_qty_increase:
            raise UserError(_(
                'No RFQ line exceeds its requested quantity — nothing to approve.'))
        if self.qty_approval_state == 'pending':
            raise UserError(_('A quantity approval is already pending.'))
        self.write({
            'qty_approval_state': 'pending',
            'qty_proposed_by_id': self.env.user.id,
            'qty_approval_reason': False,
        })
        self._notify_qty_approvers()

    def action_approve_qty(self):
        """Administrator approves the line-quantity increase."""
        self.ensure_one()
        if not self.env.user.has_group('ebshel_purchase_request.group_pr_admin'):
            raise UserError(_('Only an Administrator can approve a quantity increase.'))
        if self.qty_approval_state != 'pending':
            raise UserError(_('There is no quantity increase awaiting approval.'))
        self.write({
            'qty_approval_state': 'approved',
            'qty_approved_by_id': self.env.user.id,
        })
        self.message_post(
            body=Markup(_('✅ Quantity increase <b>approved</b> by %s. The RFQ can '
                          'now be sent to vendors.')) % self.env.user.name,
            subtype_xmlid='mail.mt_comment')

    def action_reject_qty(self, reason=None):
        """Administrator rejects the line-quantity increase."""
        self.ensure_one()
        if not self.env.user.has_group('ebshel_purchase_request.group_pr_admin'):
            raise UserError(_('Only an Administrator can reject a quantity increase.'))
        if self.qty_approval_state != 'pending':
            raise UserError(_('There is no quantity increase awaiting approval.'))
        self.write({
            'qty_approval_state': 'rejected',
            'qty_approval_reason': reason or '',
        })
        self.message_post(
            body=Markup(_('⛔ Quantity increase <b>rejected</b> by %(u)s. %(r)s '
                          'Reduce the quantity to the requested amount or request '
                          'approval again.')) % {
                'u': self.env.user.name,
                'r': (_('Reason: %s.', reason) if reason else '')},
            subtype_xmlid='mail.mt_comment')

    def _propose_award_single(self, quotation, justification):
        self.award_line_ids.unlink()
        self.write({
            'state': 'awarded',
            'award_mode': 'single',
            'award_winner_id': quotation.id,
            'award_note': justification or '',
            'award_state': 'pending',
            'award_proposed_by_id': self.env.user.id,
            'award_rejection_reason': False,
        })
        self._notify_award_approvers()

    def _propose_award_split(self, awards):
        self.award_line_ids.unlink()
        self.write({
            'state': 'awarded',
            'award_mode': 'split',
            'award_winner_id': False,
            'award_state': 'pending',
            'award_proposed_by_id': self.env.user.id,
            'award_rejection_reason': False,
            'award_line_ids': [(0, 0, {
                'rfq_line_id': a['rfq_line'].id,
                'quotation_id': a['quotation'].id,
                'qty': a['qty'],
            }) for a in awards],
        })
        self._notify_award_approvers()

    def _award_pending_notification(self):
        """Sticky notification returned to wizards/buttons after a proposal."""
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'warning',
                'title': _('Submitted for approval'),
                'message': _('The PO award has been submitted to a higher '
                             'authority. The purchase order will be created once '
                             'it is approved.'),
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def action_approve_award(self):
        """Higher authority approves the proposed award → create the PO(s)."""
        self.ensure_one()
        if not self.env.user.has_group('ebshel_purchase_request.group_pr_admin'):
            raise UserError(_('Only a manager can approve a PO award.'))
        if self.award_state != 'pending':
            raise UserError(_('There is no PO award awaiting approval.'))
        self.write({'award_state': 'approved', 'award_approved_by_id': self.env.user.id})
        if self.award_mode == 'split':
            awards = [{'quotation': l.quotation_id, 'rfq_line': l.rfq_line_id, 'qty': l.qty}
                      for l in self.award_line_ids]
            pos = self._create_split_purchase_orders(awards)
        else:
            po = self._create_purchase_order_from_winner()
            pos = po
        self.message_post(
            body=Markup(_('✅ Award <b>approved</b> by %s — purchase order(s) created.'))
            % self.env.user.name,
            subtype_xmlid='mail.mt_comment')
        return {'status': 'po_created', 'po_ids': pos.ids, 'po_names': pos.mapped('name')}

    def action_reject_award(self, reason=None):
        """Higher authority rejects the proposed award (sends it back)."""
        self.ensure_one()
        if not self.env.user.has_group('ebshel_purchase_request.group_pr_admin'):
            raise UserError(_('Only a manager can reject a PO award.'))
        if self.award_state != 'pending':
            raise UserError(_('There is no PO award awaiting approval.'))
        self.write({'award_state': 'rejected', 'award_rejection_reason': reason or '', 'award_mode': False})
        self.message_post(
            body=Markup(_('⛔ Award proposal <b>rejected</b> by %(u)s. %(r)s')) % {
                'u': self.env.user.name,
                'r': (_('Reason: %s', reason) if reason else '')},
            subtype_xmlid='mail.mt_comment')
        return {'status': 'rejected'}
    
    def action_revert_to_comparing(self):
        for rec in self :
            if rec.state != 'awarded':
                raise UserError(_("Only RFQ in Vendor Awarded state can be reverted back to comparing state"))
            if rec.purchase_order_id or rec.po_count :
                raise UserError(_("RFQ already linked with a purchase order can't be reverted back, Cancel the Purchase Order First."))
            rec.vendor_quotation_ids.filtered(lambda q : q.state in ('selected','rejected')).write({
                'is_winner': False,
                'state': 'received'
            })
            rec.write({
                'state': 'comparing',
                'award_state': 'none',
                'award_mode': False,
                'award_winner_id': False,
                'award_note': False,
                'award_proposed_by_id': False,
                'award_approved_by_id': False,
                'award_rejection_reason': False,
            })
            rec.award_line_ids.unlink()

    def action_view_purchase_order(self):
        self.ensure_one()
        # A split award can produce several POs — gather them all.
        po_lines = self.rfq_line_ids.mapped('request_line_id.purchase_line_ids')
        pos = po_lines.mapped('order_id') | self.purchase_order_id
        # `views` is set explicitly so the action is valid when handed straight to
        # the JS action service (which expects it) as well as from a form button.
        if not pos:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'type': 'warning',
                    'message': _('No purchase order has been created for this RFQ yet.'),
                },
            }
        # Use the standard "Purchase Orders" action so the breadcrumb/views are
        # the Purchase Order ones (not "Requests for Quotation").
        action = self.env['ir.actions.act_window']._for_xml_id('purchase.purchase_form_action')
        action['context'] = {}
        action['domain'] = [('id', 'in', pos.ids)]
        if len(pos) == 1:
            action['name'] = _('Purchase Order')
            action['views'] = [(self.env.ref('purchase.purchase_order_form').id, 'form')]
            action['res_id'] = pos.id
        else:
            action['name'] = _('Purchase Orders – %s', self.name)
        return action

    def action_view_requests(self):
        """Smart-button: the source purchase request(s) behind this RFQ."""
        self.ensure_one()
        requests = self.request_ids
        action = {
            'name': _('Source Requests – %s', self.name),
            'type': 'ir.actions.act_window',
            'res_model': 'ebshel.purchase.request',
            'view_mode': 'list,form',
            'domain': [('id', 'in', requests.ids)],
        }
        if len(requests) == 1:
            action.update(view_mode='form', res_id=requests.id)
        return action

    def action_view_quotations(self):
        """Smart-button: all vendor quotations on this RFQ."""
        self.ensure_one()
        return {
            'name': _('Vendor Quotations – %s', self.name),
            'type': 'ir.actions.act_window',
            'res_model': 'ebshel.purchase.vendor.quotation',
            'view_mode': 'list,form',
            'domain': [('rfq_id', '=', self.id)],
            'context': {'default_rfq_id': self.id},
        }


class RfqAwardLine(models.Model):
    """A single proposed award allocation, held while the award awaits manager
    approval. On approval these are turned into purchase-order lines."""
    _name = 'ebshel.rfq.award.line'
    _description = 'Proposed RFQ Award Line (pending approval)'

    rfq_id = fields.Many2one(
        'ebshel.purchase.rfq', required=True, ondelete='cascade', index=True)
    rfq_line_id = fields.Many2one(
        'ebshel.purchase.rfq.line', string='Product Line',
        required=True, ondelete='cascade')
    quotation_id = fields.Many2one(
        'ebshel.purchase.vendor.quotation', string='Awarded Vendor',
        required=True, ondelete='cascade')
    qty = fields.Float('Awarded Qty', digits='Product Unit')
