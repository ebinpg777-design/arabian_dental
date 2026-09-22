# -*- coding: utf-8 -*-
"""Data for the slab-progress widget on the sheet form.

The sheet already shows the final number; what it does not show at a glance is WHY —
which slab was hit, how close the executive is to the next one, and what crossing it
would be worth. That is the "calculation widget" the client asked for: not a second
calculator, a window into the one the sheet already ran.
"""
from odoo import api, fields, models


class LabIncentiveSheet(models.Model):
    _inherit = 'lab.incentive.sheet'

    slab_widget_data = fields.Json(compute='_compute_slab_widget_data')

    @api.depends('rule_id', 'total_base', 'incentive_amount')
    def _compute_slab_widget_data(self):
        for sheet in self:
            rule = sheet.rule_id
            if not rule or not rule.slab_ids:
                sheet.slab_widget_data = {'slabs': [], 'base': 0.0, 'currency': ''}
                continue
            slabs = rule.slab_ids.sorted('amount_from')
            base = sheet.total_base
            rows = []
            for slab in slabs:
                top = slab.amount_to or None
                reached = base >= slab.amount_from
                current = reached and (top is None or base <= top)
                rows.append({
                    'from': slab.amount_from,
                    'to': top,
                    'label': ('%s%%' % slab.value if slab.incentive_type == 'percent'
                              else sheet.currency_id.format(slab.value)),
                    'reached': reached,
                    'current': current,
                })
            next_slab = next((r for r in rows if not r['reached']), None)
            sheet.slab_widget_data = {
                'slabs': rows,
                'base': base,
                'amount': sheet.incentive_amount,
                'currency_symbol': sheet.currency_id.symbol or '',
                'next_gap': (next_slab['from'] - base) if next_slab else None,
                'mode': rule.slab_mode,
            }
