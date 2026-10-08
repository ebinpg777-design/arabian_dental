# -*- coding: utf-8 -*-
"""Generate — the batch entry point for month-end: one sheet per salesperson who billed
anything, computed in the same pass so nobody opens each one by hand."""
from dateutil.relativedelta import relativedelta

from odoo import _, fields, models


class LabIncentiveGenerateWizard(models.TransientModel):
    _name = 'lab.incentive.generate.wizard'
    _description = 'Generate Incentive Sheets'

    period = fields.Date(
        required=True,
        default=lambda s: fields.Date.context_today(s).replace(day=1),
        help="Any date in the month to generate for.")

    def action_generate(self):
        self.ensure_one()
        period = self.period.replace(day=1)
        Sheet = self.env['lab.incentive.sheet']
        start, end = Sheet._month_window(period)

        # Everyone billed something this month, by the Salesperson of the order the
        # invoice came from - the line a sheet computes on - and everyone whose new
        # lead was registered in it, so month-end does not create a wall of empty zero
        # sheets. Not through the field visit (no lab order carries one) and not
        # limited to the Field Work executive group, which nobody on the lab's book
        # holds. (client, 2026-10-08)
        first = period
        billed = self.env['account.move.line'].sudo().search([
            ('move_id.state', '=', 'posted'),
            ('move_id.move_type', 'in', ('out_invoice', 'out_refund')),
            ('move_id.invoice_date', '>=', first),
            ('move_id.invoice_date', '<', first + relativedelta(months=1)),
            ('move_id.company_id', '=', self.env.company.id),
            ('display_type', '=', 'product'),
            ('sale_line_ids.order_id.user_id', '!=', False),
        ]).sale_line_ids.order_id.user_id
        leads = self.env['res.partner'].sudo().search([
            ('incentive_new_lead', '=', True), ('user_id', '!=', False), ('parent_id', '=', False),
            ('create_date', '>=', start), ('create_date', '<', end),
            ('company_id', 'in', (False, self.env.company.id)),
        ]).user_id
        active_execs = (billed | leads).filtered(lambda u: u.active and not u.share)

        sheets = Sheet.browse()
        for executive in active_execs:
            sheet = Sheet.search([
                ('executive_id', '=', executive.id), ('period', '=', period),
                ('company_id', '=', self.env.company.id)], limit=1)
            if not sheet:
                sheet = Sheet.create({
                    'executive_id': executive.id, 'period': period,
                    'company_id': self.env.company.id})
            # An approved sheet is a figure someone signed off; regenerating the
            # month must not quietly reopen it with new amounts. Reopening it is a
            # deliberate Reset, not a side effect of month-end.
            if sheet.state not in ('approved', 'paid'):
                sheet.action_compute()
            sheets |= sheet

        return {
            'type': 'ir.actions.act_window',
            'name': _('Incentive Sheets — %s', period.strftime('%B %Y')),
            'res_model': 'lab.incentive.sheet', 'view_mode': 'list,form',
            'domain': [('id', 'in', sheets.ids)],
        }
