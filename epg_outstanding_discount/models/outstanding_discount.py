# -*- coding: utf-8 -*-
"""A discount on a customer's total outstanding, as one journal entry.

The accountant's job here is a negotiation that has already happened: "clear it all and
we'll knock off 5%", "make it an even fifty thousand". This model turns that sentence
into accounting — one entry, discount expense against receivables, reconciled against
the oldest open invoices so the ledger, the statements and the follow-ups all agree the
moment it is posted.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import format_amount, format_date, html_escape

REASONS = [
    ('settlement', 'Full & final settlement'),
    ('loyalty', 'Long-standing customer'),
    ('dispute', 'Disputed work resolved'),
    ('goodwill', 'Goodwill gesture'),
    ('other', 'Other'),
]


class OutstandingDiscount(models.Model):
    _name = 'epg.outstanding.discount'
    _description = 'Outstanding Discount'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'

    name = fields.Char(default=lambda self: _('New'), copy=False, readonly=True)
    state = fields.Selection(
        [('draft', 'Draft'), ('posted', 'Posted'), ('cancel', 'Cancelled')],
        default='draft', tracking=True, copy=False)
    partner_id = fields.Many2one(
        'res.partner', string='Customer', required=True, tracking=True,
        domain="[('customer_rank', '>', 0)]")
    date = fields.Date(default=fields.Date.context_today, required=True, tracking=True,
                       help="This discount's own journal entry date.")
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')

    # As on Date: the running balance, oldest-item-first, counted back through the
    # customer's WHOLE history up to `date` above - what is genuinely still open,
    # the same figure a statement would show. Period: still counted back through
    # the whole history up to `period_to` (a payment from outside the window can
    # still be what closed an item inside it), but only the items actually BILLED
    # within [period_from, period_to] are kept - "what of March's invoicing is
    # still outstanding", not the running total. (client sheet, 2026-08-29:
    # "Outstanding of current month or from date needed")
    computation_mode = fields.Selection(
        [('as_of', 'As on Date'), ('period', 'Period')],
        default='as_of', required=True, tracking=True)
    period_from = fields.Date(string='Start Date', tracking=True)
    period_to = fields.Date(
        string='End Date', tracking=True,
        help="Also the cutoff the countback itself runs to - a payment made "
             "after this date cannot be what cleared an item inside the period.")

    outstanding_total = fields.Monetary(
        compute='_compute_outstanding', string='Outstanding',
        help="As on Date: what this customer owed as at the Date above, full "
             "history. Period: of what was billed in the window, what is still "
             "open.")
    open_invoice_count = fields.Integer(compute='_compute_outstanding')

    mode = fields.Selection(
        [('percent', 'Percentage'), ('amount', 'Fixed amount'), ('target', 'Settle to')],
        default='percent', required=True,
        help="Percentage of the outstanding; a fixed amount; or 'Settle to' — type the "
             "figure the customer should end up owing and the discount is the rest.")
    percent = fields.Float(string='Discount %', default=5.0)
    amount_input = fields.Monetary(string='Discount Amount (input)')
    target_amount = fields.Monetary(
        string='Outstanding After', help="What the customer will still owe.")
    discount_amount = fields.Monetary(
        compute='_compute_discount_amount', store=True, readonly=True, tracking=True)
    after_amount = fields.Monetary(compute='_compute_discount_amount', store=True,
                                   string='Will Still Owe')

    reason = fields.Selection(REASONS, required=True, default='settlement', tracking=True)
    note = fields.Text()
    account_id = fields.Many2one(
        'account.account', string='Discount Account', required=True,
        domain="[('account_type', 'in', ('expense', 'expense_direct_cost', 'income_other'))]",
        default=lambda self: self._default_account())
    journal_id = fields.Many2one(
        'account.journal', string='Journal', required=True,
        domain="[('type', '=', 'general')]",
        default=lambda self: self.env['account.journal'].search(
            [('type', '=', 'general')], limit=1))
    move_id = fields.Many2one('account.move', string='Journal Entry',
                              readonly=True, copy=False)
    allocation_preview = fields.Html(
        compute='_compute_allocation_preview', sanitize=False,
        help="The open invoices this discount will clear, oldest first.")

    # ------------------------------------------------------------------ defaults
    @api.model
    def _default_account(self):
        """The company's own discount account, as set in Accounting settings.

        Settings > Default Accounts > "Invoice line discounts: Customer Invoices"
        (account_discount_income_allocation_id) is where the accountant already told
        Odoo where customer discounts go; the cash-discount loss account is the next
        best answer. Only when neither is configured do we fall back to the last one
        used here, then to a guess.
        """
        company = self.env.company
        configured = (company.account_discount_income_allocation_id
                      or company.account_journal_early_pay_discount_loss_account_id)
        if configured:
            return configured
        last = self.search([('state', '=', 'posted'),
                            ('company_id', '=', company.id)], limit=1)
        if last:
            return last.account_id
        return self.env['account.account'].search(
            [('account_type', '=', 'expense'),
             '|', ('name', 'ilike', 'discount'), ('code', 'like', '4%')],
            limit=1)

    @api.onchange('company_id')
    def _onchange_company_id(self):
        if self.company_id and not self.account_id:
            self.account_id = self.with_company(self.company_id)._default_account()

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        # The starting point the sheet already agrees on for this doctor - still
        # just a default, not a limit; the accountant can always type over it.
        if self.partner_id and self.partner_id.outstanding_discount_percent:
            self.mode = 'percent'
            self.percent = self.partner_id.outstanding_discount_percent

    # ------------------------------------------------------------------ computes
    def _open_items(self, as_of=None):
        """[(line, open amount)] for this customer as at a date, oldest first.

        Counted back from the clinic's own ledger rather than read off
        ``amount_residual``: receipts in this database are booked as plain journal
        entries and are never reconciled against the invoices they pay - 4 reconciled
        lines in 1,88,462 - so every invoice still claims its full face value. Read
        that way one clinic showed 12,59,140 outstanding against a real balance of
        1,69,280, and a "5% of outstanding" discount would have been more than
        seven times what was agreed. (2026-08-22)

        So the rule a shop uses: the money that has come in pays off the oldest
        items first, and whatever it has not reached is still open. Only entries up
        to `as_of` count, on both sides - which is what makes the figure follow the
        Date on the form.

        In Period mode the cutoff is `period_to`, not `date` - the countback still
        runs through the WHOLE history up to it (a payment from outside the window
        can still be what closed an item inside it, and skipping that would read as
        more outstanding than is real), but the items handed back are then narrowed
        to the ones actually billed within [period_from, period_to]. (client sheet,
        2026-08-29)
        """
        self.ensure_one()
        period = self.computation_mode == 'period'
        as_of = as_of or (period and self.period_to) or self.date \
            or fields.Date.context_today(self)
        lines = self.env['account.move.line'].search([
            ('partner_id', 'child_of', self.partner_id.commercial_partner_id.id),
            ('company_id', '=', self.company_id.id),
            ('account_id.account_type', '=', 'asset_receivable'),
            ('parent_state', '=', 'posted'),
            ('date', '<=', as_of),
        ], order='date asc, id asc')
        paid = sum(lines.mapped('credit'))
        items, running = [], 0.0
        for line in lines:
            if not line.debit:
                continue
            running += line.debit
            uncovered = min(line.debit, running - paid)
            if uncovered > 0.0:
                items.append((line, self.currency_id.round(uncovered)))
        if period and self.period_from:
            items = [(line, amount) for line, amount in items
                     if line.date >= self.period_from]
        return items

    @api.depends('partner_id', 'company_id', 'date', 'computation_mode',
                 'period_from', 'period_to')
    def _compute_outstanding(self):
        for rec in self:
            cutoff = rec.period_to if rec.computation_mode == 'period' else rec.date
            if not rec.partner_id or not cutoff:
                rec.outstanding_total = rec.open_invoice_count = 0
                continue
            items = rec._open_items()
            rec.outstanding_total = sum(amount for _line, amount in items)
            rec.open_invoice_count = len({line.move_id.id for line, _a in items})

    @api.depends('mode', 'percent', 'amount_input', 'target_amount',
                 'outstanding_total')
    def _compute_discount_amount(self):
        for rec in self:
            total = rec.outstanding_total
            if rec.mode == 'percent':
                amount = total * (rec.percent or 0.0) / 100.0
            elif rec.mode == 'amount':
                amount = rec.amount_input or 0.0
            else:
                amount = total - (rec.target_amount or 0.0)
            rec.discount_amount = rec.currency_id.round(max(amount, 0.0))
            rec.after_amount = rec.currency_id.round(total - rec.discount_amount)

    @api.depends('partner_id', 'date', 'discount_amount')
    def _compute_allocation_preview(self):
        """A live 'what will this clear' table, oldest invoice first."""
        for rec in self:
            if not rec.partner_id or rec.discount_amount <= 0:
                rec.allocation_preview = False
                continue
            rows, left = [], rec.discount_amount
            for line, open_amount in rec._open_items():
                if left <= 0:
                    break
                take = min(open_amount, left)
                left -= take
                cleared = take >= open_amount - 0.005
                rows.append(
                    '<tr><td>%s</td><td>%s</td>'
                    '<td class="text-end">%s</td><td class="text-end">%s</td>'
                    '<td>%s</td></tr>' % (
                        html_escape(line.move_id.name or ''),
                        format_date(rec.env, line.date_maturity or line.date),
                        format_amount(rec.env, open_amount, rec.currency_id),
                        format_amount(rec.env, take, rec.currency_id),
                        '<span class="badge text-bg-success">cleared</span>' if cleared
                        else '<span class="badge text-bg-warning">part</span>'))
            rec.allocation_preview = (
                '<table class="table table-sm o_main_table">'
                '<thead><tr><th>Invoice</th><th>Due</th>'
                '<th class="text-end">Open</th><th class="text-end">Discounted</th>'
                '<th/></tr></thead><tbody>%s</tbody></table>' % ''.join(rows))

    # ------------------------------------------------------------------ guards
    # NB: constrained on the INPUT fields — a stored compute does not reliably
    # re-trigger @api.constrains, and writing amount_input alone slipped past.
    @api.constrains('mode', 'percent', 'amount_input', 'target_amount', 'state')
    def _check_amount(self):
        for rec in self.filtered(lambda r: r.state == 'draft'):
            if rec.discount_amount <= 0:
                continue     # posting checks this with a clearer message
            if rec.discount_amount > rec.outstanding_total + 0.005:
                raise ValidationError(_(
                    'The discount (%(disc)s) is more than the customer\'s outstanding '
                    '(%(out)s).',
                    disc=format_amount(self.env, rec.discount_amount, rec.currency_id),
                    out=format_amount(self.env, rec.outstanding_total, rec.currency_id)))

    @api.constrains('computation_mode', 'period_from', 'period_to')
    def _check_period(self):
        for rec in self:
            if rec.computation_mode != 'period':
                continue
            if not rec.period_from or not rec.period_to:
                raise ValidationError(_(
                    'Period mode needs both a Start Date and an End Date.'))
            if rec.period_from > rec.period_to:
                raise ValidationError(_(
                    'The Start Date (%(start)s) is after the End Date (%(end)s).',
                    start=format_date(self.env, rec.period_from),
                    end=format_date(self.env, rec.period_to)))

    # ------------------------------------------------------------------ actions
    def action_post(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_('Only a draft discount can be posted.'))
            if rec.discount_amount <= 0:
                raise UserError(_('The discount amount is zero — nothing to post.'))
            if rec.discount_amount > rec.outstanding_total + 0.005:
                raise UserError(_('The discount is more than the outstanding.'))
            items = rec._open_items()
            if not items:
                raise UserError(_('This customer has nothing outstanding.'))
            lines = rec.env['account.move.line'].browse(
                [line.id for line, _a in items])
            if rec.name == _('New'):
                rec.name = self.env['ir.sequence'].next_by_code(
                    'epg.outstanding.discount') or _('New')
            receivable = lines[0].account_id
            move = self.env['account.move'].create({
                'move_type': 'entry',
                'date': rec.date,
                'journal_id': rec.journal_id.id,
                'ref': _('%(name)s — discount on outstanding, %(partner)s',
                         name=rec.name, partner=rec.partner_id.name),
                'line_ids': [
                    (0, 0, {'account_id': rec.account_id.id,
                            'partner_id': rec.partner_id.id,
                            'name': _('Discount allowed (%s)') % dict(REASONS)[rec.reason],
                            'debit': rec.discount_amount, 'credit': 0.0}),
                    (0, 0, {'account_id': receivable.id,
                            'partner_id': rec.partner_id.id,
                            'name': rec.name,
                            'debit': 0.0, 'credit': rec.discount_amount}),
                ],
            })
            move.action_post()
            # Oldest first, exactly as the preview showed: reconcile the credit against
            # open lines until the discount is used up.
            credit_line = move.line_ids.filtered(
                lambda l: l.account_id == receivable)
            to_reconcile = self.env['account.move.line']
            left = rec.discount_amount
            for line, open_amount in items:
                if left <= 0.005:
                    break
                if line.account_id != receivable:
                    continue
                # A line the countback still calls open can be fully reconciled in
                # Odoo's own terms, and `reconcile()` refuses those outright:
                # "You are trying to reconcile some entries that are already
                # reconciled", which fails the whole posting on a single line.
                #
                # `_open_items` deliberately ignores residuals because this database
                # books receipts as unreconciled journal entries - but the comment
                # there ("4 reconciled lines in 1,88,462") is no longer true: 185,010
                # of 197,983 receivable lines are now fully reconciled. So the two
                # measures disagree far more often than they used to, and the credit
                # can only ever be set against a line that has residual left to take
                # it. (client, 2026-09-09)
                if line.reconciled or not line.amount_residual:
                    continue
                to_reconcile |= line
                left -= open_amount
            if to_reconcile:
                (credit_line + to_reconcile).reconcile()
            rec.write({'state': 'posted', 'move_id': move.id})
            rec.message_post(body=_(
                '%(amount)s discount posted (%(entry)s) and set against the oldest '
                'open invoices.',
                amount=format_amount(self.env, rec.discount_amount, rec.currency_id),
                entry=move.name))
        return True

    def action_cancel(self):
        for rec in self:
            if rec.state == 'posted' and rec.move_id:
                rec._cancel_entry()
            rec.state = 'cancel'
        return True

    def _cancel_entry(self):
        """The discount's own journal entry, cancelled with it.

        It used to be REVERSED: the original stayed posted and a second posted
        entry undid it. Sound bookkeeping, but two live lines on the clinic's
        statement for a discount that was never given - a credit and a debit
        that net to nothing and still have to be explained on the phone. A
        cancelled discount is a mistake being taken back, not a transaction,
        so its entry is cancelled outright: it leaves the statement, the
        outstanding, and the ageing, and the invoices it had been set against
        are open again for the full amount. (client, 2026-09-10)

        Only where the books allow it. An entry inside a locked period or in a
        hashed journal cannot be changed at all; that one is reversed the old
        way, and the discount's chatter says so.
        """
        self.ensure_one()
        move = self.move_id
        try:
            with self.env.cr.savepoint():
                # Posted -> draft -> cancelled; the reset also lifts the
                # reconciliation the posting made against the open invoices.
                move.button_cancel()
        except UserError as why:
            reversal = move._reverse_moves(
                default_values_list=[{
                    'ref': _('Reversal of %s', move.name),
                    'date': fields.Date.context_today(self)}],
                cancel=True)
            self.message_post(body=_(
                'Discount cancelled. Its entry %(entry)s could not be cancelled '
                '(%(why)s), so it was reversed by %(reversal)s instead.',
                entry=move.name, why=str(why.args[0]) if why.args else why,
                reversal=reversal.name))
            return
        self.message_post(body=_(
            'Discount cancelled. Its entry %s is cancelled with it, and the invoices '
            'it was set against are open again for the full amount.', move.name))

    def action_reset_to_draft(self):
        for rec in self.filtered(lambda r: r.state == 'cancel'):
            rec.write({'state': 'draft', 'move_id': False, 'name': _('New')})
        return True

    def action_view_move(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'account.move',
                'res_id': self.move_id.id, 'view_mode': 'form'}

    def unlink(self):
        if self.filtered(lambda r: r.state == 'posted'):
            raise UserError(_('A posted discount cannot be deleted — cancel it, which '
                              'cancels its journal entry with it.'))
        return super().unlink()
