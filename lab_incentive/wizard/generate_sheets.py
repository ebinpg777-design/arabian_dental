# -*- coding: utf-8 -*-
"""Generate — the batch entry point for month-end: one sheet per executive who sold
anything, computed in the same pass so nobody opens each one by hand."""
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
        exec_group = self.env.ref('lab_fieldwork.group_fieldwork_executive')
        executives = exec_group.sudo().all_user_ids.filtered('active')

        # Only executives who actually sold something this month, so month-end does
        # not create a wall of empty zero sheets.
        active_execs = self.env['sale.order'].search([
            ('visit_id.user_id', 'in', executives.ids),
            ('date_order', '>=', start), ('date_order', '<', end),
            ('company_id', '=', self.env.company.id),
            ('state', 'not in', ('draft', 'sent', 'cancel')),
        ]).mapped('visit_id.user_id')

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
