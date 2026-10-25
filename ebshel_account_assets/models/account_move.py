# -*- coding: utf-8 -*-
"""Vendor bills make assets; every asset entry knows its asset."""
import re

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import float_round


class AccountMove(models.Model):
    _inherit = 'account.move'

    ebshel_asset_id = fields.Many2one('ebshel.asset', string='Asset', readonly=True, index=True, copy=False,
                                      help="The asset this depreciation or disposal entry belongs to.")
    ebshel_asset_ids = fields.One2many('ebshel.asset', 'bill_id', string='Assets created')
    ebshel_asset_count = fields.Integer(compute='_compute_ebshel_asset_count')

    @api.depends('ebshel_asset_ids')
    def _compute_ebshel_asset_count(self):
        for move in self:
            move.ebshel_asset_count = len(move.ebshel_asset_ids)

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        for move in posted.filtered(lambda m: m.move_type == 'in_invoice'):
            move._ebshel_create_assets(auto=True)
        return posted

    # The link runs both ways. A bill reset or cancelled kept its assets running, and an
    # asset's entry reset or deleted in Accounting left its line 'posted' - the asset's book
    # value and the ledger no longer agreed.
    def _ebshel_check_asset_links(self):
        for move in self:
            asset = move.ebshel_asset_id
            if asset:
                raise UserError(_(
                    "%(move)s is an entry of the asset %(asset)s. Change the asset instead (modify it, "
                    "write it down or dispose of it) so that the asset and the ledger stay in step.",
                    move=move.display_name, asset=asset.display_name))
            assets = move.with_context(active_test=False).ebshel_asset_ids
            booked = assets.filtered(lambda a: a.state in ('closed', 'disposed')
                                     or a.line_ids.filtered(lambda l: l.state == 'posted'))
            if booked:
                raise UserError(_(
                    "%(move)s made assets that have depreciation posted (%(assets)s): dispose of them "
                    "before resetting or cancelling the bill.",
                    move=move.display_name, assets=', '.join(booked.mapped('display_name'))))

    def button_draft(self):
        self._ebshel_check_asset_links()
        res = super().button_draft()
        # nothing of them is booked yet: they are cancelled, and posting the bill again makes
        # new ones from what it then says
        assets = self.with_context(active_test=False).ebshel_asset_ids.filtered(
            lambda a: a.state in ('draft', 'running', 'paused'))
        assets.action_cancel()
        return res

    def unlink(self):
        self._ebshel_check_asset_links()
        return super().unlink()

    ebshel_asset_candidate_count = fields.Integer(compute='_compute_ebshel_asset_candidates')
    ebshel_asset_candidate_note = fields.Char(compute='_compute_ebshel_asset_candidates')

    @api.depends('invoice_line_ids.account_id', 'invoice_line_ids.ebshel_asset_ids', 'state', 'move_type')
    def _compute_ebshel_asset_candidates(self):
        for move in self:
            rows = move._ebshel_candidates() if move.move_type == 'in_invoice' and move.state != 'cancel' else []
            move.ebshel_asset_candidate_count = len(rows)
            move.ebshel_asset_candidate_note = _(
                "%(count)s bill line(s) on a fixed-asset account are not assets yet: %(names)s",
                count=len(rows), names=', '.join((l.name or l.product_id.display_name or '?')[:40] for l, _c in rows)) if rows else ''

    def _ebshel_guess_category(self, line, categories=None):
        """The category a bill line belongs to: the one whose trigger accounts carry the
        line's account - when several do, the one named in the line, else the busiest -
        or, failing a trigger, the one whose asset account it is."""
        cats = categories if categories is not None else \
            self.env['ebshel.asset.category'].search([('company_id', '=', self.company_id.id)])
        hits = cats.filtered(lambda c: line.account_id in c.trigger_account_ids)
        if not hits:
            hits = cats.filtered(lambda c: c.asset_account_id == line.account_id)
        if len(hits) > 1:
            words = set(re.findall(r'[a-z0-9]+', (line.name or line.product_id.display_name or '').lower()))
            named = hits.filtered(lambda c: set(re.findall(r'[a-z0-9]+', c.name.lower())) & words)
            hits = named or hits
            if len(hits) > 1:
                hits = hits.sorted(key=lambda c: -c.asset_count)
        return hits[:1]

    def _ebshel_candidates(self):
        """[(line, category or empty)]: the bill lines on a fixed-asset account, or on a
        category's trigger account, that have no asset yet - what the wizard offers."""
        self.ensure_one()
        cats = self.env['ebshel.asset.category'].search([('company_id', '=', self.company_id.id)])
        triggers = cats.trigger_account_ids | cats.asset_account_id
        out = []
        for line in self.invoice_line_ids.filtered(lambda l: l.display_type == 'product'):
            if not line.account_id or (line.account_id not in triggers
                                       and line.account_id.account_type not in ('asset_fixed', 'asset_non_current')):
                continue
            # archived assets count too (posting again made them a second time); cancelled ones
            # - a bill reset to draft cancels its assets - do not
            existing = line.with_context(active_test=False).ebshel_asset_ids.filtered(lambda a: a.state != 'cancelled')
            if existing:
                continue
            out.append((line, self._ebshel_guess_category(line, cats)))
        return out

    def _ebshel_asset_lines(self):
        """Bill lines that a category claims (its trigger accounts) and that have no asset yet."""
        return [(line, cat) for line, cat in self._ebshel_candidates()
                if cat and line.account_id in cat.trigger_account_ids]

    def _ebshel_make_assets(self, line, cat, units=None, extra=None, confirm=False):
        """`units` assets from one bill line - the category's rule when None - each with
        the `extra` values (a name, custody, dates, a salvage), confirmed when asked. The
        last unit carries the rounding: three of 100.00 are 33.33, 33.33 and 33.34."""
        self.ensure_one()
        created = self.env['ebshel.asset']
        # the cost in the company's currency (a bill in dollars made an asset of as many
        # rupees), and only what the line adds: a credit line (a discount on the asset
        # account) made an asset of negative value, which refused - and so did the bill
        cost = line.company_currency_id.round(line.balance)
        if cost <= 0:
            return created
        if units is None:
            units = max(1, int(line.quantity)) if cat.one_per_unit and line.quantity >= 1 else 1
        units = max(1, int(units))
        each = line.company_currency_id.round(cost / units)
        extra = {k: v for k, v in (extra or {}).items() if v not in (None, '', False)}
        base_name = extra.pop('name', None) or line.name or line.product_id.display_name or self.name
        for i in range(units):
            value = each if i < units - 1 else line.company_currency_id.round(cost - each * (units - 1))
            name = '%s (%d/%d)' % (base_name, i + 1, units) if units > 1 else base_name
            vals = {
                'name': name, 'category_id': cat.id, 'company_id': self.company_id.id,
                'partner_id': self.partner_id.id, 'bill_line_id': line.id,
                'purchase_date': self.invoice_date or self.date, 'start_date': self.invoice_date or self.date,
                'purchase_value': value,
                'salvage_value': float_round(value * cat.salvage_pct / 100.0, 2) if cat.salvage_pct else 0.0,
                'analytic_distribution': line.analytic_distribution or cat.analytic_distribution or False,
            }
            vals.update(extra)
            asset = self.env['ebshel.asset'].create(vals)
            if confirm:
                asset.action_confirm()
            created |= asset
        return created

    def _ebshel_create_assets(self, auto=False):
        """One asset per bill line on an asset account - or one per unit, as the category says."""
        created = self.env['ebshel.asset']
        for move in self:
            made = self.env['ebshel.asset']
            for line, cat in move._ebshel_asset_lines():
                if auto and cat.bill_trigger == 'no':
                    continue
                made |= move._ebshel_make_assets(line, cat, confirm=not auto or cat.bill_trigger == 'running')
            if made and auto:
                move.message_post(body=_("%(count)s asset(s) made from this bill: %(names)s - %(what)s",
                                         count=len(made), names=', '.join(made.mapped('display_name')),
                                         what=_('review and confirm them under Assets')
                                         if made.filtered(lambda a: a.state == 'draft') else _('running')))
            created |= made
        return created

    def action_ebshel_create_assets(self):
        """The wizard: the lines not yet assets, each with its guessed category, to set
        up by hand before anything is created."""
        self.ensure_one()
        if not self._ebshel_candidates():
            return {'type': 'ir.actions.client', 'tag': 'display_notification',
                    'params': {'type': 'info', 'message': _("No bill line on an asset account is waiting for an asset."), 'sticky': False}}
        return {'type': 'ir.actions.act_window', 'name': _('Assets from %s', self.display_name),
                'res_model': 'ebshel.asset.from.bill', 'view_mode': 'form', 'target': 'new',
                'context': {'default_move_id': self.id, 'dialog_size': 'extra-large'}}

    def action_ebshel_open_assets(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _('Assets'), 'res_model': 'ebshel.asset',
                'view_mode': 'list,form', 'domain': [('bill_id', '=', self.id)]}


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    ebshel_asset_ids = fields.One2many('ebshel.asset', 'bill_line_id', string='Assets')
