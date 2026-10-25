# -*- coding: utf-8 -*-
"""Modify a running asset: a longer or shorter life, a new salvage, a
revaluation (a gross-increase child) or an impairment (an entry that writes
value down). The posted past stays; the draft future is rebuilt."""
from odoo import api, fields, models, _
from odoo.exceptions import UserError

from ..models.asset import period_start, add_periods


class AssetChange(models.TransientModel):
    _name = 'ebshel.asset.change'
    _description = 'Modify an asset'

    asset_id = fields.Many2one('ebshel.asset', required=True)
    company_id = fields.Many2one(related='asset_id.company_id')
    currency_id = fields.Many2one(related='asset_id.currency_id')
    date = fields.Date(required=True, default=fields.Date.context_today,
                       help="The change applies from the period after this date.")
    kind = fields.Selection([('life', 'Change the remaining life or salvage'),
                             ('increase', 'Increase the value (gross increase)'),
                             ('decrease', 'Write value down (impairment)')], default='life', required=True)
    duration = fields.Integer(string='Remaining periods', help="Periods still to depreciate, from the change date.")
    salvage_value = fields.Monetary()
    amount = fields.Monetary(string='Amount')
    account_id = fields.Many2one(
        'account.account', string='Counterpart account', check_company=True,
        help="An increase debits the asset account; this is the account credited - the revaluation "
             "reserve, the supplier or the bank, whatever paid for or justifies the extra value.")
    note = fields.Char(required=True, help="Why - this goes on the asset's timeline.")
    current_book = fields.Monetary(related='asset_id.book_value')
    current_remaining_periods = fields.Integer(compute='_compute_current')

    @api.depends('asset_id')
    def _compute_current(self):
        for w in self:
            w.current_remaining_periods = len(w.asset_id.line_ids.filtered(lambda l: l.state == 'draft'))

    @api.onchange('asset_id')
    def _onchange_asset(self):
        if self.asset_id:
            self.salvage_value = self.asset_id.salvage_value
            self.duration = len(self.asset_id.line_ids.filtered(lambda l: l.state == 'draft')) or self.asset_id.duration

    def action_apply(self):
        self.ensure_one()
        asset = self.asset_id
        if asset.state not in ('running', 'paused'):
            raise UserError(_("Only a running or paused asset can be modified."))
        # a change dated before depreciation already posted rebuilt periods that were
        # posted - charged twice
        last_posted = max(asset.line_ids.filtered(lambda l: l.state == 'posted').mapped('date'), default=None)
        if last_posted and self.date < last_posted:
            raise UserError(_("%(asset)s has depreciation posted up to %(day)s: date the change on or after it.",
                              asset=asset.name, day=fields.Date.to_string(last_posted)))
        if self.kind == 'life':
            if self.duration < 1:
                raise UserError(_("Give at least one remaining period."))
            if self.salvage_value < 0 or self.salvage_value > asset.book_value:
                raise UserError(_("The salvage value must be between zero and the current book value."))
            done = len(asset.line_ids.filtered(lambda l: l.state == 'posted' and l.kind == 'depreciation'))
            from_date = period_start(add_periods(self.date, asset.period, 1), asset.period, asset.company_id)
            # the periods counted from the change date include the one it falls in, which stays
            # as planned (it is before from_date): the new schedule is the rest of them
            kept = len(asset.line_ids.filtered(lambda l: l.state == 'draft' and l.kind == 'depreciation'
                                               and l.date < from_date))
            asset.write({'salvage_value': self.salvage_value, 'duration': done + self.duration})
            asset.build_schedule(from_date=from_date, periods=self.duration - kept)
            asset._log('modified', note=self.note, day=self.date)
        elif self.kind == 'increase':
            if self.amount <= 0:
                raise UserError(_("An increase needs a positive amount."))
            if not self.account_id:
                raise UserError(_("Choose the counterpart account the increase is credited to."))
            remaining = len(asset.line_ids.filtered(lambda l: l.state == 'draft')) or 1
            child = asset.copy({
                'name': _('%s — increase %s', asset.name, fields.Date.to_string(self.date)),
                'parent_id': asset.id, 'purchase_value': self.amount, 'salvage_value': 0.0,
                'already_depreciated': 0.0, 'purchase_date': self.date, 'start_date': self.date,
                'duration': remaining, 'state': 'draft', 'bill_line_id': False, 'code': _('New'),
            })
            # the extra value goes on the books: the child depreciated a gross value that no
            # entry had ever debited to the asset account
            move = self.env['account.move'].create({
                'move_type': 'entry', 'journal_id': child.journal_id.id, 'date': self.date,
                'ref': child.name, 'ebshel_asset_id': child.id, 'company_id': child.company_id.id,
                'line_ids': [
                    (0, 0, {'name': child.name, 'account_id': child.asset_account_id.id,
                            'debit': self.amount, 'credit': 0.0}),
                    (0, 0, {'name': child.name, 'account_id': self.account_id.id,
                            'debit': 0.0, 'credit': self.amount}),
                ]})
            move.action_post()
            if move.date != self.date:                        # a locked period moved it on
                child.write({'purchase_date': move.date, 'start_date': move.date})
            child.action_confirm()
            asset._log('revalued', note=self.note, amount=self.amount, day=self.date)
        else:
            if self.amount <= 0 or self.amount > asset.remaining_value:
                raise UserError(_("The write-down must be positive and no more than what is still to depreciate."))
            line = self.env['ebshel.asset.line'].create({
                'asset_id': asset.id, 'date': self.date, 'kind': 'impairment', 'amount': self.amount,
                'name': _('%s — impairment: %s', asset.name, self.note),
                'sequence': len(asset.line_ids) + 1})
            line.action_post()
            asset.build_schedule(from_date=period_start(add_periods(self.date, asset.period, 1), asset.period,
                                                        asset.company_id))
            asset._log('impaired', note=self.note, amount=self.amount, day=self.date)
        asset._check_closed()
        return {'type': 'ir.actions.act_window_close'}
