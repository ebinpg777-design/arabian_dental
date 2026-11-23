# -*- coding: utf-8 -*-
"""Generate — the batch entry point for month-end: one sheet per salesperson who billed
anything, computed in the same pass so nobody opens each one by hand."""
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models

from ..models.incentive_sheet import INCENTIVE_SHEET_BASES


class LabIncentiveGenerateWizard(models.TransientModel):
    _name = 'lab.incentive.generate.wizard'
    _description = 'Generate Incentive Sheets'

    period = fields.Date(
        required=True,
        default=lambda s: fields.Date.context_today(s).replace(day=1),
        help="Any date in the month to generate for.")
    # The setting, shown where month-end is run: what the sheets are computed on is
    # decided here, in front of the preview it changes. Saved when generating.
    basis = fields.Selection(
        INCENTIVE_SHEET_BASES, string='Count On', required=True,
        default=lambda s: s.env['lab.incentive.sheet']._incentive_basis(),
        help="Billed: what was invoiced in the month. Billed and paid: only invoices the "
             "clinic has paid - a cheque not yet cleared does not pay. Collected: the money "
             "received in the month, before tax; a cheque counts in the month it clears, "
             "whenever it was received, and one not cleared is not counted.")
    # What month-end will produce, executive by executive, before anything is written.
    preview_data = fields.Json(compute='_compute_preview_data')

    @api.depends('period', 'basis')
    def _compute_preview_data(self):
        for wizard in self:
            wizard.preview_data = wizard._preview() if wizard.period else {}

    # ------------------------------------------------------------------ who
    def _executives(self, period):
        """Everyone with something in the month: billed it (by the Salesperson of the
        order the invoice came from - the line a sheet computes on), registered a new lead
        in it, or - on the Collected basis - had money for their invoices come in.

        Not through the field visit (no lab order carries one) and not limited to the
        Field Work executive group, which nobody on the lab's book holds; and only those,
        so month-end does not create a wall of empty zero sheets. (client, 2026-10-08)
        """
        Sheet = self.env['lab.incentive.sheet']
        start, end = Sheet._month_window(period)
        billed = self.env['account.move.line'].sudo().search([
            ('move_id.state', '=', 'posted'),
            ('move_id.move_type', 'in', ('out_invoice', 'out_refund')),
            ('move_id.invoice_date', '>=', period),
            ('move_id.invoice_date', '<', period + relativedelta(months=1)),
            ('move_id.company_id', '=', self.env.company.id),
            ('display_type', '=', 'product'),
            ('sale_line_ids.order_id.user_id', '!=', False),
        ]).sale_line_ids.order_id.user_id
        leads = self.env['res.partner'].sudo().search([
            ('incentive_new_lead', '=', True), ('user_id', '!=', False), ('parent_id', '=', False),
            ('create_date', '>=', start), ('create_date', '<', end),
            ('company_id', 'in', (False, self.env.company.id)),
        ]).user_id
        users = billed | leads
        rows = {}
        if self.basis == 'collected':
            rows = Sheet._collection_rows(self.env.company, period)
            users |= self.env['res.users'].sudo().browse(
                [uid for uid, items in rows.items() if items])
        return users.filtered(lambda u: u.active and not u.share), rows

    # ------------------------------------------------------------------ preview
    def _preview(self):
        """One row per executive: what was billed, what came in and how, what waits,
        and the incentive the sheet would show - computed exactly as the sheet computes,
        written nowhere."""
        self.ensure_one()
        period = self.period.replace(day=1)
        Sheet = self.env['lab.incentive.sheet']
        company = self.env.company
        money = company.currency_id.sudo().format
        executives, rows = self._executives(period)
        rule = self.env['lab.incentive.rule']._active_rule(company, period)
        margin = rule.default_margin_pct if rule else 0.0
        existing = {s.executive_id.id: s for s in Sheet.sudo().search([
            ('period', '=', period), ('company_id', '=', company.id),
            ('executive_id', 'in', executives.ids)])}
        states = dict(Sheet._fields['state']._description_selection(self.env))
        result = []
        totals = dict.fromkeys(('billed', 'cash', 'cheques', 'waiting', 'bounced', 'base',
                                'incentive'), 0.0)
        for user in executives.sorted('name'):
            probe = Sheet.new({'executive_id': user.id, 'period': period,
                               'company_id': company.id})
            billed_lines = probe.with_context(
                lab_incentive_basis_override='invoiced')._billed_line_vals(margin)
            billed = sum(v['sale_amount'] for v in billed_lines)
            cash = cheques = waiting = bounced = earlier = 0.0
            waiting_n = set()
            if self.basis == 'collected':
                pairs = probe._collected_values(margin, rows.get(user.id, []))
                base = sum(v['incentive_base'] for _row, lines in pairs for v in lines)
                for row, _lines in pairs:
                    if row['status'] == 'counted':
                        if row['mode'] == 'cheque':
                            cheques += row['counted_amount']
                            earlier += row['counted_amount'] if row.get('from_earlier_month') else 0.0
                        else:
                            cash += row['counted_amount']
                    elif row['status'] in ('pending', 'later'):
                        waiting += row['executive_amount']
                        waiting_n.add(row.get('cheque_id'))
                    else:
                        bounced += row['executive_amount']
            else:
                lines = billed_lines
                if self.basis == 'paid':
                    lines = [{'incentive_base': v['incentive_base']} for v in
                             probe.with_context(lab_incentive_basis_override='paid')
                             ._billed_line_vals(margin)]
                base = sum(v['incentive_base'] for v in lines)
            slab = rule.sudo().compute_incentive(base) if rule else 0.0
            clinics = len(probe._new_clinics())
            bonus = clinics * (rule.new_clinic_bonus or 0.0) if rule else 0.0
            sheet = existing.get(user.id)
            frozen = sheet and sheet.state in ('approved', 'paid')
            item = {
                'id': user.id, 'name': user.name,
                'billed': billed, 'cash': cash, 'cheques': cheques, 'earlier': earlier,
                'waiting': waiting, 'waiting_count': len(waiting_n - {None, False}),
                'bounced': bounced, 'base': base, 'incentive': slab + bonus,
                'clinics': clinics,
                'sheet': sheet.name if sheet else '',
                'sheet_state': states.get(sheet.state, '') if sheet else _('New'),
                'frozen': bool(frozen),
                'frozen_amount': money(sheet.incentive_amount) if frozen else '',
            }
            for key in totals:
                totals[key] += item[key]
            for key in ('billed', 'cash', 'cheques', 'earlier', 'waiting', 'bounced', 'base',
                        'incentive'):
                item[key + '_text'] = money(item[key])
            result.append(item)
        return {
            'basis': self.basis,
            'month': period.strftime('%B %Y'),
            'rule': rule.name if rule else '',
            'rows': result,
            'totals': {key + '_text': money(value) for key, value in totals.items()},
            'scale': max([r['cash'] + r['cheques'] + r['waiting'] for r in result] or [0.0]),
        }

    # ------------------------------------------------------------------ generate
    def action_generate(self):
        self.ensure_one()
        period = self.period.replace(day=1)
        Sheet = self.env['lab.incentive.sheet']
        if self.basis != Sheet._incentive_basis():
            self.env['ir.config_parameter'].sudo().set_str('lab_incentive.basis', self.basis)
        executives, _rows = self._executives(period)

        sheets = Sheet.browse()
        for executive in executives:
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
