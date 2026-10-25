# -*- coding: utf-8 -*-
"""Dispose of an asset: depreciation is brought up to the date, then the gross
value and its accumulated depreciation leave the books and the net book value
goes to the loss (or gain) account. The proceeds of a sale come off the account
that took them - the sale invoice's income lines, or the account named for proceeds
received without one - so the gain or loss account carries proceeds less book value,
the gain or loss reported on the asset."""
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_is_zero, float_round

from ..models.asset import period_end, period_start


class AssetDispose(models.TransientModel):
    _name = 'ebshel.asset.dispose'
    _description = 'Dispose of an asset'

    asset_id = fields.Many2one('ebshel.asset', required=True)
    company_id = fields.Many2one(related='asset_id.company_id')
    currency_id = fields.Many2one(related='asset_id.currency_id')
    date = fields.Date(required=True, default=fields.Date.context_today)
    kind = fields.Selection([('sale', 'Sold'), ('scrap', 'Scrapped'), ('donation', 'Donated'), ('lost', 'Lost or stolen')],
                            default='sale', required=True)
    proceeds = fields.Monetary(help="What the sale brought in, before tax.")
    invoice_id = fields.Many2one('account.move', string='Sale invoice',
                                 domain="[('move_type', '=', 'out_invoice'), ('state', '=', 'posted'), "
                                        "('company_id', '=', company_id)]",
                                 help="Optional: the customer invoice for the sale; its untaxed total becomes the proceeds.")
    proceeds_account_id = fields.Many2one(
        'account.account', string='Proceeds account', check_company=True,
        help="Proceeds without a sale invoice: the account the money sits in (a disposal clearing "
             "account, the bank). With an invoice, the invoice already booked them.")
    note = fields.Char(required=True)
    book_value = fields.Monetary(related='asset_id.book_value')

    @api.onchange('invoice_id')
    def _onchange_invoice(self):
        if self.invoice_id:
            self.proceeds = self._invoice_proceeds()

    def _invoice_lines(self):
        """The sale invoice's untaxed lines: what it credited for the sale."""
        return self.invoice_id.line_ids.filtered(lambda l: l.display_type in ('product', 'rounding')
                                                 and not l.tax_repartition_line_id)

    def _invoice_proceeds(self):
        # in the company's currency: the untaxed total in the invoice's own currency made
        # proceeds of 1,000 rupees out of 1,000 dollars
        return -sum(self._invoice_lines().mapped('balance'))

    def _final_depreciation(self, asset):
        """Depreciation from the last posted line to the disposal date, prorated."""
        pending = asset.line_ids.filtered(lambda l: l.state == 'draft').sorted('date')
        due = pending.filtered(lambda l: l.date <= self.date)
        due.action_post()
        rest = pending - due
        if rest:
            current = rest[0]
            start = period_start(current.date, asset.period, asset.company_id)
            if asset.prorata == 'days' and asset.start_date and asset.start_date > start:
                # the first line already covers only the days from the start date: prorating
                # it again over the whole period depreciated days the asset never had
                start = asset.start_date
            if self.date >= start:
                days = (period_end(current.date, asset.period, asset.company_id) - start).days + 1
                share = ((self.date - start).days + 1) / days
                amount = float_round(current.amount * share, precision_rounding=asset.currency_id.rounding)
                if amount > 0:
                    current.write({'date': self.date, 'amount': amount,
                                   'name': _('%s — depreciation to disposal', asset.name)})
                    current.action_post()
                    rest -= current
            rest.write({'state': 'skipped'})

    def _dispose_children(self, asset):
        """A gross increase is part of its asset: it leaves the books with it. Disposing of the
        parent left the increases running, their value still on the asset account."""
        for child in asset.with_context(active_test=False).child_ids:
            if child.state == 'draft':
                child.action_cancel()
            elif child.state in ('running', 'paused', 'closed'):
                self.create({
                    'asset_id': child.id, 'date': self.date, 'kind': self.kind, 'proceeds': 0.0,
                    'note': _('%(note)s (with %(asset)s)', note=self.note, asset=asset.name),
                }).action_apply()

    def action_apply(self):
        self.ensure_one()
        asset = self.asset_id
        if asset.state not in ('running', 'paused', 'closed'):
            raise UserError(_("Only a running, paused or fully depreciated asset can be disposed of."))
        # disposed of before depreciation already posted: those periods stayed charged and the
        # disposal entry was dated before them
        last_posted = max(asset.line_ids.filtered(lambda l: l.state == 'posted').mapped('date'), default=None)
        if last_posted and self.date < last_posted:
            raise UserError(_("%(asset)s has depreciation posted up to %(day)s: date the disposal on or after it.",
                              asset=asset.name, day=fields.Date.to_string(last_posted)))
        if self.kind != 'sale':
            self.write({'proceeds': 0.0, 'invoice_id': False})
        if self.invoice_id:
            if self.invoice_id.company_id != asset.company_id:
                raise UserError(_("The sale invoice belongs to another company."))
            self.proceeds = self._invoice_proceeds()
        proceeds = self.proceeds or 0.0
        if proceeds and not self.invoice_id and not self.proceeds_account_id:
            raise UserError(_("Choose the account the proceeds were received in, or pick the sale invoice."))
        self._dispose_children(asset)
        if asset.state == 'paused':
            asset.write({'state': 'running', 'paused_on': False})
        self._final_depreciation(asset)
        cat = asset.category_id
        book = asset.book_value
        gain_loss = proceeds - book
        loss_account = cat.loss_account_id or asset.expense_account_id
        gain_account = cat.gain_account_id or loss_account
        rounding = asset.currency_id.rounding
        label = _('%(asset)s — %(kind)s', asset=asset.name, kind=dict(self._fields['kind'].selection)[self.kind])
        lines = [
            (0, 0, {'name': label, 'account_id': asset.asset_account_id.id, 'credit': asset.purchase_value, 'debit': 0.0}),
        ]
        if asset.depreciated_value:
            lines.append((0, 0, {'name': label, 'account_id': asset.depreciation_account_id.id,
                                 'debit': asset.depreciated_value, 'credit': 0.0}))
        # The proceeds come off the account that took them: the invoice's income lines (the
        # invoice credited them; this debits them back) or the account they were received in.
        # Only the difference reaches the gain or loss account - the whole book value went to
        # the loss account and no gain was ever posted.
        if self.invoice_id:
            by_account = {}
            for inv_line in self._invoice_lines():
                by_account[inv_line.account_id] = by_account.get(inv_line.account_id, 0.0) - inv_line.balance
            for account, amount in by_account.items():
                if float_is_zero(amount, precision_rounding=rounding):
                    continue
                lines.append((0, 0, {'name': _('%s — proceeds', asset.name), 'account_id': account.id,
                                     'debit': max(amount, 0.0), 'credit': max(-amount, 0.0)}))
        elif proceeds:
            lines.append((0, 0, {'name': _('%s — proceeds', asset.name), 'account_id': self.proceeds_account_id.id,
                                 'debit': max(proceeds, 0.0), 'credit': max(-proceeds, 0.0)}))
        if float_compare(gain_loss, 0.0, precision_rounding=rounding) < 0:
            lines.append((0, 0, {'name': _('%s — loss on disposal', asset.name), 'account_id': loss_account.id,
                                 'debit': -gain_loss, 'credit': 0.0,
                                 'analytic_distribution': asset.analytic_distribution or False}))
        elif float_compare(gain_loss, 0.0, precision_rounding=rounding) > 0:
            lines.append((0, 0, {'name': _('%s — gain on disposal', asset.name), 'account_id': gain_account.id,
                                 'debit': 0.0, 'credit': gain_loss}))
        move = self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': asset.journal_id.id, 'date': self.date, 'ref': label,
            'ebshel_asset_id': asset.id, 'company_id': asset.company_id.id, 'line_ids': lines})
        move.action_post()
        # a locked period moves the entry on: the asset records the date the ledger has
        disposal_date = move.date
        self.env['ebshel.asset.line'].create({
            'asset_id': asset.id, 'date': disposal_date, 'kind': 'disposal', 'amount': book, 'state': 'posted',
            'move_id': move.id, 'name': label, 'sequence': len(asset.line_ids) + 1,
            'cumulative': asset.depreciated_value, 'remaining': 0.0})
        asset.write({'state': 'disposed', 'disposal_date': disposal_date, 'disposal_kind': self.kind,
                     'disposal_value': proceeds, 'disposal_move_id': move.id,
                     'disposal_invoice_id': self.invoice_id.id, 'gain_loss': gain_loss})
        asset._log('disposed', note='%s — %s' % (dict(self._fields['kind'].selection)[self.kind], self.note),
                   amount=gain_loss, day=disposal_date)
        return {'type': 'ir.actions.act_window_close'}
