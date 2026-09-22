from markupsafe import Markup
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class PettyCashExpense(models.Model):
    _name = 'petty.cash.expense'
    _description = 'Petty Cash Expenses'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(copy=False, readonly=True, tracking=True, default='New')
    allocation_id = fields.Many2one(
        'petty.cash.allocation', string='Petty Cash Allocation',
        required=True, tracking=True, check_company=True)
    partner_id = fields.Many2one('res.partner', string='Vendor', required=False, tracking=True, check_company=True)
    petty_cash_partner_id = fields.Many2one('res.partner', string='Cash Holder', related='allocation_id.partner_id', store=True, readonly=True)
    total_amount = fields.Monetary(string='Total Amount', compute='_compute_total_amount', store=True, currency_field='currency_id', tracking=True)
    line_ids = fields.One2many('petty.cash.expense.line', 'expense_id', string='Expense Lines', copy=True)
    state = fields.Selection([
        ('draft', 'Draft'), ('submitted', 'Submitted'), ('approved', 'Approved'),
        ('rejected', 'Rejected'), ('bill_created', 'Bill Created'), ('paid', 'Paid'), ('cancelled', 'Cancelled'),
    ], default='draft', tracking=True)
    transaction_id = fields.Many2one('petty.cash.transaction', string='Transaction', copy=False, readonly=True)
    move_id = fields.Many2one('account.move', string='Bill', readonly=True, copy=False)
    move_state = fields.Selection(related='move_id.state', string='Entry Status', store=True, readonly=True)
    payment_id = fields.Many2one('account.payment', string='Payment', readonly=True, copy=False)
    balance_available = fields.Monetary(string='Available Balance', related='allocation_id.amount_balance', currency_field='currency_id', readonly=True)
    note = fields.Text()
    attachment_ids = fields.Many2many('ir.attachment', string='Attachments', copy=False)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    currency_id = fields.Many2one(related='company_id.currency_id', store=True)
    active = fields.Boolean(default=True)
    _company_field = 'company_id'

    submission_date = fields.Datetime(
        string='Submitted On', readonly=True, copy=False,
        help='Date and time when this expense was submitted for approval.')

    # ── Duplicate detection ────────────────────────────────────────────────────
    is_duplicate_suspect = fields.Boolean(
        string='Possible Duplicate', default=False, copy=False, tracking=True,
        help='Flagged automatically when a similar expense was found within the detection window.')
    duplicate_warning = fields.Text(
        string='Duplicate Warning', readonly=True, copy=False)
    category_budget_warning = fields.Text(
        string='Category Budget Warning', readonly=True, copy=False)

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        allocation_id = vals.get('allocation_id') or self.env.context.get('default_allocation_id')
        if allocation_id:
            allocation = self.env['petty.cash.allocation'].browse(allocation_id)
            vals.setdefault('company_id', allocation.company_id.id)
        return vals

    @api.depends('line_ids.amount')
    def _compute_total_amount(self):
        for rec in self:
            rec.total_amount = sum(rec.line_ids.mapped('amount'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals['name'] in ('New', _('New')):
                vals['name'] = self.env['ir.sequence'].next_by_code('petty.cash.expense.sequence') or 'New'
        return super().create(vals_list)

    @api.constrains('allocation_id', 'total_amount', 'state')
    def _check_balance(self):
        from collections import defaultdict
        alloc_map = defaultdict(list)
        for rec in self.filtered(lambda r: r.state in ('approved', 'bill_created') and r.allocation_id):
            alloc_map[rec.allocation_id.id].append(rec)
        for alloc_id, recs in alloc_map.items():
            alloc = recs[0].allocation_id
            rec_ids = {r.id for r in recs}
            groups = self.env['petty.cash.expense'].read_group(
                [('allocation_id', '=', alloc_id),
                 ('state', 'in', ('approved', 'bill_created')),
                 ('id', 'not in', list(rec_ids))],
                ['total_amount:sum'], [])
            reserved = groups[0]['total_amount'] or 0.0 if groups else 0.0
            this_total = sum(r.total_amount for r in recs)
            if this_total + reserved > alloc.amount_balance:
                raise ValidationError(_('Expense amount cannot exceed the available petty cash balance.'))

    def _check_for_duplicates(self):
        """Return a warning string if a similar expense exists within the detection window, else ''."""
        self.ensure_one()
        company = self.company_id
        if not company.petty_cash_duplicate_detection:
            return ''
        window = company.petty_cash_duplicate_window_days or 30
        if not window:
            return ''
        cutoff = fields.Date.subtract(fields.Date.context_today(self), days=window)
        domain = [
            ('id', '!=', self.id),
            ('state', 'not in', ('cancelled', 'rejected')),
            ('total_amount', '=', self.total_amount),
            ('allocation_id.partner_id', '=', self.allocation_id.partner_id.id),
            ('create_date', '>=', cutoff),
        ]
        if self.partner_id:
            domain.append(('partner_id', '=', self.partner_id.id))
        dupe = self.search(domain, limit=1)
        if dupe:
            return _(
                'Possible duplicate of %s (same vendor, same amount %s, submitted within %d days).'
            ) % (dupe.name, dupe.allocation_id._money(dupe.total_amount), window)
        return ''

    def action_submit(self):
        for rec in self:
            if rec.state != 'draft':
                continue
            if not rec.line_ids or rec.total_amount <= 0:
                raise UserError(_('Please add at least one expense line.'))
            company = rec.company_id
            max_amount = company.petty_cash_max_expense_amount
            if max_amount and rec.total_amount > max_amount:
                raise UserError(_(
                    'This expense (%.2f) exceeds the maximum allowed expense amount (%.2f).'
                ) % (rec.total_amount, max_amount))
            if (company.petty_cash_expense_attachment_required
                    and rec.total_amount >= company.petty_cash_expense_attachment_min
                    and not rec.attachment_ids):
                raise UserError(_(
                    'A receipt attachment is required for expenses of %.2f or more.'
                ) % company.petty_cash_expense_attachment_min)
            dup_warning = rec._check_for_duplicates()
            rec.is_duplicate_suspect = bool(dup_warning)
            rec.duplicate_warning = dup_warning
            if dup_warning:
                rec.message_post(body='🔍 ' + dup_warning)
            auto_max = company.petty_cash_expense_auto_approve_max
            if auto_max and 0 < rec.total_amount <= auto_max and not dup_warning:
                if rec.total_amount > rec.allocation_id.amount_balance:
                    raise UserError(_('Expense amount exceeds the available petty cash balance.'))
                rec.write({'state': 'approved', 'submission_date': fields.Datetime.now()})
                rec.message_post(body=_('Auto-approved (within the %.2f auto-approval limit).') % auto_max)
            else:
                rec.write({'state': 'submitted', 'submission_date': fields.Datetime.now()})
                rec.message_post(body=_('Submitted for approval.'))

    def action_approve(self):
        # The Approve button is a manager's; without this a holder could approve
        # their own expense over RPC.
        if not self.env.su and not self.env.user.has_group('petty_cash.group_petty_cash_manager'):
            raise UserError(_('Only Petty Cash Managers can approve expenses.'))
        for rec in self.filtered(lambda r: r.state == 'submitted'):
            alloc = rec.allocation_id
            if rec.total_amount > alloc.amount_balance:
                raise UserError(_('Expense amount exceeds the available petty cash balance.'))
            if alloc.monthly_budget:
                projected = alloc.amount_spent_this_month + rec.total_amount
                if projected > alloc.monthly_budget:
                    raise UserError(_(
                        'This expense would exceed the monthly spend budget of %s '
                        '(already spent %s this month).'
                    ) % (alloc._money(alloc.monthly_budget),
                         alloc._money(alloc.amount_spent_this_month)))
            if alloc.allowed_account_ids:
                bad = rec.line_ids.filtered(
                    lambda l: l.account_id and l.account_id not in alloc.allowed_account_ids)
                if bad:
                    raise UserError(_(
                        'Line "%s" uses account "%s" which is not in the allowed '
                        'expense accounts for this allocation.'
                    ) % (bad[0].name, bad[0].account_id.display_name))
            cat_warnings = alloc.get_category_budget_warnings(rec)
            if cat_warnings:
                rec.category_budget_warning = '\n'.join(cat_warnings)
                for w in cat_warnings:
                    rec.message_post(body=w)
            else:
                rec.category_budget_warning = False
            rec.state = 'approved'

    def action_reject(self):
        if not self.env.user.has_group('petty_cash.group_petty_cash_manager'):
            raise UserError(_('Only Petty Cash Managers can reject expenses.'))
        for rec in self.filtered(lambda r: r.state in ('submitted', 'approved')):
            rec.sudo().write({'state': 'rejected'})
            rec.sudo().message_post(body=Markup(
                '<b>❌ Rejected</b> by <b>{user}</b>.'
            ).format(user=self.env.user.name))

    def action_bulk_approve(self):
        """Bulk approve from list view — skips records that cannot be approved."""
        if not self.env.user.has_group('petty_cash.group_petty_cash_manager'):
            raise UserError(_('Only Petty Cash Managers can approve expenses.'))
        approved = self.env['petty.cash.expense']
        skipped = []
        for rec in self.filtered(lambda r: r.state == 'submitted'):
            try:
                rec.action_approve()
                approved |= rec
            except UserError as e:
                skipped.append('%s: %s' % (rec.name, str(e)))
        msg = _('%d expense(s) approved.') % len(approved)
        if skipped:
            msg += '\n' + _('Skipped:\n%s') % '\n'.join(skipped)
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _('Bulk Approval'), 'message': msg,
                           'type': 'success' if approved else 'warning', 'sticky': bool(skipped)}}

    def action_bulk_reject(self):
        if not self.env.user.has_group('petty_cash.group_petty_cash_manager'):
            raise UserError(_('Only Petty Cash Managers can reject expenses.'))
        to_reject = self.filtered(lambda r: r.state in ('submitted', 'approved'))
        for rec in to_reject:
            rec.sudo().message_post(body=Markup(
                '<b>❌ Rejected</b> by <b>{user}</b> (bulk action).'
            ).format(user=self.env.user.name))
        to_reject.write({'state': 'rejected'})
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _('Bulk Rejection'),
                           'message': _('%d expense(s) rejected.') % len(to_reject),
                           'type': 'warning', 'sticky': False}}

    def action_cancel(self):
        if not self.env.user.has_group('petty_cash.group_petty_cash_officer'):
            raise UserError(_('Only Petty Cash Officers or Managers can cancel expenses.'))
        self.write({'state': 'cancelled'})

    def action_reset_to_draft(self):
        if not self.env.user.has_group('petty_cash.group_petty_cash_officer'):
            raise UserError(_('Only Petty Cash Officers or Managers can reset expenses.'))
        self.write({'state': 'draft'})

    def _get_expense_account(self, line):
        product = line.product_id
        return line.account_id or product.property_account_expense_id or product.categ_id.property_account_expense_categ_id

    def _prepare_invoice_lines(self):
        self.ensure_one()
        invoice_lines = []
        for line in self.line_ids.filtered(lambda l: l.amount > 0):
            account = self._get_expense_account(line)
            if not account:
                raise UserError(_('Please configure an expense account for line %s.') % (line.name or line.product_id.display_name))
            invoice_lines.append((0, 0, {
                'name': line.name or line.product_id.display_name,
                'quantity': 1.0,
                'product_id': line.product_id.id or False,
                'account_id': account.id,
                'price_unit': line.amount,
            }))
        if not invoice_lines:
            raise UserError(_('No valid expense lines found.'))
        return invoice_lines

    def action_create_bill(self):
        self.ensure_one()
        if self.state != 'approved':
            raise UserError(_('Only approved expenses can be converted to bills.'))
        bill = self.env['account.move'].sudo().with_context(skip_petty_cash_auto_transaction=True).create({
            'move_type': 'in_invoice',
            'partner_id': self.partner_id.id,
            'invoice_date': fields.Date.context_today(self),
            'invoice_line_ids': self._prepare_invoice_lines(),
            'currency_id': self.currency_id.id,
            'company_id': self.company_id.id,
            'petty_cash_partner_id': self.petty_cash_partner_id.id,
            'petty_cash_expense_id': self.id,
        })
        self.write({'state': 'bill_created', 'move_id': bill.id})
        return self.action_view_bill()

    def action_register_cash_spend(self):
        # Checked HERE, on the person: the transaction below is created with sudo,
        # and under sudo its own approve/post guards stand aside for the system.
        # Without this a holder could post their own expense straight out of the
        # float over RPC.
        if not self.env.su and not self.env.user.has_group('petty_cash.group_petty_cash_manager'):
            raise UserError(_('Only Petty Cash Managers can post petty cash spend.'))
        for rec in self:
            if rec.state not in ('approved', 'bill_created'):
                raise UserError(_('Only approved expenses can be posted as petty cash spend.'))
            if rec.transaction_id:
                continue
            # The transaction is a movement of the *holder's* float, so it carries
            # the cash holder — the same partner as the allocation and return
            # transactions. Stamping the vendor here made an allocation's
            # transaction list read as though it held other partners' records.
            # The vendor stays on the expense and is repeated in the note.
            tx = self.env['petty.cash.transaction'].sudo().create({
                'allocation_id': rec.allocation_id.id,
                'partner_id': rec.petty_cash_partner_id.id,
                'type': 'expense',
                'amount': rec.total_amount,
                'date': fields.Date.context_today(rec),
                'company_id': rec.company_id.id,
                'note': '%s — %s' % (rec.name, rec.partner_id.display_name)
                        if rec.partner_id else rec.name,
            })
            tx.action_approve()
            tx.action_post()
            rec.write({'transaction_id': tx.id, 'state': 'paid'})
        return True

    def action_view_payment(self):
        self.ensure_one()
        if not self.payment_id:
            raise UserError(_('No payment associated with this expense.'))
        return {'type': 'ir.actions.act_window', 'name': _('Payment'), 'res_model': 'account.payment', 'view_mode': 'form', 'res_id': self.payment_id.id, 'target': 'current'}

    def action_view_bill(self):
        self.ensure_one()
        if not self.move_id:
            raise UserError(_('No bill associated with this expense.'))
        return {'type': 'ir.actions.act_window', 'name': _('Vendor Bill'), 'res_model': 'account.move', 'view_mode': 'form', 'res_id': self.move_id.id, 'target': 'current'}

    def action_view_transaction(self):
        self.ensure_one()
        if not self.transaction_id:
            raise UserError(_('No petty cash transaction associated with this expense.'))
        return {'type': 'ir.actions.act_window', 'name': _('Petty Cash Transaction'), 'res_model': 'petty.cash.transaction', 'view_mode': 'form', 'res_id': self.transaction_id.id, 'target': 'current'}

    def write(self, vals):
        if 'line_ids' in vals:
            for rec in self:
                if rec.state not in ('draft', 'cancelled', 'rejected'):
                    raise UserError(_('Expense lines cannot be modified after submission.'))
        return super().write(vals)

    def unlink(self):
        if any(rec.state != 'draft' for rec in self):
            raise UserError(_('Only expenses in draft state can be deleted.'))
        return super().unlink()


class PettyCashExpenseLine(models.Model):
    _name = 'petty.cash.expense.line'
    _description = 'Petty Cash Expense Line'

    name = fields.Char(string='Label', required=True)
    product_id = fields.Many2one('product.product', string='Product')
    account_id = fields.Many2one('account.account', string='Expense Account')
    expense_id = fields.Many2one('petty.cash.expense', string='Expense', required=True, ondelete='cascade')
    amount = fields.Monetary(string='Amount', currency_field='currency_id', required=True)
    company_id = fields.Many2one(related='expense_id.company_id', store=True)
    currency_id = fields.Many2one(related='expense_id.currency_id', store=True)
    categ_id = fields.Many2one('product.category', string='Category', related='product_id.categ_id', store=True)
    petty_cash_partner_id = fields.Many2one(
        related='expense_id.petty_cash_partner_id', string='Cash Holder', store=True)
    allocation_id = fields.Many2one(related='expense_id.allocation_id', string='Allocation', store=True)
    expense_state = fields.Selection(related='expense_id.state', string='Expense Status', store=True)
    expense_date = fields.Datetime(related='expense_id.create_date', string='Date', store=True)

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            self.name = self.product_id.display_name
            if not self.account_id:
                self.account_id = self.product_id.property_account_expense_id or self.product_id.categ_id.property_account_expense_categ_id
