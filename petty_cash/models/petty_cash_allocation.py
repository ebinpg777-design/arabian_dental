from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import formatLang


class PettyCashAllocation(models.Model):
    _name = 'petty.cash.allocation'
    _description = 'Petty Cash Allocations'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(copy=False, readonly=True, tracking=True, default='New')
    partner_id = fields.Many2one('res.partner', string='Cash Holder', required=True, tracking=True, check_company=True)
    journal_id = fields.Many2one(
        'account.journal',
        domain="[('type', 'in', ('cash', 'bank')), ('is_petty_cash', '=', True)]",
        tracking=True, check_company=True,
    )
    allocated_date = fields.Date(string="Allocated Date", default=fields.Date.context_today, required=True, tracking=True)
    return_date = fields.Date(string="Closed Date", readonly=True, copy=False, tracking=True)
    partial_return_count = fields.Integer(string='Partial Returns', compute='_compute_partial_returns', store=True)
    amount_write_off = fields.Monetary(string='Written Off', currency_field='currency_id',
                                       compute='_compute_partial_returns', store=True)
    amount_balance = fields.Monetary(string='Balance Amount', compute='_compute_amounts', currency_field='currency_id', store=True, tracking=True)
    amount_allocated = fields.Monetary(string='Allocated Amount', compute='_compute_amounts', currency_field='currency_id', store=True, tracking=True)
    amount_utilized = fields.Monetary(string='Utilized Amount', compute='_compute_amounts', currency_field='currency_id', store=True, tracking=True)
    amount_returned = fields.Monetary(string='Returned Amount', compute='_compute_amounts', currency_field='currency_id', store=True, tracking=True)
    amount_limit = fields.Monetary(string='Requested / Limit Amount', currency_field='currency_id', tracking=True)
    account_id = fields.Many2one('account.account', string='Petty Cash Account', related='journal_id.default_account_id', store=True, readonly=True)
    state = fields.Selection([
        ('draft', 'Draft'), ('submitted', 'Submitted'), ('allocated', 'Allocated'),
        ('returned', 'Closed'), ('cancelled', 'Cancelled'),
    ], default='draft', tracking=True)
    transaction_ids = fields.One2many('petty.cash.transaction', 'allocation_id', string='Transactions')
    expense_ids = fields.One2many('petty.cash.expense', 'allocation_id', string='Expenses')
    transaction_count = fields.Integer(compute='_compute_counts')
    expense_count = fields.Integer(compute='_compute_counts')
    allocation_approval_count = fields.Integer(compute='_compute_counts')
    expense_approval_count = fields.Integer(compute='_compute_counts')
    can_edit_limit = fields.Boolean(string='Can Edit Limit', compute='_compute_can_edit_limit')
    utilization_pct = fields.Float(string='Utilization %', compute='_compute_balance_indicators', store=True, digits=(5, 1))
    health_status = fields.Selection([
        ('healthy', 'Healthy'), ('warning', 'Low Balance'), ('critical', 'Critical'),
    ], string='Health', compute='_compute_balance_indicators', store=True)
    float_amount = fields.Monetary(
        string='Float Amount', currency_field='currency_id', tracking=True,
        help='Target balance to restore the cash holder to when the float is replenished. '
             'Defaults to the allocated amount.')
    auto_replenish = fields.Boolean(
        string='Auto Replenish', tracking=True,
        default=lambda self: self.env.company.petty_cash_auto_replenish_default,
        help='When the balance reaches the critical threshold, automatically create a draft '
             'top-up request that restores the holder back to the float amount.')
    last_low_balance_alert = fields.Date(string='Last Low-Balance Alert', readonly=True, copy=False)
    # ── Per-allocation spend controls ────────────────────────────────────────
    monthly_budget = fields.Monetary(
        string='Monthly Spend Budget', currency_field='currency_id', tracking=True,
        help='Maximum expenses allowed per calendar month. 0 = no monthly cap.')
    allowed_account_ids = fields.Many2many(
        'account.account', string='Allowed Expense Accounts',
        domain="[('account_type', 'like', 'expense')]",
        help='When set, expenses on this allocation are restricted to these accounts only.')
    amount_spent_this_month = fields.Monetary(
        string='Spent This Month', compute='_compute_monthly_spend',
        currency_field='currency_id')
    category_budget_ids = fields.One2many(
        'petty.cash.category.budget', 'allocation_id', string='Category Budgets',
        help='Soft budget limits per product category or expense account. '
             'Approvers receive a warning when an expense exceeds a budget line.')
    category_budget_enabled = fields.Boolean(
        string='Category Budget Warnings Enabled',
        compute='_compute_category_budget_enabled')
    note = fields.Text()
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    currency_id = fields.Many2one(related='company_id.currency_id', store=True)
    active = fields.Boolean(default=True)
    _company_field = 'company_id'

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        if 'partner_id' in fields_list and not vals.get('partner_id'):
            vals['partner_id'] = self.env.user.partner_id.id
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals['name'] == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('petty.cash.allocation.sequence') or 'New'
        return super().create(vals_list)

    @api.constrains('partner_id', 'company_id', 'state', 'account_id')
    def _check_unique_active_allocation(self):
        active_states = ['draft', 'submitted', 'allocated']
        for rec in self.filtered(lambda r: r.partner_id and r.account_id and r.state in active_states):
            existing = self.search([
                ('id', '!=', rec.id), ('partner_id', '=', rec.partner_id.id),
                ('company_id', '=', rec.company_id.id), ('state', 'in', active_states),
                ('account_id', '=', rec.account_id.id),
            ], limit=1)
            if existing:
                raise ValidationError(_('An active allocation already exists for this cash holder in the same company and account.'))

    @api.depends('transaction_ids.amount', 'transaction_ids.type', 'transaction_ids.state', 'transaction_ids.move_state')
    def _compute_amounts(self):
        for rec in self:
            posted = rec.transaction_ids.filtered(lambda t: t._counts_as_posted())
            rec.amount_allocated = sum(posted.filtered(lambda t: t.type == 'allocation').mapped('amount'))
            rec.amount_utilized = sum(posted.filtered(lambda t: t.type == 'expense').mapped('amount'))
            rec.amount_returned = sum(posted.filtered(lambda t: t.type in ('return', 'write_off')).mapped('amount'))
            rec.amount_balance = rec.amount_allocated - rec.amount_utilized - rec.amount_returned

    def _compute_counts(self):
        for rec in self:
            rec.transaction_count = len(rec.transaction_ids)
            rec.expense_count = len(rec.expense_ids)
            rec.allocation_approval_count = len(rec.transaction_ids.filtered(lambda t: t.type == 'allocation' and t.state == 'submitted'))
            rec.expense_approval_count = len(rec.expense_ids.filtered(lambda t: t.state == 'submitted'))

    @api.depends('transaction_ids.type', 'transaction_ids.state', 'transaction_ids.is_closing', 'transaction_ids.amount')
    def _compute_partial_returns(self):
        for rec in self:
            partials = rec.transaction_ids.filtered(
                lambda t: t.type == 'return' and t.state == 'posted' and not t.is_closing)
            rec.partial_return_count = len(partials)
            rec.amount_write_off = sum(
                t.amount for t in rec.transaction_ids.filtered(lambda t: t.type == 'write_off' and t.state == 'posted'))

    def _compute_category_budget_enabled(self):
        for rec in self:
            rec.category_budget_enabled = rec.company_id.petty_cash_category_budget_warning

    @api.depends('transaction_ids.type', 'transaction_ids.state', 'transaction_ids.amount', 'transaction_ids.date')
    def _compute_monthly_spend(self):
        if not self:
            return
        today = fields.Date.context_today(self)
        month_start = today.replace(day=1)
        groups = self.env['petty.cash.transaction'].read_group(
            [('allocation_id', 'in', self.ids),
             ('type', '=', 'expense'), ('state', '=', 'posted'),
             ('date', '>=', fields.Date.to_string(month_start))],
            ['amount:sum'], ['allocation_id'])
        spend_by_alloc = {g['allocation_id'][0]: g['amount'] or 0.0 for g in groups}
        for rec in self:
            rec.amount_spent_this_month = spend_by_alloc.get(rec.id, 0.0)

    # `state` must be declared: without a depends the field is never part of the
    # onchange spec, so on an unsaved record the web client receives no value,
    # falls back to False, and the Requested / Limit Amount renders read-only —
    # making it impossible to enter an amount on a brand-new request.
    @api.depends('state')
    def _compute_can_edit_limit(self):
        is_manager = self.env.user.has_group('petty_cash.group_petty_cash_manager')
        for rec in self:
            rec.can_edit_limit = is_manager or rec.state in ('draft', False)

    @api.depends('amount_allocated', 'amount_utilized', 'amount_balance')
    def _compute_balance_indicators(self):
        for rec in self:
            if rec.amount_allocated:
                rec.utilization_pct = round((rec.amount_utilized / rec.amount_allocated) * 100, 1)
                balance_pct = (rec.amount_balance / rec.amount_allocated) * 100
            else:
                rec.utilization_pct = 0.0
                balance_pct = 100.0
            warning = rec.company_id.petty_cash_warning_threshold or 40.0
            critical = rec.company_id.petty_cash_critical_threshold or 15.0
            if balance_pct >= warning:
                rec.health_status = 'healthy'
            elif balance_pct >= critical:
                rec.health_status = 'warning'
            else:
                rec.health_status = 'critical'

    def action_submit_request(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_('Only draft allocation requests can be submitted.'))
            if rec.partner_id != self.env.user.partner_id and not self.env.user.has_group('petty_cash.group_petty_cash_manager'):
                raise UserError(_('You can only request allocation for yourself.'))
            if rec.amount_limit <= 0:
                raise UserError(_('Please enter a requested amount.'))
            limit = rec.company_id.petty_cash_self_request_limit
            is_officer = self.env.user.has_group('petty_cash.group_petty_cash_officer')
            if limit and not is_officer and rec.amount_limit > limit:
                raise UserError(_(
                    'The requested amount exceeds your self-service limit of %s. '
                    'Please ask a petty cash officer or manager to raise the request.'
                ) % rec._money(limit))
            rec.state = 'submitted'

    def action_allocate(self):
        for rec in self:
            if rec.state != 'submitted':
                raise UserError(_('Only submitted allocations can be allocated.'))
            if not rec.journal_id:
                raise UserError(_('Please select a petty cash journal before allocation.'))
            amount = rec.amount_limit
            if amount <= 0:
                raise UserError(_('Please enter an allocation amount.'))
            tx = self.env['petty.cash.transaction'].create(rec._prepare_transaction_vals('allocation', amount))
            if not rec.float_amount:
                rec.float_amount = amount
            rec.partner_id.is_petty_cash_holder = True
            return {
                'name': _('Allocation Transaction'),
                'type': 'ir.actions.act_window',
                'res_model': 'petty.cash.transaction',  
                'view_mode': 'form',
                'res_id': tx.id,
                'target': 'current',
            }

    def action_request_reallocation(self):
        self.ensure_one()
        if self.state != 'allocated':
            raise UserError(_('Only active allocations can be reallocated.'))
        return self._open_transaction_wizard('allocation', 0.0, _('Re-allocation Request'))

    def action_partial_return(self):
        """Open the close/partial-return wizard in partial mode."""
        self.ensure_one()
        if self.state != 'allocated':
            raise UserError(_('Only active allocations can have a partial return.'))
        if self.amount_balance <= 0:
            raise UserError(_('There is no remaining balance to return.'))
        return self._open_close_wizard(closing=False)

    def action_close(self):
        """Open the close/partial-return wizard in closing mode."""
        self.ensure_one()
        if self.state != 'allocated':
            raise UserError(_('Only active allocations can be closed.'))
        return self._open_close_wizard(closing=True)

    def _open_close_wizard(self, closing=True):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Close Allocation') if closing else _('Partial Return'),
            'res_model': 'petty.cash.close.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_allocation_id': self.id,
                'default_is_closing': closing,
                'default_amount': self.amount_balance if closing else 0.0,
                'default_date': fields.Date.context_today(self),
            },
        }

    def get_category_budget_warnings(self, expense):
        """Return list of warning strings if expense lines exceed category budgets.
        Called from the expense model; returns [] when no company flag or no budgets set.
        """
        self.ensure_one()
        if not self.company_id.petty_cash_category_budget_warning:
            return []
        warnings = []
        for budget in self.category_budget_ids:
            if budget.categ_id:
                lines = expense.line_ids.filtered(lambda l: l.categ_id == budget.categ_id)
            elif budget.account_id:
                lines = expense.line_ids.filtered(lambda l: l.account_id == budget.account_id)
            else:
                continue
            line_total = sum(lines.mapped('amount'))
            projected = budget.amount_spent + line_total
            if projected > budget.budget_amount:
                warnings.append(_(
                    '⚠ Category budget exceeded: %s — budget %s, spent %s, this expense adds %s.'
                ) % (budget._label(), self._money(budget.budget_amount),
                     self._money(budget.amount_spent), self._money(line_total)))
        return warnings

    def _funding_account(self):
        """The cash account this float was funded from.

        It is the contra side of the allocation entry (Dr petty cash / Cr this),
        and therefore the account returned cash has to go back to so a return
        mirrors the allocation instead of landing on the holder's payable.
        Posted allocations win over pending ones; empty when the float was
        funded without a contra account.
        """
        self.ensure_one()
        funding = self.transaction_ids.filtered(
            lambda t: t.type == 'allocation' and t.counter_account_id)
        candidates = funding.filtered(lambda t: t.state == 'posted') or funding
        return candidates.sorted(lambda t: (t.date, t.id))[-1:].counter_account_id

    def _close(self, date=None):
        """Mark this allocation as closed. Called when a closing return is posted."""
        self.ensure_one()
        self.write({
            'state': 'returned',
            'return_date': date or fields.Date.context_today(self),
        })
        self.partner_id.is_petty_cash_holder = False
        self.message_post(body=_('Allocation closed. Final balance returned: %s') % self._money(self.amount_returned))

    # ─── Imprest replenishment ──────────────────────────────────────────────
    def _money(self, amount):
        self.ensure_one()
        return formatLang(self.env, amount, currency_obj=self.currency_id)

    def _replenishment_amount(self):
        """Top-up needed to restore the holder back to the float amount."""
        self.ensure_one()
        return self.currency_id.round(self.float_amount - self.amount_balance) if self.float_amount else 0.0

    def _has_pending_replenishment(self):
        self.ensure_one()
        return bool(self.transaction_ids.filtered(
            lambda t: t.type == 'allocation' and t.state in ('draft', 'submitted', 'approved')))

    def _create_replenishment_request(self, automated=False):
        """Create a draft allocation (top-up) transaction restoring the float.

        Returns the created transaction, or an empty recordset when nothing is due
        (no float configured, balance already at/above float, or a top-up is already pending).
        """
        self.ensure_one()
        Transaction = self.env['petty.cash.transaction']
        if self.state != 'allocated' or self._has_pending_replenishment():
            return Transaction
        amount = self._replenishment_amount()
        if amount <= 0:
            return Transaction
        tx = Transaction.create(self._prepare_transaction_vals('allocation', amount))
        self.message_post(body=_(
            '%s top-up of %s created to restore the float to %s.'
        ) % (_('Automatic') if automated else _('Manual'),
             self._money(amount), self._money(self.float_amount)))
        self.activity_schedule(
            'mail.mail_activity_data_todo',
            summary=_('Approve petty cash replenishment'),
            note=_('Balance for %s fell to %s. A top-up request of %s is awaiting approval.') % (
                self.partner_id.display_name, self._money(self.amount_balance), self._money(amount)),
            user_id=self._alert_user().id,
        )
        return tx

    def action_replenish(self):
        self.ensure_one()
        if self.state != 'allocated':
            raise UserError(_('Only active allocations can be replenished.'))
        if not self.float_amount:
            raise UserError(_('Please set a float amount before replenishing.'))
        tx = self._create_replenishment_request(automated=False)
        if not tx:
            raise UserError(_('Nothing to replenish — the balance is already at or above the float amount.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Replenishment Request'),
            'res_model': 'petty.cash.transaction',
            'view_mode': 'form',
            'res_id': tx.id,
            'target': 'current',
        }

    # ─── Notifications ──────────────────────────────────────────────────────
    def _alert_user(self):
        """User to be notified/assigned: the cash holder's user, else the creator."""
        self.ensure_one()
        return self.partner_id.user_ids[:1] or self.create_uid

    def _notify_low_balance(self):
        self.ensure_one()
        template = self.env.ref('petty_cash.mail_template_petty_cash_low_balance', raise_if_not_found=False)
        if template:
            template.send_mail(self.id, force_send=False)
        self.activity_schedule(
            'mail.mail_activity_data_todo',
            summary=_('Petty cash balance low'),
            note=_('Balance for %s is %s of an allocated %s (%s%% used).') % (
                self.partner_id.display_name, self._money(self.amount_balance),
                self._money(self.amount_allocated), round(self.utilization_pct, 1)),
            user_id=self._alert_user().id,
        )
        self.last_low_balance_alert = fields.Date.context_today(self)

    @api.model
    def _cron_check_low_balance(self):
        """Daily: alert holders/managers about low or critical petty cash balances."""
        today = fields.Date.context_today(self)
        records = self.search([('state', '=', 'allocated'), ('health_status', 'in', ('warning', 'critical'))])
        for rec in records:
            if not rec.company_id.petty_cash_alerts_enabled or rec.last_low_balance_alert == today:
                continue
            rec._notify_low_balance()
        return True

    @api.model
    def _cron_auto_replenish(self):
        """Daily: raise draft top-up requests for critical, auto-replenish allocations."""
        records = self.search([
            ('state', '=', 'allocated'), ('auto_replenish', '=', True),
            ('health_status', '=', 'critical'), ('float_amount', '>', 0),
        ])
        for rec in records:
            rec._create_replenishment_request(automated=True)
        return True

    # ─── KPI dashboard ──────────────────────────────────────────────────────
    @api.model
    def _dashboard_period_start(self, period):
        today = fields.Date.context_today(self)
        if period == 'month':
            return today.replace(day=1)
        if period == 'quarter':
            return today.replace(month=((today.month - 1) // 3) * 3 + 1, day=1)
        if period == 'year':
            return today.replace(month=1, day=1)
        return None

    @api.model
    def get_dashboard_data(self, period='all'):
        """Aggregated KPIs for the dashboard. Read through normal ACLs/record rules,
        so a User sees only their own figures while Officers/Managers see everyone."""
        currency = self.env.company.currency_id

        def money(amount):
            return formatLang(self.env, amount or 0.0, currency_obj=currency)

        start = self._dashboard_period_start(period)
        start_str = fields.Date.to_string(start) if start else None
        active_domain = [('state', '=', 'allocated')] + ([('allocated_date', '>=', start_str)] if start_str else [])
        returned_domain = [('state', '=', 'returned')] + ([('return_date', '>=', start_str)] if start_str else [])
        # Active allocations are scoped by when they were allocated…
        active = self.search(active_domain)
        # …but returns belong to the period they were actually returned in (return_date).
        returned = self.search(returned_domain)

        total_allocated = sum(active.mapped('amount_allocated'))
        total_utilized = sum(active.mapped('amount_utilized'))
        total_balance = sum(active.mapped('amount_balance'))
        # Partial returns on ACTIVE allocations — cash already handed back but allocation still open.
        total_partial_returned = sum(active.mapped('amount_returned'))
        # Returns from CLOSED allocations (scoped to period by return_date).
        total_returned = sum(returned.mapped('amount_returned'))
        total_float = sum(active.mapped('float_amount'))
        # Deployed = what is still outstanding (not yet returned or utilized).
        # Utilization measures how much of the currently deployed float has been spent.
        total_deployed = total_allocated - total_partial_returned
        utilization = round((total_utilized / total_deployed) * 100, 1) if total_deployed else 0.0
        avg_utilization = round(sum(active.mapped('utilization_pct')) / len(active), 1) if active else 0.0
        avg_balance = (total_balance / len(active)) if active else 0.0
        pending_replenishment = self.env['petty.cash.transaction'].search_count([
            ('type', '=', 'allocation'), ('state', 'in', ('draft', 'submitted', 'approved')),
            ('allocation_id.state', '=', 'allocated'),
        ])

        # Period spend (posted expense transactions) and pending allocation requests
        submitted = self.search(
            [('state', '=', 'submitted')] + ([('allocated_date', '>=', start_str)] if start_str else []))
        expense_domain = ([('type', '=', 'expense'), ('state', '=', 'posted')]
                          + ([('date', '>=', start_str)] if start_str else []))
        exp_txs = self.env['petty.cash.transaction'].search(expense_domain)
        expense_total = sum(exp_txs.mapped('amount'))

        # Per-holder breakdown
        holders = {}
        for rec in active:
            row = holders.setdefault(rec.partner_id.id, {
                'id': rec.partner_id.id, 'name': rec.partner_id.display_name,
                'allocated': 0.0, 'utilized': 0.0, 'partial_returned': 0.0, 'balance': 0.0,
            })
            row['allocated'] += rec.amount_allocated
            row['utilized'] += rec.amount_utilized
            row['partial_returned'] += rec.amount_returned
            row['balance'] += rec.amount_balance
        warning_th = self.env.company.petty_cash_warning_threshold or 40.0
        critical_th = self.env.company.petty_cash_critical_threshold or 15.0
        holder_rows = []
        for row in holders.values():
            deployed = row['allocated'] - row['partial_returned']
            pct = round((row['utilized'] / deployed) * 100, 1) if deployed else 0.0
            bal_pct = (row['balance'] / row['allocated'] * 100) if row['allocated'] else 100.0
            health = 'healthy' if bal_pct >= warning_th else 'warning' if bal_pct >= critical_th else 'critical'
            holder_rows.append({
                **row, 'utilization': pct, 'health': health,
                'allocated_str': money(row['allocated']),
                'utilized_str': money(row['utilized']),
                'partial_returned_str': money(row['partial_returned']),
                'balance_str': money(row['balance']),
            })
        holder_rows.sort(key=lambda r: r['balance'])

        # Top spend by product (visible expense lines)
        Line = self.env['petty.cash.expense.line']
        spend = Line.read_group(
            [('expense_id.state', 'in', ('approved', 'bill_created', 'paid'))],
            ['amount:sum'], ['product_id'], orderby='amount desc', limit=6)
        top_spend = [{
            'name': g['product_id'][1] if g.get('product_id') else _('Uncategorized'),
            'amount': g['amount'] or 0.0, 'amount_str': money(g['amount'] or 0.0),
        } for g in spend if (g.get('amount') or 0.0) > 0]

        is_manager = self.env.user.has_group('petty_cash.group_petty_cash_officer')
        pending_alloc = pending_exp = pending_ret = 0
        if is_manager:
            Tx = self.env['petty.cash.transaction']
            pending_alloc = Tx.search_count([('type', '=', 'allocation'), ('state', '=', 'submitted')])
            pending_ret = Tx.search_count([('type', '=', 'return'), ('state', '=', 'submitted')])
            pending_exp = self.env['petty.cash.expense'].search_count([('state', '=', 'submitted')])

        # Allocations needing attention
        attention = [{
            'id': rec.id, 'name': rec.name, 'holder': rec.partner_id.display_name,
            'balance_str': money(rec.amount_balance), 'health': rec.health_status,
            'utilization': round(rec.utilization_pct, 1),
        } for rec in active.filtered(lambda a: a.health_status in ('warning', 'critical')).sorted(
            lambda a: a.amount_balance)[:8]]

        return {
            'currency': {'symbol': currency.symbol, 'position': currency.position},
            'is_manager': is_manager,
            'period': period,
            'domains': {
                'active': active_domain,
                'returned': returned_domain,
                'expense': expense_domain,
                'partial_returned': [('state', '=', 'allocated'), ('amount_returned', '>', 0)],
            },
            'kpis': {
                'allocated': total_allocated, 'allocated_str': money(total_allocated),
                'utilized': total_utilized, 'utilized_str': money(total_utilized),
                'balance': total_balance, 'balance_str': money(total_balance),
                'returned': total_returned, 'returned_str': money(total_returned),
                'partial_returned': total_partial_returned,
                'partial_returned_str': money(total_partial_returned),
                'utilization': utilization,
                'avg_utilization': avg_utilization,
                'active_count': len(active),
                'returned_count': len(returned),
                'submitted_count': len(submitted),
                'holder_count': len(holders),
                'expense_total': expense_total, 'expense_total_str': money(expense_total),
                'expense_count': len(exp_txs),
                'float_total': total_float, 'float_total_str': money(total_float),
                'avg_balance': avg_balance, 'avg_balance_str': money(avg_balance),
                'pending_replenishment': pending_replenishment,
                'healthy_count': len([r for r in holder_rows if r['health'] == 'healthy']),
                'low_count': len([r for r in holder_rows if r['health'] == 'warning']),
                'critical_count': len([r for r in holder_rows if r['health'] == 'critical']),
            },
            'pending': {'allocation': pending_alloc, 'expense': pending_exp, 'return': pending_ret},
            'holders': holder_rows,
            'top_spend': top_spend,
            'attention': attention,
        }

    @api.model
    def action_open_dashboard_allocations(self, domain=None):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Allocations'),
            'res_model': 'petty.cash.allocation',
            'view_mode': 'list,kanban,form',
            'views': [(False, 'list'), (False, 'kanban'), (False, 'form')],
            'domain': domain or [],
            'target': 'current',
        }

    # ─── Settlement statement ───────────────────────────────────────────────
    def get_statement_lines(self):
        """Posted transactions in chronological order with a running balance, for the PDF."""
        self.ensure_one()
        posted = self.transaction_ids.filtered(
            lambda t: t._counts_as_posted()
        ).sorted(lambda t: (t.date, t.id))
        running = 0.0
        lines = []
        for tx in posted:
            running += tx.amount_signed
            lines.append({
                'date': tx.date,
                'name': tx.name,
                'type_label': dict(tx._fields['type'].selection).get(tx.type),
                'type': tx.type,
                'reference': tx.note or (tx.partner_id.display_name if tx.partner_id else ''),
                'inflow': tx.amount if tx.type == 'allocation' else 0.0,
                'outflow': tx.amount if tx.type != 'allocation' else 0.0,
                'balance': running,
            })
        return lines

    def action_print_statement(self):
        return self.env.ref('petty_cash.action_report_petty_cash_statement').report_action(self)

    def _prepare_transaction_vals(self, transaction_type, amount):
        self.ensure_one()
        return {
            'allocation_id': self.id,
            'partner_id': self.partner_id.id,
            'type': transaction_type,
            'amount': amount,
            'date': fields.Date.context_today(self),
            'petty_cash_account_id': self.account_id.id,
            # Top-ups reuse whatever funded the float, so only the very first
            # allocation has to be given a contra account by hand.
            'counter_account_id': self._funding_account().id,
            'company_id': self.company_id.id,
        }

    def _open_transaction_wizard(self, transaction_type, amount, label):
        self.ensure_one()
        return {
            'name': label,
            'type': 'ir.actions.act_window',
            'res_model': 'petty.cash.transaction',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_allocation_id': self.id,
                'default_partner_id': self.partner_id.id,
                'default_type': transaction_type,
                'default_amount': amount,
                'default_date': fields.Date.context_today(self),
                'default_petty_cash_account_id': self.account_id.id,
                'default_company_id': self.company_id.id,
            },
        }

    def action_new_expense(self):
        self.ensure_one()
        if self.state != 'allocated':
            raise UserError(_('Expenses can be added only to active allocations.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('New Petty Cash Expense'),
            'res_model': 'petty.cash.expense',
            'view_mode': 'form',
            'target': 'current',
            'context': {'default_allocation_id': self.id, 'default_company_id': self.company_id.id},
        }

    def action_cancel(self):
        if not self.env.su and not self.env.user.has_group('petty_cash.group_petty_cash_officer'):
            raise UserError(_('Only Petty Cash Officers or Managers can cancel allocations.'))
        for rec in self:
            if rec.amount_balance:
                raise UserError(_('Cannot cancel an allocation with a remaining balance.'))
            pending_expenses = rec.expense_ids.filtered(
                lambda e: e.state in ('submitted', 'approved', 'bill_created'))
            if pending_expenses:
                raise UserError(_(
                    'Cannot cancel this allocation — %d expense(s) are still active (%s). '
                    'Please reject or cancel them first.'
                ) % (len(pending_expenses),
                     ', '.join(pending_expenses.mapped('name'))))
            rec.transaction_ids.filtered(
                lambda t: t.state in ('draft', 'submitted', 'approved')
            ).write({'state': 'cancelled'})
            rec.state = 'cancelled'
            rec.message_post(body=_('Allocation cancelled by %s.') % self.env.user.name)

    def action_reset_to_draft(self):
        # Only a cancelled request is reopened: resetting an allocated or closed
        # float to draft would hide money that is still out with a holder.
        if not self.env.su and not self.env.user.has_group('petty_cash.group_petty_cash_officer'):
            raise UserError(_('Only Petty Cash Officers or Managers can reset allocations.'))
        if self.filtered(lambda rec: rec.state != 'cancelled'):
            raise UserError(_('Only cancelled allocations can be reset to draft.'))
        self.write({'state': 'draft'})
        
    def action_view_allocation_approvals(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Allocation Approvals'),
            'res_model': 'petty.cash.transaction',
            'view_mode': 'list,form',
            'domain': [
                ('allocation_id', '=', self.id),
                ('type', 'in', ['allocation', 'return']),
                ('state', 'in', ['submitted', 'approved']),
            ],
            'context': {
                'default_allocation_id': self.id,
                'default_partner_id': self.partner_id.id,
            },
        }

    def action_view_expense_approvals(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Expense Approvals'),
            'res_model': 'petty.cash.expense',
            'view_mode': 'list,form',
            'domain': [
                ('allocation_id', '=', self.id),
                ('state', 'in', ['submitted', 'approved']),
            ],
            'context': {
                'default_allocation_id': self.id,
                'default_partner_id': self.partner_id.id,
            },
        }

    def action_view_expenses(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Expenses'),
            'res_model': 'petty.cash.expense',
            'view_mode': 'list,form',
            'domain': [('allocation_id', '=', self.id)],
            'context': {
                'default_allocation_id': self.id,
                'default_partner_id': self.partner_id.id,
            },
        }

    def action_view_transactions(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Transactions'),
            'res_model': 'petty.cash.transaction',
            'view_mode': 'list,form',
            'domain': [('allocation_id', '=', self.id)],
            'context': {
                'default_allocation_id': self.id,
                'default_petty_cash_partner_id': self.partner_id.id,
            },
        }

    def action_view_partial_returns(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Partial Returns'),
            'res_model': 'petty.cash.transaction',
            'view_mode': 'list,form',
            'domain': [
                ('allocation_id', '=', self.id),
                ('type', '=', 'return'),
                ('is_closing', '=', False),
            ],
            'context': {
                'default_allocation_id': self.id,
                'default_type': 'return',
                'default_is_closing': False,
            },
        }
        
    def unlink(self):
        if any(rec.state != 'draft' or rec.amount_utilized for rec in self):
            raise UserError(_('Cannot delete allocations that are not in draft state or have utilized amounts.'))
        return super().unlink()
