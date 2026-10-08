# -*- coding: utf-8 -*-
"""Data for the slab-progress widget on the sheet form.

The sheet already shows the final number; what it does not show at a glance is WHY —
which slab was hit, how close the executive is to the next one, and what crossing it
would be worth. That is the "calculation widget" the client asked for: not a second
calculator, a window into the one the sheet already ran.
"""
from odoo import _, api, fields, models


class LabIncentiveSheet(models.Model):
    _inherit = 'lab.incentive.sheet'

    slab_widget_data = fields.Json(compute='_compute_slab_widget_data')

    @api.depends('rule_id', 'total_base', 'slab_amount', 'new_clinic_count',
                 'new_clinic_amount', 'incentive_amount')
    def _compute_slab_widget_data(self):
        for sheet in self:
            # The slabs are read elevated: an executive may read the rule but not
            # the slab model (manager-only ACL). On Odoo 20 an unreadable one2many
            # reads back EMPTY, so the executive's own sheet said "No incentive
            # rule is active" while showing the amount that rule produced.
            rule = sheet.rule_id.sudo()
            if not rule or not rule.slab_ids:
                sheet.slab_widget_data = {'slabs': [], 'base': 0.0, 'currency': ''}
                continue
            # elevated like the rule: how a currency is written is no secret, and an
            # executive without the accounting groups may not read res.currency
            money = sheet.currency_id.sudo().format
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
                    'label': sheet._slab_rate_label(slab),
                    'reached': reached,
                    'current': current,
                })
            next_slab = next((r for r in rows if not r['reached']), None)
            sheet.slab_widget_data = {
                'slabs': rows,
                'base': base,
                'amount': sheet.incentive_amount,
                'currency_symbol': sheet.currency_id.sudo().symbol or '',
                'next_gap': (next_slab['from'] - base) if next_slab else None,
                'mode': rule.slab_mode,
                # The working, line by line (client, 2026-10-08: a telescopic figure
                # with only its slabs beside it "makes confusion")
                'steps': sheet._calculation_steps(rule, base),
                'mode_note': sheet._calculation_note(rule),
                'base_text': money(base),
                'slab_text': money(sheet.slab_amount or 0.0),
                'clinics': {
                    'count': sheet.new_clinic_count,
                    'each': money(rule.new_clinic_bonus or 0.0),
                    'total': money(sheet.new_clinic_amount or 0.0),
                } if (sheet.new_clinic_count or rule.new_clinic_bonus) else None,
                'total_text': money(sheet.incentive_amount or 0.0),
            }

    def _slab_rate_label(self, slab):
        if slab.incentive_type == 'percent':
            return '%s%%' % ('%g' % slab.value)
        return '%s fixed' % self.currency_id.sudo().format(slab.value)

    def _calculation_note(self, rule):
        if rule.slab_mode == 'telescopic':
            return _("Telescopic: each band of the base is paid at its own rate, and the "
                     "results are added up.")
        return _("Flat: the slab the base reached is applied to the whole base.")

    def _calculation_steps(self, rule, base):
        """The rows of "How this was calculated": what each slab did to the base."""
        money = self.currency_id.sudo().format
        steps = []
        for step in rule.explain(base):
            slab = step['slab']
            band = '%s – %s' % (money(slab.amount_from), money(slab.amount_to)) \
                if slab.amount_to else _('%s and above', money(slab.amount_from))
            rate = self._slab_rate_label(slab)
            if not step['applied']:
                working = _('not reached') if base < slab.amount_from else _('not applied')
            elif slab.incentive_type == 'percent':
                working = '%s × %s' % (money(step['amount']), rate)
            else:
                working = rate
            steps.append({
                'band': band, 'rate': rate, 'working': working,
                'amount': money(step['amount']) if step['applied'] else '',
                'result': money(step['result']) if step['applied'] else '',
                'applied': step['applied'],
            })
        return steps
