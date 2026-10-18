# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.
from markupsafe import Markup, escape
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

class PurchaseRequestApprovalConfig(models.Model):
    """Defines approval levels for purchase requests.
    Levels are applied in ascending sequence order. Amount and department
    conditions narrow which levels apply to a given request. Each level can
    require any single approver, every approver, or a configurable quorum.
    """
    _name = 'ebshel.purchase.request.approval.config'
    _description = 'Purchase Request Approval Level'
    _order = 'sequence, id'

    name = fields.Char('Level Name', required=True, translate=True)
    sequence = fields.Integer('Sequence', default=10)
    active = fields.Boolean('Active', default=True)
    color = fields.Integer('Color')
    company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company, required=True,
    )
    approver_group_ids = fields.Many2many(
        'res.groups', 'pr_approval_config_group_rel',
        'config_id', 'group_id',
        string='Approver Groups',
        help='Users belonging to any of these groups can approve at this level.',
    )
    approver_ids = fields.Many2many(
        'res.users', 'pr_approval_config_user_rel',
        'config_id', 'user_id',
        string='Specific Approvers',
        domain=[('share', '=', False)],
        help='Named users who can approve at this level (in addition to group members).',
    )
    amount_min = fields.Monetary(
        'Min Amount', currency_field='currency_id',
        help='Level applies only when request total >= this amount. Leave 0 to ignore.',
    )
    amount_max = fields.Monetary(
        'Max Amount', currency_field='currency_id',
        help='Level applies only when request total <= this amount. Leave 0 to ignore.',
    )
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True,
    )
    analytic_account_ids = fields.Many2many(
        'account.analytic.account', 'pr_approval_config_analytic_rel',
        'config_id', 'analytic_id',
        string='Departments / Cost Centers',
        help='Level applies only when the request department (analytic account) '
             'is in this list. Leave empty to apply to all departments.',
    )
    category_limit_ids = fields.One2many(
        'ebshel.pr.approval.category.limit', 'config_id',
        string='Category Amount Limits',
        help='Per product-category spend bands. When set, this level only applies '
             'if the request\'s spend in at least one listed category falls within '
             'its band — letting you route, e.g., IT purchases over a threshold to '
             'a dedicated approver.',
    )

    # ── Approval quorum policy ────────────────────────────────────────────────
    approval_mode = fields.Selection([
        ('any', 'Any One Approver'),
        ('all', 'All Approvers'),
        ('quorum', 'Minimum Number (Quorum)'),
    ], string='Approval Policy', default='any', required=True,
        help='• Any One Approver — a single approval clears this level.\n'
             '• All Approvers — every designated approver must approve.\n'
             '• Quorum — a minimum number of approvers must approve.')
    min_approvers = fields.Integer(
        'Required Approvals', default=2,
        help='Minimum distinct approvers required when the policy is "Quorum".')

    # ── Governance (SLA / escalation / notifications) ─────────────────────────
    escalation_days = fields.Integer(
        'SLA (days)', default=0,
        help='Target turnaround for this level. Requests pending longer than this '
             'are flagged as overdue. 0 disables the SLA.')
    notify_on_pending = fields.Boolean(
        'Notify Approvers', default=True,
        help='Send a notification / activity to the approvers when a request '
             'reaches this level.')

    # ── Computed insight fields ──────────────────────────────────────────────
    approver_count = fields.Integer(
        'Eligible Approvers', compute='_compute_approver_count')
    required_approvals = fields.Integer(
        'Approvals Needed', compute='_compute_approver_count',
        help='How many distinct approvals this level needs to pass.')
    approver_user_ids = fields.Many2many(
        'res.users', compute='_compute_approver_count',
        string='Effective Approvers',
        help='Everyone who can actually approve at this level (named approvers '
             '+ members of the approver groups).')
    pending_request_count = fields.Integer(
        'Pending Requests', compute='_compute_pending_request_count')
    amount_band = fields.Char('Amount Band', compute='_compute_amount_band')
    policy_summary = fields.Char('Policy', compute='_compute_approver_count')

    description = fields.Text('Notes')

    @api.depends('approver_ids', 'approver_group_ids', 'approver_group_ids.all_user_ids',
                 'approval_mode', 'min_approvers')
    def _compute_approver_count(self):
        for rec in self:
            users = rec.get_approver_users()
            count = len(users)
            rec.approver_user_ids = users
            rec.approver_count = count
            if rec.approval_mode == 'all':
                rec.required_approvals = count
                rec.policy_summary = _('All %s approver(s)', count) if count else _('All approvers')
            elif rec.approval_mode == 'quorum':
                need = min(max(1, rec.min_approvers), count) if count else max(1, rec.min_approvers)
                rec.required_approvals = need
                rec.policy_summary = _('Any %(n)s of %(t)s', n=need, t=count)
            else:
                rec.required_approvals = 1 if count else 0
                rec.policy_summary = _('Any one of %s', count) if count else _('Any approver')

    @api.depends('amount_min', 'amount_max', 'currency_id')
    def _compute_amount_band(self):
        for rec in self:
            sym = rec.currency_id.symbol or ''
            if rec.amount_min and rec.amount_max:
                rec.amount_band = '%s%s – %s%s' % (sym, int(rec.amount_min), sym, int(rec.amount_max))
            elif rec.amount_min:
                rec.amount_band = '≥ %s%s' % (sym, int(rec.amount_min))
            elif rec.amount_max:
                rec.amount_band = '≤ %s%s' % (sym, int(rec.amount_max))
            else:
                rec.amount_band = _('Any amount')

    def _compute_pending_request_count(self):
        # Batched with a single read_group instead of one search_count per row
        # (this field shows in the list + kanban — per-row counting would be N+1).
        recs = self.filtered('id')
        for rec in self - recs:
            rec.pending_request_count = 0
        if not recs:
            return
        groups = self.env['ebshel.purchase.request']._read_group(
            [('state', '=', 'submitted'),
             ('company_id', 'in', recs.company_id.ids),
             ('current_approval_level', 'in', recs.mapped('sequence'))],
            groupby=['company_id', 'current_approval_level'],
            aggregates=['__count'])
        counts = {(company.id, level): count for company, level, count in groups}
        for rec in recs:
            rec.pending_request_count = counts.get((rec.company_id.id, rec.sequence), 0)

    @api.constrains('amount_min', 'amount_max')
    def _check_amount_range(self):
        for rec in self:
            if rec.amount_min and rec.amount_max and rec.amount_min > rec.amount_max:
                raise ValidationError(_('Minimum amount cannot exceed maximum amount.'))

    @api.constrains('sequence', 'company_id', 'active')
    def _check_unique_sequence(self):
        for rec in self:
            if not rec.active:
                continue
            dup = self.search_count([
                ('id', '!=', rec.id),
                ('active', '=', True),
                ('company_id', '=', rec.company_id.id),
                ('sequence', '=', rec.sequence),
            ])
            if dup:
                raise ValidationError(_(
                    'Another active approval level in %(company)s already uses '
                    'sequence %(seq)s. Each level must have a unique order so '
                    'requests route deterministically.',
                    company=rec.company_id.name, seq=rec.sequence))

    @api.constrains('active', 'approver_ids', 'approver_group_ids', 'approval_mode', 'min_approvers')
    def _check_has_approvers(self):
        for rec in self:
            if rec.active and not rec.approver_ids and not rec.approver_group_ids:
                raise ValidationError(_(
                    'Approval level "%s" is active but has no approvers. Add at '
                    'least one approver group or named approver.', rec.name))
            if rec.approval_mode == 'quorum' and rec.min_approvers < 1:
                raise ValidationError(_(
                    'The quorum for "%s" must require at least one approval.', rec.name))

    @api.onchange('approval_mode')
    def _onchange_approval_mode(self):
        if self.approval_mode == 'quorum' and self.min_approvers < 1:
            self.min_approvers = 2

    def get_approver_users(self):
        """Return all users who can approve at this level."""
        self.ensure_one()
        users = self.approver_ids
        for group in self.approver_group_ids:
            users |= group.all_user_ids
        return users.filtered(lambda u: u.active and not u.share)

    def matches_request(self, request, cat_amounts=None):
        """True when this level applies to `request` — checks the global amount
        band, the department filter and the category-wise amount limits.

        `cat_amounts` (the request's per-category spend) may be supplied by the
        caller so it is computed once when evaluating many levels, instead of
        once per level — important for requests with many lines.
        """
        self.ensure_one()
        if self.amount_min and request.amount_total < self.amount_min:
            return False
        if self.amount_max and request.amount_total > self.amount_max:
            return False
        if self.analytic_account_ids and request.analytic_account_id not in self.analytic_account_ids:
            return False
        if self.category_limit_ids:
            if cat_amounts is None:
                cat_amounts = request._category_amounts()
            matched = False
            for cl in self.category_limit_ids:
                spend = cat_amounts.get(cl.product_category_id.id, 0.0)
                if spend <= 0:
                    continue
                if cl.amount_min and spend < cl.amount_min:
                    continue
                if cl.amount_max and spend > cl.amount_max:
                    continue
                matched = True
                break
            if not matched:
                return False
        return True

    def is_satisfied_by(self, approved_user_ids):
        """True when the set of users who have approved meets this level's policy."""
        self.ensure_one()
        approvers = self.get_approver_users()
        approved = set(approved_user_ids)
        if not approvers:
            return bool(approved)
        eligible_approved = approved & set(approvers.ids)
        if self.approval_mode == 'all':
            return set(approvers.ids).issubset(approved)
        if self.approval_mode == 'quorum':
            return len(eligible_approved) >= max(1, self.min_approvers)
        return len(eligible_approved) >= 1

    def action_view_pending_requests(self):
        """Smart-button: open the requests currently waiting at this level."""
        self.ensure_one()
        return {
            'name': _('Pending at: %s', self.name),
            'type': 'ir.actions.act_window',
            'res_model': 'ebshel.purchase.request',
            'view_mode': 'list,form',
            'domain': [
                ('state', '=', 'submitted'),
                ('company_id', '=', self.company_id.id),
                ('current_approval_level', '=', self.sequence),
            ],
        }


class PurchaseRequestApprovalLog(models.Model):
    """Immutable audit trail of every approval action on a purchase request."""
    _name = 'ebshel.purchase.request.approval.log'
    _description = 'Purchase Request Approval Log'
    _order = 'date desc, id desc'

    request_id = fields.Many2one(
        'ebshel.purchase.request', string='Purchase Request',
        required=True, ondelete='cascade', index=True,
    )
    level_id = fields.Many2one(
        'ebshel.purchase.request.approval.config', string='Approval Level',
        ondelete='set null',
    )
    level_name = fields.Char('Level', help='Stored name in case level config is deleted.')
    approver_id = fields.Many2one(
        'res.users', string='Approver', required=True,
        default=lambda self: self.env.user,
    )
    action = fields.Selection([
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('reset', 'Reset to Draft'),
        ('cancelled', 'Cancelled'),
        ('budget_requested', 'Budget Approval Requested'),
        ('budget_approved', 'Budget Approved'),
        ('budget_rejected', 'Budget Rejected'),
    ], string='Action', required=True)
    date = fields.Datetime('Date', default=fields.Datetime.now, readonly=True)
    comments = fields.Text('Comments / Reason')


class ApprovalCategoryLimit(models.Model):
    """Per product-category spend band on an approval level. Lets the same level
    apply different amount thresholds depending on the product category."""
    _name = 'ebshel.pr.approval.category.limit'
    _description = 'Approval Level Category Amount Limit'
    _order = 'product_category_id'

    config_id = fields.Many2one(
        'ebshel.purchase.request.approval.config', string='Approval Level',
        required=True, ondelete='cascade', index=True)
    product_category_id = fields.Many2one(
        'product.category', string='Product Category', required=True)
    amount_min = fields.Monetary('Min Amount', currency_field='currency_id')
    amount_max = fields.Monetary('Max Amount', currency_field='currency_id')
    currency_id = fields.Many2one(related='config_id.currency_id', readonly=True)

    @api.constrains('amount_min', 'amount_max')
    def _check_amount_range(self):
        for rec in self:
            if rec.amount_min and rec.amount_max and rec.amount_min > rec.amount_max:
                raise ValidationError(_(
                    'Category "%s": minimum amount cannot exceed maximum.',
                    rec.product_category_id.display_name))


class ApprovalRoutingSimulator(models.TransientModel):
    """Interactive 'what-if' tool: enter an amount + department and instantly see
    which approval levels a request would route through, in order — so admins can
    validate their approval rules before going live."""
    _name = 'ebshel.pr.approval.simulator'
    _description = 'Approval Routing Simulator'

    company_id = fields.Many2one(
        'res.company', default=lambda s: s.env.company, required=True)
    currency_id = fields.Many2one(related='company_id.currency_id', readonly=True)
    amount = fields.Monetary('Request Amount', currency_field='currency_id')
    analytic_account_id = fields.Many2one(
        'account.analytic.account', string='Department / Cost Centre')
    result_html = fields.Html('Routing', compute='_compute_result', sanitize=False)

    def _applicable_levels(self):
        self.ensure_one()
        levels = self.env['ebshel.purchase.request.approval.config'].search(
            [('active', '=', True), ('company_id', '=', self.company_id.id)])
        out = self.env['ebshel.purchase.request.approval.config']
        for lv in levels:
            if lv.amount_min and self.amount < lv.amount_min:
                continue
            if lv.amount_max and self.amount > lv.amount_max:
                continue
            if lv.analytic_account_ids and self.analytic_account_id not in lv.analytic_account_ids:
                continue
            out |= lv
        return out.sorted('sequence')

    @api.depends('company_id', 'amount', 'analytic_account_id')
    def _compute_result(self):
        for wiz in self:
            levels = wiz._applicable_levels()
            if not levels:
                company = wiz.company_id
                auto = company.pr_auto_approve_enabled and company.pr_auto_approve_amount \
                    and wiz.amount <= company.pr_auto_approve_amount
                if auto:
                    wiz.result_html = Markup(
                        '<div class="alert alert-success mb-0">⚡ <b>Auto-approved</b> — '
                        'below the company auto-approval threshold. No approval levels apply.</div>')
                else:
                    wiz.result_html = Markup(
                        '<div class="alert alert-warning mb-0">⚠ No approval levels match — '
                        'this request would be approved by any manager (no routing configured).</div>')
                continue
            rows = Markup('')
            total_needed = 0
            for idx, lv in enumerate(levels, 1):
                total_needed += lv.required_approvals
                approvers = ', '.join(lv.approver_user_ids.mapped('name')[:6]) or '—'
                sla = ('%s d' % lv.escalation_days) if lv.escalation_days else '—'
                rows += Markup(
                    '<tr><td style="padding:6px 10px;"><b>%(i)s</b></td>'
                    '<td style="padding:6px 10px;">%(name)s</td>'
                    '<td style="padding:6px 10px;">%(policy)s</td>'
                    '<td style="padding:6px 10px;color:#475569;">%(appr)s</td>'
                    '<td style="padding:6px 10px;text-align:center;">%(sla)s</td></tr>'
                ) % {
                    'i': idx, 'name': escape(lv.name), 'policy': escape(lv.policy_summary),
                    'appr': escape(approvers), 'sla': sla,
                }
            wiz.result_html = Markup(
                '<div style="margin-bottom:8px;"><b>%(n)s level(s)</b> · '
                '<b>%(t)s</b> approval(s) needed in total</div>'
                '<table class="table table-sm" style="border:1px solid #e2e8f0;">'
                '<thead><tr style="background:#f1f5f9;">'
                '<th style="padding:6px 10px;">#</th><th style="padding:6px 10px;">Level</th>'
                '<th style="padding:6px 10px;">Policy</th><th style="padding:6px 10px;">Approvers</th>'
                '<th style="padding:6px 10px;text-align:center;">SLA</th></tr></thead>'
                '<tbody>%(rows)s</tbody></table>'
            ) % {'n': len(levels), 't': total_needed, 'rows': rows}

    def action_dummy(self):
        # Recompute trigger (form stays open); the result is reactive anyway.
        return {'type': 'ir.actions.act_window', 'res_model': self._name,
                'res_id': self.id, 'view_mode': 'form', 'target': 'new',
                'name': _('Approval Routing Simulator')}
