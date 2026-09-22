from markupsafe import Markup
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import formatLang


class PettyCashCloseWizard(models.TransientModel):
    """Smart Close / Partial-Return Wizard.

    Handles three scenarios:
    1. Full close with full return   – balance > 0, is_closing=True
    2. Partial return only           – balance > 0, is_closing=False
    3. Close with write-off          – tiny remainder written off instead of cash collected
    """
    _name = 'petty.cash.close.wizard'
    _description = 'Petty Cash Close / Partial Return'

    allocation_id = fields.Many2one('petty.cash.allocation', required=True, ondelete='cascade')
    is_closing = fields.Boolean(string='Close Allocation After This Return', default=True)
    date = fields.Date(string='Transaction Date', required=True, default=fields.Date.context_today)
    amount = fields.Monetary(string='Amount to Return', currency_field='currency_id', required=True)
    use_write_off = fields.Boolean(
        string='Write Off Remainder',
        help='Write off the small remaining balance instead of collecting cash. '
             'Only available when closing and a write-off account is configured.')
    write_off_account_id = fields.Many2one(
        'account.account', string='Write-off Account',
        domain="[('account_type', 'like', 'expense')]")
    counter_account_id = fields.Many2one(
        'account.account', string='Return Cash To',
        domain="[('account_type', '=', 'asset_cash')]",
        help='Cash account the returned money goes back into — normally the account the '
             'float was funded from, so the entry mirrors the original allocation. '
             'Left empty, the return is booked against the cash holder’s payable instead.')
    note = fields.Text(string='Notes / Remarks')

    # ── read-only context fields ──────────────────────────────────────────
    partner_id = fields.Many2one(related='allocation_id.partner_id', string='Cash Holder')
    company_id = fields.Many2one(related='allocation_id.company_id')
    currency_id = fields.Many2one(related='allocation_id.currency_id')
    current_balance = fields.Monetary(
        related='allocation_id.amount_balance', string='Current Balance', currency_field='currency_id')
    amount_allocated = fields.Monetary(
        related='allocation_id.amount_allocated', string='Total Allocated', currency_field='currency_id')
    amount_utilized = fields.Monetary(
        related='allocation_id.amount_utilized', string='Total Utilized', currency_field='currency_id')
    amount_returned = fields.Monetary(
        related='allocation_id.amount_returned', string='Already Returned', currency_field='currency_id')
    partial_return_count = fields.Integer(related='allocation_id.partial_return_count')
    remaining_after = fields.Monetary(
        string='Balance After Return', compute='_compute_remaining_after',
        currency_field='currency_id')
    write_off_amount = fields.Monetary(
        string='Write-off Amount', compute='_compute_remaining_after',
        currency_field='currency_id')
    can_write_off = fields.Boolean(compute='_compute_remaining_after')

    @api.depends('amount', 'current_balance', 'is_closing', 'use_write_off')
    def _compute_remaining_after(self):
        company = self.env.company
        for wiz in self:
            remaining = wiz.current_balance - (wiz.amount or 0.0)
            wiz.remaining_after = max(remaining, 0.0)
            wiz.write_off_amount = remaining if remaining > 0 and wiz.use_write_off else 0.0
            threshold = company.petty_cash_write_off_threshold or 0.0
            wiz.can_write_off = (
                wiz.is_closing and remaining > 0
                and (threshold == 0 or remaining <= threshold)
                and bool(wiz.write_off_account_id or company.petty_cash_write_off_account_id)
            )

    @api.onchange('allocation_id', 'is_closing')
    def _onchange_allocation(self):
        if self.allocation_id and self.is_closing:
            self.amount = self.current_balance

    @api.onchange('use_write_off')
    def _onchange_use_write_off(self):
        if self.use_write_off:
            company = self.env.company
            if not self.write_off_account_id and company.petty_cash_write_off_account_id:
                self.write_off_account_id = company.petty_cash_write_off_account_id

    @api.constrains('amount', 'current_balance')
    def _check_amount(self):
        for wiz in self:
            if wiz.amount <= 0:
                raise ValidationError(_('Return amount must be greater than zero.'))
            if wiz.amount > wiz.current_balance:
                raise ValidationError(_(
                    'Return amount (%.2f) cannot exceed the available balance (%.2f).'
                ) % (wiz.amount, wiz.current_balance))

    @api.model
    def default_get(self, fields_list):
        """Default the contra account to wherever the float was funded from.

        Both entry points (Close Allocation and Partial Return) open this wizard
        with ``default_allocation_id`` in the context, so the account is resolved
        before the form is rendered and the user can still override it.
        """
        vals = super().default_get(fields_list)
        alloc_id = vals.get('allocation_id') or self.env.context.get('default_allocation_id')
        if alloc_id and 'counter_account_id' in fields_list and not vals.get('counter_account_id'):
            allocation = self.env['petty.cash.allocation'].browse(alloc_id)
            vals['counter_account_id'] = allocation._funding_account().id or False
        return vals

    def _create_return_transaction(self, is_closing):
        """Create and return a draft return transaction."""
        self.ensure_one()
        alloc = self.allocation_id
        vals = {
            'allocation_id': alloc.id,
            'partner_id': alloc.partner_id.id,
            'type': 'return',
            'amount': self.amount,
            'date': self.date,
            'petty_cash_account_id': alloc.account_id.id,
            'counter_account_id': self.counter_account_id.id,
            'company_id': alloc.company_id.id,
            'is_closing': is_closing,
            'note': self.note or '',
        }
        return self.env['petty.cash.transaction'].create(vals)

    def _create_write_off_transaction(self, amount=None):
        """Create a write-off transaction for the remainder.

        `amount` is the remainder as it stood BEFORE the return posted:
        write_off_amount is computed from the live balance, so once the return is
        in it reads zero and the write-off was silently skipped.
        """
        self.ensure_one()
        alloc = self.allocation_id
        account = self.write_off_account_id or alloc.company_id.petty_cash_write_off_account_id
        if not account:
            raise UserError(_('Please set a write-off account.'))
        tx = self.env['petty.cash.transaction'].create({
            'allocation_id': alloc.id,
            'partner_id': alloc.partner_id.id,
            'type': 'write_off',
            'amount': self.write_off_amount if amount is None else amount,
            'date': self.date,
            'counter_account_id': account.id,
            'petty_cash_account_id': alloc.account_id.id,
            'company_id': alloc.company_id.id,
            'is_closing': True,
            'note': _('Write-off on closure: %s') % (self.note or ''),
        })
        return tx

    def action_confirm(self):
        """Process the return and optionally close the allocation."""
        self.ensure_one()
        alloc = self.allocation_id

        if alloc.state != 'allocated':
            raise UserError(_('This allocation is no longer active.'))

        is_closing = self.is_closing
        # Captured before anything posts: both are computed from the live balance
        # and change the moment the return is in.
        balance_before = self.current_balance
        write_off = self.write_off_amount if (self.use_write_off and is_closing) else 0.0
        is_manager = self.env.user.has_group('petty_cash.group_petty_cash_manager')
        if write_off > 0:
            if not self.can_write_off:
                raise UserError(_(
                    'The remainder cannot be written off: it is above the write-off '
                    'threshold or no write-off account is set.'))
            # A write-off is money nobody hands back, so it is a manager's call. The
            # submitted return has nowhere to carry it, and ignoring the tick box
            # silently left the float open with the remainder still on it.
            if not is_manager:
                raise UserError(_(
                    'Only a Petty Cash Manager can write off a remainder. Return the '
                    'full balance, or ask a manager to close this allocation.'))
        tx = self._create_return_transaction(is_closing)

        # Auto-submit + approve + post (manager path) or leave in submitted for approval
        if is_manager:
            tx.action_approve()
            tx.with_context(petty_cash_defer_close=write_off > 0).action_post()
            if write_off > 0:
                wo_tx = self._create_write_off_transaction(write_off)
                wo_tx.action_approve()
                wo_tx.action_post()
        else:
            tx.action_submit()
            # Notify managers about the pending return
            manager_group = self.env.ref('petty_cash.group_petty_cash_manager', raise_if_not_found=False)
            if manager_group:
                manager_partners = manager_group.users.filtered(
                    lambda u: u.active and u != self.env.user
                ).mapped('partner_id')
                if manager_partners:
                    alloc.sudo().message_post(
                        body=Markup(_(
                            '<b>Return submitted for approval</b> — '
                            '{amount} returned by <b>{user}</b>. Awaiting manager approval.'
                        )).format(
                            amount=formatLang(self.env, self.amount, currency_obj=self.currency_id),
                            user=self.env.user.name,
                        ),
                        partner_ids=manager_partners.ids,
                    )

        action_label = (
            _('Allocation Closed') if (is_closing and is_manager) else
            _('Partial Return Submitted') if not is_manager else
            _('Partial Return Posted')
        )
        remainder_note = (
            Markup(' ') + _('Remainder %s written off.') % formatLang(
                self.env, write_off, currency_obj=self.currency_id)
            if write_off else Markup('')
        )
        alloc.message_post(body=Markup(
            '<b>{action}</b> — {amount} of {total} returned by {user}.{remainder}'
        ).format(
            action=action_label,
            amount=formatLang(self.env, self.amount, currency_obj=self.currency_id),
            total=formatLang(self.env, balance_before, currency_obj=self.currency_id),
            user=self.env.user.name,
            remainder=remainder_note,
        ))

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'petty.cash.allocation',
            'view_mode': 'form',
            'res_id': alloc.id,
            'target': 'current',
        }
