# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.
from dateutil.relativedelta import relativedelta
from markupsafe import Markup
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

class PurchaseRequest(models.Model):
    _name = 'ebshel.purchase.request'
    _description = 'Purchase Request'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_request desc, id desc'
    _rec_name = 'name'

    # ── Identity ──────────────────────────────────────────────────────────────
    name = fields.Char(
        'Reference', required=True, copy=False, readonly=True,
        default=lambda self: _('New'),
        tracking=True,
    )
    description = fields.Char('Purpose / Title', required=True, tracking=True)
    priority = fields.Selection(
        [
            ('0', 'Normal'),
            ('1', 'High'),
            ('2', 'Urgent'),
            ('3', 'Very Urgent'),
        ],
        string='Priority',
        default='0',
        tracking=True,
    )
    source = fields.Selection([
        ('manual', 'Manual'),
        ('auto_reorder', 'Auto-Reorder'),
        ('job_card', 'Job Card'),
        ('oem', 'OEM / Vehicle'),
        ('other', 'Other'),
    ], string='Source', default='manual', tracking=True, index=True,
        help='Where this request originated — drives the unified intake view '
             'and reporting.')
    origin = fields.Char('Source Document', copy=False,
        help='Free-text reference to the originating document (job card, '
             'reorder rule, etc.).')

    # ── State ─────────────────────────────────────────────────────────────────
    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
        ('closed', 'Closed'),
    ], string='Status', default='draft', required=True,
        copy=False, tracking=True, index=True,
    )
    current_approval_level = fields.Integer(
        'Current Approval Level', default=0, copy=False,
        help='Sequence of the approval level currently pending.',
    )
    rejection_reason = fields.Text('Rejection Reason', copy=False)

    # ── Organisational ────────────────────────────────────────────────────────
    requester_id = fields.Many2one(
        'res.users', string='Requested By', required=True,
        default=lambda self: self.env.user, tracking=True,
        domain=[('share', '=', False)],
    )
    analytic_account_id = fields.Many2one(
        'account.analytic.account', string='Department / Cost Center',
        tracking=True, index=True,
        domain="[('is_cost_center', '=', True)]",
        help='Analytic account representing the requesting department or cost '
             'center. Only analytic accounts flagged as a department / cost '
             'center are selectable. Drives department-wise approval routing, '
             'budgeting and reporting, and seeds each line\'s analytic distribution.',
    )
    product_category_id = fields.Many2one(
        'product.category', string='Product Category',
        help='Optional. When set, the product picker on each request line is '
             'restricted to products in this category (and its sub-categories).',
    )
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, index=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id',
        string='Currency', readonly=True, store=True,
    )
    allow_approved_qty_edit = fields.Boolean(
        related='company_id.pr_allow_approved_qty_edit', readonly=True,
        help='Technical: drives visibility of the "Update Quantities" action.',
    )
    company_allow_product_change = fields.Boolean(
        related='company_id.pr_allow_product_change', readonly=True,
        help='Technical: drives visibility of the "Change Product" action.',
    )
    has_product_change_line = fields.Boolean(
        compute='_compute_has_product_change_line',
        help='Technical: True when at least one active line allows product change.',
    )

    @api.depends('line_ids.allow_product_change', 'line_ids.cancelled')
    def _compute_has_product_change_line(self):
        for rec in self:
            rec.has_product_change_line = any(
                l.allow_product_change and not l.cancelled for l in rec.line_ids)

    # ── Dates ─────────────────────────────────────────────────────────────────
    date_request = fields.Date(
        'Request Date', required=True, index=True,
        default=fields.Date.context_today, tracking=True,
    )
    date_required = fields.Date(
        'Required By', tracking=True,
        help='Latest date by which all products must be delivered.',
    )

    # ── Lines & Relations ─────────────────────────────────────────────────────
    line_ids = fields.One2many(
        'ebshel.purchase.request.line', 'request_id',
        string='Request Lines', copy=True,
    )
    approval_log_ids = fields.One2many(
        'ebshel.purchase.request.approval.log', 'request_id',
        string='Approval History', copy=False,
    )
    notes = fields.Html('Internal Notes')

    # ── Computed ──────────────────────────────────────────────────────────────
    amount_total = fields.Monetary(
        'Estimated Total', compute='_compute_amount_total', store=True,
        currency_field='currency_id',
    )
    rfq_count = fields.Integer('RFQs', compute='_compute_rfq_count')
    quotation_count = fields.Integer('Quotations', compute='_compute_quotation_count')
    po_count = fields.Integer('Purchase Orders', compute='_compute_po_count')
    move_count = fields.Integer('Receipts', compute='_compute_move_count')
    allocation_count = fields.Integer('Allocations', compute='_compute_allocation_count')
    pending_approver_ids = fields.Many2many(
        'res.users',
        relation='pr_pending_approver_rel',
        column1='request_id', column2='user_id',
        string='Pending Approvers',
        copy=False,
    )

    # ── Cockpit insights (power the form dashboard) ────────────────────────────
    line_count = fields.Integer('Lines', compute='_compute_pr_insights')
    fulfilment_pct = fields.Integer(
        'Fulfilment', compute='_compute_pr_insights',
        help='Share of the requested quantity that has been received.')
    progress_pct = fields.Integer('Progress', compute='_compute_pr_insights')
    days_to_required = fields.Integer('Days to Required', compute='_compute_required_state')
    required_state = fields.Selection([
        ('none', 'No date'),
        ('overdue', 'Overdue'),
        ('today', 'Due today'),
        ('soon', 'Due soon'),
        ('ok', 'On track'),
    ], string='Required-by Status', compute='_compute_required_state')

    # ── Department / Cost-Centre budget (current period) ───────────────────────
    dept_has_budget = fields.Boolean(compute='_compute_dept_budget')
    dept_budget_amount = fields.Monetary(
        'Department Budget', compute='_compute_dept_budget', currency_field='currency_id')
    dept_budget_committed = fields.Monetary(
        'Committed', compute='_compute_dept_budget', currency_field='currency_id')
    dept_budget_achieved = fields.Monetary(
        'Spent', compute='_compute_dept_budget', currency_field='currency_id')
    dept_budget_available = fields.Monetary(
        'Budget Available', compute='_compute_dept_budget', currency_field='currency_id',
        help='Budgeted amount minus what is already committed, for the current period.')
    dept_budget_usage_pct = fields.Integer(
        'Budget Used', compute='_compute_dept_budget')
    dept_budget_remaining_after = fields.Monetary(
        'Available After This Request', compute='_compute_dept_budget_after',
        currency_field='currency_id')
    dept_over_budget = fields.Boolean(compute='_compute_dept_budget_after')

    pr_for_amount_exceed_budget = fields.Boolean(related="company_id.pr_for_amount_exceed_budget")

    # ── Over-budget (budget-exceed) approval ───────────────────────────────────
    budget_approval_state = fields.Selection([
        ('none', 'Not Required'),
        ('pending', 'Pending Budget Approval'),
        ('approved', 'Budget Approved'),
        ('rejected', 'Budget Rejected'),
    ], string='Budget Approval', default='none', copy=False, tracking=True,
        readonly=True,
        help='Tracks the administrator approval required when a request exceeds '
             'the department budget and the company allows over-budget requests.')
    budget_approved_by = fields.Many2one(
        'res.users', string='Budget Approved By', copy=False, readonly=True)
    budget_approval_date = fields.Datetime(
        'Budget Approval Date', copy=False, readonly=True)
    budget_approved_amount = fields.Monetary(
        'Budget-Approved Amount', currency_field='currency_id', copy=False,
        readonly=True,
        help='Estimated total that was approved to exceed the department budget. '
             'If the request grows beyond this, budget approval is needed again.')
    budget_approval_required = fields.Boolean(
        compute='_compute_budget_approval_required',
        help='Technical: over budget, allowed by policy, and not yet approved '
             'for the current amount — must get budget approval before submitting.')
    budget_approval_blocked = fields.Boolean(
        compute='_compute_budget_approval_required',
        help='Technical: over budget while the company forbids over-budget '
             'requests — cannot be submitted at all.')

    # ── Compute methods ───────────────────────────────────────────────────────
    @api.depends('analytic_account_id', 'company_id', 'date_request')
    def _compute_dept_budget(self):
        # Figures come from purchase.analytic.line._budget_figures: the confirmed
        # budget and the encumbrance ledger (open PRs = Purchased, open POs =
        # Invoiced, sum = Committed). The request's own request-type entries are
        # excluded so a record never blocks itself and `available` reads as "what
        # is left before this request".
        Line = self.env['purchase.analytic.line']
        today = fields.Date.context_today(self)
        for rec in self:
            rec.dept_has_budget = False
            rec.dept_budget_amount = 0.0
            rec.dept_budget_committed = 0.0
            rec.dept_budget_achieved = 0.0
            rec.dept_budget_available = 0.0
            rec.dept_budget_usage_pct = 0
            if not rec.analytic_account_id:
                continue
            ref_date = rec.date_request or today
            fig = Line._budget_figures(
                rec.analytic_account_id.id, rec.company_id.id, ref_date,
                exclude_request_ids=rec.ids)
            if not fig['has_budget']:
                continue
            rec.dept_has_budget = True
            rec.dept_budget_amount = fig['budget']
            rec.dept_budget_committed = fig['committed']
            rec.dept_budget_achieved = fig['achieved']
            rec.dept_budget_available = fig['available']
            rec.dept_budget_usage_pct = (
                round((fig['achieved'] + fig['committed']) / fig['budget'] * 100)
                if fig['budget'] else 0)

    @api.depends('amount_total', 'dept_budget_available', 'dept_budget_amount', 'dept_has_budget')
    def _compute_dept_budget_after(self):
        # Cheap arithmetic only — kept separate from the budget-line lookup so
        # editing request lines (which changes amount_total) does not re-query
        # the budget every time.
        for rec in self:
            rec.dept_budget_remaining_after = rec.dept_budget_available - rec.amount_total
            rec.dept_over_budget = (
                rec.dept_has_budget and rec.dept_budget_amount > 0
                and rec.amount_total > rec.dept_budget_available)

    @api.depends('dept_over_budget', 'pr_for_amount_exceed_budget',
                 'budget_approval_state', 'budget_approved_amount', 'amount_total')
    def _compute_budget_approval_required(self):
        for rec in self:
            over = rec.dept_over_budget
            # Flag off: over-budget requests are simply not allowed.
            rec.budget_approval_blocked = bool(
                over and not rec.pr_for_amount_exceed_budget)
            # Flag on: needs (re-)approval unless already approved for at least
            # the current amount.
            approved_ok = (rec.budget_approval_state == 'approved'
                           and rec.amount_total <= rec.budget_approved_amount)
            rec.budget_approval_required = bool(
                over and rec.pr_for_amount_exceed_budget and not approved_ok)

    @api.depends('line_ids.qty', 'line_ids.qty_done', 'line_ids.cancelled', 'state')
    def _compute_pr_insights(self):
        steps = {'draft': 10, 'submitted': 45, 'approved': 70,
                 'closed': 100, 'rejected': 0, 'cancelled': 0}
        for rec in self:
            active = rec.line_ids.filtered(lambda l: not l.cancelled)
            rec.line_count = len(active)
            requested = sum(active.mapped('qty'))
            received = sum(active.mapped('qty_done'))
            rec.fulfilment_pct = round(received / requested * 100) if requested else 0
            base = steps.get(rec.state, 0)
            # Once approved, blend in real receipt progress for the last leg.
            if rec.state == 'approved' and rec.fulfilment_pct:
                base = 70 + round(rec.fulfilment_pct * 0.30)
            rec.progress_pct = base

    @api.depends('date_required', 'state')
    def _compute_required_state(self):
        today = fields.Date.context_today(self)
        for rec in self:
            if not rec.date_required or rec.state in ('closed', 'rejected', 'cancelled'):
                rec.days_to_required = 0
                rec.required_state = 'none'
                continue
            delta = (rec.date_required - today).days
            rec.days_to_required = delta
            if delta < 0:
                rec.required_state = 'overdue'
            elif delta == 0:
                rec.required_state = 'today'
            elif delta <= 5:
                rec.required_state = 'soon'
            else:
                rec.required_state = 'ok'

    @api.depends('line_ids.estimated_total', 'line_ids.cancelled')
    def _compute_amount_total(self):
        for rec in self:
            rec.amount_total = sum(
                rec.line_ids.filtered(lambda l: not l.cancelled).mapped('estimated_total')
            )

    @api.depends('line_ids.rfq_line_ids.rfq_id',
                 'line_ids.rfq_line_ids.rfq_id.vendor_quotation_ids')
    def _compute_rfq_count(self):
        # Batched: one search for the whole recordset instead of one per row
        # (these fields appear in list views — a per-record search would be N+1).
        by_req = self._rfqs_by_request()
        for rec in self:
            rec.rfq_count = len(by_req.get(rec.id, self.env['ebshel.purchase.rfq']))

    @api.depends('line_ids.rfq_line_ids.rfq_id.vendor_quotation_ids')
    def _compute_quotation_count(self):
        by_req = self._rfqs_by_request()
        for rec in self:
            rfqs = by_req.get(rec.id)
            rec.quotation_count = len(rfqs.vendor_quotation_ids) if rfqs else 0

    def _rfqs_by_request(self):
        """{request_id: rfq recordset} for every request in self, in one search."""
        Rfq = self.env['ebshel.purchase.rfq']
        by_req = {}
        if not self.ids:
            return by_req
        lines = self.env['ebshel.purchase.rfq.line'].search(
            [('request_line_id.request_id', 'in', self.ids)])
        for line in lines:
            req_id = line.request_line_id.request_id.id
            by_req[req_id] = by_req.get(req_id, Rfq) | line.rfq_id
        return by_req

    @api.depends('line_ids.allocation_ids')
    def _compute_allocation_count(self):
        for rec in self:
            rec.allocation_count = len(rec.line_ids.mapped('allocation_ids'))

    @api.depends('line_ids.purchase_line_ids', 'line_ids.po_line_id')
    def _compute_po_count(self):
        for rec in self:
            po_lines = rec.line_ids.mapped('purchase_line_ids') | rec.line_ids.mapped('po_line_id')
            rec.po_count = len(po_lines.mapped('order_id'))

    @api.depends('line_ids.allocation_ids.stock_move_id')
    def _compute_move_count(self):
        for rec in self:
            moves = rec.line_ids.mapped('allocation_ids.stock_move_id')
            rec.move_count = len(moves)

    # ── ORM overrides ─────────────────────────────────────────────────────────
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('ebshel.purchase.request') or _('New')
        return super().create(vals_list)

    def write(self, vals):
        res = super().write(vals)
        # A state change (submit -> reserves budget; cancel/reject/reset/close ->
        # releases it) shifts the encumbrance this request holds. The submission
        # budget gate runs in action_submit *before* the state is written, so this
        # only records the reservation once the request has cleared the gate.
        if 'state' in vals:
            self.env['purchase.analytic.line']._sync_request_lines(
                self.line_ids)
        return res

    def unlink(self):
        if any(r.state != 'draft' for r in self):
            raise UserError(_('Only Draft purchase requests can be deleted. '
                              'Cancel the request instead.'))
        return super().unlink()
    # ── Over-budget (budget-exceed) approval workflow ──────────────────────────
    def _budget_approver_users(self):
        """PR administrators who approve over-budget requests."""
        group = self.env.ref(
            'ebshel_purchase_request.group_pr_admin', raise_if_not_found=False)
        users = group.all_user_ids if group else self.env['res.users']
        return users.filtered(lambda u: u.active and not u.share)

    def _check_budget_before_submit(self):
        """Gate submission on the department budget: hard-block when the company
        forbids over-budget requests, otherwise require administrator budget
        approval first."""
        self.ensure_one()
        if not self.dept_over_budget:
            return
        if not self.pr_for_amount_exceed_budget:
            raise UserError(_(
                'This request (%(amt)s) exceeds the budget still available for '
                '%(dept)s (%(avail)s). Over-budget purchase requests are not '
                'allowed — reduce the total to within budget.',
                amt=self.amount_total,
                dept=self.analytic_account_id.display_name,
                avail=self.dept_budget_available))
        approved_ok = (self.budget_approval_state == 'approved'
                       and self.amount_total <= self.budget_approved_amount)
        if not approved_ok:
            raise UserError(_(
                'This request exceeds the department budget and needs '
                'administrator budget approval before it can be submitted. '
                'Use "Request Budget Approval" first.'))

    def action_request_budget_approval(self):
        """Requester action: route an over-budget draft to the PR administrators
        for budget approval."""
        self.ensure_one()
        if self.state != 'draft':
            raise UserError(_(
                'Budget approval can be requested only on a draft request.'))
        if not self.line_ids.filtered(lambda l: not l.cancelled):
            raise UserError(_(
                'Add at least one product line before requesting budget approval.'))
        if not self.dept_over_budget:
            raise UserError(_(
                'This request is within the department budget — no budget '
                'approval is needed.'))
        if not self.pr_for_amount_exceed_budget:
            raise UserError(_(
                'Purchase requests above the department budget are not allowed. '
                'Reduce the request total, or ask an administrator to enable '
                '"Allow to create even if the department budget exceeded" in '
                'Settings.'))
        self.write({'budget_approval_state': 'pending'})
        self._create_approval_log(
            'budget_requested',
            comments=_(
                'Budget-exceed approval requested (total %(amt)s vs available '
                '%(avail)s).',
                amt=self.amount_total, avail=self.dept_budget_available))
        self._notify_budget_approvers()

    def _notify_budget_approvers(self):
        """Notify the PR administrators (chatter message + to-do activity) that an
        over-budget request awaits their budget approval."""
        self.ensure_one()
        approvers = self._budget_approver_users()
        if not approvers:
            raise UserError(_(
                'No Purchase Request Administrator is configured to approve '
                'over-budget requests. Please assign the Administrator role to a '
                'user first.'))
        for approver in approvers:
            self.with_context(
                mail_create_nosubscribe=True, mail_notrack=True,
            ).activity_schedule(
                'mail.mail_activity_data_todo',
                user_id=approver.id,
                note=_('Purchase request %s exceeds the department budget and '
                       'needs your budget approval.', self.name),
            )
        self.message_post(
            body=Markup(_(
                'Purchase request <b>%(name)s</b> exceeds the available budget '
                'for <b>%(dept)s</b> (estimated <b>%(amt)s</b> vs available '
                '<b>%(avail)s</b>) and has been sent to the administrators for '
                'budget approval: %(admins)s.')) % {
                'name': self.name,
                'dept': self.analytic_account_id.display_name,
                'amt': self.amount_total,
                'avail': self.dept_budget_available,
                'admins': ', '.join(approvers.mapped('name')),
            },
            partner_ids=approvers.mapped('partner_id').ids,
            subtype_xmlid='mail.mt_comment',
        )

    def action_budget_approve(self):
        """Administrator action: approve an over-budget request so the requester
        can submit it through the normal approval workflow."""
        self.ensure_one()
        if not self.env.user.has_group('ebshel_purchase_request.group_pr_admin'):
            raise UserError(_(
                'Only a Purchase Request Administrator can approve an '
                'over-budget request.'))
        if self.budget_approval_state != 'pending':
            raise UserError(_(
                'Only requests pending budget approval can be approved here.'))
        self.write({
            'budget_approval_state': 'approved',
            'budget_approved_by': self.env.user.id,
            'budget_approval_date': fields.Datetime.now(),
            'budget_approved_amount': self.amount_total,
        })
        self._create_approval_log(
            'budget_approved',
            comments=_('Over-budget request approved (up to %s).', self.amount_total))
        self.message_post(
            body=Markup(_(
                'Budget exceed <b>approved</b> by %(user)s. The request can now '
                'be submitted for the normal approval workflow.')) % {
                'user': self.env.user.name},
            partner_ids=self.requester_id.partner_id.ids,
            subtype_xmlid='mail.mt_comment')
        self.activity_feedback(['mail.mail_activity_data_todo'])

    def action_budget_reject(self):
        """Administrator action: reject an over-budget request."""
        self.ensure_one()
        if not self.env.user.has_group('ebshel_purchase_request.group_pr_admin'):
            raise UserError(_(
                'Only a Purchase Request Administrator can reject an over-budget '
                'request.'))
        if self.budget_approval_state != 'pending':
            raise UserError(_(
                'Only requests pending budget approval can be rejected here.'))
        self.write({'budget_approval_state': 'rejected', 'budget_approved_amount': 0.0})
        self._create_approval_log(
            'budget_rejected', comments=_('Over-budget request rejected.'))
        self.message_post(
            body=Markup(_(
                'Budget exceed <b>rejected</b> by %(user)s. Reduce the request '
                'total to within budget before submitting.')) % {
                'user': self.env.user.name},
            partner_ids=self.requester_id.partner_id.ids,
            subtype_xmlid='mail.mt_comment')
        self.activity_feedback(['mail.mail_activity_data_todo'])

    # ── Workflow actions ──────────────────────────────────────────────────────
    def action_submit(self):
        for rec in self:
            if not rec.line_ids:
                raise UserError(_('Please add at least one product line before submitting.'))
            rec._validate_lines()
            rec._check_budget_before_submit()
            # Clear any approver product suggestions now that the requester has
            # revised and is resubmitting.
            suggested = rec.line_ids.filtered('suggested_product_id')
            if suggested:
                suggested.write({'suggested_product_id': False})
            # Park the request AT the first applicable level (its sequence), so the
            # first approval evaluates and clears that level rather than re-routing.
            levels = rec._get_applicable_approval_levels()
            rec.write({
                'state': 'submitted',
                'current_approval_level': levels[0].sequence if levels else 0,
            })
            rec._create_approval_log('submitted', comments=_('Request submitted for approval.'))
            # Auto-approval: small requests skip the approval chain entirely.
            company = rec.company_id
            threshold = company.pr_auto_approve_amount
            if (company.pr_auto_approve_enabled and threshold
                    and rec.amount_total <= threshold):
                rec.write({'state': 'approved', 'pending_approver_ids': [(5, 0, 0)]})
                rec._create_approval_log(
                    'approved', level_name=_('Auto-approved'),
                    comments=_('Below the auto-approval threshold (%s).', threshold))
                rec.message_post(
                    body=Markup(_('Purchase request <b>%s</b> auto-approved (under threshold).')) % (rec.name,),
                    subtype_xmlid='mail.mt_comment')
                rec._send_approval_notification()
                continue
            rec._set_pending_approvers()
            rec._notify_next_approvers()
            rec.message_post(
                body=Markup(_('Purchase request <b>%s</b> has been submitted for approval.')) % (rec.name,),
                subtype_xmlid='mail.mt_note',
            )

    def action_approve(self):
        """Advance through approval levels; mark approved when all pass.

        A level only advances once its policy (any / all / quorum) is met, so a
        level with several approvers stays pending until enough have signed off.
        """
        self.ensure_one()
        self._check_can_approve()
        level = self._get_next_approval_level()
        self._create_approval_log(
            'approved',
            level_id=level.id if level else False,
            level_name=level.name if level else _('Final'),
        )
        # Multi-approver levels: hold here until the quorum/all policy is met.
        if level:
            approved_ids = self._approved_user_ids_at_level(level)
            if not level.is_satisfied_by(approved_ids):
                remaining = level.get_approver_users().filtered(
                    lambda u: u.id not in approved_ids)
                self.pending_approver_ids = [(6, 0, remaining.ids)]
                # self.message_post(
                #     body=_(
                #         'Approved by <b>%(user)s</b> at level <b>%(lvl)s</b> '
                #         '(%(have)s of %(need)s required). Awaiting %(rem)s more.',
                #         user=self.env.user.name, lvl=level.name,
                #         have=len(approved_ids), need=level.required_approvals,
                #         rem=len(remaining)),
                #     subtype_xmlid='mail.mt_note',
                # )
                return
        next_level = self._get_next_approval_level(after_current=True)
        if next_level:
            self.current_approval_level = next_level.sequence
            self._set_pending_approvers(level=next_level)
            self._notify_next_approvers(level=next_level)
            self.message_post(
                body=Markup(_('Approved at level: <b>%s</b>. Awaiting next approval level: <b>%s</b>.')) % (level.name if level else '', next_level.name),
                subtype_xmlid='mail.mt_note',
            )
        else:
            # Budget is reserved at approval, so this is the moment to gate it:
            # earlier parallel approvals may have consumed what was available at
            # submission time.
            self._check_budget_before_submit()
            self.write({'state': 'approved', 'rejection_reason': False, 'pending_approver_ids': [(5, 0, 0)]})
            self.message_post(
                body=Markup(_('Purchase request <b>%s</b> has been fully approved.')) % (self.name,),
                subtype_xmlid='mail.mt_note',
            )
            self._send_approval_notification()

    def action_reject(self):
        return {
            'name': _('Reject Purchase Request'),
            'type': 'ir.actions.act_window',
            'res_model': 'ebshel.pr.reject.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_request_id': self.id},
        }

    def action_reset_to_draft(self):
        for rec in self:
            if rec.state not in ('rejected', 'cancelled'):
                raise UserError(_('Only Rejected or Cancelled requests can be reset to Draft.'))
            rec.write({'state': 'draft', 'current_approval_level': 0, 'rejection_reason': False,
                       'pending_approver_ids': [(5, 0, 0)]})
            rec._create_approval_log('reset', comments=_('Reset to draft.'))
            rec.message_post(body=_('Request reset to draft.'), subtype_xmlid='mail.mt_note')

    def action_cancel(self):
        for rec in self:
            if rec.state in ('approved', 'closed'):
                rfq_count = self.env['ebshel.purchase.rfq'].search_count(
                    [('rfq_line_ids.request_line_id.request_id', '=', rec.id),
                     ('state', 'not in', ('cancelled',))]
                )
                if rfq_count:
                    raise UserError(_('Cannot cancel: active RFQs exist for this request.'))
            rec.write({'state': 'cancelled', 'pending_approver_ids': [(5, 0, 0)]})
            rec._create_approval_log('cancelled')
            rec.message_post(body=_('Request cancelled.'), subtype_xmlid='mail.mt_note')

    def action_close(self):
        for rec in self:
            rec.write({'state': 'closed'})
            rec.message_post(body=_('Request closed.'), subtype_xmlid='mail.mt_note')

    # ── Quantity edit on approved requests ─────────────────────────────────────
    def action_open_edit_qty(self):
        """Open the wizard to reduce line quantities on an approved request
        (e.g. drop the unneeded remainder after a partial RFQ)."""
        self.ensure_one()
        if not self.company_id.pr_allow_approved_qty_edit:
            raise UserError(_(
                'Editing quantities on approved requests is disabled. Enable it '
                'in Purchase Requests Settings.'))
        if self.state != 'approved':
            raise UserError(_('Only approved requests can be edited here.'))
        lines = self.line_ids.filtered(lambda l: not l.cancelled)
        if not lines:
            raise UserError(_('This request has no active lines to edit.'))
        return {
            'name': _('Update Quantities – %s', self.name),
            'type': 'ir.actions.act_window',
            'res_model': 'ebshel.pr.qty.edit.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_request_id': self.id,
                'default_line_ids': [
                    (0, 0, {'request_line_id': line.id, 'new_qty': line.qty})
                    for line in lines
                ],
            },
        }

    def _apply_qty_edit_followup(self, changes):
        """Record the quantity change and, when configured, send the request
        back through approval. `changes` is a list of (line, old_qty, new_qty)."""
        self.ensure_one()
        summary = Markup('<br/>').join(
            Markup('• %s: %s → %s') % (
                line.product_id.display_name or line.name, old, new)
            for line, old, new in changes)
        if self.company_id.pr_qty_edit_reapproval:
            self.message_post(
                body=Markup(_('Quantities reduced — request sent back for '
                              'approval:<br/>%s')) % summary,
                subtype_xmlid='mail.mt_note')
            self._reapprove_after_qty_change()
        else:
            self.message_post(
                body=Markup(_('Quantities reduced (request remains '
                              'approved):<br/>%s')) % summary,
                subtype_xmlid='mail.mt_note')

    def _reapprove_after_qty_change(self):
        """Reset an approved request to the start of the approval workflow after
        its quantities changed, reusing the same routing as initial submission."""
        self.ensure_one()
        levels = self._get_applicable_approval_levels()
        self.write({
            'state': 'submitted',
            'current_approval_level': levels[0].sequence if levels else 0,
            'pending_approver_ids': [(5, 0, 0)],
        })
        self._create_approval_log(
            'submitted', comments=_('Re-submitted after a quantity change.'))
        # Auto-approval threshold still applies to the new (lower) amount.
        company = self.company_id
        threshold = company.pr_auto_approve_amount
        if (company.pr_auto_approve_enabled and threshold
                and self.amount_total <= threshold):
            self.write({'state': 'approved', 'pending_approver_ids': [(5, 0, 0)]})
            self._create_approval_log(
                'approved', level_name=_('Auto-approved'),
                comments=_('Below the auto-approval threshold after quantity change.'))
            self._send_approval_notification()
            return
        self._set_pending_approvers()
        self._notify_next_approvers()

    # ── Product change during approval ─────────────────────────────────────────
    def action_open_product_change(self):
        """Approver action: open the wizard to swap a line's product for another
        in the same category, or return the request to the requester."""
        self.ensure_one()
        if not self.company_id.pr_allow_product_change:
            raise UserError(_(
                'Product change is disabled. Enable it in Purchase Requests '
                'Settings.'))
        if self.state != 'submitted':
            raise UserError(_(
                'Products can be changed only while the request is pending '
                'approval.'))
        lines = self.line_ids.filtered(
            lambda l: l.allow_product_change and not l.cancelled and l.product_id)
        if not lines:
            raise UserError(_(
                'No lines are flagged "Allow Product Change" by the requester.'))
        return {
            'name': _('Change Product – %s', self.name),
            'type': 'ir.actions.act_window',
            'res_model': 'ebshel.pr.product.change.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_request_id': self.id,
                'default_line_ids': [
                    (0, 0, {
                        'request_line_id': line.id,
                        'new_product_id': (line.suggested_product_id.id
                                           or line.product_id.id),
                    })
                    for line in lines
                ],
            },
        }

    def _apply_product_change_followup(self, changes):
        """Log an approver's in-place product swap. `changes` is a list of
        (line, old_product, new_product)."""
        self.ensure_one()
        summary = Markup('<br/>').join(
            Markup('• %s → %s') % (old.display_name, new.display_name)
            for _line, old, new in changes)
        self.message_post(
            body=Markup(_('Product(s) changed by the approver:<br/>%s')) % summary,
            subtype_xmlid='mail.mt_note')

    def _return_to_requester_for_product(self, changes):
        """Return a submitted request to the requester (Draft) so they can change
        the product, recording the approver's suggestions on the lines."""
        self.ensure_one()
        for line, _old, new in changes:
            line.suggested_product_id = new.id
        self.write({
            'state': 'draft',
            'current_approval_level': 0,
            'pending_approver_ids': [(5, 0, 0)],
        })
        self._create_approval_log(
            'reset', comments=_('Returned to requester for a product change.'))
        if changes:
            summary = Markup('<br/>').join(
                Markup('• %s → suggested: %s') % (old.display_name, new.display_name)
                for _line, old, new in changes)
            body = Markup(_('Request returned for a product change. '
                            'Suggested:<br/>%s')) % summary
        else:
            body = Markup(_('Request returned to the requester to revise the '
                            'product(s).'))
        self.message_post(
            body=body, subtype_xmlid='mail.mt_comment',
            partner_ids=self.requester_id.partner_id.ids)

    # ── List dashboard (KPI tiles) ─────────────────────────────────────────────
    @api.model
    def retrieve_dashboard(self):
        """Aggregate counts powering the KPI tiles above the request list.
        Returns global (company-wide) and personal ('my') buckets plus the
        open committed value, mirroring the standard Purchase dashboard."""
        uid = self.env.uid
        base = [('company_id', '=', self.env.company.id)]

        def count(extra):
            return self.search_count(base + extra)

        urgent = [('state', 'not in', ('closed', 'cancelled', 'rejected')),
                  ('priority', 'in', ('1', '2'))]
        glob = {
            'draft': count([('state', '=', 'draft')]),
            'submitted': count([('state', '=', 'submitted')]),
            'pending': count([('state', '=', 'submitted')]),
            'approved': count([('state', '=', 'approved')]),
            'urgent': count(urgent),
            'rejected': count([('state', '=', 'rejected')]),
            'closed': count([('state', '=', 'closed')]),
        }
        my = {
            'draft': count([('state', '=', 'draft'), ('requester_id', '=', uid)]),
            'submitted': count([('state', '=', 'submitted'), ('requester_id', '=', uid)]),
            'pending': count([('state', '=', 'submitted'), ('pending_approver_ids', 'in', [uid])]),
            'approved': count([('state', '=', 'approved'), ('requester_id', '=', uid)]),
            'urgent': count(urgent + [('requester_id', '=', uid)]),
            'rejected': count([('state', '=', 'rejected'), ('requester_id', '=', uid)]),
            'closed': count([('state', '=', 'closed'), ('requester_id', '=', uid)]),
        }
        open_recs = self.search(base + [('state', 'in', ('submitted', 'approved'))])
        open_mine = open_recs.filtered(lambda r: r.requester_id.id == uid)
        currency = self.env.company.currency_id
        return {
            'global': glob,
            'my': my,
            'open_value': round(sum(open_recs.mapped('amount_total')), 2),
            'my_open_value': round(sum(open_mine.mapped('amount_total')), 2),
            'currency_symbol': currency.symbol or currency.name or '',
            'currency_position': currency.position or 'before',
        }

    # ── Smart button actions ──────────────────────────────────────────────────
    def action_view_rfqs(self):
        self.ensure_one()
        rfq_ids = self.env['ebshel.purchase.rfq'].search(
            [('rfq_line_ids.request_line_id.request_id', '=', self.id)]
        )
        return {
            'name': _('RFQs'),
            'type': 'ir.actions.act_window',
            'res_model': 'ebshel.purchase.rfq',
            'view_mode': 'list,form',
            'domain': [('id', 'in', rfq_ids.ids)],
            'context': {'default_origin_request_id': self.id},
        }

    def action_view_quotations(self):
        """Smart-button: every vendor quotation across this request's RFQs."""
        self.ensure_one()
        rfqs = self.env['ebshel.purchase.rfq'].search(
            [('rfq_line_ids.request_line_id.request_id', '=', self.id)]
        )
        quotations = rfqs.mapped('vendor_quotation_ids')
        return {
            'name': _('Vendor Quotations'),
            'type': 'ir.actions.act_window',
            'res_model': 'ebshel.purchase.vendor.quotation',
            'view_mode': 'list,form',
            'domain': [('id', 'in', quotations.ids)],
        }

    def action_view_allocations(self):
        """Smart-button: receipt allocations linking this request to PO lines."""
        self.ensure_one()
        allocations = self.line_ids.mapped('allocation_ids')
        return {
            'name': _('Receipt Allocations'),
            'type': 'ir.actions.act_window',
            'res_model': 'ebshel.purchase.request.allocation',
            'view_mode': 'list,form',
            'domain': [('id', 'in', allocations.ids)],
        }

    def action_view_purchase_orders(self):
        self.ensure_one()
        po_lines = self.line_ids.mapped('purchase_line_ids') | self.line_ids.mapped('po_line_id')
        pos = po_lines.mapped('order_id')
        # Use the standard "Purchase Orders" action (not the RFQ one) so the
        # breadcrumb, views and context are the Purchase Order ones.
        action = self.env['ir.actions.act_window']._for_xml_id('purchase.purchase_form_action')
        action['context'] = {}
        action['domain'] = [('id', 'in', pos.ids)]
        if len(pos) == 1:
            action['views'] = [(self.env.ref('purchase.purchase_order_form').id, 'form')]
            action['res_id'] = pos.id
        return action

    def action_view_stock_moves(self):
        self.ensure_one()
        moves = self.line_ids.mapped('allocation_ids.stock_move_id')
        pickings = moves.mapped('picking_id')
        return {
            'name': _('Receipts'),
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'view_mode': 'list,form',
            'domain': [('id', 'in', pickings.ids)],
        }

    def action_create_rfq(self):
        self.ensure_one()
        return self._open_create_rfq_wizard(self)

    def action_open_create_rfq_wizard(self):
        """Multi-record entry point (list 'Action' menu): consolidate the
        eligible lines of every selected approved request into one RFQ."""
        return self._open_create_rfq_wizard(self)

    @api.model
    def _open_create_rfq_wizard(self, requests):
        """Build the Create-RFQ wizard action pre-loaded with the eligible
        lines of the given requests (works for one or many PRs)."""
        approved = requests.filtered(lambda r: r.state == 'approved')
        if not approved:
            raise UserError(_(
                'Select one or more <b>Approved</b> purchase requests to source. '
                'Only approved requests can feed an RFQ.'))
        # Eligible = lines with quantity still left to source on a new RFQ.
        eligible_lines = approved.mapped('line_ids').filtered(
            lambda l: not l.cancelled and l.qty_to_rfq > 0)
        if not eligible_lines:
            raise UserError(_(
                'Nothing left to source — every line of the selected request(s) '
                'is already fully on an RFQ/PO or cancelled.'))
        return {
            'name': _('Create RFQ from %d Request(s)', len(approved)),
            'type': 'ir.actions.act_window',
            'res_model': 'ebshel.pr.create.rfq.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_request_ids': [(6, 0, approved.ids)],
            },
        }

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _category_amounts(self):
        """{product_category_id: estimated spend} for this request's active lines.
        Drives the category-wise approval-amount limits."""
        self.ensure_one()
        amounts = {}
        for line in self.line_ids.filtered(lambda l: not l.cancelled):
            categ = line.product_id.categ_id
            if categ:
                amounts[categ.id] = amounts.get(categ.id, 0.0) + line.estimated_total
        return amounts

    def _get_applicable_approval_levels(self):
        """Return approval config levels applicable for this request (amount band,
        department filter and category-wise amount limits)."""
        self.ensure_one()
        levels = self.env['ebshel.purchase.request.approval.config'].search([
            ('active', '=', True),
            ('company_id', '=', self.company_id.id),
        ])
        # Compute the per-category spend once (only if any level uses category
        # limits) and reuse it across all levels — avoids re-scanning the
        # request lines once per level on requests with many lines.
        cat_amounts = self._category_amounts() if any(
            levels.mapped('category_limit_ids')) else {}
        return levels.filtered(
            lambda lv: lv.matches_request(self, cat_amounts=cat_amounts)
        ).sorted('sequence')

    def _get_next_approval_level(self, after_current=False):
        """Return the approval level that is currently pending (or next after current)."""
        self.ensure_one()
        levels = self._get_applicable_approval_levels()
        if not levels:
            return False
        if after_current:
            return levels.filtered(lambda l: l.sequence > self.current_approval_level)[:1]
        if self.current_approval_level == 0:
            return levels[:1]
        return levels.filtered(lambda l: l.sequence == self.current_approval_level)[:1]

    def _check_can_approve(self):
        self.ensure_one()
        if self.state != 'submitted':
            raise UserError(_('Only submitted requests can be approved.'))
        level = self._get_next_approval_level()
        if not level:
            # No levels configured → any purchase manager can approve
            if not self.env.user.has_group('ebshel_purchase_request.group_pr_approver'):
                raise UserError(_('You are not authorised to approve this request (Either approval level must be configured or you must be Purchase Request Approver to approve this request.).'))
            return
        allowed = level.get_approver_users()
        if self.env.user not in allowed:
            raise UserError(_(
                'You are not designated as an approver for level "%s".',
                level.name,
            ))
        # Block the same approver from signing the same level twice.
        if self.env.user.id in self._approved_user_ids_at_level(level):
            raise UserError(_(
                'You have already approved this request. '
                'It is waiting on the remaining approvers.'))
        # Self-approval guard, configurable per company.
        if (self.env.user == self.requester_id
                and not self.company_id.pr_allow_self_approval):
            raise UserError(_(
                'Self-approval is disabled — a requester cannot approve their '
                'own request. Enable it in Settings if your policy allows it.'))

    def _validate_lines(self):
        active = self.line_ids.filtered(lambda l: not l.cancelled)
        if not active:
            raise ValidationError(_('Add at least one active line before submitting.'))
        for line in active:
            if not line.product_id:
                raise ValidationError(_('All lines must have a product selected.'))
            if line.qty <= 0:
                raise ValidationError(_('Quantity must be greater than zero for all lines.'))

    def _check_auto_reject(self):
        """If every line is cancelled, auto-reject the request."""
        for rec in self:
            if rec.state in ('draft', 'rejected', 'cancelled', 'closed'):
                continue
            if rec.line_ids and all(l.cancelled for l in rec.line_ids):
                rec.write({'state': 'rejected', 'pending_approver_ids': [(5, 0, 0)]})
                rec._create_approval_log(
                    'rejected', comments=_('Auto-rejected: all lines cancelled.'))
                rec.message_post(
                    body=_('Request auto-rejected because all lines were cancelled.'),
                    subtype_xmlid='mail.mt_note',
                )

    def _create_approval_log(self, action, level_id=False, level_name=False, comments=False):
        # Audit records are system-managed: created via sudo so any actor (incl.
        # a requester submitting) can write the trail, and they stay immutable to
        # ordinary users (no write/unlink access on the log model).
        self.env['ebshel.purchase.request.approval.log'].sudo().create({
            'request_id': self.id,
            'level_id': level_id,
            'level_name': level_name,
            'approver_id': self.env.user.id,
            'action': action,
            'comments': comments,
        })

    def _approved_user_ids_at_level(self, level):
        """Set of user ids who have approved `level` during the current cycle
        (i.e. since the most recent submission)."""
        self.ensure_one()
        if not level:
            return set()
        submit_dates = self.approval_log_ids.filtered(
            lambda l: l.action == 'submitted').mapped('date')
        cycle_start = max(submit_dates) if submit_dates else False
        logs = self.approval_log_ids.filtered(
            lambda l: l.action == 'approved' and l.level_id == level
            and (not cycle_start or l.date >= cycle_start))
        return set(logs.mapped('approver_id').ids)

    def _set_pending_approvers(self, level=None):
        """Populate pending_approver_ids from the given (or current) approval level."""
        self.ensure_one()
        if level is None:
            level = self._get_next_approval_level()
        approvers = level.get_approver_users() if level else self.env['res.users']
        self.pending_approver_ids = [(6, 0, approvers.ids)]

    
    def _notify_next_approvers(self, level=None):
        if level is None:
            level = self._get_next_approval_level()
        if not level:
            return
        approvers = level.get_approver_users()
        if not approvers:
            return
        company = self.company_id
        days = company.pr_escalation_days or 0
        deadline = (fields.Date.context_today(self) + relativedelta(days=days)
                    if company.pr_escalation_enabled and days else None)
        for approver in approvers:
            self.with_context(mail_create_nosubscribe=True, mail_notrack=True, mail_activity_quick_update=True,).activity_schedule(
                'mail.mail_activity_data_todo',
                user_id=approver.id,
                date_deadline=deadline,
                note=_('Purchase request %s requires your approval.', self.name),
            )
        template = self.env.ref(
            'ebshel_purchase_request.mail_template_pr_approval_request',
            raise_if_not_found=False,
        )
        if template:
            for approver in approvers:
                template.with_context(
                    approver_name=approver.partner_id.name or approver.name,
                ).send_mail(
                    self.id,
                    email_values={'email_to': approver.partner_id.email},
                    force_send=True,
                )

        self.message_post(
            body=_(
                'Approval request sent to: %s (level: %s).',
                ', '.join(approvers.mapped('name')),
                level.name,
            ),
            subtype_xmlid='mail.mt_note',
        )


    def _send_approval_notification(self):
        template = self.env.ref(
            'ebshel_purchase_request.mail_template_pr_approved',
            raise_if_not_found=False,
        )
        if template:
            template.send_mail(
                self.id,
                email_values={
                    'email_to': self.requester_id.email,
                },
                force_send=True,
            )

    # ── Department / Cost-Centre Insight ──────────────────────────────────────
    def action_department_insight(self):
        """Open the advanced department-spend cockpit for this PR's cost centre."""
        self.ensure_one()
        if not self.analytic_account_id:
            raise UserError(_(
                'Select a Department / Cost Centre on this request to view its insight.'))
        return {
            'type': 'ir.actions.client',
            'tag': 'ebshel_department_insight',
            'name': _('Department Insight – %s', self.analytic_account_id.name),
            'target': 'current',
            'context': {
                'analytic_account_id': self.analytic_account_id.id,
                'analytic_name': self.analytic_account_id.name,
                'origin_pr_id': self.id,
            },
        }

    @api.model
    def get_department_insight(self, analytic_account_id):
        """Aggregate procurement activity for one cost centre (analytic account).
        Drives the Department Insight cockpit; manager-level visibility."""
        aa = self.env['account.analytic.account'].browse(analytic_account_id)
        Request = self.env['ebshel.purchase.request']
        Line = self.env['ebshel.purchase.request.line']
        currency = self.env.company.currency_id
        state_labels = dict(Request._fields['state'].selection)

        prs = Request.search([('analytic_account_id', '=', analytic_account_id)])

        def amt(records):
            return round(sum(records.mapped('amount_total')), 2)

        committed = prs.filtered(lambda p: p.state in ('submitted', 'approved', 'closed'))
        approved = prs.filtered(lambda p: p.state in ('approved', 'closed'))
        pending = prs.filtered(lambda p: p.state == 'submitted')
        draft = prs.filtered(lambda p: p.state == 'draft')

        status_breakdown = []
        for st, label in Request._fields['state'].selection:
            sub = prs.filtered(lambda p: p.state == st)
            if sub:
                status_breakdown.append({
                    'state': st, 'label': label,
                    'count': len(sub), 'amount': amt(sub),
                })

        today = fields.Date.today()
        monthly = []
        for i in range(5, -1, -1):
            month_start = today.replace(day=1) - relativedelta(months=i)
            month_end = month_start + relativedelta(months=1)
            sub = committed.filtered(
                lambda p: p.date_request and month_start <= p.date_request < month_end)
            monthly.append({'label': month_start.strftime('%b'), 'amount': amt(sub)})

        lines = Line.search([('request_id', 'in', prs.ids), ('cancelled', '=', False)])
        prod_map = {}
        for l in lines:
            d = prod_map.setdefault(l.product_id.id, {
                'name': l.product_id.display_name or l.name or '—',
                'qty': 0.0, 'amount': 0.0})
            d['qty'] += l.qty
            d['amount'] += l.estimated_total
        top_products = sorted(prod_map.values(), key=lambda x: x['amount'], reverse=True)[:5]
        for p in top_products:
            p['amount'] = round(p['amount'], 2)

        po_lines = lines.mapped('purchase_line_ids')
        vend_map = {}
        for pol in po_lines:
            if pol.state == 'cancel':
                continue
            partner = pol.order_id.partner_id
            d = vend_map.setdefault(partner.id, {'name': partner.name or '—', 'amount': 0.0})
            d['amount'] += pol.price_subtotal
        top_vendors = sorted(vend_map.values(), key=lambda x: x['amount'], reverse=True)[:5]
        for v in top_vendors:
            v['amount'] = round(v['amount'], 2)

        recent = prs.sorted(lambda p: p.create_date or fields.Datetime.now(), reverse=True)[:8]
        recent_requests = [{
            'id': p.id, 'name': p.name,
            'description': p.description or '',
            'state': p.state, 'state_label': state_labels.get(p.state, p.state),
            'requester': p.requester_id.name or '',
            'amount': round(p.amount_total, 2),
            'date': p.date_request.strftime('%d %b %Y') if p.date_request else '',
        } for p in recent]

        return {
            'department': aa.name or _('Unassigned'),
            'currency_symbol': currency.symbol or '$',
            'currency_position': currency.position or 'before',
            'total_requests': len(prs),
            'committed_amount': amt(committed),
            'approved_amount': amt(approved),
            'pending_amount': amt(pending),
            'pending_count': len(pending),
            'draft_amount': amt(draft),
            'avg_request': round(amt(prs) / len(prs), 2) if prs else 0.0,
            'status_breakdown': status_breakdown,
            'monthly': monthly,
            'top_products': top_products,
            'top_vendors': top_vendors,
            'recent_requests': recent_requests,
        }

    # ── Procurement Intelligence Dashboard ───────────────────────────────────
    @api.model
    def get_dashboard_data(self, period='month'):
        today = fields.Date.today()
        company_id = self.env.company.id
        uid = self.env.uid
        currency = self.env.company.currency_id

        # ── Resolve the selected period and its previous comparison window ────
        if period == 'year':
            period_start = today.replace(month=1, day=1)
            prev_start = period_start - relativedelta(years=1)
            period_label = 'This Year'
        elif period == 'quarter':
            q_month = ((today.month - 1) // 3) * 3 + 1
            period_start = today.replace(month=q_month, day=1)
            prev_start = period_start - relativedelta(months=3)
            period_label = 'This Quarter'
        else:
            period = 'month'
            period_start = today.replace(day=1)
            prev_start = period_start - relativedelta(months=1)
            period_label = 'This Month'
        prev_end = period_start
        month_start = period_start  # period-scoped metrics below

        my_pending = self.search_count([
            ('state', '=', 'submitted'),
            ('pending_approver_ids', 'in', [uid]),
            ('company_id', '=', company_id),
        ])

        active_rfqs = self.env['ebshel.purchase.rfq'].search_count([
            ('state', 'in', ('draft', 'sent', 'comparing')),
            ('company_id', '=', company_id),
        ])

        rfqs_awarded = self.env['ebshel.purchase.rfq'].search([
            ('state', 'in', ('awarded', 'po_created')),
            ('date_rfq', '>=', month_start),
            ('company_id', '=', company_id),
        ])
        savings_month = sum(
            max(rfq.vendor_quotation_ids.mapped('amount_total') or [0])
            - min(rfq.vendor_quotation_ids.mapped('amount_total') or [0])
            for rfq in rfqs_awarded
            if len(rfq.vendor_quotation_ids) >= 2
        )

        pos_month = self.env['ebshel.purchase.rfq'].search_count([
            ('state', '=', 'po_created'),
            ('date_rfq', '>=', month_start),
            ('company_id', '=', company_id),
        ])

        ytd_recs = self.search([
            ('state', 'in', ('approved', 'closed')),
            ('date_request', '>=', period_start),
            ('company_id', '=', company_id),
        ])
        total_ytd = sum(ytd_recs.mapped('amount_total'))
        total_requests = self.search_count([
            ('company_id', '=', company_id),
            ('date_request', '>=', period_start),
        ])

        # Spend trend vs the previous equivalent period
        prev_recs = self.search([
            ('state', 'in', ('approved', 'closed')),
            ('date_request', '>=', prev_start),
            ('date_request', '<', prev_end),
            ('company_id', '=', company_id),
        ])
        prev_total = sum(prev_recs.mapped('amount_total'))
        if prev_total:
            trend = round((total_ytd - prev_total) / prev_total * 100, 1)
        else:
            trend = 100.0 if total_ytd else 0.0
        spend_trend = trend
        spend_trend_dir = 'up' if trend > 0 else ('down' if trend < 0 else 'flat')

        # Status distribution
        state_labels = dict(self._fields['state'].selection)
        state_colors = {
            'draft': '#94a3b8', 'submitted': '#3b82f6', 'approved': '#22c55e',
            'rejected': '#ef4444', 'cancelled': '#64748b', 'closed': '#8b5cf6',
        }
        status_dist = []
        for state, label in state_labels.items():
            count = self.search_count([('state', '=', state), ('company_id', '=', company_id)])
            if count:
                status_dist.append({
                    'state': state, 'label': label,
                    'count': count, 'color': state_colors.get(state, '#94a3b8'),
                })

        # Monthly spend — last 6 months
        monthly_spend = []
        for i in range(5, -1, -1):
            m_start = today.replace(day=1) - relativedelta(months=i)
            m_end = m_start + relativedelta(months=1)
            recs = self.search([
                ('state', 'in', ('approved', 'closed')),
                ('date_request', '>=', m_start),
                ('date_request', '<', m_end),
                ('company_id', '=', company_id),
            ])
            monthly_spend.append({
                'month': m_start.strftime('%b %Y'),
                'short': m_start.strftime('%b'),
                'amount': round(sum(recs.mapped('amount_total')), 2),
                'count': len(recs),
            })

        # Department (analytic account) spend — top 6 YTD
        dept_groups = self._read_group(
            [('state', 'in', ('approved', 'closed')), ('company_id', '=', company_id),
             ('date_request', '>=', period_start)],
            ['analytic_account_id'], ['amount_total:sum'],
            order='amount_total:sum desc',
            limit=6,
        )
        max_dept = max((total or 0 for _dept, total in dept_groups), default=1) or 1
        dept_spend = [{
            'dept': dept.display_name if dept else _('No Department'),
            'amount': round(total or 0, 2),
            'pct': round((total or 0) / max_dept * 100, 1),
        } for dept, total in dept_groups]

        # Vendor leaderboard — top 5 winners YTD
        won_quotes = self.env['ebshel.purchase.vendor.quotation'].search([
            ('is_winner', '=', True),
            ('rfq_id.company_id', '=', company_id),
            ('rfq_id.date_rfq', '>=', period_start),
            ('vendor_id', '!=', False),
        ])
        vmap = {}
        for q in won_quotes:
            vid = q.vendor_id.id
            if vid not in vmap:
                vmap[vid] = {'name': q.vendor_id.name, 'wins': 0, 'total': 0.0}
            vmap[vid]['wins'] += 1
            vmap[vid]['total'] += q.amount_total
        vendor_wins = sorted(vmap.values(), key=lambda v: v['wins'], reverse=True)[:5]
        max_wins = max((v['wins'] for v in vendor_wins), default=1) or 1
        for v in vendor_wins:
            v['pct'] = round(v['wins'] / max_wins * 100, 1)
            v['total'] = round(v['total'], 2)

        # Recent activity
        recent = self.search([('company_id', '=', company_id)],
                             limit=10, order='write_date desc')
        recent_activity = [{
            'id': pr.id,
            'name': pr.name,
            'description': pr.description or '',
            'state': pr.state,
            'state_label': state_labels.get(pr.state, pr.state),
            'requester': pr.requester_id.name or '',
            'date': pr.write_date.strftime('%d %b, %H:%M') if pr.write_date else '',
            'amount': round(pr.amount_total, 2),
            'priority': pr.priority,
        } for pr in recent]

        # Recently added vendors (suppliers), with their quotation activity.
        Partner = self.env['res.partner']
        Quotation = self.env['ebshel.purchase.vendor.quotation']
        new_vendors = Partner.search(
            [('supplier_rank', '>', 0)], limit=6, order='create_date desc')
        recent_vendors = [{
            'id': v.id,
            'name': v.name or '',
            'email': v.email or '',
            'phone': v.phone or '',
            'city': v.city or '',
            'country': v.country_id.name or '',
            'created': v.create_date.strftime('%d %b %Y') if v.create_date else '',
            'quotation_count': Quotation.search_count([('vendor_id', '=', v.id)]),
        } for v in new_vendors]
        new_vendor_count = Partner.search_count([('supplier_rank', '>', 0)])

        return {
            'my_pending': my_pending,
            'active_rfqs': active_rfqs,
            'savings_month': round(savings_month, 2),
            'pos_month': pos_month,
            'total_ytd': round(total_ytd, 2),
            'total_requests': total_requests,
            'status_distribution': status_dist,
            'monthly_spend': monthly_spend,
            'dept_spend': dept_spend,
            'vendor_wins': vendor_wins,
            'recent_vendors': recent_vendors,
            'new_vendor_count': new_vendor_count,
            'recent_activity': recent_activity,
            'currency_symbol': currency.symbol or '$',
            'currency_position': currency.position or 'before',
            'company_name': self.env.company.name,
            'user_name': self.env.user.name,
            'period': period,
            'period_label': period_label,
            'spend_trend': spend_trend,
            'spend_trend_dir': spend_trend_dir,
        }
