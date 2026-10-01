# -*- coding: utf-8 -*-
"""The ledger, summed the way a report needs it.

One SQL per column, grouped by account (or partner, or month), on the stored
columns of account_move_line - never a browse per line. Everything a report
shows is cut from these sums: a statement line is a set of accounts, a ledger
row is one account, an ageing bucket is a date arithmetic over open items.

Odoo 19 traps this engine lives with:

* an account's code is company-dependent, stored in ``code_store`` as JSON keyed
  by the ROOT company id - there is no ``code`` column;
* account and journal names are translated JSON;
* ``account_type`` is not stored on the line - it is read through the account;
* the analytic distribution is JSON ``{"<analytic account id>": <percent>}`` and
  an analytic filter must WEIGHT the amount by that percent, not just keep the
  line.
"""
import calendar
from ast import literal_eval
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.tools import SQL, date_utils
from odoo.tools.misc import format_date, formatLang, get_lang

PRESETS = [
    ('this_month', 'This month'),
    ('last_month', 'Last month'),
    ('this_quarter', 'This quarter'),
    ('last_quarter', 'Last quarter'),
    ('fiscal_year', 'This fiscal year'),
    ('last_fiscal_year', 'Last fiscal year'),
    ('this_year', 'This calendar year'),
    ('last_year', 'Last calendar year'),
    ('today', 'Up to today'),
    ('custom', 'Custom'),
]

COMPARISON_MODES = [
    ('none', 'No comparison'),
    ('previous', 'Previous period'),
    ('last_year', 'Same period last year'),
    ('custom', 'Custom period'),
]

JOURNAL_TYPES = ('sale', 'purchase', 'bank', 'cash', 'credit', 'general')
# the unit the figures are shown in; the ledger itself is never rounded
UNITS = {1: '', 1000: 'thousands', 100000: 'lakhs', 1000000: 'millions', 10000000: 'crores'}

# what decides which journal items a figure reads: two readings with the same values
# here read the same items
FILTER_KEYS = ('companies', 'posted_only', 'journals', 'partners', 'analytic', 'journal_types',
               'partner_categories', 'salespeople', 'teams', 'product_categories', 'unreconciled',
               'label', 'amount_min', 'amount_max')
NEGATIVE_STYLES = [('minus', '-100'), ('brackets', '(100)'), ('trailing', '100-')]


class Memo(dict):
    """What one reading of a report has already worked out: the accounts, the sums of
    each window, the writer of numbers. It lives exactly as long as that reading (it is
    made in `_prepare` and travels in the context), so nothing in it can be stale.

    Compared by identity: a context holding an equal-looking memo is not the same
    context, and two readings must never share one."""
    __slots__ = ()
    __hash__ = object.__hash__

    def __eq__(self, other):
        return self is other

    def __ne__(self, other):
        return self is not other


def group_digits(digits, grouping, separator):
    """'1234567' -> '1,234,567' the way a language groups: [3, 0] repeats threes,
    [3, 2, 0] is the Indian lakh and crore, -1 stops grouping."""
    if not separator or not grouping:
        return digits
    out, rest, last = [], digits, 0
    for size in grouping:
        if size == -1:
            break
        if size == 0:
            while last and len(rest) > last:
                out.append(rest[-last:])
                rest = rest[:-last]
            break
        if len(rest) <= size:
            break
        out.append(rest[-size:])
        rest = rest[:-size]
        last = size
    out.append(rest)
    return separator.join(reversed(out))


# account types whose balance starts again every fiscal year (no opening balance)
PL_ONLY_TYPES = ('income', 'income_other', 'expense', 'expense_other', 'expense_depreciation', 'expense_direct_cost', 'equity_unaffected')
PL_TYPES = ('income', 'income_other', 'expense', 'expense_other', 'expense_direct_cost',
            'expense_depreciation')
BS_TYPES = ('asset_receivable', 'asset_cash', 'asset_current', 'asset_non_current',
            'asset_prepayments', 'asset_fixed', 'liability_payable',
            'liability_credit_card', 'liability_current', 'liability_non_current',
            'equity', 'equity_unaffected')
LIQUIDITY_TYPES = ('asset_cash', 'liability_credit_card')

# How a line's window is cut from the column's period.
BALANCE_MODES = [
    ('flow', 'Movement in the period'),
    ('cumulative', 'Balance at the end of the period'),
    ('opening', 'Balance at the start of the period'),
    ('earnings_current', "This fiscal year's profit and loss"),
    ('earnings_previous', "Profit and loss before this fiscal year"),
]


class FinEngine(models.AbstractModel):
    _name = 'ebshel.fin.engine'
    _description = 'Financial report engine'

    # ------------------------------------------------------------------ dates
    @api.model
    def preset_dates(self, preset, today=None, company=None):
        """(date_from, date_to) for a preset, on the company's fiscal year."""
        today = today or fields.Date.context_today(self)
        company = company or self.env.company
        if preset == 'this_month':
            return date_utils.start_of(today, 'month'), date_utils.end_of(today, 'month')
        if preset == 'last_month':
            last = today - relativedelta(months=1)
            return date_utils.start_of(last, 'month'), date_utils.end_of(last, 'month')
        if preset == 'this_quarter':
            return date_utils.start_of(today, 'quarter'), date_utils.end_of(today, 'quarter')
        if preset == 'last_quarter':
            last = date_utils.start_of(today, 'quarter') - timedelta(days=1)
            return date_utils.start_of(last, 'quarter'), date_utils.end_of(last, 'quarter')
        if preset == 'fiscal_year':
            fy = company.compute_fiscalyear_dates(today)
            return fy['date_from'], fy['date_to']
        if preset == 'last_fiscal_year':
            fy = company.compute_fiscalyear_dates(today)
            last = company.compute_fiscalyear_dates(fy['date_from'] - timedelta(days=1))
            return last['date_from'], last['date_to']
        if preset == 'this_year':
            return date(today.year, 1, 1), date(today.year, 12, 31)
        if preset == 'last_year':
            return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
        if preset == 'today':
            fy = company.compute_fiscalyear_dates(today)
            return fy['date_from'], today
        return date_utils.start_of(today, 'month'), date_utils.end_of(today, 'month')

    @api.model
    def fiscal_year_start(self, day, company=None):
        company = company or self.env.company
        return company.compute_fiscalyear_dates(day)['date_from']

    @api.model
    def shift_period(self, date_from, date_to, mode, steps=1):
        """The period `steps` back: a whole month/quarter/year moves by its own
        length, anything else by its number of days."""
        span = (date_to - date_from).days + 1
        if date_from.day == 1 and date_to == date_utils.end_of(date_to, 'month'):
            months = (date_to.year - date_from.year) * 12 + date_to.month - date_from.month + 1
            if mode == 'last_year':
                months = 12
            start = date_from - relativedelta(months=months * steps)
            end = start + relativedelta(months=months) - timedelta(days=1)
            if mode == 'last_year':
                end = date_to - relativedelta(years=steps)
                start = date_from - relativedelta(years=steps)
            return start, end
        if mode == 'last_year':
            return date_from - relativedelta(years=steps), date_to - relativedelta(years=steps)
        end = date_from - timedelta(days=1 + span * (steps - 1))
        return end - timedelta(days=span - 1), end

    @api.model
    def fmt_date(self, day):
        """A date the way the reader writes dates (their language), never the database's."""
        if not day:
            return ''
        return format_date(self.env, fields.Date.to_date(day))

    @api.model
    def period_label(self, date_from, date_to, single=False):
        if single:
            return _('As of %s', self.fmt_date(date_to))
        if date_from.day == 1 and date_to == date_utils.end_of(date_to, 'month'):
            if date_from.month == date_to.month and date_from.year == date_to.year:
                return date_from.strftime('%b %Y')
            if date_from.month == 1 and date_to.month == 12 and date_from.year == date_to.year:
                return str(date_from.year)
            return '%s – %s' % (date_from.strftime('%b %Y'), date_to.strftime('%b %Y'))
        return '%s – %s' % (self.fmt_date(date_from), self.fmt_date(date_to))

    # ------------------------------------------------------------------ options
    @api.model
    def normalize(self, report, options):
        """A complete, safe options dict, whatever the client sent."""
        options = dict(options or {})
        company = self.env.company
        today = fields.Date.context_today(self)
        date_opt = dict(options.get('date') or {})
        preset = date_opt.get('preset') or report.default_preset or 'this_month'
        d_from = fields.Date.to_date(date_opt.get('from')) if date_opt.get('from') else None
        d_to = fields.Date.to_date(date_opt.get('to')) if date_opt.get('to') else None
        if preset != 'custom' or not d_to:
            d_from, d_to = self.preset_dates(preset, today, company)
        if not d_from or d_from > d_to:
            d_from = self.fiscal_year_start(d_to, company)
        options['date'] = {'preset': preset, 'from': fields.Date.to_string(d_from),
                           'to': fields.Date.to_string(d_to)}
        comp = dict(options.get('comparison') or {})
        mode = comp.get('mode') or ('none' if not report.allow_comparison else report.default_comparison or 'none')
        if not report.allow_comparison:
            mode = 'none'
        try:
            periods = max(1, min(12, int(comp.get('periods') or 1)))
        except (TypeError, ValueError):
            periods = 1
        options['comparison'] = {'mode': mode, 'periods': periods,
                                 'from': comp.get('from'), 'to': comp.get('to')}
        for key in ('journals', 'partners', 'analytic', 'expanded', 'companies'):
            values = options.get(key) or []
            if key == 'expanded':
                options[key] = [str(v) for v in values]
            else:
                options[key] = [int(v) for v in values if str(v).lstrip('-').isdigit()]
        allowed = self.env.companies.ids
        options['companies'] = [c for c in options['companies'] if c in allowed] or allowed
        options['posted_only'] = bool(options.get('posted_only', True))
        options['hierarchy'] = bool(options.get('hierarchy', False)) and report.allow_hierarchy
        options['unfold_all'] = bool(options.get('unfold_all', report.unfold_all_default))
        options['trend'] = bool(options.get('trend', False))
        options['growth'] = bool(options.get('growth', report.show_growth))
        options['show_zero'] = bool(options.get('show_zero', False))
        options['accounts_query'] = (options.get('accounts_query') or '').strip()
        options['search'] = (options.get('search') or '').strip()
        options['account_types'] = [t for t in (options.get('account_types') or []) if t in PL_TYPES + BS_TYPES]
        try:
            options['aged_interval'] = max(1, int(options.get('aged_interval') or report.aged_interval or 30))
            options['aged_buckets'] = max(2, min(8, int(options.get('aged_buckets') or report.aged_buckets or 5)))
        except (TypeError, ValueError):
            options['aged_interval'], options['aged_buckets'] = 30, 5
        # ---- the wider filters
        for key in ('partner_categories', 'salespeople', 'product_categories', 'teams'):
            options[key] = [int(v) for v in (options.get(key) or []) if str(v).isdigit()]
        options['journal_types'] = [t for t in (options.get('journal_types') or []) if t in JOURNAL_TYPES]
        options['unreconciled'] = bool(options.get('unreconciled', False))
        options['label'] = (options.get('label') or '').strip()[:80]
        for key in ('amount_min', 'amount_max'):
            try:
                options[key] = abs(float(options.get(key))) if options.get(key) not in (None, '', False) else None
            except (TypeError, ValueError):
                options[key] = None
        # ---- how the figures are shown
        try:
            unit = int(options.get('unit') or 1)
        except (TypeError, ValueError):
            unit = 1
        options['unit'] = unit if unit in UNITS else 1
        options['share'] = bool(options.get('share', False)) and bool(report.share_code)
        options['report_id'] = report.id
        return options

    @api.model
    def unit_label(self, options):
        return UNITS.get(options.get('unit') or 1, '')

    @api.model
    def period_columns(self, report, options):
        """The amount columns: the period, then each comparison period."""
        d_from = fields.Date.to_date(options['date']['from'])
        d_to = fields.Date.to_date(options['date']['to'])
        single = report.date_mode == 'single'
        cols = [{'key': 'p0', 'label': self.period_label(d_from, d_to, single),
                 'from': fields.Date.to_string(d_from), 'to': fields.Date.to_string(d_to),
                 'type': 'amount'}]
        comp = options['comparison']
        if comp['mode'] == 'custom' and comp.get('to'):
            c_to = fields.Date.to_date(comp['to'])
            c_from = fields.Date.to_date(comp['from']) if comp.get('from') else self.fiscal_year_start(c_to)
            cols.append({'key': 'p1', 'label': self.period_label(c_from, c_to, single),
                         'from': fields.Date.to_string(c_from), 'to': fields.Date.to_string(c_to),
                         'type': 'amount'})
        elif comp['mode'] in ('previous', 'last_year'):
            for step in range(1, comp['periods'] + 1):
                c_from, c_to = self.shift_period(d_from, d_to, comp['mode'], step)
                cols.append({'key': 'p%d' % step, 'label': self.period_label(c_from, c_to, single),
                             'from': fields.Date.to_string(c_from), 'to': fields.Date.to_string(c_to),
                             'type': 'amount'})
        if len(cols) > 1 and options.get('growth'):
            cols.append({'key': 'growth', 'label': _('Change'), 'type': 'growth',
                         'from': cols[0]['from'], 'to': cols[0]['to']})
        return cols

    # ------------------------------------------------------------------ SQL parts
    @api.model
    def _root_company_keys(self, options):
        """The keys an account's code may be stored under, the reader's own company first."""
        keys = []
        for company in self.env['res.company'].browse(options['companies']).sudo():
            key = str(company.root_id.id)
            if key not in keys:
                keys.append(key)
        return keys

    @api.model
    def code_sql(self, options, alias='a'):
        # a code is stored per company: with two companies open, an account of the second has
        # no code under the first one's key - it came out blank and no prefix rule could find it
        store = SQL.identifier(alias, 'code_store')
        return SQL("COALESCE(%s)", SQL(", ").join(SQL("%s->>%s", store, key) for key in self._root_company_keys(options)))

    @api.model
    def name_sql(self, alias='a', field='name'):
        lang = self.env.lang or 'en_US'
        col = SQL.identifier(alias, field)
        return SQL("COALESCE(%s->>%s, %s->>'en_US')", col, lang, col)

    @api.model
    def base_where(self, options, alias='l'):
        """Company, state, journal, partner and analytic filters as SQL."""
        l = lambda f: SQL.identifier(alias, f)              # noqa: E731
        parts = [SQL("%s IN %s", l('company_id'), tuple(options['companies']))]
        if options['posted_only']:
            parts.append(SQL("%s = 'posted'", l('parent_state')))
        else:
            parts.append(SQL("%s IN ('posted', 'draft')", l('parent_state')))
        if options.get('journals'):
            parts.append(SQL("%s IN %s", l('journal_id'), tuple(options['journals'])))
        if options.get('partners'):
            parts.append(SQL("%s IN %s", l('partner_id'), tuple(options['partners'])))
        if options.get('analytic'):
            keys = [str(i) for i in options['analytic']]
            parts.append(SQL("%s ?| %s::text[]", l('analytic_distribution'), keys))
        if options.get('journal_types'):
            parts.append(SQL("%s IN (SELECT id FROM account_journal WHERE type IN %s)",
                             l('journal_id'), tuple(options['journal_types'])))
        if options.get('partner_categories'):
            parts.append(SQL("%s IN (SELECT partner_id FROM res_partner_res_partner_category_rel WHERE category_id IN %s)",
                             l('partner_id'), tuple(options['partner_categories'])))
        if options.get('salespeople'):
            parts.append(SQL("%s IN (SELECT id FROM account_move WHERE invoice_user_id IN %s)",
                             l('move_id'), tuple(options['salespeople'])))
        if options.get('teams') and 'team_id' in self.env['account.move']._fields:
            parts.append(SQL("%s IN (SELECT id FROM account_move WHERE team_id IN %s)",
                             l('move_id'), tuple(options['teams'])))
        if options.get('product_categories'):
            categs = self.env['product.category'].sudo().search([('id', 'child_of', options['product_categories'])]).ids
            parts.append(SQL("""%s IN (SELECT pp.id FROM product_product pp JOIN product_template pt ON pt.id = pp.product_tmpl_id
                                        WHERE pt.categ_id IN %s)""", l('product_id'), tuple(categs or [0])))
        if options.get('unreconciled'):
            parts.append(SQL("%s IS NULL AND %s IN (SELECT id FROM account_account WHERE reconcile)",
                             l('full_reconcile_id'), l('account_id')))
        if options.get('label'):
            like = '%' + options['label'] + '%'
            parts.append(SQL("(%s ILIKE %s OR %s ILIKE %s OR %s ILIKE %s)",
                             l('name'), like, l('ref'), like, l('move_name'), like))
        if options.get('amount_min') is not None:
            parts.append(SQL("ABS(%s) >= %s", l('balance'), options['amount_min']))
        if options.get('amount_max') is not None:
            parts.append(SQL("ABS(%s) <= %s", l('balance'), options['amount_max']))
        return SQL(" AND ").join(parts)

    @api.model
    def filter_domain(self, options):
        """The same wider filters as an ORM domain, for the lists a figure opens."""
        dom = []
        if options.get('journal_types'):
            dom.append(('journal_id.type', 'in', options['journal_types']))
        if options.get('partner_categories'):
            dom.append(('partner_id.category_id', 'in', options['partner_categories']))
        if options.get('salespeople'):
            dom.append(('move_id.invoice_user_id', 'in', options['salespeople']))
        if options.get('teams') and 'team_id' in self.env['account.move']._fields:
            dom.append(('move_id.team_id', 'in', options['teams']))
        if options.get('product_categories'):
            dom.append(('product_id.categ_id', 'child_of', options['product_categories']))
        if options.get('unreconciled'):
            dom += [('full_reconcile_id', '=', False), ('account_id.reconcile', '=', True)]
        if options.get('label'):
            dom += ['|', '|', ('name', 'ilike', options['label']), ('ref', 'ilike', options['label']),
                    ('move_name', 'ilike', options['label'])]
        if options.get('amount_min') is not None:
            dom += ['|', ('balance', '>=', options['amount_min']), ('balance', '<=', -options['amount_min'])]
        if options.get('amount_max') is not None:
            dom += [('balance', '<=', options['amount_max']), ('balance', '>=', -options['amount_max'])]
        return dom

    @api.model
    def weight_sql(self, options, alias='l'):
        """1, or the share of the line that belongs to the selected analytic accounts."""
        if not options.get('analytic'):
            return SQL("1")
        keys = [str(i) for i in options['analytic']]
        return SQL("""(SELECT COALESCE(SUM(v.value::numeric), 0) / 100.0
                        FROM jsonb_each_text(%s) v WHERE v.key = ANY(%s::text[]))""",
                   SQL.identifier(alias, 'analytic_distribution'), keys)

    @api.model
    def date_where(self, mode, date_from, date_to, options, alias='l', account_alias='a'):
        """The window a balance mode reads."""
        d = SQL.identifier(alias, 'date')
        company = self.env['ebshel.fin.engine'].lead_company(options)
        if mode == 'flow':
            return SQL("%s >= %s AND %s <= %s", d, date_from, d, date_to)
        if mode == 'cumulative':
            return SQL("%s <= %s", d, date_to)
        if mode == 'opening':
            return SQL("%s < %s", d, date_from)
        fy_start = self.fiscal_year_start(date_to, company)
        if mode == 'earnings_current':
            return SQL("%s >= %s AND %s <= %s", d, fy_start, d, date_to)
        if mode == 'earnings_previous':
            return SQL("%s < %s", d, fy_start)
        if mode == 'initial':
            # Opening balance for a ledger: everything before the period for balance
            # sheet accounts, only this fiscal year's part for profit and loss ones.
            fy_from = self.fiscal_year_start(date_from, company)
            return SQL("%s < %s AND (%s OR %s >= %s)", d, date_from,
                       self.carries_opening_sql(account_alias), d, fy_from)
        if mode == 'ledger':
            # a ledger's closing balance: its opening and the period together
            fy_from = self.fiscal_year_start(date_from, company)
            return SQL("%s <= %s AND (%s OR %s >= %s)", d, date_to,
                       self.carries_opening_sql(account_alias), d, fy_from)
        raise ValueError(mode)

    # ------------------------------------------------------------------ one reading, one memo
    @api.model
    def lead_company(self, options):
        """The company a reading is made FOR: the one the reader is in, when it is among
        those being read; the first of them otherwise. Its currency, and the way it
        wants its reports written, are the reading's. (The first of the list was taken
        before - with two companies open that is not always the reader's own.)"""
        ids = options.get('companies') or []
        if self.env.company.id in ids or not ids:
            return self.env.company
        return self.env['res.company'].browse(ids[0])

    @api.model
    def _memo(self):
        memo = self.env.context.get('fin_memo')
        return memo if isinstance(memo, Memo) else None

    @api.model
    def _filters_key(self, options):
        return tuple(tuple(v) if isinstance(v, (list, tuple)) else v
                     for v in (options.get(k) for k in FILTER_KEYS))

    @api.model
    def preload_sums(self, options, windows):
        """Every window a report is about to ask for, read in ONE pass over the ledger.

        A balance sheet asked for three windows and a ledger for two, each a scan of
        every journal item: the scan is what costs, not the sum, so the windows are
        summed side by side with FILTER and the pass is made once. Without a memo
        (a caller outside a prepared reading) nothing is kept and nothing is read."""
        memo = self._memo()
        if memo is None:
            return
        fkey = self._filters_key(options)
        todo = []
        for mode, date_from, date_to in windows:
            key = ('sums', fkey, mode, date_from, date_to)
            if key not in memo and key not in (t[0] for t in todo):
                todo.append((key, self.date_where(mode, date_from, date_to, options)))
        if not todo:
            return
        w = self.weight_sql(options)
        picks = SQL(", ").join(
            SQL("SUM(l.balance * %s) FILTER (WHERE %s), SUM(l.debit * %s) FILTER (WHERE %s), "
                "SUM(l.credit * %s) FILTER (WHERE %s), COUNT(*) FILTER (WHERE %s)",
                w, cond, w, cond, w, cond, cond) for _key, cond in todo)
        rows = self.env.execute_query(SQL("""
            SELECT l.account_id, %s
              FROM account_move_line l
              JOIN account_account a ON a.id = l.account_id
             WHERE %s AND (%s)
             GROUP BY l.account_id
        """, picks, self.base_where(options), SQL(" OR ").join(SQL("(%s)", cond) for _key, cond in todo)))
        for i, (key, _cond) in enumerate(todo):
            table = {}
            for row in rows:
                balance, debit, credit, count = row[1 + 4 * i:5 + 4 * i]
                if count:
                    table[row[0]] = {'balance': float(balance or 0), 'debit': float(debit or 0),
                                     'credit': float(credit or 0), 'count': count}
            memo[key] = table

    # ------------------------------------------------------------------ sums
    @api.model
    def sums_by_account(self, options, mode, date_from, date_to, account_ids=None,
                        extra_where=None):
        """{account_id: {'balance', 'debit', 'credit', 'count'}} for one window."""
        memo = self._memo()
        if memo is not None and extra_where is None:
            table = memo.get(('sums', self._filters_key(options), mode, date_from, date_to))
            if table is not None:
                if account_ids is None:
                    return dict(table)
                wanted = set(account_ids)
                return {a: v for a, v in table.items() if a in wanted}
        where = [self.base_where(options), self.date_where(mode, date_from, date_to, options)]
        if account_ids is not None:
            if not account_ids:
                return {}
            where.append(SQL("l.account_id IN %s", tuple(account_ids)))
        if extra_where is not None:
            where.append(extra_where)
        w = self.weight_sql(options)
        rows = self.env.execute_query(SQL("""
            SELECT l.account_id, SUM(l.balance * %s), SUM(l.debit * %s), SUM(l.credit * %s), COUNT(*)
              FROM account_move_line l
              JOIN account_account a ON a.id = l.account_id
             WHERE %s
             GROUP BY l.account_id
        """, w, w, w, SQL(" AND ").join(where)))
        return {r[0]: {'balance': float(r[1] or 0), 'debit': float(r[2] or 0),
                       'credit': float(r[3] or 0), 'count': r[4]} for r in rows}

    @api.model
    def sums_by(self, options, group_sql, mode, date_from, date_to, account_ids=None,
                extra_where=None, joins=None, order=None, limit=None):
        """Generic grouped sums: `group_sql` is the SELECT/GROUP BY expression."""
        where = [self.base_where(options), self.date_where(mode, date_from, date_to, options)]
        if account_ids is not None:
            if not account_ids:
                return []
            where.append(SQL("l.account_id IN %s", tuple(account_ids)))
        if extra_where is not None:
            where.append(extra_where)
        w = self.weight_sql(options)
        return self.env.execute_query(SQL("""
            SELECT %s AS grp, SUM(l.balance * %s), SUM(l.debit * %s), SUM(l.credit * %s), COUNT(*)
              FROM account_move_line l
              JOIN account_account a ON a.id = l.account_id
              %s
             WHERE %s
             GROUP BY grp
             %s %s
        """, group_sql, w, w, w, joins or SQL(""), SQL(" AND ").join(where),
             SQL("ORDER BY %s", order) if order is not None else SQL("ORDER BY grp"),
             SQL("LIMIT %s", limit) if limit else SQL("")))

    @api.model
    def monthly(self, options, mode, account_ids, months=12, date_to=None):
        """[(month_start, balance)] for the last `months` months ending in date_to's
        month - the movement each month for flows, the closing balance for stocks."""
        if not account_ids:
            return []
        d_to = date_to or fields.Date.to_date(options['date']['to'])
        end = date_utils.end_of(d_to, 'month')
        start = date_utils.start_of(end - relativedelta(months=months - 1), 'month')
        rows = self.sums_by(options, SQL("date_trunc('month', l.date)::date"), 'flow', start, end,
                            account_ids=account_ids)
        by_month = {r[0]: float(r[1] or 0) for r in rows}
        out, running = [], 0.0
        if mode in ('cumulative', 'opening'):
            before = self.sums_by_account(options, 'opening', start, start, account_ids=account_ids)
            running = sum(v['balance'] for v in before.values())
        for i in range(months):
            month = date_utils.start_of(start + relativedelta(months=i), 'month')
            value = by_month.get(month, 0.0)
            if mode in ('cumulative', 'opening'):
                running += value
                out.append((month, running))
            else:
                out.append((month, value))
        return out

    @api.model
    def monthly_table(self, options, account_ids, months=12, date_to=None):
        """(months, {account: {month: movement}}, {account: balance before the first month})
        for many accounts at once. A trend per line used to be a query per line - 142 of
        them to open the general ledger with its trends on; this is two."""
        d_to = date_to or fields.Date.to_date(options['date']['to'])
        end = date_utils.end_of(d_to, 'month')
        start = date_utils.start_of(end - relativedelta(months=months - 1), 'month')
        span = [date_utils.start_of(start + relativedelta(months=i), 'month') for i in range(months)]
        moves, before = {}, {}
        if account_ids:
            rows = self.env.execute_query(SQL("""
                SELECT l.account_id, date_trunc('month', l.date)::date, SUM(l.balance * %s)
                  FROM account_move_line l
                  JOIN account_account a ON a.id = l.account_id
                 WHERE %s AND %s AND l.account_id IN %s
                 GROUP BY 1, 2
            """, self.weight_sql(options), self.base_where(options),
                self.date_where('flow', start, end, options), tuple(account_ids)))
            for account, month, value in rows:
                moves.setdefault(account, {})[month] = float(value or 0)
            before = {a: v['balance'] for a, v in self.sums_by_account(
                options, 'opening', start, start, account_ids=account_ids).items()}
        return span, moves, before

    @api.model
    def monthly_of(self, table, mode, account_ids):
        """One line's twelve months out of `monthly_table`: what moved each month for a
        flow, where the balance stood at each month's end for a stock."""
        span, moves, before = table
        stock = mode in ('cumulative', 'opening')
        running = sum(before.get(a, 0.0) for a in account_ids) if stock else 0.0
        out = []
        for month in span:
            value = sum(moves.get(a, {}).get(month, 0.0) for a in account_ids)
            if stock:
                running += value
            out.append((month, running if stock else value))
        return out

    # ------------------------------------------------------------------ accounts
    @api.model
    def carries_opening_sql(self, alias):
        """True for accounts whose balance carries over a year end (Odoo 19 no longer
        stores include_initial_balance; this is the same rule as its compute)."""
        return SQL("(%s NOT IN %s)", SQL.identifier(alias, 'account_type'), tuple(PL_ONLY_TYPES))

    @api.model
    def accounts(self, options):
        """Every account of the companies: {id: {code, name, type, initial, group}}."""
        memo = self._memo()
        key = ('accounts', tuple(options['companies']), self.env.lang)
        if memo is not None and key in memo:
            return memo[key]
        rows = self.env.execute_query(SQL("""
            SELECT a.id, %s, %s, a.account_type, %s, a.active
              FROM account_account a
              JOIN account_account_res_company_rel r ON r.account_account_id = a.id
             WHERE r.res_company_id IN %s
             ORDER BY 2
        """, self.code_sql(options), self.name_sql(), self.carries_opening_sql('a'), tuple(options['companies'])))
        out = {}
        for aid, code, name, atype, initial, active in rows:
            out[aid] = {'id': aid, 'code': code or '', 'name': name or '', 'type': atype,
                        'initial': bool(initial), 'active': bool(active)}
        if memo is not None:
            memo[key] = out
        return out

    @api.model
    def select_accounts(self, accounts, prefixes='', exclude='', types='', account_ids=None,
                        tag_ids=None, domain=None, query=''):
        """The ids of the accounts a rule names. Rules AND together when several
        are given; an empty rule set selects nothing."""
        chosen = None

        def keep(ids):
            nonlocal chosen
            chosen = set(ids) if chosen is None else chosen & set(ids)

        if prefixes:
            pfx = tuple(p.strip() for p in prefixes.split(',') if p.strip())
            keep(a['id'] for a in accounts.values() if a['code'].startswith(pfx))
        if types:
            wanted = {t.strip() for t in types.split(',') if t.strip()}
            keep(a['id'] for a in accounts.values() if a['type'] in wanted)
        if account_ids:
            keep(account_ids)
        if tag_ids:
            tagged = self.env['account.account'].with_context(active_test=False).search(
                [('tag_ids', 'in', list(tag_ids))]).ids
            keep(tagged)
        if domain:
            query = self.env['account.move.line'].with_context(active_test=False)._search(domain)
            rows = self.env.execute_query(SQL("SELECT DISTINCT account_id FROM (%s) q",
                                              query.select(SQL("account_move_line.account_id"))))
            keep(r[0] for r in rows)
        if query:
            q = query.lower()
            keep(a['id'] for a in accounts.values()
                 if a['code'].lower().startswith(q) or q in a['name'].lower())
        if chosen is None:
            return set()
        if exclude:
            pfx = tuple(p.strip() for p in exclude.split(',') if p.strip())
            chosen = {i for i in chosen if not accounts.get(i, {}).get('code', '').startswith(pfx)}
        return chosen

    # ------------------------------------------------------------------ domains
    @api.model
    def domain(self, options, mode, date_from, date_to, account_ids=None):
        """The ORM domain that opens the same journal items the SQL summed."""
        company = self.env['ebshel.fin.engine'].lead_company(options)
        dom = [('company_id', 'in', options['companies'])]
        dom.append(('parent_state', '=', 'posted') if options['posted_only']
                   else ('parent_state', 'in', ('posted', 'draft')))
        if options.get('journals'):
            dom.append(('journal_id', 'in', options['journals']))
        if options.get('partners'):
            dom.append(('partner_id', 'in', options['partners']))
        if options.get('analytic'):
            dom.append(('analytic_distribution', 'in', options['analytic']))
        dom += self.filter_domain(options)
        if mode == 'flow':
            dom += [('date', '>=', date_from), ('date', '<=', date_to)]
        elif mode == 'cumulative':
            dom.append(('date', '<=', date_to))
        elif mode == 'opening':
            dom.append(('date', '<', date_from))
        elif mode == 'earnings_current':
            dom += [('date', '>=', self.fiscal_year_start(date_to, company)), ('date', '<=', date_to)]
        elif mode == 'earnings_previous':
            dom.append(('date', '<', self.fiscal_year_start(date_to, company)))
        elif mode == 'initial':
            fy_from = self.fiscal_year_start(date_from, company)
            dom += [('date', '<', date_from), '|', ('account_id.account_type', 'not in', PL_ONLY_TYPES),
                    ('date', '>=', fy_from)]
        elif mode == 'ledger':
            fy_from = self.fiscal_year_start(date_from, company)
            dom += [('date', '<=', date_to), '|', ('account_id.account_type', 'not in', PL_ONLY_TYPES),
                    ('date', '>=', fy_from)]
        if account_ids is not None:
            dom.append(('account_id', 'in', list(account_ids)))
        return dom

    # ------------------------------------------------------------------ formatting
    @api.model
    def currency(self, options):
        return self.env['ebshel.fin.engine'].lead_company(options).currency_id

    @api.model
    def _writer(self, digits, currency=None):
        """A function that writes a number the way `formatLang` does, without asking the
        language and the currency again for every figure. `formatLang` cost 85 microseconds
        a cell - more than half the time of an aged report of 800 partners, which is 6,000
        cells. What it would have written is checked once, when the writer is made; a
        language it cannot follow is handed back to `formatLang`."""
        memo = self._memo()
        style = self.env.context.get('fin_negative') or 'minus'
        key = ('writer', self.env.lang, digits, currency.id if currency else 0, style)
        if memo is not None and key in memo:
            return memo[key]
        lang = get_lang(self.env)
        try:
            grouping = [int(g) for g in literal_eval(lang.grouping or '[]')]
        except (ValueError, SyntaxError, TypeError):
            grouping = []
        point, comma = lang.decimal_point or '.', lang.thousands_sep or ''
        symbol = currency.symbol if currency else ''
        before = bool(currency) and currency.position == 'before'

        def plain(value):
            text = '%.*f' % (digits, abs(value))
            whole, _dot, part = text.partition('.')
            body = group_digits(whole, grouping, comma) + (point + part if part else '')
            return ('-' + body) if value < 0 and body.strip('0' + point + comma) else body

        def dressed(text):
            if not currency:
                return text
            return '%s\N{NO-BREAK SPACE}%s' % ((symbol, text) if before else (text, symbol))

        def slow(value):
            return formatLang(self.env, value, currency_obj=currency) if currency \
                else formatLang(self.env, value, digits=digits)

        def write(value):
            if value < 0 and style != 'minus':
                text = dressed(plain(-value))
                # a figure that rounds to nothing is nothing, in any style
                if not text.strip('0' + point + comma + symbol + '\N{NO-BREAK SPACE}'):
                    return text
                return '(%s)' % text if style == 'brackets' else text + '-'
            return dressed(plain(value))

        try:
            same = all(dressed(plain(v)) == slow(v) for v in (1234567.891, -9876.5, 0.0, 12.0, 100000.0))
        except Exception:
            same = False
        writer = write if same else slow
        if memo is not None:
            memo[key] = writer
        return writer

    @api.model
    def fmt(self, value, currency, display='amount'):
        if value is None:
            return ''
        unit = self.env.context.get('fin_unit') or 1
        if display == 'amount':
            value = value / unit
            if abs(value) < 0.005:
                value = 0.0          # never "-0.00": three rupees in thousands is nothing, not minus nothing
            # shown in thousands, lakhs, millions or crores: no symbol, the heading says which
            return self._writer(2)(value) if unit != 1 else self._writer(currency.decimal_places, currency)(value)
        if display == 'percent':
            return '%s%%' % self._writer(1)(value)
        if display == 'ratio':
            return self._writer(2)(value)
        if display == 'days':
            return _('%s days', self._writer(0)(value))
        if display == 'count':
            return self._writer(0)(value)
        return self._writer(currency.decimal_places, currency)(value)

    @api.model
    def growth(self, current, previous):
        if not previous or abs(previous) < 0.005:
            return None
        return round((current - previous) / abs(previous) * 100, 1)

    @api.model
    def days_in(self, date_from, date_to):
        return (date_to - date_from).days + 1
