# -*- coding: utf-8 -*-
"""An asset and its depreciation.

The schedule is a list of lines, each becoming one journal entry when posted:
debit the depreciation expense, credit the accumulated depreciation. The book
value is what the posted lines leave. Everything that happens to the asset -
confirmed, paused, modified, disposed - is an event, and the events are what
the lifecycle timeline draws.
"""
import calendar
import json
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

import logging

from odoo.modules import module as odoo_module
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import date_utils, float_compare, float_round

METHODS = [('linear', 'Straight line'), ('declining', 'Declining balance'),
           ('declining_linear', 'Declining, then straight line')]
PERIODS = [('month', 'Monthly'), ('year', 'Yearly')]
PRORATA = [('none', 'Full first period'), ('days', 'Prorated by days')]
STATES = [('draft', 'Draft'), ('running', 'Running'), ('paused', 'Paused'),
          ('closed', 'Fully depreciated'), ('disposed', 'Disposed'), ('cancelled', 'Cancelled')]
EVENTS = [('created', 'Created'), ('running', 'Confirmed'), ('paused', 'Paused'), ('resumed', 'Resumed'),
          ('modified', 'Modified'), ('revalued', 'Revalued'), ('impaired', 'Impaired'),
          ('posted', 'Depreciation posted'), ('disposed', 'Disposed'), ('closed', 'Fully depreciated'),
          ('reset', 'Reset to draft'), ('cancelled', 'Cancelled'), ('note', 'Note')]

_logger = logging.getLogger(__name__)


def period_end(day, period):
    return date_utils.end_of(day, 'month' if period == 'month' else 'year')


def period_start(day, period):
    return date_utils.start_of(day, 'month' if period == 'month' else 'year')


def add_periods(day, period, n):
    return day + (relativedelta(months=n) if period == 'month' else relativedelta(years=n))


class AssetGroup(models.Model):
    _name = 'ebshel.asset.group'
    _description = 'Asset group'
    _parent_name = 'parent_id'
    _parent_store = True
    _rec_name = 'complete_name'
    _order = 'complete_name'

    name = fields.Char(required=True, translate=True)
    parent_id = fields.Many2one('ebshel.asset.group', ondelete='cascade', index=True)
    parent_path = fields.Char(index=True)
    complete_name = fields.Char(compute='_compute_complete_name', store=True, recursive=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)

    @api.depends('name', 'parent_id.complete_name')
    def _compute_complete_name(self):
        for group in self:
            group.complete_name = '%s / %s' % (group.parent_id.complete_name, group.name) if group.parent_id else group.name


class AssetCategory(models.Model):
    _name = 'ebshel.asset.category'
    _inherit = ['analytic.mixin']
    _description = 'Asset category'
    _order = 'name'

    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)
    group_id = fields.Many2one('ebshel.asset.group', string='Group')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    asset_account_id = fields.Many2one(
        'account.account', string='Asset account', required=True, check_company=True,
        domain="[('account_type', 'in', ('asset_fixed', 'asset_non_current'))]",
        help="Where the purchase value sits (the gross value).")
    depreciation_account_id = fields.Many2one(
        'account.account', string='Accumulated depreciation account', required=True, check_company=True,
        domain="[('account_type', 'in', ('asset_fixed', 'asset_non_current'))]",
        help="Credited by every depreciation entry.")
    expense_account_id = fields.Many2one(
        'account.account', string='Depreciation expense account', required=True, check_company=True,
        domain="[('account_type', 'in', ('expense', 'expense_depreciation'))]")
    gain_account_id = fields.Many2one(
        'account.account', string='Gain on disposal account', check_company=True,
        domain="[('account_type', 'in', ('income', 'income_other'))]")
    loss_account_id = fields.Many2one(
        'account.account', string='Loss on disposal account', check_company=True,
        domain="[('account_type', 'in', ('expense', 'expense_depreciation'))]")
    journal_id = fields.Many2one('account.journal', required=True, check_company=True,
                                 domain="[('type', '=', 'general')]")
    method = fields.Selection(METHODS, default='linear', required=True)
    declining_rate = fields.Float(string='Declining rate (% a year)', default=30.0,
                                  help="Applied to the book value at the start of each year "
                                       "(a twelfth of it each month when the period is monthly).")
    period = fields.Selection(PERIODS, default='year', required=True)
    duration = fields.Integer(string='Duration (periods)', default=5, required=True)
    prorata = fields.Selection(PRORATA, default='days', required=True)
    salvage_pct = fields.Float(string='Salvage (% of value)', default=0.0)
    auto_post = fields.Boolean(string='Post automatically', default=True,
                               help="The nightly job posts each depreciation entry on its date.")
    bill_trigger = fields.Selection([('no', 'No'), ('draft', 'As a draft to review'), ('running', 'Confirmed and running')],
                                    string='Create from vendor bills', default='draft', required=True,
                                    help="What happens when a vendor bill line lands on one of the trigger accounts.")
    trigger_account_ids = fields.Many2many('account.account', 'ebshel_asset_category_trigger_rel', 'category_id', 'account_id',
                                           string='Trigger accounts', check_company=True,
                                           help="Bill lines on these accounts become assets of this category. "
                                                "Usually the asset account itself.")
    one_per_unit = fields.Boolean(string='One asset per unit', default=False,
                                  help="A bill line for 4 chairs makes 4 assets rather than one.")
    asset_count = fields.Integer(compute='_compute_asset_count')
    note = fields.Text()

    @api.constrains('duration', 'declining_rate', 'salvage_pct')
    def _check_numbers(self):
        for cat in self:
            if cat.duration < 1:
                raise ValidationError(_("A category needs at least one period."))
            if cat.method != 'linear' and not (0 < cat.declining_rate <= 100):
                raise ValidationError(_("The declining rate must be between 0 and 100."))
            if not (0 <= cat.salvage_pct < 100):
                raise ValidationError(_("The salvage percentage must be between 0 and 100."))

    def _compute_asset_count(self):
        counts = {r['category_id'][0]: r['category_id_count'] for r in self.env['ebshel.asset'].read_group(
            [('category_id', 'in', self.ids)], ['category_id'], ['category_id'])} if self.ids else {}
        for cat in self:
            cat.asset_count = counts.get(cat.id, 0)

    def action_open_assets(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': self.name, 'res_model': 'ebshel.asset',
                'view_mode': 'list,kanban,form', 'domain': [('category_id', '=', self.id)],
                'context': {'default_category_id': self.id}}

    @api.onchange('asset_account_id')
    def _onchange_asset_account(self):
        if self.asset_account_id and not self.trigger_account_ids:
            self.trigger_account_ids = self.asset_account_id


class Asset(models.Model):
    _name = 'ebshel.asset'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'analytic.mixin']
    _description = 'Asset'
    _order = 'purchase_date desc, id desc'
    _check_company_auto = True

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(readonly=True, copy=False, default=lambda self: _('New'))
    active = fields.Boolean(default=True)
    state = fields.Selection(STATES, default='draft', required=True, tracking=True, copy=False)
    category_id = fields.Many2one('ebshel.asset.category', required=True, tracking=True, check_company=True)
    group_id = fields.Many2one(related='category_id.group_id', store=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    user_id = fields.Many2one('res.users', string='Responsible', default=lambda self: self.env.user, tracking=True)
    partner_id = fields.Many2one('res.partner', string='Supplier', tracking=True)
    bill_line_id = fields.Many2one('account.move.line', string='Bill line', readonly=True, copy=False)
    bill_id = fields.Many2one('account.move', string='Vendor bill', related='bill_line_id.move_id', store=True)
    purchase_date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    start_date = fields.Date(string='First depreciation', required=True, tracking=True,
                             help="The period this date falls in is the first one depreciated.")
    purchase_value = fields.Monetary(required=True, tracking=True)
    salvage_value = fields.Monetary(tracking=True, help="What it will still be worth at the end: never depreciated.")
    already_depreciated = fields.Monetary(string='Depreciated before', tracking=True,
                                          help="For an asset brought in from another system: depreciation already "
                                               "booked, which the schedule starts after.")
    depreciable_value = fields.Monetary(compute='_compute_values', store=True)
    depreciated_value = fields.Monetary(compute='_compute_values', store=True, string='Depreciated to date')
    book_value = fields.Monetary(compute='_compute_values', store=True)
    remaining_value = fields.Monetary(compute='_compute_values', store=True, string='Still to depreciate')
    method = fields.Selection(METHODS, required=True, default='linear', tracking=True)
    declining_rate = fields.Float(string='Declining rate (% a year)', default=30.0)
    period = fields.Selection(PERIODS, required=True, default='year')
    duration = fields.Integer(string='Duration (periods)', required=True, default=5, tracking=True)
    prorata = fields.Selection(PRORATA, required=True, default='days')
    asset_account_id = fields.Many2one('account.account', string='Asset account', check_company=True)
    depreciation_account_id = fields.Many2one('account.account', string='Accumulated depreciation', check_company=True)
    expense_account_id = fields.Many2one('account.account', string='Depreciation expense', check_company=True)
    journal_id = fields.Many2one('account.journal', check_company=True)
    line_ids = fields.One2many('ebshel.asset.line', 'asset_id', string='Schedule', copy=False)
    event_ids = fields.One2many('ebshel.asset.event', 'asset_id', string='Events', copy=False)
    move_ids = fields.One2many('account.move', 'ebshel_asset_id', string='Journal entries', copy=False)
    entry_count = fields.Integer(compute='_compute_counts')
    posted_count = fields.Integer(compute='_compute_counts')
    line_count = fields.Integer(compute='_compute_counts')
    parent_id = fields.Many2one('ebshel.asset', string='Increases', readonly=True, copy=False,
                                help="The asset this one is a gross increase of.")
    child_ids = fields.One2many('ebshel.asset', 'parent_id', string='Gross increases')
    child_count = fields.Integer(compute='_compute_counts')
    # custody and cover
    custodian_id = fields.Many2one('res.partner', string='Held by', tracking=True)
    location = fields.Char(tracking=True)
    serial = fields.Char(string='Serial / tag no.')
    warranty_end = fields.Date(tracking=True)
    insurance_end = fields.Date(tracking=True)
    image_1920 = fields.Image(string='Photo', max_width=1920, max_height=1920)
    image_128 = fields.Image(string='Thumbnail', related='image_1920', max_width=128, max_height=128, store=True)
    notes = fields.Html()
    # pause & disposal
    paused_on = fields.Date(readonly=True, copy=False)
    paused_days = fields.Integer(readonly=True, copy=False)
    disposal_date = fields.Date(readonly=True, copy=False)
    disposal_kind = fields.Selection([('sale', 'Sold'), ('scrap', 'Scrapped'), ('donation', 'Donated'), ('lost', 'Lost or stolen')],
                                     readonly=True, copy=False)
    disposal_value = fields.Monetary(string='Proceeds', readonly=True, copy=False)
    disposal_move_id = fields.Many2one('account.move', readonly=True, copy=False)
    disposal_invoice_id = fields.Many2one('account.move', readonly=True, copy=False, string='Sale invoice')
    gain_loss = fields.Monetary(string='Gain (loss) on disposal', readonly=True, copy=False)
    lifecycle_json = fields.Json(compute='_compute_lifecycle')
    next_line_date = fields.Date(compute='_compute_next', string='Next depreciation')
    cover_state = fields.Selection([('ok', 'Covered'), ('soon', 'Expiring soon'), ('expired', 'Expired'), ('none', 'No cover')],
                                   compute='_compute_cover', search='_search_cover')

    # ------------------------------------------------------------------ computes
    @api.depends('purchase_value', 'salvage_value', 'already_depreciated', 'line_ids.state', 'line_ids.amount', 'line_ids.kind')
    def _compute_values(self):
        for asset in self:
            posted = sum(asset.line_ids.filtered(lambda l: l.state == 'posted' and l.kind != 'disposal').mapped('amount'))
            asset.depreciable_value = asset.purchase_value - asset.salvage_value - asset.already_depreciated
            asset.depreciated_value = asset.already_depreciated + posted
            asset.book_value = asset.purchase_value - asset.depreciated_value
            asset.remaining_value = max(asset.book_value - asset.salvage_value, 0.0)

    @api.depends('line_ids', 'line_ids.state', 'move_ids', 'child_ids')
    def _compute_counts(self):
        for asset in self:
            asset.line_count = len(asset.line_ids)
            asset.posted_count = len(asset.line_ids.filtered(lambda l: l.state == 'posted'))
            asset.entry_count = len(asset.move_ids)
            asset.child_count = len(asset.child_ids)

    @api.depends('line_ids.date', 'line_ids.state')
    def _compute_next(self):
        for asset in self:
            pending = asset.line_ids.filtered(lambda l: l.state == 'draft').sorted('date')
            asset.next_line_date = pending[0].date if pending else False

    @api.depends('warranty_end', 'insurance_end')
    def _compute_cover(self):
        today = fields.Date.context_today(self)
        soon = today + timedelta(days=60)
        for asset in self:
            dates = [d for d in (asset.warranty_end, asset.insurance_end) if d]
            if not dates:
                asset.cover_state = 'none'
            elif min(dates) < today:
                asset.cover_state = 'expired'
            elif min(dates) <= soon:
                asset.cover_state = 'soon'
            else:
                asset.cover_state = 'ok'

    def _search_cover(self, operator, value):
        today = fields.Date.context_today(self)
        soon = today + timedelta(days=60)
        if value == 'none':
            dom = [('warranty_end', '=', False), ('insurance_end', '=', False)]
        elif value == 'expired':
            dom = ['|', ('warranty_end', '<', today), ('insurance_end', '<', today)]
        elif value == 'soon':
            dom = ['|', '&', ('warranty_end', '>=', today), ('warranty_end', '<=', soon),
                   '&', ('insurance_end', '>=', today), ('insurance_end', '<=', soon)]
        else:
            dom = ['|', ('warranty_end', '>', soon), ('insurance_end', '>', soon)]
        if operator in ('!=', 'not in'):
            return ['!'] + dom
        return dom

    @api.depends('event_ids', 'line_ids.state', 'line_ids.amount', 'line_ids.date', 'purchase_value')
    def _compute_lifecycle(self):
        for asset in self:
            asset.lifecycle_json = asset._lifecycle()

    def _lifecycle(self):
        """Events and the depreciation curve, for the timeline widget."""
        self.ensure_one()
        events = [{'date': fields.Date.to_string(e.date), 'kind': e.kind, 'label': dict(EVENTS).get(e.kind, e.kind),
                   'note': e.note or '', 'amount': e.amount, 'user': e.user_id.name or ''}
                  for e in self.event_ids.sorted('date')]
        curve, book = [], self.purchase_value - self.already_depreciated
        curve.append({'date': fields.Date.to_string(self.start_date or self.purchase_date), 'book': book, 'amount': 0.0, 'posted': True})
        for line in self.line_ids.sorted(lambda l: (l.date, l.id)):
            if line.state == 'skipped':
                continue
            book -= line.amount if line.kind != 'disposal' else 0.0
            curve.append({'date': fields.Date.to_string(line.date), 'book': round(book, 2), 'amount': line.amount,
                          'posted': line.state == 'posted', 'kind': line.kind})
        return {'events': events, 'curve': curve, 'purchase_value': self.purchase_value,
                'salvage': self.salvage_value, 'state': self.state,
                'currency': self.currency_id.symbol or ''}

    # ------------------------------------------------------------------ defaults
    @api.onchange('category_id')
    def _onchange_category(self):
        cat = self.category_id
        if not cat:
            return
        self.method = cat.method
        self.declining_rate = cat.declining_rate
        self.period = cat.period
        self.duration = cat.duration
        self.prorata = cat.prorata
        self.asset_account_id = cat.asset_account_id
        self.depreciation_account_id = cat.depreciation_account_id
        self.expense_account_id = cat.expense_account_id
        self.journal_id = cat.journal_id
        if cat.analytic_distribution and not self.analytic_distribution:
            self.analytic_distribution = cat.analytic_distribution
        if cat.salvage_pct and self.purchase_value:
            self.salvage_value = float_round(self.purchase_value * cat.salvage_pct / 100.0, 2)

    @api.onchange('purchase_date')
    def _onchange_purchase_date(self):
        if self.purchase_date and not self.start_date:
            self.start_date = self.purchase_date

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('code') or vals['code'] == _('New'):
                vals['code'] = self.env['ir.sequence'].next_by_code('ebshel.asset') or _('New')
            if vals.get('category_id'):
                cat = self.env['ebshel.asset.category'].browse(vals['category_id'])
                for field, src in (('method', 'method'), ('declining_rate', 'declining_rate'), ('period', 'period'),
                                   ('duration', 'duration'), ('prorata', 'prorata'), ('asset_account_id', 'asset_account_id'),
                                   ('depreciation_account_id', 'depreciation_account_id'),
                                   ('expense_account_id', 'expense_account_id'), ('journal_id', 'journal_id')):
                    if field not in vals or vals[field] in (None, False):
                        value = cat[src]
                        vals[field] = value.id if hasattr(value, 'id') else value
                if not vals.get('analytic_distribution') and cat.analytic_distribution:
                    vals['analytic_distribution'] = cat.analytic_distribution
            if not vals.get('start_date'):
                vals['start_date'] = vals.get('purchase_date') or fields.Date.context_today(self)
        assets = super().create(vals_list)
        for asset in assets:
            asset._log('created', note=_('Created with a value of %s', asset._money(asset.purchase_value)))
        return assets

    def _money(self, amount):
        return self.currency_id.format(amount) if hasattr(self.currency_id, 'format') else '%.2f' % amount

    def _log(self, kind, note='', amount=0.0, day=None):
        self.env['ebshel.asset.event'].sudo().create({
            'asset_id': self.id, 'kind': kind, 'note': note, 'amount': amount,
            'date': day or fields.Date.context_today(self), 'user_id': self.env.uid})

    @api.constrains('purchase_value', 'salvage_value', 'already_depreciated', 'duration', 'declining_rate')
    def _check_values(self):
        for asset in self:
            if asset.purchase_value < 0:
                raise ValidationError(_("An asset's value cannot be negative."))
            if asset.salvage_value < 0 or asset.salvage_value > asset.purchase_value:
                raise ValidationError(_("The salvage value must be between zero and the purchase value."))
            if asset.already_depreciated < 0 or asset.already_depreciated > asset.purchase_value - asset.salvage_value:
                raise ValidationError(_("'Depreciated before' cannot exceed what there is to depreciate."))
            if asset.duration < 1:
                raise ValidationError(_("The duration must be at least one period."))
            if asset.method != 'linear' and not (0 < asset.declining_rate <= 100):
                raise ValidationError(_("The declining rate must be between 0 and 100."))

    # ------------------------------------------------------------------ the schedule
    def _schedule_amounts(self, total, start, periods, method, rate, period, prorata, first_date=None):
        """[(date, amount)] that depreciate `total` from `start` over `periods`.

        Straight line splits evenly; declining takes a share of what is left each
        period; declining-then-straight takes whichever is larger. The first
        period is prorated by days when asked, the last carries the rounding.
        """
        if total <= 0 or periods < 1:
            return []
        rounding = self.currency_id.rounding or 0.01
        first_end = period_end(start, period)
        days_in_first = (first_end - period_start(start, period)).days + 1
        first_share = ((first_end - start).days + 1) / days_in_first if prorata == 'days' else 1.0
        per_rate = rate / 100.0 * (1.0 if period == 'year' else 1.0 / 12.0)
        out, left, day, n = [], total, first_end, 0
        # With a prorated first period the schedule runs one period longer, the tail
        # carrying what the first one did not take.
        slots = periods + (1 if first_share < 1.0 and prorata == 'days' else 0)
        while n < slots and left > rounding / 2:
            remaining_slots = slots - n
            if method == 'linear':
                amount = total / periods
            else:
                declining = left * per_rate
                linear = left / remaining_slots
                amount = max(declining, linear) if method == 'declining_linear' else declining
            if n == 0:
                amount *= first_share
            if n == slots - 1 or amount > left:
                amount = left
            amount = float_round(amount, precision_rounding=rounding)
            if n == slots - 1:
                amount = float_round(left, precision_rounding=rounding)
            out.append((day, amount))
            left -= amount
            day = period_end(add_periods(day, period, 1), period)
            n += 1
        if left > rounding / 2 and out:
            d, a = out[-1]
            out[-1] = (d, float_round(a + left, precision_rounding=rounding))
        return out

    def build_schedule(self, from_date=None):
        """(Re)build the draft lines. Posted lines stay; the rest is replaced by a
        schedule for what is still to depreciate, from `from_date` (default: the
        start date, or the period after the last posted line)."""
        for asset in self:
            posted = asset.line_ids.filtered(lambda l: l.state == 'posted')
            asset.line_ids.filtered(lambda l: l.state == 'draft').unlink()
            total = asset.purchase_value - asset.salvage_value - asset.already_depreciated \
                - sum(posted.filtered(lambda l: l.kind != 'disposal').mapped('amount'))
            if total <= 0:
                continue
            done = len(posted.filtered(lambda l: l.kind == 'depreciation'))
            periods = max(asset.duration - done, 1)
            if from_date:
                start = from_date
            elif posted:
                start = period_start(add_periods(max(posted.mapped('date')), asset.period, 1), asset.period)
            else:
                start = asset.start_date
            prorata = asset.prorata if not posted and not from_date else 'none'
            if from_date and from_date != period_start(from_date, asset.period):
                prorata = 'days'
            rows = asset._schedule_amounts(total, start, periods, asset.method, asset.declining_rate,
                                           asset.period, prorata)
            seq = len(posted)
            cumulative = asset.already_depreciated + sum(posted.filtered(lambda l: l.kind != 'disposal').mapped('amount'))
            vals = []
            for day, amount in rows:
                seq += 1
                cumulative += amount
                vals.append({'asset_id': asset.id, 'sequence': seq, 'date': day, 'amount': amount,
                             'cumulative': cumulative, 'remaining': asset.purchase_value - cumulative,
                             'name': _('%(asset)s — depreciation %(n)s', asset=asset.name, n=seq)})
            self.env['ebshel.asset.line'].create(vals)
        return True

    # ------------------------------------------------------------------ lifecycle actions
    def action_confirm(self):
        for asset in self:
            if asset.state != 'draft':
                raise UserError(_("Only a draft asset can be confirmed."))
            for field in ('asset_account_id', 'depreciation_account_id', 'expense_account_id', 'journal_id'):
                if not asset[field]:
                    raise UserError(_("%s: set the accounts and the journal before confirming.", asset.name))
            asset.build_schedule()
            asset.state = 'running'
            asset._log('running', note=_('Depreciation starts %s', fields.Date.to_string(asset.start_date)))
            asset._check_closed()
        return True

    def action_reset_draft(self):
        for asset in self:
            if asset.line_ids.filtered(lambda l: l.state == 'posted'):
                raise UserError(_("%s has posted depreciation; it cannot go back to draft.", asset.name))
            asset.line_ids.unlink()
            asset.write({'state': 'draft', 'paused_on': False})
            asset._log('reset')
        return True

    def action_pause(self):
        for asset in self:
            if asset.state != 'running':
                raise UserError(_("Only a running asset can be paused."))
            asset.write({'state': 'paused', 'paused_on': fields.Date.context_today(self)})
            asset._log('paused')
        return True

    def action_resume(self):
        today = fields.Date.context_today(self)
        for asset in self:
            if asset.state != 'paused':
                raise UserError(_("Only a paused asset can be resumed."))
            days = (today - asset.paused_on).days if asset.paused_on else 0
            for line in asset.line_ids.filtered(lambda l: l.state == 'draft'):
                line.date = period_end(line.date + timedelta(days=days), asset.period)
            asset.write({'state': 'running', 'paused_on': False, 'paused_days': asset.paused_days + days})
            asset._log('resumed', note=_('Paused for %s days; the schedule moved on by as much.', days))
        return True

    def action_cancel(self):
        for asset in self:
            if asset.line_ids.filtered(lambda l: l.state == 'posted'):
                raise UserError(_("%s has posted depreciation; dispose of it instead.", asset.name))
            asset.line_ids.unlink()
            asset.state = 'cancelled'
            asset._log('cancelled')
        return True

    def _check_closed(self):
        for asset in self:
            if asset.state == 'running' and not asset.line_ids.filtered(lambda l: l.state == 'draft') \
                    and float_compare(asset.remaining_value, 0.0, precision_rounding=asset.currency_id.rounding) <= 0:
                asset.state = 'closed'
                asset._log('closed')

    def action_post_due(self):
        """Post every draft line dated today or earlier."""
        today = fields.Date.context_today(self)
        for asset in self.filtered(lambda a: a.state == 'running'):
            asset.line_ids.filtered(lambda l: l.state == 'draft' and l.date <= today).sorted('date').action_post()
        return True

    def action_open_change(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _('Modify %s', self.name), 'res_model': 'ebshel.asset.change',
                'view_mode': 'form', 'target': 'new', 'context': {'default_asset_id': self.id}}

    def action_open_dispose(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _('Dispose of %s', self.name), 'res_model': 'ebshel.asset.dispose',
                'view_mode': 'form', 'target': 'new', 'context': {'default_asset_id': self.id}}

    def action_open_entries(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _('Journal entries'), 'res_model': 'account.move',
                'view_mode': 'list,form', 'domain': [('ebshel_asset_id', '=', self.id)], 'context': {'create': False}}

    def action_open_children(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _('Gross increases'), 'res_model': 'ebshel.asset',
                'view_mode': 'list,form', 'domain': [('parent_id', '=', self.id)],
                'context': {'default_parent_id': self.id, 'default_category_id': self.category_id.id}}

    def action_print_label(self):
        return self.env.ref('ebshel_account_assets.action_report_asset_label').report_action(self)

    # ------------------------------------------------------------------ crons
    @api.model
    def _cron_post_due(self):
        today = fields.Date.context_today(self)
        lines = self.env['ebshel.asset.line'].search([
            ('state', '=', 'draft'), ('date', '<=', today), ('asset_id.state', '=', 'running'),
            ('asset_id.category_id.auto_post', '=', True)], order='date, id')
        for line in lines:
            try:
                with self.env.cr.savepoint():
                    line.action_post()
            except Exception:                                          # noqa: BLE001
                _logger.exception("asset %s: depreciation of %s not posted", line.asset_id.code, line.date)
                line.asset_id.message_post(body=_("The depreciation of %s could not be posted automatically; "
                                                  "post it by hand.", fields.Date.to_string(line.date)))
            if not odoo_module.current_test:                  # one line per transaction outside tests
                self.env.cr.commit()
        return True

    @api.model
    def _cron_cover_reminders(self):
        today = fields.Date.context_today(self)
        soon = today + timedelta(days=30)
        for asset in self.search([('state', 'in', ('running', 'paused')), '|',
                                  '&', ('warranty_end', '>=', today), ('warranty_end', '<=', soon),
                                  '&', ('insurance_end', '>=', today), ('insurance_end', '<=', soon)]):
            what = []
            if asset.warranty_end and today <= asset.warranty_end <= soon:
                what.append(_('warranty ends %s', fields.Date.to_string(asset.warranty_end)))
            if asset.insurance_end and today <= asset.insurance_end <= soon:
                what.append(_('insurance ends %s', fields.Date.to_string(asset.insurance_end)))
            summary = '%s: %s' % (asset.name, '; '.join(what))
            if not asset.activity_ids.filtered(lambda a: a.summary == summary):
                asset.activity_schedule('mail.mail_activity_data_todo', date_deadline=min(
                    d for d in (asset.warranty_end, asset.insurance_end) if d and d >= today),
                    summary=summary, user_id=asset.user_id.id or self.env.uid)
        return True


class AssetLine(models.Model):
    _name = 'ebshel.asset.line'
    _description = 'Depreciation line'
    _order = 'date, sequence, id'

    asset_id = fields.Many2one('ebshel.asset', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='asset_id.company_id', store=True)
    currency_id = fields.Many2one(related='asset_id.currency_id')
    sequence = fields.Integer(default=1)
    name = fields.Char()
    date = fields.Date(required=True)
    kind = fields.Selection([('depreciation', 'Depreciation'), ('impairment', 'Impairment'), ('disposal', 'Disposal')],
                            default='depreciation', required=True)
    amount = fields.Monetary(required=True)
    cumulative = fields.Monetary(string='Depreciated after')
    remaining = fields.Monetary(string='Book value after')
    state = fields.Selection([('draft', 'To post'), ('posted', 'Posted'), ('skipped', 'Skipped')],
                             default='draft', required=True)
    move_id = fields.Many2one('account.move', readonly=True, copy=False)

    def _move_vals(self):
        self.ensure_one()
        asset = self.asset_id
        label = self.name or _('%(asset)s — %(kind)s', asset=asset.name, kind=dict(self._fields['kind'].selection)[self.kind])
        expense = asset.expense_account_id
        if self.kind == 'impairment' and asset.category_id.loss_account_id:
            expense = asset.category_id.loss_account_id
        return {
            'move_type': 'entry', 'journal_id': asset.journal_id.id, 'date': self.date, 'ref': label,
            'ebshel_asset_id': asset.id, 'company_id': asset.company_id.id,
            'line_ids': [
                (0, 0, {'name': label, 'account_id': expense.id, 'debit': self.amount, 'credit': 0.0,
                        'partner_id': asset.partner_id.id,
                        'analytic_distribution': asset.analytic_distribution or False}),
                (0, 0, {'name': label, 'account_id': asset.depreciation_account_id.id, 'debit': 0.0,
                        'credit': self.amount, 'partner_id': asset.partner_id.id}),
            ],
        }

    def action_post(self):
        for line in self.sorted(lambda l: (l.date, l.id)):
            if line.state != 'draft':
                continue
            if line.asset_id.state not in ('running', 'disposed') and line.kind == 'depreciation':
                raise UserError(_("%s is not running.", line.asset_id.name))
            move = self.env['account.move'].create(line._move_vals())
            move.action_post()
            line.write({'state': 'posted', 'move_id': move.id})
            line.asset_id._log('posted', note=_('%s, dated %s', line.name or _('Depreciation'), fields.Date.to_string(line.date)), amount=line.amount)
        for asset in self.mapped('asset_id'):
            asset._check_closed()
        return True

    def action_open_move(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'account.move', 'res_id': self.move_id.id,
                'view_mode': 'form', 'target': 'current'}

    def unlink(self):
        if self.filtered(lambda l: l.state == 'posted'):
            raise UserError(_("A posted depreciation line cannot be deleted; reverse its entry instead."))
        return super().unlink()


class AssetEvent(models.Model):
    _name = 'ebshel.asset.event'
    _description = 'Asset event'
    _order = 'date, id'

    asset_id = fields.Many2one('ebshel.asset', required=True, ondelete='cascade', index=True)
    date = fields.Date(required=True, default=fields.Date.context_today)
    kind = fields.Selection(EVENTS, required=True)
    note = fields.Char()
    amount = fields.Monetary(currency_field='currency_id')
    currency_id = fields.Many2one(related='asset_id.currency_id')
    user_id = fields.Many2one('res.users', default=lambda self: self.env.user)
