# -*- coding: utf-8 -*-
"""Thirteen weeks of cash: what is in the bank, what customers should pay
(when they usually do), what is owed to vendors, and the planned items that
are not in the ledger yet."""
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import AccessError
from odoo.tools import SQL

LIQUIDITY = ('asset_cash', 'liability_credit_card')


class CashItem(models.Model):
    _name = 'ebshel.cash.item'
    _description = 'Planned cash item'
    _order = 'date, id'

    name = fields.Char(required=True)
    kind = fields.Selection([('in', 'Money in'), ('out', 'Money out')], required=True, default='out')
    amount = fields.Monetary(required=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    date = fields.Date('First on', required=True, default=fields.Date.context_today)
    frequency = fields.Selection([('once', 'Once'), ('weekly', 'Every week'), ('monthly', 'Every month'), ('quarterly', 'Every quarter')],
                                 default='once', required=True)
    date_end = fields.Date('Until')
    partner_id = fields.Many2one('res.partner')
    note = fields.Char()
    active = fields.Boolean(default=True)

    def _occurrences(self, start, end):
        self.ensure_one()
        day = self.date
        stop = min(end, self.date_end) if self.date_end else end
        step = {'weekly': timedelta(days=7), 'monthly': relativedelta(months=1), 'quarterly': relativedelta(months=3)}.get(self.frequency)
        out = []
        while day <= stop:
            if day >= start:
                out.append(day)
            if not step:
                break
            day = day + step
        return out


class CashForecast(models.AbstractModel):
    _name = 'ebshel.cash.forecast'
    _description = 'Cash forecast'

    @api.model
    def _check(self):
        if not self.env.su and not self.env.user.has_groups('account.group_account_readonly,account.group_account_invoice'):
            raise AccessError(_("The cash forecast is for the accounting team."))

    @api.model
    def _liquidity_accounts(self, company):
        return self.env['account.account'].with_company(company).search(
            [('account_type', 'in', LIQUIDITY), ('company_ids', 'in', company.id)])

    @api.model
    def _cash_balance(self, company, date):
        accounts = self._liquidity_accounts(company)
        if not accounts:
            return 0.0, []
        rows = self.env.execute_query(SQL("""
            SELECT l.account_id, COALESCE(SUM(l.balance), 0) FROM account_move_line l
             WHERE l.company_id = %s AND l.account_id IN %s AND l.parent_state = 'posted' AND l.date <= %s
             GROUP BY l.account_id
        """, company.id, tuple(accounts.ids), date))
        by_id = {r[0]: float(r[1]) for r in rows}
        detail = [{'id': a.id, 'name': a.display_name, 'balance': by_id.get(a.id, 0.0)} for a in accounts]
        return sum(by_id.values()), detail

    @api.model
    def _history(self, company, weeks, monday):
        """Closing cash at the end of each of the last `weeks` weeks."""
        accounts = self._liquidity_accounts(company)
        out = []
        if not accounts:
            return out
        start = monday - timedelta(days=7 * weeks)
        opening = self.env.execute_query(SQL("""
            SELECT COALESCE(SUM(balance), 0) FROM account_move_line
             WHERE company_id = %s AND account_id IN %s AND parent_state = 'posted' AND date < %s
        """, company.id, tuple(accounts.ids), start))[0][0]
        rows = self.env.execute_query(SQL("""
            SELECT (date - %s::date) / 7 AS wk, COALESCE(SUM(balance), 0) FROM account_move_line
             WHERE company_id = %s AND account_id IN %s AND parent_state = 'posted' AND date >= %s AND date < %s
             GROUP BY 1 ORDER BY 1
        """, start, company.id, tuple(accounts.ids), start, monday))
        by_week = {int(r[0]): float(r[1]) for r in rows}
        running = float(opening or 0.0)
        for i in range(weeks):
            running += by_week.get(i, 0.0)
            out.append({'label': (start + timedelta(days=7 * i)).strftime('%d %b'), 'closing': running})
        return out

    @api.model
    def get_data(self, options=None):
        self._check()
        self.env.flush_all()
        options = options or {}
        company = self.env.company
        today = fields.Date.context_today(self)
        weeks = int(options.get('weeks') or company.ebshel_cash_weeks or 13)
        rate = options.get('collection_rate')
        rate = (float(rate) if rate is not None else float(company.ebshel_cash_collection_rate or 100)) / 100.0
        per_partner = options.get('per_partner', True)
        include_draft = options.get('include_draft', True)
        delay_override = options.get('delay_days')
        monday = today - timedelta(days=today.weekday())
        horizon_end = monday + timedelta(days=7 * weeks - 1)
        buckets = [{'index': i, 'start': monday + timedelta(days=7 * i), 'end': monday + timedelta(days=7 * i + 6),
                    'in_ar': 0.0, 'in_items': 0.0, 'in_draft': 0.0, 'out_ap': 0.0, 'out_items': 0.0, 'out_draft': 0.0,
                    'detail': []} for i in range(weeks)]

        def week_of(day):
            day = max(day, today)
            if day > horizon_end:
                return None
            return buckets[(day - monday).days // 7]

        def add(day, key, amount, detail):
            b = week_of(day)
            if b is None:
                return
            b[key] += amount
            if len(b['detail']) < 60:
                b['detail'].append(dict(detail, week=b['index'], amount=amount, key=key))

        opening, cash_detail = self._cash_balance(company, today)
        AML = self.env['account.move.line']
        base = [('company_id', '=', company.id), ('reconciled', '=', False), ('parent_state', '=', 'posted')]
        receivable = AML.search(base + [('account_id.account_type', '=', 'asset_receivable')])
        habits = self.env['res.partner']._ebshel_pay_habits(receivable.mapped('partner_id.commercial_partner_id').ids) if per_partner else {}
        for line in receivable:
            if line.payment_id:
                continue                                   # a payment already in the bank
            due = line.date_maturity or line.date
            delay = int(delay_override) if delay_override is not None else int(round((habits.get(line.partner_id.commercial_partner_id.id) or {}).get('late') or 0))
            expected = max(due, today) + timedelta(days=max(delay, 0))
            amount = line.amount_residual * rate
            add(expected, 'in_ar', amount, {'id': line.id, 'model': 'account.move.line', 'name': line.move_id.name or line.name,
                                            'partner': line.partner_id.display_name or '', 'date': fields.Date.to_string(expected)})
        payable = AML.search(base + [('account_id.account_type', '=', 'liability_payable')])
        for line in payable:
            if line.payment_id:
                continue
            due = line.date_maturity or line.date
            add(due, 'out_ap', -line.amount_residual, {'id': line.id, 'model': 'account.move.line', 'name': line.move_id.name or line.name,
                                                       'partner': line.partner_id.display_name or '', 'date': fields.Date.to_string(max(due, today))})
        if include_draft:
            drafts = self.env['account.move'].search([('company_id', '=', company.id), ('state', '=', 'draft'),
                                                      ('move_type', 'in', ('out_invoice', 'out_refund', 'in_invoice', 'in_refund'))])
            for move in drafts:
                due = move.invoice_date_due or move.invoice_date or move.date or today
                amount = move.amount_total_signed if move.move_type.startswith('out') else -move.amount_total_signed
                if move.move_type.startswith('out'):
                    add(due, 'in_draft', amount * rate, {'id': move.id, 'model': 'account.move', 'name': move.name or _('Draft invoice'),
                                                         'partner': move.partner_id.display_name or '', 'date': fields.Date.to_string(max(due, today))})
                else:
                    add(due, 'out_draft', amount, {'id': move.id, 'model': 'account.move', 'name': move.name or _('Draft bill'),
                                                   'partner': move.partner_id.display_name or '', 'date': fields.Date.to_string(max(due, today))})
        for item in self.env['ebshel.cash.item'].search([('company_id', '=', company.id)]):
            for day in item._occurrences(today, horizon_end):
                key = 'in_items' if item.kind == 'in' else 'out_items'
                add(day, key, item.amount, {'id': item.id, 'model': 'ebshel.cash.item', 'name': item.name,
                                            'partner': item.partner_id.display_name or '', 'date': fields.Date.to_string(day)})
        running, lowest = opening, None
        for b in buckets:
            b['in'] = b['in_ar'] + b['in_items'] + b['in_draft']
            b['out'] = b['out_ap'] + b['out_items'] + b['out_draft']
            b['net'] = b['in'] - b['out']
            running += b['net']
            b['closing'] = running
            b['label'] = b['start'].strftime('%d %b')
            b['start'] = fields.Date.to_string(b['start'])
            b['end'] = fields.Date.to_string(b['end'])
            if lowest is None or running < lowest['value']:
                lowest = {'week': b['index'], 'label': b['label'], 'value': running}
        return {
            'today': fields.Date.to_string(today), 'weeks': buckets,
            'kpis': {'opening': opening, 'closing': running, 'total_in': sum(b['in'] for b in buckets),
                     'total_out': sum(b['out'] for b in buckets), 'lowest': lowest,
                     'negative_weeks': sum(1 for b in buckets if b['closing'] < 0),
                     'receivable': sum(l.amount_residual for l in receivable if not l.payment_id),
                     'payable': -sum(l.amount_residual for l in payable if not l.payment_id)},
            'cash_accounts': cash_detail, 'history': self._history(company, min(weeks, 13), monday),
            'params': {'weeks': weeks, 'collection_rate': round(rate * 100), 'per_partner': per_partner,
                       'include_draft': include_draft, 'delay_days': delay_override},
            'items': [{'id': i.id, 'name': i.name, 'kind': i.kind, 'amount': i.amount, 'date': fields.Date.to_string(i.date),
                       'frequency': i.frequency} for i in self.env['ebshel.cash.item'].search([('company_id', '=', company.id)])],
            'currency': {'symbol': company.currency_id.symbol, 'position': company.currency_id.position, 'decimals': company.currency_id.decimal_places},
            'can_write': self.env.su or self.env.user.has_groups('account.group_account_user,account.group_account_manager'),
        }

    @api.model
    def save_item(self, vals):
        if not self.env.su and not self.env.user.has_groups('account.group_account_user,account.group_account_manager'):
            raise AccessError(_("Only accountants can plan cash items."))
        Item = self.env['ebshel.cash.item']
        item_id = vals.pop('id', None)
        if item_id:
            Item.browse(item_id).write(vals)
            return item_id
        return Item.create(vals).id

    @api.model
    def delete_item(self, item_id):
        if not self.env.su and not self.env.user.has_groups('account.group_account_user,account.group_account_manager'):
            raise AccessError(_("Only accountants can plan cash items."))
        self.env['ebshel.cash.item'].browse(item_id).unlink()
        return True
