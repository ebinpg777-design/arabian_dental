# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, float_is_zero


class LabCheque(models.Model):
    """Cheque as a distinct collection mode with invoice-wise allocation (gap 9).

    Odoo models a cheque as a payment method on an ``account.payment``, which
    means the money is assumed to have arrived the moment it is recorded. A lab
    collecting post-dated cheques in the field needs the opposite: a cheque held,
    banked, and only then in the ledger. So a cheque lives here through
    Received -> Deposited -> Cleared, and only clearing creates the payment.
    A bounce puts it back in the register with the reason on the record instead
    of leaving a reversed journal entry to explain.
    """
    _name = 'lab.cheque'
    _description = 'Cheque Register'
    _inherit = ['lab.lock.mixin', 'mail.thread', 'mail.activity.mixin']
    # Locked once closed - enforced in write(), not just the view.
    _lock_states = ('cleared', 'bounced', 'cancel')
    _lock_exempt_fields = ('note', 'bounce_reason')
    _lock_bypass_groups = ('account.group_account_manager',)
    _order = 'cheque_date desc, id desc'
    _rec_name = 'display_name'

    _number_partner_uniq = models.Constraint(
        'unique(cheque_number, partner_id, company_id)',
        'This cheque number is already recorded for this clinic.')

    name = fields.Char(default=lambda self: _('New'), copy=False, readonly=True)
    cheque_number = fields.Char(required=True, tracking=True, index=True)
    partner_id = fields.Many2one(
        'res.partner', string='Received From', required=True, index=True, tracking=True,
        domain="[]")
    bank_id = fields.Many2one('res.bank', string='Drawee Bank', tracking=True)
    branch = fields.Char('Drawee Branch')

    cheque_date = fields.Date(
        required=True, default=fields.Date.context_today, tracking=True,
        help="Date written on the cheque. A future date makes it post-dated.")
    received_date = fields.Date(default=fields.Date.context_today, required=True)
    deposit_date = fields.Date(readonly=True, copy=False, tracking=True)
    clear_date = fields.Date(readonly=True, copy=False, tracking=True)
    is_post_dated = fields.Boolean(compute='_compute_is_post_dated', store=True)

    amount = fields.Monetary(required=True, currency_field='currency_id', tracking=True)
    allocated_amount = fields.Monetary(
        compute='_compute_allocated', store=True, currency_field='currency_id')
    unallocated_amount = fields.Monetary(
        compute='_compute_allocated', store=True, currency_field='currency_id',
        help="Part of the cheque not yet pointed at a specific invoice — it will sit as "
             "an open credit on the clinic's account.")

    journal_id = fields.Many2one(
        'account.journal', string='Deposit Bank Account',
        domain="[('type', 'in', ('bank', 'cash'))]", tracking=True,
        help="Journal the cheque is banked into when it clears.")
    collected_by_id = fields.Many2one(
        'res.users', string='Collected By', default=lambda self: self.env.user, tracking=True)

    allocation_ids = fields.One2many(
        'lab.cheque.allocation', 'cheque_id', string='Invoice-wise Allocation')
    payment_id = fields.Many2one(
        'account.payment', string='Payment', copy=False, readonly=True)

    state = fields.Selection(
        [('received', 'Received'), ('deposited', 'Deposited'), ('cleared', 'Cleared'),
         ('bounced', 'Bounced'), ('cancel', 'Cancelled')],
        default='received', required=True, tracking=True, copy=False)
    bounce_reason = fields.Char(copy=False, tracking=True)
    note = fields.Text()

    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company, required=True)
    currency_id = fields.Many2one(related='company_id.currency_id', readonly=True)

    # ------------------------------------------------------------------ computes
    @api.depends('cheque_number', 'partner_id', 'amount')
    def _compute_display_name(self):
        for cheque in self:
            cheque.display_name = "%s — %s" % (
                cheque.cheque_number or _('New'), cheque.partner_id.name or '')

    @api.depends('cheque_date')
    def _compute_is_post_dated(self):
        today = fields.Date.context_today(self)
        for cheque in self:
            cheque.is_post_dated = bool(cheque.cheque_date and cheque.cheque_date > today)

    @api.depends('allocation_ids.amount', 'amount')
    def _compute_allocated(self):
        for cheque in self:
            cheque.allocated_amount = sum(cheque.allocation_ids.mapped('amount'))
            cheque.unallocated_amount = cheque.amount - cheque.allocated_amount

    @api.constrains('allocation_ids', 'amount')
    def _check_allocation(self):
        for cheque in self:
            rounding = cheque.currency_id.rounding or 0.01
            if float_compare(cheque.allocated_amount, cheque.amount,
                             precision_rounding=rounding) > 0:
                raise ValidationError(_(
                    "Cheque %(num)s allocates %(alloc)s across invoices but is only worth "
                    "%(amount)s.",
                    num=cheque.cheque_number,
                    alloc=cheque.currency_id.format(cheque.allocated_amount),
                    amount=cheque.currency_id.format(cheque.amount)))

    # ------------------------------------------------------------------ actions
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals['name'] == _('New'):
                seq = self.env['ir.sequence']
                if vals.get('company_id'):
                    seq = seq.with_company(vals['company_id'])
                vals['name'] = seq.next_by_code('lab.cheque') or _('New')
        return super().create(vals_list)

    def action_load_open_invoices(self):
        """Fill the allocation grid with the clinic's open invoices, oldest first,
        consuming the cheque until it runs out — how the ledger is actually settled."""
        self.ensure_one()
        if self.state != 'received':
            raise UserError(_("Invoices can only be allocated while the cheque is in hand."))
        self.allocation_ids.unlink()
        moves = self.env['account.move'].search([
            ('partner_id', 'child_of', self.partner_id.commercial_partner_id.id),
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'posted'),
            ('payment_state', 'in', ('not_paid', 'partial')),
            ('company_id', '=', self.company_id.id),
        ], order='invoice_date, id')
        remaining = self.amount
        lines = []
        rounding = self.currency_id.rounding or 0.01
        for move in moves:
            if float_is_zero(remaining, precision_rounding=rounding):
                break
            due = move.amount_residual
            if float_compare(due, 0.0, precision_rounding=rounding) <= 0:
                continue
            take = min(due, remaining)
            lines.append((0, 0, {'move_id': move.id, 'amount': take}))
            remaining -= take
        if not lines:
            raise UserError(_("%s has no open invoices to allocate this cheque against.",
                              self.partner_id.display_name))
        self.allocation_ids = lines
        return True

    def action_deposit(self):
        for cheque in self:
            if cheque.state != 'received':
                raise UserError(_("Only a cheque in hand can be deposited."))
            if not cheque.journal_id:
                raise UserError(_("Choose the bank account %s is being deposited into.",
                                  cheque.cheque_number))
            cheque.write({'state': 'deposited', 'deposit_date': fields.Date.context_today(cheque)})
        return True

    def action_clear(self):
        """Bank confirms the funds: create, post and reconcile the payment."""
        for cheque in self:
            if cheque.state != 'deposited':
                raise UserError(_("Deposit %s before clearing it.", cheque.cheque_number))
            payment = cheque._create_payment()
            cheque.write({
                'state': 'cleared',
                'clear_date': fields.Date.context_today(cheque),
                'payment_id': payment.id,
            })
            cheque._reconcile_allocations(payment)
        return True

    def _get_payment_method_line(self):
        self.ensure_one()
        lines = self.journal_id.inbound_payment_method_line_ids
        # Prefer a method actually named for cheques when the journal offers one.
        cheque_line = lines.filtered(
            lambda l: 'cheque' in (l.name or '').lower() or 'check' in (l.name or '').lower())
        return (cheque_line or lines)[:1]

    def _create_payment(self):
        self.ensure_one()
        method_line = self._get_payment_method_line()
        if not method_line:
            raise UserError(_(
                "The journal %s has no inbound payment method configured.",
                self.journal_id.display_name))
        return self.env['account.payment'].with_context(lab_cheque_clearing=True).create({
            'cheque_id': self.id,
            'payment_type': 'inbound',
            'partner_type': 'customer',
            'partner_id': self.partner_id.id,
            'amount': self.amount,
            'date': self.clear_date or fields.Date.context_today(self),
            'journal_id': self.journal_id.id,
            'payment_method_line_id': method_line.id,
            'memo': _('Cheque %(num)s / %(bank)s',
                      num=self.cheque_number, bank=self.bank_id.name or '-'),
            'company_id': self.company_id.id,
        })

    def _reconcile_allocations(self, payment):
        """Match the payment against exactly the invoices the cheque was allocated to.

        Anything unallocated is deliberately left unreconciled — it becomes an open
        credit on the clinic's account rather than being silently applied to the
        oldest invoice behind the accountant's back.
        """
        self.ensure_one()
        if payment.state == 'draft':
            payment.action_post()
        if not self.allocation_ids:
            return
        _liquidity, counterpart, _writeoff = payment._seek_for_lines()
        counterpart = counterpart.filtered(lambda l: not l.reconciled)
        if not counterpart:
            return
        for allocation in self.allocation_ids:
            invoice_lines = allocation.move_id.line_ids.filtered(
                lambda l: l.account_id.account_type == 'asset_receivable' and not l.reconciled)
            if invoice_lines:
                (counterpart | invoice_lines).reconcile()
                counterpart = counterpart.filtered(lambda l: not l.reconciled)
                if not counterpart:
                    break

    def action_bounce(self):
        for cheque in self:
            if cheque.state not in ('deposited', 'cleared'):
                raise UserError(_("Only a banked cheque can bounce."))
            if not cheque.bounce_reason:
                raise UserError(_("Record why cheque %s bounced.", cheque.cheque_number))
            if cheque.payment_id and cheque.payment_id.state != 'canceled':
                # Unwinding the payment restores the invoices to unpaid, which is
                # what a bounce means to the ledger. Reconciliation has to come off
                # first — button_draft refuses nothing but leaves the match behind.
                cheque.payment_id.move_id.line_ids.remove_move_reconcile()
                cheque.payment_id.action_draft()
                cheque.payment_id.action_cancel()
            cheque.write({'state': 'bounced'})
            cheque.message_post(body=_("Cheque bounced: %s", cheque.bounce_reason))
        return True

    def action_cancel(self):
        for cheque in self:
            if cheque.payment_id and cheque.payment_id.state not in ('draft', 'canceled'):
                raise UserError(_(
                    "Cancel or reverse the payment on %s before cancelling the cheque.",
                    cheque.cheque_number))
            cheque.state = 'cancel'
        return True

    def action_draft(self):
        self.filtered(lambda c: c.state in ('bounced', 'cancel')).write({
            'state': 'received', 'deposit_date': False, 'clear_date': False})
        return True

    def action_view_payment(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.payment',
            'res_id': self.payment_id.id,
            'view_mode': 'form',
        }

    @api.model
    def _cron_notify_due_cheques(self):
        """Post-dated cheques falling due today are the thing most often missed."""
        today = fields.Date.context_today(self)
        due = self.search([('state', '=', 'received'), ('cheque_date', '<=', today)])
        for cheque in due:
            cheque.activity_schedule(
                'mail.mail_activity_data_todo',
                summary=_('Cheque %s is due for deposit', cheque.cheque_number),
                user_id=cheque.collected_by_id.id or self.env.uid)
        return True


class LabChequeAllocation(models.Model):
    _name = 'lab.cheque.allocation'
    _description = 'Cheque Invoice-wise Allocation'
    _order = 'invoice_date, id'

    cheque_id = fields.Many2one('lab.cheque', required=True, ondelete='cascade', index=True)
    move_id = fields.Many2one(
        'account.move', string='Invoice', required=True,
        domain="[('move_type', '=', 'out_invoice'), ('state', '=', 'posted')]")
    invoice_date = fields.Date(related='move_id.invoice_date', store=True, string='Invoice Date')
    invoice_date_due = fields.Date(related='move_id.invoice_date_due', store=True, string='Due Date')
    move_total = fields.Monetary(
        related='move_id.amount_total', string='Invoice Total', currency_field='currency_id')
    move_residual = fields.Monetary(
        related='move_id.amount_residual', string='Outstanding', currency_field='currency_id')
    amount = fields.Monetary('Allocated', required=True, currency_field='currency_id')
    currency_id = fields.Many2one(related='cheque_id.currency_id', readonly=True)
    company_id = fields.Many2one(related='cheque_id.company_id', store=True, readonly=True)

    @api.onchange('move_id')
    def _onchange_move_id(self):
        if self.move_id and not self.amount:
            self.amount = min(self.move_id.amount_residual,
                              self.cheque_id.unallocated_amount or self.move_id.amount_residual)
