# -*- coding: utf-8 -*-
"""Dispose of an asset: depreciation is brought up to the date, then the gross
value and its accumulated depreciation leave the books and the net book value
goes to the loss (or gain) account. The proceeds of a sale come through the
customer invoice; the gain or loss reported on the asset is proceeds less book
value."""
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import float_round

from ..models.asset import period_end, period_start


class AssetDispose(models.TransientModel):
    _name = 'ebshel.asset.dispose'
    _description = 'Dispose of an asset'

    asset_id = fields.Many2one('ebshel.asset', required=True)
    currency_id = fields.Many2one(related='asset_id.currency_id')
    date = fields.Date(required=True, default=fields.Date.context_today)
    kind = fields.Selection([('sale', 'Sold'), ('scrap', 'Scrapped'), ('donation', 'Donated'), ('lost', 'Lost or stolen')],
                            default='sale', required=True)
    proceeds = fields.Monetary(help="What the sale brought in, before tax.")
    invoice_id = fields.Many2one('account.move', string='Sale invoice',
                                 domain="[('move_type', '=', 'out_invoice'), ('state', '=', 'posted')]",
                                 help="Optional: the customer invoice for the sale; its untaxed total becomes the proceeds.")
    note = fields.Char(required=True)
    book_value = fields.Monetary(related='asset_id.book_value')

    @api.onchange('invoice_id')
    def _onchange_invoice(self):
        if self.invoice_id:
            self.proceeds = self.invoice_id.amount_untaxed

    def _final_depreciation(self, asset):
        """Depreciation from the last posted line to the disposal date, prorated."""
        pending = asset.line_ids.filtered(lambda l: l.state == 'draft').sorted('date')
        due = pending.filtered(lambda l: l.date <= self.date)
        due.action_post()
        rest = pending - due
        if rest:
            current = rest[0]
            start = period_start(current.date, asset.period)
            if self.date >= start:
                days = (period_end(current.date, asset.period) - start).days + 1
                share = ((self.date - start).days + 1) / days
                amount = float_round(current.amount * share, precision_rounding=asset.currency_id.rounding)
                if amount > 0:
                    current.write({'date': self.date, 'amount': amount,
                                   'name': _('%s — depreciation to disposal', asset.name)})
                    current.action_post()
                    rest -= current
            rest.write({'state': 'skipped'})

    def action_apply(self):
        self.ensure_one()
        asset = self.asset_id
        if asset.state not in ('running', 'paused', 'closed'):
            raise UserError(_("Only a running, paused or fully depreciated asset can be disposed of."))
        if self.invoice_id:
            self.proceeds = self.invoice_id.amount_untaxed
        if asset.state == 'paused':
            asset.write({'state': 'running', 'paused_on': False})
        self._final_depreciation(asset)
        cat = asset.category_id
        book = asset.book_value
        gain_loss = (self.proceeds or 0.0) - book
        loss_account = cat.loss_account_id or asset.expense_account_id
        gain_account = cat.gain_account_id or loss_account
        label = _('%(asset)s — %(kind)s', asset=asset.name, kind=dict(self._fields['kind'].selection)[self.kind])
        lines = [
            (0, 0, {'name': label, 'account_id': asset.asset_account_id.id, 'credit': asset.purchase_value, 'debit': 0.0}),
        ]
        if asset.depreciated_value:
            lines.append((0, 0, {'name': label, 'account_id': asset.depreciation_account_id.id,
                                 'debit': asset.depreciated_value, 'credit': 0.0}))
        if book > 0:
            lines.append((0, 0, {'name': _('%s — net book value written off', asset.name), 'account_id': loss_account.id,
                                 'debit': book, 'credit': 0.0, 'analytic_distribution': asset.analytic_distribution or False}))
        elif book < 0:
            lines.append((0, 0, {'name': label, 'account_id': gain_account.id, 'debit': 0.0, 'credit': -book}))
        move = self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': asset.journal_id.id, 'date': self.date, 'ref': label,
            'ebshel_asset_id': asset.id, 'company_id': asset.company_id.id, 'line_ids': lines})
        move.action_post()
        self.env['ebshel.asset.line'].create({
            'asset_id': asset.id, 'date': self.date, 'kind': 'disposal', 'amount': book, 'state': 'posted',
            'move_id': move.id, 'name': label, 'sequence': len(asset.line_ids) + 1,
            'cumulative': asset.depreciated_value, 'remaining': 0.0})
        asset.write({'state': 'disposed', 'disposal_date': self.date, 'disposal_kind': self.kind,
                     'disposal_value': self.proceeds or 0.0, 'disposal_move_id': move.id,
                     'disposal_invoice_id': self.invoice_id.id, 'gain_loss': gain_loss})
        asset._log('disposed', note='%s — %s' % (dict(self._fields['kind'].selection)[self.kind], self.note),
                   amount=gain_loss, day=self.date)
        return {'type': 'ir.actions.act_window_close'}
