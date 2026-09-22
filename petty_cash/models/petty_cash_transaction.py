from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare

# The movements that take cash out of a float.
DEDUCTIONS = ('expense', 'return', 'write_off')


class PettyCashTransfer(models.Model):
    _name = 'petty.cash.transaction'
    _description = 'Petty Cash Transaction'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'

    name = fields.Char(copy=False, readonly=True, tracking=True, default='New')
    allocation_id = fields.Many2one(
        'petty.cash.allocation', string='Petty Cash Allocation',
        required=True, tracking=True, check_company=True)
    petty_cash_partner_id = fields.Many2one('res.partner', string='Petty Cash Holder', related='allocation_id.partner_id', store=True, readonly=True)
    move_id = fields.Many2one('account.move', string='Journal Entry', readonly=True, copy=False)
    move_state = fields.Selection(related='move_id.state', string='Entry Status', store=True)
    payment_id = fields.Many2one('account.payment', string='Payment', readonly=True, copy=False)
    date = fields.Date(default=fields.Date.context_today, required=True, tracking=True)
    partner_id = fields.Many2one('res.partner', string='Partner', tracking=True, check_company=True)
    counter_account_id = fields.Many2one('account.account', string='Contra Account', tracking=True)
    petty_cash_account_id = fields.Many2one('account.account', string='Petty Cash Account',related='allocation_id.account_id', store=True, readonly=True)
    amount = fields.Monetary(string='Amount', required=True, currency_field='currency_id', tracking=True)
    amount_signed = fields.Monetary(string='Amount Signed', compute='_compute_amount_signed', store=True, currency_field='currency_id', tracking=True)
    type = fields.Selection([
        ('allocation', 'Allocation'), ('expense', 'Expense'),
        ('return', 'Return'), ('write_off', 'Write-off'),
    ], default='allocation', tracking=True)
    is_closing = fields.Boolean(
        string='Closes Allocation', default=False, copy=False,
        help='A return that closes the allocation once posted (full settlement). '
             'Partial returns leave the allocation active.')
    state = fields.Selection([
        ('draft', 'Draft'), ('submitted', 'Submitted'), ('approved', 'Approved'), 
        ('posted', 'Posted'), ('cancelled', 'Cancelled'), ('rejected', 'Rejected'),
    ], string='Status',default='draft', tracking=True)
    attachment_ids = fields.Many2many('ir.attachment', string='Attachments', copy=False)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    _company_field = 'company_id'
    currency_id = fields.Many2one(related='company_id.currency_id', store=True)
    note = fields.Text()
    active = fields.Boolean(default=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals['name'] == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('petty.cash.transaction.sequence') or 'New'
        return super().create(vals_list)

    def _counts_as_posted(self):
        """Whether this transaction affects the allocation balance.

        Expenses and write-offs count once posted (no payment move needed).
        Allocation/return transactions count once posted and their journal entry,
        if any, is posted — in Odoo 18 a cash payment may stay 'in_process'
        without an immediate move (move_state is then False), which still counts.
        """
        self.ensure_one()
        if self.state != 'posted':
            return False
        if self.type in ('expense', 'write_off'):
            return True
        return self.move_state in ('posted', False)

    @api.onchange('allocation_id', 'type')
    def _onchange_default_counter_account(self):
        """Propose the float's funding account as the contra side.

        Applies to any cash movement created by hand — a return raised straight
        from the Transactions list would otherwise have no contra account and be
        booked against the holder's payable.
        """
        for rec in self:
            if rec.type in ('allocation', 'return') and rec.allocation_id and not rec.counter_account_id:
                rec.counter_account_id = rec.allocation_id._funding_account()

    @api.depends('type', 'amount')
    def _compute_amount_signed(self):
        for rec in self:
            rec.amount_signed = rec.amount if rec.type == 'allocation' else -rec.amount

    @api.model
    def _deduction_types(self):
        """The movement types that take cash OUT of a float.

        A method rather than the bare module constant so a module adding a
        type (a field executive handing collected cash to the office) can say
        it is a deduction, and have every balance guard below honour it.
        """
        return DEDUCTIONS

    def _counts_as_balance_deduction(self):
        """Returns True if this transaction reduces the available balance."""
        self.ensure_one()
        return self.state == 'posted' and self.type in self._deduction_types()

    @api.constrains('amount')
    def _check_positive_amount(self):
        for rec in self:
            if rec.amount <= 0:
                raise ValidationError(_('Amount must be greater than zero.'))

    def _prepare_allocation_payment_vals(self):
        self.ensure_one()
        # An allocation funds the petty cash box, so cash flows *into* the petty
        # cash journal: Dr petty cash account / Cr contra (main cash, bank…).
        # A return empties it again and reverses that. This has to agree with
        # ``account.move._create_petty_cash_transaction``, which reads a debit on
        # the petty cash account back as an 'allocation'.
        # partner_type stays 'supplier' in both directions: the cash holder is a
        # payee, not a customer, and filing allocations as customer payments
        # would drop them into the AR side of the payments screens. It no longer
        # affects the entry itself — the contra account is mandatory before
        # posting, so destination_account_id is always set explicitly below.
        is_return = self.type == 'return'
        vals = {
            'payment_type': 'outbound' if is_return else 'inbound',
            'partner_type': 'supplier',
            'partner_id': (self.partner_id or self.allocation_id.partner_id).id,
            'amount': self.amount,
            'currency_id': self.currency_id.id,
            'date': self.date,
            'petty_cash_partner_id': self.petty_cash_partner_id.id,
            'journal_id': self.allocation_id.journal_id.id,
            'memo': f"{self.allocation_id.name} - {dict(self._fields['type'].selection).get(self.type)}",
        }
        # Only pass destination_account_id when explicitly set; otherwise let
        # Odoo compute it from the partner's payable/receivable accounts.
        if self.counter_account_id:
            vals['destination_account_id'] = self.counter_account_id.id
        return vals

    def _check_amount_limit(self):
        """The limit caps the cash a holder may have *in hand*, not the lifetime
        total handed over.

        Comparing against ``amount_allocated`` (every allocation ever posted)
        made replenishment impossible: restoring a 6 000 float that had been
        spent down to 3 900 was rejected as "exceeds 6 000", even though the
        holder would end up holding exactly the sanctioned 6 000. What matters
        is the outstanding balance once this transaction is posted.
        """
        for rec in self.filtered(lambda r: r.type == 'allocation'):
            allocation = rec.allocation_id
            if allocation.amount_limit and allocation.amount_balance + rec.amount > allocation.amount_limit:
                raise ValidationError(_(
                    'This allocation would leave %(holder)s holding %(new)s, which exceeds '
                    'the sanctioned limit of %(limit)s (current balance %(balance)s).'
                ) % {
                    'holder': allocation.partner_id.display_name,
                    'new': allocation._money(allocation.amount_balance + rec.amount),
                    'limit': allocation._money(allocation.amount_limit),
                    'balance': allocation._money(allocation.amount_balance),
                })

    def _check_balance_available(self):
        """Approving reserves the cash: count what is approved and not yet posted.

        A submitted return reserves nothing, so two returns of the whole balance
        were both approvable - and both postable, leaving the float negative and
        closed twice. Earlier records of the same batch reserve too.
        """
        reserved = {}
        deductions = self._deduction_types()
        for rec in self.filtered(lambda r: r.type in deductions):
            allocation = rec.allocation_id
            if allocation.id not in reserved:
                reserved[allocation.id] = sum(allocation.transaction_ids.filtered(
                    lambda t: t.state == 'approved' and t.type in deductions
                    and t not in self).mapped('amount'))
            wanted = reserved[allocation.id] + rec.amount
            if float_compare(wanted, allocation.amount_balance,
                             precision_rounding=allocation.currency_id.rounding or 0.01) > 0:
                raise ValidationError(_('Amount cannot exceed the available petty cash balance.'))
            reserved[allocation.id] = wanted

    def _check_postable(self):
        """Re-check at post what approve checked: the float may have moved since.

        Approval can be old - another return or expense may have posted in between -
        so the balance is read again here, with earlier records of the same batch
        already taken off it, and a float that was closed takes nothing more.
        """
        spent = {}
        for rec in self.filtered(lambda r: r.type in self._deduction_types()):
            allocation = rec.allocation_id
            if allocation.state != 'allocated':
                raise UserError(_(
                    '%(ref)s cannot be posted: allocation %(alloc)s is no longer active.'
                ) % {'ref': rec.name, 'alloc': allocation.name})
            available = allocation.amount_balance - spent.get(allocation.id, 0.0)
            if float_compare(rec.amount, available,
                             precision_rounding=allocation.currency_id.rounding or 0.01) > 0:
                raise UserError(_(
                    '%(ref)s cannot be posted: %(amount)s exceeds the %(available)s '
                    'still available on %(alloc)s.'
                ) % {'ref': rec.name, 'amount': allocation._money(rec.amount),
                     'available': allocation._money(max(available, 0.0)),
                     'alloc': allocation.name})
            spent[allocation.id] = spent.get(allocation.id, 0.0) + rec.amount

    def action_submit(self):
        for rec in self:
            if rec.state != 'draft':
                continue
            rec.state = 'submitted'

    def action_approve(self):
        # The same person the Approve button is shown to; sudo is the system
        # (lab_fieldwork's "Cash into My Float"), see action_post.
        if not self.env.su and not self.env.user.has_group(
                'petty_cash.group_petty_cash_manager'):
            raise UserError(_('Only Petty Cash Managers can approve transactions.'))
        self._check_amount_limit()
        self._check_balance_available()
        self.write({'state': 'approved'})

    def action_post(self):
        # `self.env.su` first, because this guard is about which PERSON may post,
        # and under sudo there is no person: `env.user` is OdooBot, which holds no
        # petty cash group, so every system-initiated post failed with a message
        # about officers that nobody could act on.
        #
        # That is not theoretical. lab_fieldwork's "Cash into My Float" takes a
        # visit's cash into the executive's float and has to do it as the system —
        # an executive is deliberately not an officer — so the button raised this
        # error for everyone, every time, since the day it shipped. On the live
        # database: 4 visits collected cash, 0 ever reached a float.
        #
        # su is only ever set by server code calling .sudo(); no request can ask
        # for it, so honouring it here is what sudo means everywhere else in Odoo.
        # (2026-09-12)
        if not self.env.su and not self.env.user.has_group(
                'petty_cash.group_petty_cash_officer'):
            raise UserError(_('Only Petty Cash Officers or Managers can post transactions.'))
        invalid = self.filtered(lambda rec: rec.state != 'approved')
        if invalid:
            raise UserError(_('Only approved transactions can be posted.'))
        self._check_postable()

        # Allocations and returns generate a payment, and the contra account is
        # the other half of that entry. Without it Odoo silently falls back to
        # the holder's payable, so the cash never appears to move between the
        # petty cash box and the account funding it.
        missing = self.filtered(
            lambda rec: rec.type in ('allocation', 'return') and not rec.counter_account_id)
        if missing:
            raise UserError(_(
                'Set the Contra Account before posting %(refs)s.\n\n'
                'It is the cash or bank account the money moves between: an allocation '
                'debits the petty cash account and credits the contra account, and a '
                'return does the reverse.'
            ) % {'refs': ', '.join(missing.mapped('name'))})

        payable = self.filtered(lambda r: r.type in ('allocation', 'return'))
        payments = self.env['account.payment'].sudo().with_context(skip_petty_cash_auto_transaction=True).create([
            rec._prepare_allocation_payment_vals() for rec in payable
        ]) if payable else self.env['account.payment']
        if payments:
            payments.with_context(skip_petty_cash_auto_transaction=True).action_post()

        payment_by_rec = dict(zip(payable.ids, payments))
        for rec in self:
            vals = {'state': 'posted'}
            payment = payment_by_rec.get(rec.id)
            if payment:
                vals.update({'payment_id': payment.id, 'move_id': payment.move_id.id, 'name': payment.name})
            if not rec.partner_id:
                vals['partner_id'] = rec.allocation_id.partner_id.id
            rec.write(vals)

            allocation = rec.allocation_id
            if rec.type == 'allocation':
                allocation.state = 'allocated'
            elif rec.type in ('return', 'write_off'):
                # The close wizard posts a closing return and THEN its write-off:
                # closing on the return would leave the write-off nothing active
                # to post against.
                if self.env.context.get('petty_cash_defer_close'):
                    continue
                auto_close = allocation.company_id.petty_cash_auto_close and allocation.amount_balance <= 0
                if (rec.is_closing or auto_close) and allocation.state == 'allocated':
                    allocation._close(rec.date)
        return True

    def action_reject(self):
        self.write({'state': 'rejected'})

    def action_bulk_approve(self):
        """Bulk approve allocation/return transactions from list view."""
        if not self.env.user.has_group('petty_cash.group_petty_cash_manager'):
            raise UserError(_('Only Petty Cash Managers can approve transactions.'))
        approved = self.env['petty.cash.transaction']
        skipped = []
        for rec in self.filtered(lambda r: r.state == 'submitted'):
            try:
                rec.action_approve()
                approved |= rec
            except (UserError, ValidationError) as e:
                skipped.append('%s: %s' % (rec.name, str(e)))
        msg = _('%d transaction(s) approved.') % len(approved)
        if skipped:
            msg += '\n' + _('Skipped:\n%s') % '\n'.join(skipped)
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _('Bulk Approval'), 'message': msg,
                           'type': 'success' if approved else 'warning', 'sticky': bool(skipped)}}

    def action_bulk_post(self):
        """Bulk post approved transactions."""
        if not self.env.user.has_group('petty_cash.group_petty_cash_officer'):
            raise UserError(_('Only Petty Cash Officers or Managers can post transactions.'))
        to_post = self.filtered(lambda r: r.state == 'approved')
        if not to_post:
            raise UserError(_('No approved transactions selected.'))
        to_post.action_post()
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _('Bulk Post'),
                           'message': _('%d transaction(s) posted.') % len(to_post),
                           'type': 'success', 'sticky': False}}

    def action_cancel(self):
        if not self.env.user.has_group('petty_cash.group_petty_cash_officer'):
            raise UserError(_('Only Petty Cash Officers or Managers can cancel transactions.'))
        for rec in self:
            if rec.state == 'posted':
                raise UserError(_(
                    'Transaction %s is posted and cannot be cancelled. '
                    'A reversal journal entry must be created to undo this accounting entry.'
                ) % rec.name)
        self.write({'state': 'cancelled'})

    def action_reset_to_draft(self):
        if not self.env.user.has_group('petty_cash.group_petty_cash_officer'):
            raise UserError(_('Only Petty Cash Officers or Managers can reset transactions.'))
        self.write({'state': 'draft'})

    def action_view_allocation(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _('Petty Cash Allocation'), 'res_model': 'petty.cash.allocation', 'view_mode': 'form', 'res_id': self.allocation_id.id, 'target': 'current'}

    def action_view_payment(self):
        self.ensure_one()
        if not self.payment_id:
            raise UserError(_('No payment found for this transaction.'))
        return {'type': 'ir.actions.act_window', 'name': _('Payment'), 'res_model': 'account.payment', 'view_mode': 'form', 'res_id': self.payment_id.id, 'target': 'current'}

    def action_view_move(self):
        self.ensure_one()
        if not self.move_id:
            raise UserError(_('No journal entry found for this transaction.'))
        return {'type': 'ir.actions.act_window', 'name': _('Journal Entry'), 'res_model': 'account.move', 'view_mode': 'form', 'res_id': self.move_id.id, 'target': 'current'}
