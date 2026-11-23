# -*- coding: utf-8 -*-
"""Data for the slab-progress widget on the sheet form.

The sheet already shows the final number; what it does not show at a glance is WHY —
which slab was hit, how close the executive is to the next one, and what crossing it
would be worth. That is the "calculation widget" the client asked for: not a second
calculator, a window into the one the sheet already ran.
"""
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.tools.misc import format_date


class LabIncentiveSheet(models.Model):
    _inherit = 'lab.incentive.sheet'

    slab_widget_data = fields.Json(compute='_compute_slab_widget_data')

    @api.depends('rule_id', 'total_base', 'total_sales', 'slab_amount', 'new_clinic_count',
                 'new_clinic_amount', 'incentive_amount', 'line_ids.incentive_basis')
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
                'base_note': sheet._base_note(money),
                'slab_text': money(sheet.slab_amount or 0.0),
                'clinics': {
                    'count': sheet.new_clinic_count,
                    'each': money(rule.new_clinic_bonus or 0.0),
                    'total': money(sheet.new_clinic_amount or 0.0),
                } if (sheet.new_clinic_count or rule.new_clinic_bonus) else None,
                'total_text': money(sheet.incentive_amount or 0.0),
            }

    def _base_note(self, money):
        """Why the base is not the amount billed, when it is not: the lines whose
        product is counted on its incentive price or the lab's margin on it.
        (Ortho, 2026-10-08)"""
        own = self.line_ids.filtered(lambda l: (l.incentive_basis or 'sale') != 'sale')
        if not own:
            return ''
        labels = {'price': _('incentive price'), 'margin_pct': _('margin %'),
                  'margin_amount': _('margin per unit')}
        counts = {}
        for line in own:
            counts[line.incentive_basis] = counts.get(line.incentive_basis, 0) + 1
        parts = ', '.join('%s %s' % (n, labels.get(basis, basis))
                          for basis, n in counts.items())
        if self.basis == 'collected':
            return _("Collected %(collected)s before tax. The base is %(base)s because "
                     "%(count)s line(s) count on their own base, not the amount collected "
                     "(%(parts)s).", collected=money(self.total_sales),
                     base=money(self.total_base), count=len(own), parts=parts)
        return _("Billed %(billed)s. The base is %(base)s because %(count)s line(s) "
                 "count on their own base, not the amount billed (%(parts)s).",
                 billed=money(self.total_sales), base=money(self.total_base),
                 count=len(own), parts=parts)

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


class LabIncentiveSheetCollectionWidget(models.Model):
    """Data for the receipts widget: the month's money at a glance, and every cheque's
    journey - received, deposited, cleared or bounced - against the month, so nobody has
    to work out from dates why a cheque did or did not count."""
    _inherit = 'lab.incentive.sheet'

    collection_widget_data = fields.Json(compute='_compute_collection_widget_data')

    @api.depends('basis', 'period', 'collection_ids', 'total_sales',
                 'collected_cash_bank', 'collected_cheques', 'cheques_waiting_amount',
                 'cheques_bounced_amount', 'taken_back_amount')
    def _compute_collection_widget_data(self):
        for sheet in self:
            if sheet.basis != 'collected' or not sheet.period:
                sheet.collection_widget_data = {}
                continue
            sheet.collection_widget_data = sheet._collection_widget_payload()

    def _collection_widget_payload(self):
        self.ensure_one()
        money = self.currency_id.sudo().format
        first = self.period.replace(day=1)
        last = first + relativedelta(months=1, days=-1)
        span = (last - first).days + 1
        rows = self.sudo().collection_ids

        def at(day):
            """Where `day` falls on the month's bar, 0-100, clamped at its ends."""
            if not day:
                return None
            return max(0.0, min(100.0, ((day - first).days + 0.5) * 100.0 / span))

        def label(day):
            return format_date(self.env, day, date_format='d MMM') if day else ''

        cheques = {}
        for row in rows.filtered('cheque_id'):
            entry = cheques.setdefault(row.cheque_id.id, {
                'id': row.cheque_id.id, 'number': row.cheque_number,
                'clinic': row.partner_id.name or '', 'bank': row.cheque_bank or '',
                'status': row.status, 'note': row.note or '',
                'invoices': [], 'amount': 0.0, 'counted': 0.0,
                'events': [],
            })
            entry['amount'] += row.amount
            entry['counted'] += row.counted_amount
            entry['invoices'].append(row.invoice_ref)
            if not entry['events']:
                for kind, day in (('received', row.receipt_date), ('deposited', row.deposit_date),
                                  ('cleared', row.clear_date), ('bounced', row.bounce_date)):
                    if not day:
                        continue
                    entry['events'].append({
                        'kind': kind, 'pos': at(day), 'label': label(day),
                        'before': day < first, 'after': day > last,
                    })
        journeys = []
        for entry in sorted(cheques.values(), key=lambda e: (
                {'counted': 0, 'taken_back': 1, 'pending': 2, 'later': 3}.get(e['status'], 4),
                e['events'][0]['pos'] if e['events'] else 0)):
            entry['amount_text'] = money(entry['amount'])
            entry['counted_text'] = money(entry['counted']) if entry['counted'] else ''
            entry['invoices'] = ', '.join(sorted(set(entry['invoices'])))
            entry['status_label'] = dict(self.env['lab.incentive.collection']._fields[
                'status']._description_selection(self.env)).get(entry['status'], '')
            journeys.append(entry)

        cash = rows.filtered(lambda r: r.status == 'counted' and r.mode != 'cheque')
        return {
            'month': format_date(self.env, first, date_format='MMMM y'),
            'month_start': label(first), 'month_end': label(last),
            'tiles': [
                {'key': 'total', 'label': _('Counted this month'),
                 'value': money(self.total_sales),
                 'sub': _('before tax, what the slabs pay on'), 'tone': 'main'},
                {'key': 'cash', 'label': _('Cash & bank'),
                 'value': money(self.collected_cash_bank),
                 'sub': _('%s receipt(s)', len(cash.payment_id)), 'tone': 'ok'},
                {'key': 'cleared', 'label': _('Cheques cleared'),
                 'value': money(self.collected_cheques),
                 'sub': _('%(count)s cheque(s), %(earlier)s received earlier',
                          count=self.cheques_cleared_count,
                          earlier=money(self.collected_earlier)), 'tone': 'ok'},
                {'key': 'waiting', 'label': _('Not cleared - not counted'),
                 'value': money(self.cheques_waiting_amount),
                 'sub': _('%s cheque(s), each counts in the month it clears',
                          self.cheques_waiting_count), 'tone': 'wait'},
                {'key': 'bounced', 'label': _('Bounced'),
                 'value': money(self.cheques_bounced_amount),
                 'sub': (_('%s taken back from an earlier month', money(self.taken_back_amount))
                         if self.taken_back_amount else
                         _('%s cheque(s)', self.cheques_bounced_count)), 'tone': 'bad'},
            ],
            'cheques': journeys,
        }
