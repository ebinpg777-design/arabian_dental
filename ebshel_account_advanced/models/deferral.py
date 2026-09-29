# -*- coding: utf-8 -*-
"""Deferrals: revenue and expenses recognised over the period they cover,
straight from the invoice line that carries the dates."""
import logging
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.modules import module as odoo_module
from odoo.tools import date_utils, float_round

_logger = logging.getLogger(__name__)


class Deferral(models.Model):
    _name = 'ebshel.deferral'
    _inherit = ['mail.thread', 'analytic.mixin']
    _description = 'Deferral'
    _order = 'date_from desc, id desc'

    name = fields.Char(required=True)
    code = fields.Char(readonly=True, copy=False, default=lambda self: _('New'))
    kind = fields.Selection([('revenue', 'Deferred revenue'), ('expense', 'Prepaid expense')], required=True, default='revenue')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    move_id = fields.Many2one('account.move', string='Invoice', ondelete='set null', index=True, copy=False)
    move_line_id = fields.Many2one('account.move.line', string='Invoice line', ondelete='set null', copy=False)
    partner_id = fields.Many2one('res.partner')
    date_from = fields.Date('Service starts', required=True)
    date_to = fields.Date('Service ends', required=True)
    total = fields.Monetary(required=True)
    method = fields.Selection([('days', 'By days'), ('months', 'Equal months')], required=True,
                              default=lambda self: self.env.company.ebshel_deferral_method or 'days')
    journal_id = fields.Many2one('account.journal', domain="[('type', '=', 'general')]", check_company=True)
    deferral_account_id = fields.Many2one('account.account', string='Balance sheet account', check_company=True)
    pl_account_id = fields.Many2one('account.account', string='Profit and loss account', required=True, check_company=True)
    state = fields.Selection([('draft', 'Draft'), ('running', 'Running'), ('done', 'Done'), ('cancelled', 'Cancelled')],
                             default='draft', tracking=True, copy=False)
    line_ids = fields.One2many('ebshel.deferral.line', 'deferral_id', copy=False)
    reclass_move_id = fields.Many2one('account.move', string='Deferral entry', copy=False, readonly=True)
    recognised = fields.Monetary(compute='_compute_progress', string='Recognised')
    remaining = fields.Monetary(compute='_compute_progress')
    progress_pct = fields.Float(compute='_compute_progress', string='Progress %')
    next_date = fields.Date(compute='_compute_progress')
    line_count = fields.Integer(compute='_compute_progress')
    move_count = fields.Integer(compute='_compute_progress')

    _dates_ok = models.Constraint('CHECK(date_to >= date_from)', 'The service must end after it starts.')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('code') or vals['code'] == _('New'):
                vals['code'] = self.env['ir.sequence'].next_by_code('ebshel.deferral') or _('New')
        return super().create(vals_list)

    @api.depends('line_ids.state', 'line_ids.amount', 'total')
    def _compute_progress(self):
        for d in self:
            posted = d.line_ids.filtered(lambda l: l.state == 'posted')
            d.recognised = sum(posted.mapped('amount'))
            d.remaining = d.total - d.recognised
            d.progress_pct = (d.recognised / d.total * 100.0) if d.total else 0.0
            drafts = d.line_ids.filtered(lambda l: l.state == 'draft').sorted('date')
            d.next_date = drafts[0].date if drafts else False
            d.line_count = len(d.line_ids)
            d.move_count = len(d._moves())

    def _moves(self):
        return self.env['account.move'].search([('ebshel_deferral_id', 'in', self.ids)])

    # ------------------------------------------------------------------ schedule
    def _slices(self):
        """[(from, to, amount)] one per calendar month touched, summing to the total."""
        self.ensure_one()
        months, start = [], self.date_from
        while start <= self.date_to:
            end = min(date_utils.end_of(start, 'month'), self.date_to)
            months.append((start, end))
            start = end + timedelta(days=1)
        total_days = (self.date_to - self.date_from).days + 1
        if self.method == 'days':
            raw = [self.total * ((b - a).days + 1) / total_days for a, b in months]
        else:
            raw = [self.total / len(months)] * len(months)
        rounding = self.currency_id.rounding or 0.01
        rounded = [float_round(x, precision_rounding=rounding) for x in raw[:-1]]
        amounts = rounded + [float_round(self.total - sum(rounded), precision_rounding=rounding)]
        return [(a, b, amt) for (a, b), amt in zip(months, amounts)]

    def build_schedule(self):
        for d in self:
            if d.line_ids.filtered(lambda l: l.state == 'posted'):
                raise UserError(_("%s already has posted lines; cancel it to start again.", d.code))
            d.line_ids.unlink()
            slices = d._slices()
            d.write({'line_ids': [(0, 0, {'sequence': i + 1, 'date': b, 'date_from': a, 'date_to': b, 'amount': amt,
                                          'name': _('%(what)s %(i)d/%(n)d · %(month)s', what=d.name, i=i + 1, n=len(slices),
                                                    month=a.strftime('%b %Y'))})
                                  for i, (a, b, amt) in enumerate(slices)]})
        return True

    # ------------------------------------------------------------------ accounts
    def _accounts(self):
        self.ensure_one()
        company = self.company_id
        balance = self.deferral_account_id or (company.ebshel_deferred_revenue_account_id if self.kind == 'revenue'
                                               else company.ebshel_deferred_expense_account_id)
        journal = self.journal_id or company.ebshel_deferral_journal_id or self.env['account.journal'].search(
            [('type', '=', 'general'), ('company_id', '=', company.id)], limit=1)
        if not balance:
            raise UserError(_("Set the %s account in Accounting → Configuration → Settings → Deferrals.",
                              _('deferred revenue') if self.kind == 'revenue' else _('prepaid expense')))
        if not journal:
            raise UserError(_("No miscellaneous journal to post deferrals in."))
        return balance, journal

    def _entry_vals(self, date, amount, ref, from_pl):
        """A balanced entry moving `amount` between the P&L account and the balance sheet one.
        from_pl=True takes it OFF the P&L (the deferral itself); False recognises it."""
        self.ensure_one()
        balance, journal = self._accounts()
        # revenue sits on the credit side of the P&L: taking it off means debiting the income account
        pl_debit = amount if (self.kind == 'revenue') == from_pl else -amount
        lines = [
            {'account_id': self.pl_account_id.id, 'partner_id': self.partner_id.id, 'name': ref,
             'debit': max(pl_debit, 0.0), 'credit': max(-pl_debit, 0.0), 'analytic_distribution': self.analytic_distribution},
            {'account_id': balance.id, 'partner_id': self.partner_id.id, 'name': ref,
             'debit': max(-pl_debit, 0.0), 'credit': max(pl_debit, 0.0)},
        ]
        return {'move_type': 'entry', 'journal_id': journal.id, 'date': date, 'ref': ref,
                'ebshel_deferral_id': self.id, 'line_ids': [(0, 0, l) for l in lines]}

    # ------------------------------------------------------------------ lifecycle
    def action_run(self):
        """Post the deferral entry at the invoice date, build the months, recognise what is already due."""
        today = fields.Date.context_today(self)
        for d in self:
            if d.state != 'draft':
                continue
            if not d.line_ids:
                d.build_schedule()
            date = d.move_id.date if d.move_id and d.move_id.date else d.date_from
            move = self.env['account.move'].create(d._entry_vals(
                date, d.total, _('%(code)s: %(name)s deferred', code=d.code, name=d.name), from_pl=True))
            move.action_post()
            d.write({'state': 'running', 'reclass_move_id': move.id})
            d.line_ids.filtered(lambda l: l.date <= today).action_post()
        return True

    def action_post_due(self):
        today = fields.Date.context_today(self)
        self.line_ids.filtered(lambda l: l.state == 'draft' and l.date <= today).action_post()
        return True

    def action_cancel(self):
        """Reverse every posted entry and stop."""
        for d in self:
            posted = d._moves().filtered(lambda m: m.state == 'posted')
            if posted:
                reversals = posted._reverse_moves([{'date': fields.Date.context_today(self), 'ref': _('Cancel %s', d.code),
                                                    'ebshel_deferral_id': d.id} for _m in posted], cancel=True)
                reversals.filtered(lambda m: m.state == 'draft').action_post()
            d.line_ids.filtered(lambda l: l.state == 'draft').write({'state': 'skipped'})
            d.write({'state': 'cancelled'})
        return True

    def action_draft(self):
        for d in self:
            if d._moves():
                raise UserError(_("%s has entries; cancel it instead.", d.code))
            d.state = 'draft'
        return True

    def action_open_entries(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'account.move', 'name': _('Deferral entries'),
                'view_mode': 'list,form', 'domain': [('id', 'in', self._moves().ids)], 'context': {'create': False}}

    def _check_done(self):
        for d in self:
            if d.state == 'running' and d.line_ids and all(l.state in ('posted', 'skipped') for l in d.line_ids):
                d.state = 'done'

    @api.model
    def _cron_post_due(self):
        today = fields.Date.context_today(self)
        lines = self.env['ebshel.deferral.line'].search([('state', '=', 'draft'), ('date', '<=', today),
                                                          ('deferral_id.state', '=', 'running')], order='date, id')
        for line in lines:
            try:
                with self.env.cr.savepoint():
                    line.action_post()
            except Exception:                                          # noqa: BLE001
                _logger.exception("deferral %s: line of %s not posted", line.deferral_id.code, line.date)
            if not odoo_module.current_test:
                self.env.cr.commit()
        return True


class DeferralLine(models.Model):
    _name = 'ebshel.deferral.line'
    _description = 'Deferral month'
    _order = 'deferral_id, date, id'

    deferral_id = fields.Many2one('ebshel.deferral', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='deferral_id.currency_id')
    sequence = fields.Integer()
    name = fields.Char(required=True)
    date = fields.Date(required=True)
    date_from = fields.Date()
    date_to = fields.Date()
    amount = fields.Monetary(required=True)
    state = fields.Selection([('draft', 'To recognise'), ('posted', 'Recognised'), ('skipped', 'Skipped')], default='draft')
    move_id = fields.Many2one('account.move', readonly=True)

    def action_post(self):
        for line in self:
            if line.state != 'draft':
                continue
            d = line.deferral_id
            if d.state != 'running':
                raise UserError(_("Start %s before recognising its months.", d.code))
            move = self.env['account.move'].create(d._entry_vals(line.date, line.amount, '%s: %s' % (d.code, line.name), from_pl=False))
            move.action_post()
            line.write({'state': 'posted', 'move_id': move.id})
            d._check_done()
        return True

    def action_open_move(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'account.move', 'res_id': self.move_id.id, 'view_mode': 'form'}

    @api.ondelete(at_uninstall=False)
    def _no_delete_posted(self):
        if any(l.state == 'posted' for l in self):
            raise UserError(_("A recognised month cannot be deleted; cancel the deferral instead."))
