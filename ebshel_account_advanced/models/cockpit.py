# -*- coding: utf-8 -*-
"""The home page of the accounting app: the profit and loss as a waterfall, the
balance sheet as two stacks, twelve months of trend, the ratios a banker asks for,
and the work waiting on the team - every figure opening what is behind it."""
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import AccessError
from odoo.tools import SQL, date_utils

INCOME = ('income', 'income_other')
EXPENSES = ('expense', 'expense_other', 'expense_direct_cost', 'expense_depreciation')


class FinanceCockpit(models.AbstractModel):
    _name = 'ebshel.finance.cockpit'
    _description = 'Finance cockpit'

    @api.model
    def _check(self):
        if not self.env.su and not self.env.user.has_groups('account.group_account_readonly,account.group_account_invoice'):
            raise AccessError(_("The finance cockpit is for the accounting team."))

    @api.model
    def _period(self, key, today, company):
        fy = company.compute_fiscalyear_dates(today)
        if key == 'last_month':
            start = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
            return start, date_utils.end_of(start, 'month'), start.strftime('%B %Y')
        if key == 'quarter':
            start = date_utils.start_of(today, 'quarter')
            return start, today, _('This quarter')
        if key == 'fy':
            return fy['date_from'], today, _('Fiscal year to date')
        if key == 'last_fy':
            prev = company.compute_fiscalyear_dates(fy['date_from'] - timedelta(days=1))
            return prev['date_from'], prev['date_to'], _('Last fiscal year')
        start = today.replace(day=1)
        return start, today, start.strftime('%B %Y')

    @api.model
    def _values(self, key, date_from, date_to, comparison=True):
        report = self.env['ebshel.fin.report'].by_key(key)
        options = {'date': {'preset': 'custom', 'from': fields.Date.to_string(date_from), 'to': fields.Date.to_string(date_to)}}
        if comparison:
            options['comparison'] = {'mode': 'previous', 'periods': 1}
        data = report.get_report_data(options)
        out = {}
        for line in data['lines']:
            code = line.get('code')
            if code:
                cols = [c.get('value') for c in line['columns']]
                out[code] = {'now': cols[0] or 0.0, 'before': (cols[1] if len(cols) > 1 and comparison else None) or 0.0,
                             'id': line['id']}
        return out, data['options']

    @api.model
    def _months(self, company, today, months=12):
        first = (today.replace(day=1) - relativedelta(months=months - 1))
        rows = self.env.execute_query(SQL("""
            SELECT date_trunc('month', l.date)::date AS m,
                   COALESCE(SUM(-l.balance) FILTER (WHERE a.account_type IN %(inc)s), 0),
                   COALESCE(SUM(l.balance) FILTER (WHERE a.account_type IN %(exp)s), 0),
                   COALESCE(SUM(l.balance) FILTER (WHERE a.account_type IN ('asset_cash', 'liability_credit_card')), 0)
              FROM account_move_line l JOIN account_account a ON a.id = l.account_id
             WHERE l.company_id = %(co)s AND l.parent_state = 'posted' AND l.date >= %(first)s AND l.date <= %(today)s
             GROUP BY 1 ORDER BY 1
        """, inc=INCOME, exp=EXPENSES, co=company.id, first=first, today=today))
        opening = self.env.execute_query(SQL("""
            SELECT COALESCE(SUM(l.balance), 0) FROM account_move_line l JOIN account_account a ON a.id = l.account_id
             WHERE l.company_id = %s AND l.parent_state = 'posted' AND l.date < %s
               AND a.account_type IN ('asset_cash', 'liability_credit_card')
        """, company.id, first))[0][0]
        by = {r[0]: r for r in rows}
        out, cash = [], float(opening or 0.0)
        for i in range(months):
            m = first + relativedelta(months=i)
            r = by.get(m)
            rev, exp, dcash = (float(r[1]), float(r[2]), float(r[3])) if r else (0.0, 0.0, 0.0)
            cash += dcash
            out.append({'month': m.strftime('%b %y'), 'from': fields.Date.to_string(m),
                        'to': fields.Date.to_string(date_utils.end_of(m, 'month')),
                        'revenue': rev, 'expenses': exp, 'profit': rev - exp, 'cash': cash})
        return out

    @api.model
    def _top(self, company, date_from, date_to, types, sign, limit=6):
        rows = self.env.execute_query(SQL("""
            SELECT l.account_id, SUM(l.balance) * %s AS v FROM account_move_line l JOIN account_account a ON a.id = l.account_id
             WHERE l.company_id = %s AND l.parent_state = 'posted' AND l.date >= %s AND l.date <= %s AND a.account_type IN %s
             GROUP BY 1 HAVING SUM(l.balance) * %s > 0 ORDER BY 2 DESC LIMIT %s
        """, sign, company.id, date_from, date_to, tuple(types), sign, limit))
        accounts = {a.id: a for a in self.env['account.account'].browse([r[0] for r in rows])}
        return [{'id': r[0], 'name': accounts[r[0]].display_name, 'value': float(r[1])} for r in rows]

    @api.model
    def _todo(self, company, today):
        todo = []

        def add(key, label, count, action, amount=None, tone='warn'):
            if count:
                todo.append({'key': key, 'label': label, 'count': count, 'amount': amount, 'action': action, 'tone': tone})
        StLine = self.env['account.bank.statement.line']
        n = StLine.search_count([('company_id', '=', company.id), ('is_reconciled', '=', False)])
        add('bank', _('bank lines to reconcile'), n, {'tag': 'ebshel_bank_rec'})
        drafts = self.env['account.move'].search_count([('company_id', '=', company.id), ('state', '=', 'draft'),
                                                        ('date', '<=', today)])
        add('drafts', _('draft entries dated today or earlier'), drafts,
            {'model': 'account.move', 'domain': [('state', '=', 'draft'), ('date', '<=', fields.Date.to_string(today))]})
        try:
            action_due = self.env['res.partner'].with_company(company).search_count([('ebshel_followup_state', '=', 'action')])
        except Exception:                                              # noqa: BLE001
            action_due = 0
        add('followup', _('customers due a reminder'), action_due, {'tag': 'ebshel_followup_desk'})
        findings = self.env['ebshel.ledger.finding'].search_count([('company_id', '=', company.id), ('state', '=', 'open'),
                                                                    ('severity', '=', 'error')])
        add('health', _('ledger errors found'), findings, {'tag': 'ebshel_ledger_health'}, tone='bad')
        deferrals = self.env['ebshel.deferral.line'].search_count([('deferral_id.company_id', '=', company.id), ('state', '=', 'draft'),
                                                                    ('deferral_id.state', '=', 'running'), ('date', '<=', today)])
        add('deferrals', _('deferral months to recognise'), deferrals,
            {'model': 'ebshel.deferral', 'domain': [('state', '=', 'running')]})
        if 'ebshel.asset.line' in self.env:
            dep = self.env['ebshel.asset.line'].search_count([('asset_id.company_id', '=', company.id), ('state', '=', 'draft'),
                                                               ('asset_id.state', '=', 'running'), ('date', '<=', today)])
            add('assets', _('depreciation entries to post'), dep, {'tag': 'ebshel_asset_dashboard'})
        over = self.env['ebshel.budget.line'].search([('company_id', '=', company.id), ('state', '=', 'confirmed')]).filtered(
            lambda l: l.alert == 'over')
        add('budget', _('budget lines over budget'), len(over), {'tag': 'ebshel_budget_board'}, tone='bad')
        stale = self.env.execute_query(SQL("""
            SELECT COUNT(*) FROM account_move_line l JOIN account_move m ON m.id = l.move_id
              JOIN account_journal j ON j.default_account_id = l.account_id AND j.type = 'bank'
             WHERE l.company_id = %s AND l.parent_state = 'posted' AND l.ebshel_cleared_date IS NULL
               AND m.statement_line_id IS NULL AND l.date < %s
               AND EXISTS (SELECT 1 FROM account_move_line c WHERE c.account_id = l.account_id AND c.ebshel_cleared_date IS NOT NULL)
        """, company.id, today - timedelta(days=90)))[0][0]
        add('stale', _('bank items not seen by the bank in 90 days'), stale, {'tag': 'ebshel_bank_rec', 'params': {'tab': 'tick'}})
        return todo

    @api.model
    def get_data(self, period='month'):
        self._check()
        self.env.flush_all()
        company = self.env.company
        today = fields.Date.context_today(self)
        date_from, date_to, label = self._period(period, today, company)
        pl, pl_options = self._values('profit_loss', date_from, date_to)
        bs, _bs = self._values('balance_sheet', date_to, date_to, comparison=False)

        def v(src, code, when='now'):
            return (src.get(code) or {}).get(when, 0.0) or 0.0
        waterfall = [
            {'key': 'REV', 'label': _('Revenue'), 'value': v(pl, 'REV'), 'kind': 'total'},
            {'key': 'COGS', 'label': _('Cost of revenue'), 'value': -v(pl, 'COGS'), 'kind': 'step'},
            {'key': 'GP', 'label': _('Gross profit'), 'value': v(pl, 'GP'), 'kind': 'total'},
            {'key': 'OPEX', 'label': _('Operating expenses'), 'value': -v(pl, 'OPEX'), 'kind': 'step'},
            {'key': 'DEP', 'label': _('Depreciation'), 'value': -v(pl, 'DEP'), 'kind': 'step'},
            {'key': 'OINC', 'label': _('Other income'), 'value': v(pl, 'OINC'), 'kind': 'step'},
            {'key': 'OEXP', 'label': _('Other expenses'), 'value': -v(pl, 'OEXP'), 'kind': 'step'},
            {'key': 'NET', 'label': _('Net profit'), 'value': v(pl, 'NET'), 'kind': 'total'},
        ]
        rev, rev_b = v(pl, 'REV'), v(pl, 'REV', 'before')
        net, net_b = v(pl, 'NET'), v(pl, 'NET', 'before')
        cur_a, cur_l = v(bs, 'CUR_ASSETS'), v(bs, 'CUR_LIAB')
        cash, recv, pay = v(bs, 'CASH'), v(bs, 'RECV'), v(bs, 'PAYABLE')
        days = max((date_to - date_from).days + 1, 1)
        expenses = v(pl, 'COGS') + v(pl, 'OPEX')
        months = self._months(company, today)
        burn = [m['profit'] for m in months[-3:]]
        avg_burn = -sum(burn) / len(burn) if burn and sum(burn) < 0 else 0.0

        def growth(a, b):
            return round((a - b) / abs(b) * 100, 1) if b else None
        ratios = [
            {'key': 'gm', 'label': _('Gross margin'), 'value': round(v(pl, 'GP') / rev * 100, 1) if rev else None, 'unit': '%',
             'good': 30, 'bad': 10, 'hint': _('Gross profit ÷ revenue')},
            {'key': 'nm', 'label': _('Net margin'), 'value': round(net / rev * 100, 1) if rev else None, 'unit': '%',
             'good': 10, 'bad': 0, 'hint': _('Net profit ÷ revenue')},
            {'key': 'cr', 'label': _('Current ratio'), 'value': round(cur_a / cur_l, 2) if cur_l else None, 'unit': '×',
             'good': 1.5, 'bad': 1.0, 'hint': _('Current assets ÷ current liabilities')},
            {'key': 'qr', 'label': _('Quick ratio'), 'value': round((cash + recv) / cur_l, 2) if cur_l else None, 'unit': '×',
             'good': 1.0, 'bad': 0.7, 'hint': _('(Cash + receivables) ÷ current liabilities')},
            {'key': 'dso', 'label': _('Days to collect'), 'value': round(recv / rev * days) if rev else None, 'unit': ' d',
             'good': 45, 'bad': 90, 'lower': True, 'hint': _('Receivables ÷ revenue × days in the period')},
            {'key': 'dpo', 'label': _('Days to pay'), 'value': round(pay / expenses * days) if expenses else None, 'unit': ' d',
             'good': 30, 'bad': 90, 'lower': False, 'hint': _('Payables ÷ costs × days in the period')},
        ]
        for r in ratios:
            val = r['value']
            if val is None:
                r['tone'] = 'muted'
            elif r.get('lower'):
                r['tone'] = 'good' if val <= r['good'] else 'bad' if val >= r['bad'] else 'warn'
            else:
                r['tone'] = 'good' if val >= r['good'] else 'bad' if val < r['bad'] else 'warn'
        assets = [{'key': k, 'label': lbl, 'value': v(bs, k)} for k, lbl in (
            ('CASH', _('Bank and cash')), ('RECV', _('Receivables')), ('CUR_A', _('Other current')), ('PREPAID', _('Prepayments')),
            ('FIXED', _('Fixed assets')), ('NONCUR_ASSETS', _('Non-current')))]
        claims = [{'key': k, 'label': lbl, 'value': v(bs, k)} for k, lbl in (
            ('PAYABLE', _('Payables')), ('CUR_L', _('Other current liabilities')), ('CREDIT_CARD', _('Credit cards')),
            ('NONCUR_LIAB', _('Non-current liabilities')), ('EQUITY', _('Equity')))]
        c = company.currency_id
        return {
            'period': period, 'label': label, 'from': fields.Date.to_string(date_from), 'to': fields.Date.to_string(date_to),
            'compare_label': _('against the period before'),
            'kpis': [
                {'key': 'REV', 'label': _('Revenue'), 'value': rev, 'growth': growth(rev, rev_b), 'report': 'profit_loss'},
                {'key': 'NET', 'label': _('Net profit'), 'value': net, 'growth': growth(net, net_b), 'report': 'profit_loss',
                 'tone': 'good' if net >= 0 else 'bad'},
                {'key': 'CASH', 'label': _('Bank and cash'), 'value': cash, 'report': 'cash_book',
                 'sub': _('%s months of costs', round(cash / (expenses / days * 30), 1)) if expenses and cash > 0 else ''},
                {'key': 'RECV', 'label': _('Customers owe'), 'value': recv, 'report': 'aged_receivable'},
                {'key': 'PAYABLE', 'label': _('Owed to vendors'), 'value': -pay if pay < 0 else pay, 'report': 'aged_payable'},
                {'key': 'WC', 'label': _('Working capital'), 'value': cur_a - cur_l, 'report': 'balance_sheet',
                 'tone': 'good' if cur_a >= cur_l else 'bad'},
                {'key': 'RUNWAY', 'label': _('Runway'), 'value': round(cash / avg_burn, 1) if avg_burn else None,
                 'unit': _('months'), 'report': 'cash_flow', 'sub': _('at the last 3 months\' burn') if avg_burn else _('not burning cash')},
            ],
            'waterfall': waterfall, 'assets': assets, 'claims': claims,
            'balanced': abs(v(bs, 'CHECK')) < 1, 'months': months, 'ratios': ratios,
            'top_expenses': self._top(company, date_from, date_to, EXPENSES, 1),
            'top_income': self._top(company, date_from, date_to, INCOME, -1),
            'todo': self._todo(company, today),
            'currency': {'symbol': c.symbol, 'position': c.position, 'decimals': c.decimal_places},
        }
