# -*- coding: utf-8 -*-
"""Assets from a vendor bill, line by line, with the person looking.

The bill already makes assets by itself when it is posted (a line on a category's
trigger account becomes a draft or a running asset). This is the other way in: the
lines of the bill that are not assets yet, each with the category guessed from its
account and name, the number of units it splits into, where it will live and who
holds it, and what each asset will cost per period - decided in one screen before
anything is created. (client, 2026-10-25: "make asset creation from bills easy")
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import float_round


class AssetFromBill(models.TransientModel):
    _name = 'ebshel.asset.from.bill'
    _description = 'Assets from a vendor bill'

    move_id = fields.Many2one('account.move', string='Vendor bill', required=True, readonly=True)
    company_id = fields.Many2one(related='move_id.company_id')
    currency_id = fields.Many2one(related='move_id.company_currency_id')
    line_ids = fields.One2many('ebshel.asset.from.bill.line', 'wizard_id', string='Assets to create')
    start = fields.Selection([('draft', 'Create as drafts, to review before they run'),
                              ('running', 'Create and start depreciating')],
                             default='draft', required=True, string='Then')
    custodian_id = fields.Many2one('res.partner', string='Held by',
                                   help="Filled into every line below; a line can still say otherwise.")
    location = fields.Char(help="Filled into every line below; a line can still say otherwise.")

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        move_id = vals.get('move_id') or self.env.context.get('default_move_id') or self.env.context.get('active_id')
        move = self.env['account.move'].browse(move_id).exists() if move_id else None
        if move and 'line_ids' in fields_list:
            rows = []
            for line, cat in move._ebshel_candidates():
                units = max(1, int(line.quantity)) if cat and cat.one_per_unit and line.quantity >= 1 else 1
                rows.append((0, 0, {
                    'bill_line_id': line.id, 'name': line.name or line.product_id.display_name or move.name,
                    'category_id': cat.id if cat else False, 'units': units,
                    'start_date': move.invoice_date or move.date}))
            vals.update({'move_id': move.id, 'line_ids': rows})
        return vals

    @api.onchange('custodian_id')
    def _onchange_custodian(self):
        for row in self.line_ids:
            row.custodian_id = self.custodian_id

    @api.onchange('location')
    def _onchange_location(self):
        for row in self.line_ids:
            row.location = self.location

    def action_create(self):
        self.ensure_one()
        rows = self.line_ids.filtered('include')
        if not rows:
            raise UserError(_("Tick at least one line."))
        missing = rows.filtered(lambda r: not r.category_id)
        if missing:
            raise UserError(_("Pick a category for: %s", ', '.join(missing.mapped('name'))))
        created = self.env['ebshel.asset']
        for row in rows:
            extra = {'name': row.name, 'start_date': row.start_date, 'custodian_id': row.custodian_id.id,
                     'location': row.location, 'serial': row.serial, 'warranty_end': row.warranty_end,
                     'insurance_end': row.insurance_end, 'salvage_value': row.salvage_value}
            created |= self.move_id._ebshel_make_assets(row.bill_line_id, row.category_id, units=row.units,
                                                        extra=extra, confirm=self.start == 'running')
        if not created:
            raise UserError(_("Nothing was created: a line with no cost, or a credit, cannot become an asset."))
        self.move_id.message_post(body=_("%(count)s asset(s) created from this bill: %(names)s",
                                         count=len(created), names=', '.join(created.mapped('display_name'))))
        if len(created) == 1:
            return {'type': 'ir.actions.act_window', 'res_model': 'ebshel.asset', 'res_id': created.id,
                    'view_mode': 'form', 'target': 'current'}
        return {'type': 'ir.actions.act_window', 'name': _('Assets from %s', self.move_id.display_name),
                'res_model': 'ebshel.asset', 'view_mode': 'list,kanban,form', 'domain': [('id', 'in', created.ids)],
                'target': 'current'}


class AssetFromBillLine(models.TransientModel):
    _name = 'ebshel.asset.from.bill.line'
    _description = 'A bill line becoming an asset'

    wizard_id = fields.Many2one('ebshel.asset.from.bill', required=True, ondelete='cascade')
    currency_id = fields.Many2one(related='wizard_id.currency_id')
    include = fields.Boolean(string='Create', default=True)
    bill_line_id = fields.Many2one('account.move.line', string='Bill line', required=True, readonly=True)
    account_id = fields.Many2one(related='bill_line_id.account_id', string='Account')
    name = fields.Char(string='Asset name', required=True)
    category_id = fields.Many2one('ebshel.asset.category', string='Category',
                                  domain="[('company_id', '=', parent.company_id)]")
    units = fields.Integer(string='Units', default=1,
                           help="How many assets this line becomes: 4 chairs on one line are 4 assets.")
    value = fields.Monetary(string='Line cost', compute='_compute_values')
    unit_value = fields.Monetary(string='Cost each', compute='_compute_values')
    salvage_value = fields.Monetary(string='Salvage each', compute='_compute_salvage', store=True, readonly=False,
                                    help="What each asset will still be worth at the end: never depreciated.")
    charge = fields.Monetary(string='Charge / month each', compute='_compute_values',
                             help="What each asset will cost per month under the category's rule.")
    start_date = fields.Date(string='Depreciate from')
    custodian_id = fields.Many2one('res.partner', string='Held by')
    location = fields.Char()
    serial = fields.Char(string='Serial / tag no.')
    warranty_end = fields.Date()
    insurance_end = fields.Date()

    @api.depends('bill_line_id', 'units', 'category_id', 'salvage_value')
    def _compute_values(self):
        for row in self:
            line = row.bill_line_id
            cost = line.company_currency_id.round(line.balance) if line else 0.0
            units = max(1, row.units or 1)
            each = line.company_currency_id.round(cost / units) if line else 0.0
            row.value = cost
            row.unit_value = each
            cat = row.category_id
            charge = 0.0
            if cat and cat.duration and each > row.salvage_value:
                months = cat.duration if cat.period == 'month' else cat.duration * 12
                charge = (each - row.salvage_value) / months if cat.method == 'linear' \
                    else each * cat.declining_rate / 100.0 / 12.0
            row.charge = float_round(charge, 2)

    @api.depends('category_id', 'unit_value')
    def _compute_salvage(self):
        for row in self:
            cat = row.category_id
            row.salvage_value = float_round(row.unit_value * cat.salvage_pct / 100.0, 2) if cat and cat.salvage_pct else 0.0
