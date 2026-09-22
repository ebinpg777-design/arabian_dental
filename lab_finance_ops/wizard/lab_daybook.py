# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class LabDaybookWizard(models.TransientModel):
    """Daybook (gap 19).

    The Indian daybook is the same data as the native journal items, asked a
    particular way: one book, one date range, opening balance -> the day's
    entries -> closing balance. Rather than build a parallel report engine, this
    opens the native journal-items view with the right domain and hands the
    balances over in the wizard, so drill-down, export and the audit trail stay
    exactly where the accountant already knows to find them.
    """
    _name = 'lab.daybook.wizard'
    _description = 'Daybook'

    date_from = fields.Date(
        required=True, default=lambda self: fields.Date.context_today(self))
    date_to = fields.Date(
        required=True, default=lambda self: fields.Date.context_today(self))
    book_type = fields.Selection(
        [('cash', 'Cash Book'), ('bank', 'Bank Book'), ('all', 'General Daybook')],
        default='cash', required=True)
    journal_ids = fields.Many2many(
        'account.journal', string='Journals',
        help="Leave empty to cover every journal of the selected book type.")
    target_move = fields.Selection(
        [('posted', 'Posted Entries Only'), ('all', 'All Entries')],
        default='posted', required=True)
    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company, required=True)
    currency_id = fields.Many2one(related='company_id.currency_id', readonly=True)

    opening_balance = fields.Monetary(
        compute='_compute_balances', currency_field='currency_id')
    period_debit = fields.Monetary(compute='_compute_balances', currency_field='currency_id')
    period_credit = fields.Monetary(compute='_compute_balances', currency_field='currency_id')
    closing_balance = fields.Monetary(
        compute='_compute_balances', currency_field='currency_id')

    @api.onchange('book_type')
    def _onchange_book_type(self):
        self.journal_ids = False

    def _resolve_journals(self):
        self.ensure_one()
        if self.journal_ids:
            return self.journal_ids
        domain = [('company_id', '=', self.company_id.id)]
        if self.book_type in ('cash', 'bank'):
            domain.append(('type', '=', self.book_type))
        # Archived books too: all 56 cash journals on this ledger are archived, and
        # the money booked through them is still in the cash accounts.
        return self.env['account.journal'].with_context(active_test=False).search(domain)

    def _state_domain(self):
        if self.target_move == 'posted':
            return [('parent_state', '=', 'posted')]
        return [('parent_state', 'in', ('draft', 'posted'))]

    def _liquidity_accounts(self):
        """The accounts a cash or bank book is the balance of.

        Every line of a journal's moves sums to zero - the cash side and its
        counterpart cancel - so a balance over "the journal" was always 0. A book's
        balance is its cash accounts: each journal's default account plus every
        asset_cash account actually posted through it (CNRB's default account is
        not where its entries land). The General Daybook's balance is the cash and
        bank position as a whole.
        """
        self.ensure_one()
        books = self._resolve_journals().filtered(lambda j: j.type in ('cash', 'bank'))
        if self.book_type == 'all':
            books = self.env['account.journal'].with_context(active_test=False).search([
                ('company_id', '=', self.company_id.id), ('type', 'in', ('cash', 'bank'))])
        accounts = books.default_account_id
        if books:
            for (account,) in self.env['account.move.line']._read_group(
                    [('journal_id', 'in', books.ids),
                     ('company_id', '=', self.company_id.id),
                     ('account_id.account_type', '=', 'asset_cash')],
                    ['account_id']):
                accounts |= account
        return accounts

    def _balance_domain(self):
        """Lines moving the book's cash accounts, whichever journal booked them -
        the opening balances on this ledger were entered through MISC."""
        self.ensure_one()
        return [
            ('account_id', 'in', self._liquidity_accounts().ids),
            ('company_id', '=', self.company_id.id),
        ] + self._state_domain()

    def _base_domain(self):
        """What the list shows: a cash or bank book lists exactly the lines its
        balance is made of; the General Daybook lists every entry of the day."""
        self.ensure_one()
        if self.book_type in ('cash', 'bank'):
            return self._balance_domain()
        return [
            ('journal_id', 'in', self._resolve_journals().ids),
            ('company_id', '=', self.company_id.id),
        ] + self._state_domain()

    @api.depends('date_from', 'date_to', 'book_type', 'journal_ids', 'target_move', 'company_id')
    def _compute_balances(self):
        AML = self.env['account.move.line']
        for wizard in self:
            if not (wizard.date_from and wizard.date_to):
                wizard.opening_balance = wizard.period_debit = 0.0
                wizard.period_credit = wizard.closing_balance = 0.0
                continue
            base = wizard._balance_domain()
            opening = AML._read_group(
                base + [('date', '<', wizard.date_from)], [], ['balance:sum'])
            wizard.opening_balance = opening[0][0] if opening and opening[0][0] else 0.0
            period = AML._read_group(
                base + [('date', '>=', wizard.date_from), ('date', '<=', wizard.date_to)],
                [], ['debit:sum', 'credit:sum'])
            wizard.period_debit = period[0][0] if period and period[0][0] else 0.0
            wizard.period_credit = period[0][1] if period and period[0][1] else 0.0
            wizard.closing_balance = (
                wizard.opening_balance + wizard.period_debit - wizard.period_credit)

    def action_view_daybook(self):
        self.ensure_one()
        if self.date_to < self.date_from:
            raise UserError(_("The 'to' date cannot precede the 'from' date."))
        journals = self._resolve_journals()
        if not journals:
            raise UserError(_("There is no journal matching this book type."))
        domain = self._base_domain() + [
            ('date', '>=', self.date_from), ('date', '<=', self.date_to)]
        labels = dict(self._fields['book_type'].selection)
        return {
            'type': 'ir.actions.act_window',
            'name': _('%(book)s — %(from)s to %(to)s (opening %(open)s, closing %(close)s)',
                      book=labels.get(self.book_type),
                      **{'from': self.date_from, 'to': self.date_to,
                         'open': self.currency_id.format(self.opening_balance),
                         'close': self.currency_id.format(self.closing_balance)}),
            'res_model': 'account.move.line',
            'view_mode': 'list,pivot,form',
            'domain': domain,
            'context': {
                'search_default_group_by_journal': 1,
                'create': False,
                'expand': True,
            },
        }
