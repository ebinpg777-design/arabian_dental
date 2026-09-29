# -*- coding: utf-8 -*-
"""The invoice side of deferrals: two dates on a line start one."""
from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .budget import EXPENSE_TYPES, REVENUE_TYPES


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    ebshel_deferral_start = fields.Date('Deferral start', copy=False)
    ebshel_deferral_end = fields.Date('Deferral end', copy=False)
    ebshel_deferral_ids = fields.One2many('ebshel.deferral', 'move_line_id', string='Deferrals')


class AccountMove(models.Model):
    _inherit = 'account.move'

    ebshel_deferral_ids = fields.One2many('ebshel.deferral', 'move_id', string='Deferrals')
    ebshel_deferral_id = fields.Many2one('ebshel.deferral', string='Deferral entry of', readonly=True, copy=False, index=True)
    ebshel_deferral_count = fields.Integer(compute='_compute_ebshel_deferral_count')

    @api.depends('ebshel_deferral_ids')
    def _compute_ebshel_deferral_count(self):
        for move in self:
            move.ebshel_deferral_count = len(move.ebshel_deferral_ids)

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        posted._ebshel_create_deferrals()
        return posted

    def _ebshel_create_deferrals(self):
        Deferral = self.env['ebshel.deferral']
        for move in self.filtered(lambda m: m.is_invoice(include_receipts=True) and m.state == 'posted'):
            kind = 'revenue' if move.move_type in ('out_invoice', 'out_refund', 'out_receipt') else 'expense'
            wanted = REVENUE_TYPES if kind == 'revenue' else EXPENSE_TYPES
            for line in move.invoice_line_ids:
                if not (line.ebshel_deferral_start and line.ebshel_deferral_end) or line.ebshel_deferral_ids \
                        or line.display_type != 'product' or line.account_id.account_type not in wanted:
                    continue
                if line.ebshel_deferral_end < line.ebshel_deferral_start:
                    raise UserError(_("On %s the deferral ends before it starts.", line.name or move.name))
                total = -line.balance if kind == 'revenue' else line.balance
                if move.company_id.currency_id.is_zero(total):
                    continue
                deferral = Deferral.create({
                    'name': line.name or move.name, 'kind': kind, 'company_id': move.company_id.id,
                    'move_id': move.id, 'move_line_id': line.id, 'partner_id': move.commercial_partner_id.id,
                    'date_from': line.ebshel_deferral_start, 'date_to': line.ebshel_deferral_end, 'total': total,
                    'pl_account_id': line.account_id.id, 'analytic_distribution': line.analytic_distribution,
                    'method': move.company_id.ebshel_deferral_method or 'days'})
                if move.company_id.ebshel_deferral_auto:
                    deferral.action_run()
        return True

    def _ebshel_stop_deferrals(self):
        for move in self:
            running = move.ebshel_deferral_ids.filtered(lambda d: d.state in ('running', 'done'))
            if running:
                raise UserError(_("%s has deferrals with posted entries (%s). Cancel them first.",
                                  move.display_name, ', '.join(running.mapped('code'))))
            move.ebshel_deferral_ids.filtered(lambda d: d.state == 'draft').unlink()

    def button_draft(self):
        self._ebshel_stop_deferrals()
        return super().button_draft()

    def button_cancel(self):
        self._ebshel_stop_deferrals()
        return super().button_cancel()

    def action_ebshel_open_deferrals(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'ebshel.deferral', 'name': _('Deferrals'),
                'view_mode': 'list,form', 'domain': [('move_id', '=', self.id)]}
