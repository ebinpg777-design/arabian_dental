# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import Command, api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, float_is_zero

# What a receipt already booked was made of: changing one of these afterwards would leave
# the payment saying something else.
BOOKED_FIELDS = ('partner_id', 'amount', 'journal_id', 'received_date', 'company_id')


class LabCheque(models.Model):
    """A cheque from a clinic, from the day it is received to the day the bank confirms it.

    Received -> Deposited -> Cleared, or Bounced. How the books follow it (client,
    2026-11-23: "the customer invoices should be matched with postdated cheques on the
    date of cheque received. bank account will reflect on the date when the cheque
    deposited in the bank"), as Odoo 17 did with its "PDC - <bank>" accounts:

    * Received: a payment dated the received day settles the allocated invoices; its
      money waits in the bank journal's Cheques in Hand (PDC) account.
    * Deposited: an entry dated the deposit day moves it from the PDC account to the
      bank account.
    * Cleared: the bank confirmed it - nothing more to book.
    * Bounced (or a received cheque cancelled): both entries are reversed on that day and
      the invoices are open again.

    Cheques recorded before this (`accounting = 'clearing'`) keep the old way: a payment
    made when they clear - and none at all when they already carry one, which every
    cheque brought over from Odoo 17 does.
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
    # Odoo 20 has no res.bank master (bank details live on res.partner.bank), so the
    # drawee bank is the name written on the cheque.
    bank_name = fields.Char('Drawee Bank', tracking=True)
    branch = fields.Char('Drawee Branch')

    cheque_date = fields.Date(
        required=True, default=fields.Date.context_today, tracking=True,
        help="Date written on the cheque. A future date makes it post-dated.")
    received_date = fields.Date(
        default=fields.Date.context_today, required=True, tracking=True,
        help="The day the cheque came in: its invoices are settled on this day.")
    deposit_date = fields.Date(
        copy=False, tracking=True,
        help="The day it goes to the bank: the bank account shows it from this day. "
             "Left empty, Deposit uses today.")
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
        help="The bank the cheque is deposited into. Its Cheques in Hand (PDC) account "
             "holds the cheque until then.")
    collected_by_id = fields.Many2one(
        'res.users', string='Collected By', default=lambda self: self.env.user, tracking=True)

    allocation_ids = fields.One2many(
        'lab.cheque.allocation', 'cheque_id', string='Invoice-wise Allocation')
    payment_id = fields.Many2one(
        'account.payment', string='Payment', copy=False, readonly=True)
    deposit_move_id = fields.Many2one(
        'account.move', string='Deposit Entry', copy=False, readonly=True)
    accounting = fields.Selection(
        [('holding', 'Settled when received, banked when deposited'),
         ('clearing', 'Booked when cleared (recorded before 2026-11-23)')],
        default='holding', required=True, readonly=True, copy=False)
    receipt_booked = fields.Boolean(
        compute='_compute_receipt_booked',
        help="The invoices are settled: the receipt is in the books.")

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

    @api.depends('accounting', 'payment_id.state')
    def _compute_receipt_booked(self):
        for cheque in self:
            cheque.receipt_booked = cheque._is_booked()

    def _is_booked(self):
        self.ensure_one()
        return bool(self.accounting == 'holding' and self.payment_id
                    and self.payment_id.state not in ('draft', 'canceled', 'rejected'))

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

    # ------------------------------------------------------------------ CRUD
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals['name'] == _('New'):
                seq = self.env['ir.sequence']
                if vals.get('company_id'):
                    seq = seq.with_company(vals['company_id'])
                vals['name'] = seq.next_by_code('lab.cheque') or _('New')
        return super().create(vals_list)

    def _differs(self, fname, value):
        field, current = self._fields[fname], self[fname]
        if field.type == 'many2one':
            return current.id != (value or False)
        if field.type == 'date':
            return current != fields.Date.to_date(value)
        if field.type == 'monetary':
            return float_compare(current, value or 0.0,
                                 precision_rounding=self.currency_id.rounding or 0.01) != 0
        return current != value

    def write(self, vals):
        changed = set(vals) & set(BOOKED_FIELDS)
        if changed:
            for cheque in self:
                if cheque._is_booked() and any(cheque._differs(f, vals[f]) for f in changed):
                    raise UserError(_(
                        "Cheque %s is already in the books. Cancel it and record it again "
                        "to change the clinic, the amount, the bank or the received date.",
                        cheque.cheque_number))
        return super().write(vals)

    # ------------------------------------------------------------------ allocation
    def _open_invoice_amounts(self):
        """[(invoice, open amount)] of the clinic, oldest first - what a cheque may
        settle. lab_collections answers this from its countback."""
        self.ensure_one()
        moves = self.env['account.move'].search([
            ('partner_id', 'child_of', self.partner_id.commercial_partner_id.id),
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'posted'),
            ('payment_state', 'in', ('not_paid', 'partial')),
            ('company_id', '=', self.company_id.id),
        ], order='invoice_date, id')
        rounding = self.currency_id.rounding or 0.01
        return [(move, move.amount_residual) for move in moves
                if float_compare(move.amount_residual, 0.0, precision_rounding=rounding) > 0]

    def action_load_open_invoices(self):
        """Fill the allocation grid with the clinic's open invoices, oldest first,
        consuming the cheque until it runs out — how the ledger is actually settled."""
        self.ensure_one()
        if self.state != 'received':
            raise UserError(_("Invoices can only be allocated while the cheque is in hand."))
        remaining = self.amount
        lines = []
        rounding = self.currency_id.rounding or 0.01
        for move, due in self._open_invoice_amounts():
            if float_is_zero(remaining, precision_rounding=rounding):
                break
            take = min(due, remaining)
            lines.append(Command.create({'move_id': move.id, 'amount': take}))
            remaining -= take
        if not lines:
            raise UserError(_("%s has no open invoices to allocate this cheque against.",
                              self.partner_id.display_name))
        self.allocation_ids.unlink()
        self.allocation_ids = lines
        return True

    # ------------------------------------------------------------------ the books
    def _holding_account(self):
        self.ensure_one()
        if not self.journal_id:
            raise UserError(_("Choose the bank account cheque %s will be deposited into.",
                              self.cheque_number))
        account = self.journal_id.lab_pdc_account_id
        if not account:
            raise UserError(_(
                "Set the Cheques in Hand (PDC) account of %s first: Accounting > "
                "Configuration > Journals > %s.",
                self.journal_id.display_name, self.journal_id.name))
        return account

    def _book_receipt(self):
        """The receipt, dated the day the cheque came in: the allocated invoices are
        settled that day; the money waits in the bank's PDC account."""
        self.ensure_one()
        self._holding_account()
        payment = self._create_payment(self.received_date, holding=True)
        self.payment_id = payment
        self._reconcile_allocations(payment)
        self.message_post(body=_(
            "Received on %(day)s: %(payment)s settles the allocated invoices; the cheque "
            "waits in %(account)s until it is deposited.",
            day=self.received_date, payment=payment.name,
            account=payment.outstanding_account_id.display_name))
        return payment

    def _book_deposit(self, day):
        """From the PDC account to the bank account, dated the day it was deposited,
        matched to the receipt."""
        self.ensure_one()
        holding = self.payment_id.outstanding_account_id
        bank = self.journal_id.default_account_id
        if not bank:
            raise UserError(_("The journal %s has no bank account.", self.journal_id.display_name))
        label = _('Cheque %(num)s deposited', num=self.cheque_number)
        partner = self.partner_id.commercial_partner_id
        move = self.env['account.move'].create({
            'move_type': 'entry',
            'date': day,
            'journal_id': self.journal_id.id,
            'company_id': self.company_id.id,
            'ref': label,
            'line_ids': [
                Command.create({'name': label, 'account_id': bank.id,
                                'partner_id': partner.id, 'balance': self.amount}),
                Command.create({'name': label, 'account_id': holding.id,
                                'partner_id': partner.id, 'balance': -self.amount}),
            ],
        })
        move.action_post()
        pair = (move.line_ids | self.payment_id.move_id.line_ids).filtered(
            lambda l: l.account_id == holding and not l.reconciled)
        if len(pair) > 1:
            pair.reconcile()
        return move

    def _unbook(self, day, reason):
        """Undo the receipt (and the deposit) by reversing them on `day`: the bank first,
        then the payment, so the invoices are open again from that day."""
        self.ensure_one()
        payment = self.payment_id
        moves = self.deposit_move_id | payment.move_id
        moves.line_ids.remove_move_reconcile()
        for move in (self.deposit_move_id, payment.move_id):
            if move and move.state == 'posted':
                move._reverse_moves([{
                    'date': day,
                    'ref': _('Reversal of %(ref)s: %(reason)s',
                             ref=move.ref or move.name, reason=reason),
                }], cancel=True)
        if payment:
            payment.action_reject()

    # ------------------------------------------------------------------ actions
    def action_confirm_receipt(self):
        """Settle the allocated invoices now, dated the received day."""
        for cheque in self:
            if cheque.state != 'received':
                raise UserError(_("Only a cheque in hand can be received."))
            if cheque.accounting == 'holding' and not cheque.payment_id:
                cheque._book_receipt()
        return True

    def action_deposit(self):
        today = fields.Date.context_today(self)
        for cheque in self:
            if cheque.state != 'received':
                raise UserError(_("Only a cheque in hand can be deposited."))
            if not cheque.journal_id:
                raise UserError(_("Choose the bank account %s is being deposited into.",
                                  cheque.cheque_number))
            day = cheque.deposit_date or today
            if day > today:
                raise UserError(_("Cheque %(num)s cannot be deposited on %(day)s: that is still "
                                  "to come.", num=cheque.cheque_number, day=day))
            if day < cheque.received_date:
                raise UserError(_("Cheque %(num)s cannot be deposited on %(day)s, before it was "
                                  "received.", num=cheque.cheque_number, day=day))
            vals = {'state': 'deposited', 'deposit_date': day}
            if cheque.accounting == 'holding':
                if not cheque.payment_id:
                    cheque._book_receipt()
                vals['deposit_move_id'] = cheque._book_deposit(day).id
            cheque.write(vals)
        return True

    def action_clear(self):
        """The bank confirms the funds. A cheque settled when received is already in the
        bank from its deposit; one recorded the old way gets its payment now - unless it
        already has one (every cheque brought over from Odoo 17 does)."""
        today = fields.Date.context_today(self)
        for cheque in self:
            if cheque.state != 'deposited':
                raise UserError(_("Deposit %s before clearing it.", cheque.cheque_number))
            if cheque.accounting == 'clearing' and not cheque.payment_id:
                cheque.write({'state': 'cleared', 'clear_date': today})
                payment = cheque._create_payment()
                cheque.payment_id = payment
                cheque._reconcile_allocations(payment)
            else:
                cheque.write({'state': 'cleared', 'clear_date': today})
        return True

    def _get_payment_method_line(self):
        self.ensure_one()
        lines = self.journal_id.inbound_payment_method_line_ids
        # Prefer a method actually named for cheques when the journal offers one.
        cheque_line = lines.filtered(
            lambda l: 'cheque' in (l.name or '').lower() or 'check' in (l.name or '').lower())
        return (cheque_line or lines)[:1]

    def _create_payment(self, date=None, holding=False):
        self.ensure_one()
        method_line = self._get_payment_method_line()
        if not method_line:
            raise UserError(_(
                "The journal %s has no inbound payment method configured.",
                self.journal_id.display_name))
        return self.env['account.payment'].with_context(lab_cheque_clearing=True).create({
            'cheque_id': self.id,
            'lab_cheque_holding': holding,
            'payment_type': 'inbound',
            'partner_type': 'customer',
            'partner_id': self.partner_id.id,
            'amount': self.amount,
            'date': date or self.clear_date or fields.Date.context_today(self),
            'journal_id': self.journal_id.id,
            'payment_method_line_id': method_line.id,
            'memo': _('Cheque %(num)s / %(bank)s',
                      num=self.cheque_number, bank=self.bank_name or '-'),
            'company_id': self.company_id.id,
        })

    def _reconcile_allocations(self, payment):
        """Match the payment against exactly the invoices the cheque was allocated to,
        each for the amount allocated to it - no more.

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
        counterpart = counterpart.filtered(lambda l: not l.reconciled)[:1]
        if not counterpart:
            return
        rounding = self.currency_id.rounding or 0.01
        for allocation in self.allocation_ids:
            invoice_line = allocation.move_id.line_ids.filtered(
                lambda l: l.account_id.account_type == 'asset_receivable' and not l.reconciled)[:1]
            if not invoice_line or float_is_zero(allocation.amount, precision_rounding=rounding):
                continue
            if hasattr(counterpart, '_reconcile_up_to'):
                # ebshel_payment_allocation: for the allocated amount and no more
                counterpart._reconcile_up_to(invoice_line, allocation.amount)
            else:
                (counterpart | invoice_line).reconcile()
            if counterpart.reconciled:
                break

    def action_bounce(self):
        today = fields.Date.context_today(self)
        for cheque in self:
            if cheque.state not in ('deposited', 'cleared'):
                raise UserError(_("Only a banked cheque can bounce."))
            if not cheque.bounce_reason:
                raise UserError(_("Record why cheque %s bounced.", cheque.cheque_number))
            if cheque.accounting == 'holding':
                if cheque.payment_id and cheque.payment_id.state != 'rejected':
                    cheque._unbook(today, _('bounced, %s', cheque.bounce_reason))
            elif cheque.payment_id and cheque.payment_id.state != 'canceled':
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
        today = fields.Date.context_today(self)
        for cheque in self:
            if cheque.accounting == 'holding':
                if cheque.state == 'deposited':
                    raise UserError(_(
                        "Cheque %s is in the bank: mark it bounced instead of cancelling it.",
                        cheque.cheque_number))
                if cheque.payment_id and cheque.payment_id.state not in ('rejected', 'canceled'):
                    cheque._unbook(today, _('cancelled'))
            elif cheque.payment_id and cheque.payment_id.state not in ('draft', 'canceled'):
                raise UserError(_(
                    "Cancel or reverse the payment on %s before cancelling the cheque.",
                    cheque.cheque_number))
            cheque.state = 'cancel'
        return True

    def action_draft(self):
        """Back in hand - a bounced cheque presented again, or a cancelled one restored.
        One settled when received is received again today, with a new receipt."""
        today = fields.Date.context_today(self)
        for cheque in self.filtered(lambda c: c.state in ('bounced', 'cancel')):
            vals = {'state': 'received', 'deposit_date': False, 'clear_date': False}
            if cheque.accounting == 'holding':
                vals.update(payment_id=False, deposit_move_id=False, received_date=today)
            cheque.write(vals)
        return True

    def action_view_payment(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.payment',
            'res_id': self.payment_id.id,
            'view_mode': 'form',
        }

    def action_view_deposit(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': self.deposit_move_id.id,
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

    # ------------------------------------------------------------------ quick entry
    # The Add Cheque card on Payments and on the Cheque Register
    # (static/src/cheque_entry): one round trip to fill it, one to save it.
    @api.model
    def lab_cheque_entry_data(self, partner_id=False):
        """What the card offers: the banks a cheque can go to, and for a clinic its open
        invoices, the bank its last cheque was drawn on, and what is already in hand."""
        company = self.env.company
        journals = self.env['account.journal'].search([
            ('type', '=', 'bank'), ('company_id', '=', company.id)], order='sequence, id')
        out = {
            'journals': [{'id': j.id, 'name': j.name, 'pdc': bool(j.lab_pdc_account_id)}
                         for j in journals],
            'currency': {'symbol': company.currency_id.symbol,
                         'position': company.currency_id.position,
                         'decimals': company.currency_id.decimal_places},
            'today': fields.Date.to_string(fields.Date.context_today(self)),
            'invoices': [], 'last': False, 'in_hand': {'count': 0, 'amount': 0.0},
        }
        if not partner_id:
            return out
        partner = self.env['res.partner'].browse(int(partner_id)).exists()
        if not partner:
            return out
        commercial = partner.commercial_partner_id
        probe = self.new({'partner_id': partner.id, 'company_id': company.id})
        out['invoices'] = [{
            'id': move.id, 'name': move.name,
            'date': fields.Date.to_string(move.invoice_date),
            'due': fields.Date.to_string(move.invoice_date_due),
            'total': move.amount_total, 'open': round(due, 2),
        } for move, due in probe._open_invoice_amounts()]
        theirs = self.search([('partner_id', 'child_of', commercial.id),
                              ('company_id', '=', company.id)], order='id desc')
        last = theirs[:1]
        if last:
            out['last'] = {
                'bank_name': last.bank_name or '', 'branch': last.branch or '',
                'journal_id': last.journal_id.id, 'cheque_number': last.cheque_number,
                'amount': last.amount, 'cheque_date': fields.Date.to_string(last.cheque_date),
            }
        in_hand = theirs.filtered(lambda c: c.state == 'received')
        out['in_hand'] = {'count': len(in_hand), 'amount': sum(in_hand.mapped('amount'))}
        out['numbers'] = theirs.mapped('cheque_number')
        return out

    @api.model
    def lab_cheque_quick_create(self, vals, allocations=(), receive=True):
        """A cheque from the card, with its invoices; received at once unless asked not to."""
        allowed = ('partner_id', 'cheque_number', 'cheque_date', 'received_date', 'bank_name',
                   'branch', 'amount', 'journal_id', 'note')
        clean = {k: v for k, v in (vals or {}).items() if k in allowed and v not in (None, '')}
        clean['allocation_ids'] = [
            Command.create({'move_id': int(a['move_id']), 'amount': float(a['amount'])})
            for a in allocations or () if float(a.get('amount') or 0) > 0]
        cheque = self.create(clean)
        if receive:
            cheque.action_confirm_receipt()
        return {'id': cheque.id, 'name': cheque.display_name, 'reference': cheque.name,
                'payment': cheque.payment_id.name or False}

    @api.model
    def lab_cheque_summary(self):
        """The cheques strip above Payments: what is in hand, due, coming and bounced."""
        today = fields.Date.context_today(self)
        day, week = fields.Date.to_string(today), fields.Date.to_string(today + timedelta(days=7))
        company = self.env.company
        base = [('company_id', '=', company.id)]
        groups = {
            'in_hand': [('state', '=', 'received')],
            'due': [('state', '=', 'received'), ('cheque_date', '<=', day)],
            'week': [('state', '=', 'received'), ('cheque_date', '>', day),
                     ('cheque_date', '<=', week)],
            'deposited': [('state', '=', 'deposited')],
            'bounced': [('state', '=', 'bounced')],
        }
        if not self.has_access('read'):
            return {}
        currency = company.currency_id
        out = {'currency': {'symbol': currency.symbol, 'position': currency.position}}
        for key, domain in groups.items():
            rows = self._read_group(base + domain, [], ['__count', 'amount:sum'])
            count, amount = rows[0] if rows else (0, 0.0)
            out[key] = {'count': count, 'amount': amount or 0.0, 'domain': base + domain}
        return out


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

    def _check_receipt_open(self, cheques):
        booked = cheques.filtered(lambda c: c._is_booked())
        if booked:
            raise UserError(_(
                "The invoices of cheque %s were settled when it was received. Cancel the "
                "cheque and record it again to settle others.", booked[0].cheque_number))

    @api.model_create_multi
    def create(self, vals_list):
        self._check_receipt_open(self.env['lab.cheque'].browse(
            [v['cheque_id'] for v in vals_list if v.get('cheque_id')]))
        return super().create(vals_list)

    def write(self, vals):
        self._check_receipt_open(self.cheque_id)
        return super().write(vals)

    def unlink(self):
        self._check_receipt_open(self.cheque_id)
        return super().unlink()
