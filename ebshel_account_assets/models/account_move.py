# -*- coding: utf-8 -*-
"""Vendor bills make assets; every asset entry knows its asset."""
from odoo import api, fields, models, _
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

    def _ebshel_asset_lines(self):
        """Bill lines on a category's trigger account that have no asset yet."""
        self.ensure_one()
        categories = self.env['ebshel.asset.category'].search([('company_id', '=', self.company_id.id)])
        by_account = {}
        for cat in categories:
            for account in cat.trigger_account_ids:
                by_account.setdefault(account.id, cat)
        out = []
        for line in self.invoice_line_ids.filtered(lambda l: l.display_type == 'product'):
            cat = by_account.get(line.account_id.id)
            if cat and not line.ebshel_asset_ids:
                out.append((line, cat))
        return out

    def _ebshel_create_assets(self, auto=False):
        """One asset per bill line on an asset account - or one per unit."""
        created = self.env['ebshel.asset']
        for move in self:
            for line, cat in move._ebshel_asset_lines():
                if auto and cat.bill_trigger == 'no':
                    continue
                units = max(1, int(line.quantity)) if cat.one_per_unit and line.quantity >= 1 else 1
                value = float_round(line.price_subtotal / units, 2)
                for i in range(units):
                    name = line.name or line.product_id.display_name or move.name
                    if units > 1:
                        name = '%s (%d/%d)' % (name, i + 1, units)
                    asset = self.env['ebshel.asset'].create({
                        'name': name, 'category_id': cat.id, 'company_id': move.company_id.id,
                        'partner_id': move.partner_id.id, 'bill_line_id': line.id,
                        'purchase_date': move.invoice_date or move.date, 'start_date': move.invoice_date or move.date,
                        'purchase_value': value,
                        'salvage_value': float_round(value * cat.salvage_pct / 100.0, 2) if cat.salvage_pct else 0.0,
                        'analytic_distribution': line.analytic_distribution or cat.analytic_distribution or False,
                    })
                    if not auto or cat.bill_trigger == 'running':
                        asset.action_confirm()
                    created |= asset
        return created

    def action_ebshel_create_assets(self):
        created = self._ebshel_create_assets(auto=False)
        if not created:
            return {'type': 'ir.actions.client', 'tag': 'display_notification',
                    'params': {'type': 'info', 'message': _("No bill line on an asset account is waiting for an asset."), 'sticky': False}}
        return self.action_ebshel_open_assets()

    def action_ebshel_open_assets(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _('Assets'), 'res_model': 'ebshel.asset',
                'view_mode': 'list,form', 'domain': [('bill_id', '=', self.id)]}


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    ebshel_asset_ids = fields.One2many('ebshel.asset', 'bill_line_id', string='Assets')
