from odoo import _, fields, models
from odoo.exceptions import UserError, ValidationError


class AccountMove(models.Model):
    _inherit = 'account.move'

    # Stored Boolean set explicitly when the move is created via a petty-cash
    # payment or expense.  Using a stored non-computed field removes the
    # expensive "depends on every line's account_id.is_petty_cash" recompute
    # that previously fired on every journal entry in the system.
    is_petty_cash = fields.Boolean(
        string='Is Petty Cash Entry', default=False, copy=False,
        help='Designates this move as a petty cash move.')
    petty_cash_partner_id = fields.Many2one(
        'res.partner', string='Petty Cash Holder', readonly=False, copy=False)
    petty_cash_expense_id = fields.Many2one(
        'petty.cash.expense', string='Petty Cash Expense', readonly=True, copy=False)

    def _post(self, soft=True):
        res = super()._post(soft=soft)
        # Guard: the context flag is set by our own payment/transaction posting
        # path to prevent recursion.  Also skip completely if no petty-cash moves
        # in this batch — avoids any overhead for ordinary accounting operations.
        if self.env.context.get('skip_petty_cash_auto_transaction'):
            return res
        self._petty_cash_auto_generate()
        return res

    def _petty_cash_auto_generate(self):
        """Create the petty cash expense/transaction records for petty-cash moves.

        A move is treated as petty cash when it carries a petty cash holder and
        either the ``is_petty_cash`` flag (payments/bills stamped by this module)
        or at least one line booked on a petty cash account (manual journal
        entries created straight from the Journal Entries screen).

        Called from ``_post`` for moves flagged before posting (manual journal
        entries / vendor bills) and from the petty-cash payment posting path —
        ``account.payment`` stamps its move only after ``super().action_post``
        has already run ``_post``, so the flag isn't visible during that pass.
        """
        pc_moves = self.filtered(
            lambda m: m.petty_cash_partner_id and (
                m.is_petty_cash
                or any(l.account_id.is_petty_cash for l in m.line_ids)))
        for move in pc_moves:
            # Stamp raw journal entries so they are recognised as petty cash and
            # protected by the draft/cancel guards, like payment/bill moves.
            if not move.is_petty_cash:
                move.is_petty_cash = True
            if move.move_type == 'in_invoice':
                if not self.env['petty.cash.expense'].search_count(
                        [('move_id', '=', move.id)], limit=1):
                    move._create_petty_cash_expense_from_bill()
            else:
                if not self.env['petty.cash.transaction'].search_count(
                        [('move_id', '=', move.id)], limit=1):
                    is_expense = any(
                        l.balance > 0
                        for l in move.line_ids
                        if l.account_id.account_type == 'expense')
                    if is_expense:
                        move._create_petty_cash_expense()
                    else:
                        move._create_petty_cash_transaction()

    def _get_petty_cash_allocation(self):
        self.ensure_one()
        return self.env['petty.cash.allocation'].sudo().search([
            ('partner_id', '=', self.petty_cash_partner_id.id),
            ('company_id', '=', self.company_id.id),
            ('state', '=', 'allocated'),
        ], limit=1)

    def _create_petty_cash_expense_from_bill(self):
        self.ensure_one()
        if self.petty_cash_expense_id:
            return self.petty_cash_expense_id
        allocation = self._get_petty_cash_allocation()
        if not allocation:
            raise ValidationError(_(
                'No allocated petty cash found for holder %s.'
            ) % self.petty_cash_partner_id.display_name)
        lines = [
            (0, 0, {
                'name': line.name or line.product_id.display_name,
                'product_id': line.product_id.id or False,
                'account_id': line.account_id.id,
                'amount': line.price_subtotal,
            })
            for line in self.invoice_line_ids.filtered(lambda l: l.price_subtotal > 0)
        ]
        if not lines:
            return False
        expense = self.env['petty.cash.expense'].sudo().create({
            'allocation_id': allocation.id,
            'partner_id': self.partner_id.id,
            'line_ids': lines,
            'state': 'bill_created',
            'move_id': self.id,
            'company_id': self.company_id.id,
        })
        self.petty_cash_expense_id = expense.id
        return expense

    def _create_petty_cash_transaction(self):
        self.ensure_one()
        pc_lines = self.line_ids.filtered(lambda l: l.account_id.is_petty_cash)
        amount = sum(l.debit - l.credit for l in pc_lines)
        if not amount:
            return False
        allocation = self._get_petty_cash_allocation()
        if not allocation:
            raise ValidationError(_(
                'No allocated petty cash found for holder %s. '
                'Please allocate petty cash before posting this entry.'
            ) % self.petty_cash_partner_id.display_name)
        return self.env['petty.cash.transaction'].sudo().create({
            'allocation_id': allocation.id,
            'date': self.date,
            'amount': abs(amount),
            'petty_cash_account_id': pc_lines[:1].account_id.id or False,
            'type': 'allocation' if amount > 0 else 'expense',
            'move_id': self.id,
            'payment_id': self.origin_payment_id.id if self.origin_payment_id else False,
            'company_id': self.company_id.id,
            # Cash movements always carry the cash holder, never the vendor —
            # see petty.cash.expense.action_register_cash_spend.
            'partner_id': (self.petty_cash_partner_id or self.partner_id).id or False,
            'note': self.partner_id.display_name if self.partner_id else False,
            'state': 'posted',
        })

    def _create_petty_cash_expense(self):
        self.ensure_one()
        expense_lines = self.line_ids.filtered(
            lambda l: l.account_id.account_type == 'expense' and l.balance > 0)
        if not expense_lines:
            return False
        allocation = self._get_petty_cash_allocation()
        if not allocation:
            raise ValidationError(_(
                'No allocated petty cash found for holder %s. '
                'Please allocate petty cash before posting this entry.'
            ) % self.petty_cash_partner_id.display_name)
        lines = [
            (0, 0, {
                'name': line.name or (line.product_id.display_name if line.product_id else ''),
                'product_id': line.product_id.id or False,
                'account_id': line.account_id.id,
                'amount': line.balance,
            })
            for line in expense_lines
        ]
        if not lines:
            return False
        transaction = self._create_petty_cash_transaction()
        expense = self.env['petty.cash.expense'].sudo().create({
            'allocation_id': allocation.id,
            'partner_id': self.partner_id.id,
            'line_ids': lines,
            'state': 'paid',
            'transaction_id': transaction.id if transaction else False,
            'company_id': self.company_id.id,
        })
        self.petty_cash_expense_id = expense.id
        return expense

    def button_draft(self):
        # Only check petty-cash moves — avoid looping non-petty entries.
        for move in self.filtered(lambda m: m.is_petty_cash and m.state == 'posted'):
            raise UserError(_(
                'Journal entry %s is linked to a petty cash record and cannot be reset to draft.'
            ) % move.name)
        return super().button_draft()

    def button_cancel(self):
        for move in self.filtered('is_petty_cash'):
            raise UserError(_(
                'Journal entry %s is linked to a petty cash record and cannot be cancelled.'
            ) % move.name)
        return super().button_cancel()

    def action_view_petty_cash_expense(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Petty Cash Expense'),
            'res_model': 'petty.cash.expense',
            'view_mode': 'form',
            'res_id': self.petty_cash_expense_id.id,
            'target': 'current',
        }
