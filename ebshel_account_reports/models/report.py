# -*- coding: utf-8 -*-
"""A report is a record: its kind names the handler that computes it, its lines
(for statement reports) say what to sum, and the record carries every switch the
screen offers. Users design their own the same way."""
import ast
import json
import operator
import re

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import SQL

from .engine import BALANCE_MODES, COMPARISON_MODES, PRESETS, Memo

DISPLAYS = [('amount', 'Amount'), ('percent', 'Percentage'), ('ratio', 'Ratio'),
            ('days', 'Days'), ('count', 'Count'), ('check', 'Balance check')]
ACCOUNT_TYPES = [
    ('asset_receivable', 'Receivable'), ('asset_cash', 'Bank and Cash'),
    ('asset_current', 'Current Assets'), ('asset_non_current', 'Non-current Assets'),
    ('asset_prepayments', 'Prepayments'), ('asset_fixed', 'Fixed Assets'),
    ('liability_payable', 'Payable'), ('liability_credit_card', 'Credit Card'),
    ('liability_current', 'Current Liabilities'), ('liability_non_current', 'Non-current Liabilities'),
    ('equity', 'Equity'), ('equity_unaffected', 'Current Year Earnings'),
    ('income', 'Income'), ('income_other', 'Other Income'), ('expense', 'Expenses'), ('expense_other', 'Other Expenses'),
    ('expense_depreciation', 'Depreciation'), ('expense_direct_cost', 'Cost of Revenue'),
    ('off_balance', 'Off-Balance Sheet'),
]

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {'abs': abs, 'min': min, 'max': max, 'round': round}


def evaluate_formula(expr, values):
    """A safe arithmetic over line codes: + - * / ( ) abs min max round, numbers,
    and the names in `values`. A division by zero reads as nothing."""
    tree = ast.parse(expr, mode='eval')

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in values:
                raise KeyError(node.id)
            return values[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            left, right = ev(node.left), ev(node.right)
            if left is None or right is None:
                return None
            if isinstance(node.op, ast.Div) and not right:
                return None
            return _OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            value = ev(node.operand)
            return None if value is None else _OPS[type(node.op)](value)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
            args = [ev(a) for a in node.args]
            if any(a is None for a in args):
                return None
            return float(_FUNCS[node.func.id](*args))
        raise ValueError(_("Not allowed in a formula: %s", ast.dump(node)[:40]))
    return ev(tree)


def formula_names(expr):
    return sorted({n.id for n in ast.walk(ast.parse(expr, mode='eval')) if isinstance(n, ast.Name)})


class FinReport(models.Model):
    _name = 'ebshel.fin.report'
    _description = 'Dynamic financial report'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    key = fields.Char(required=True, help="Stable identifier, used by menus and by other modules.")
    kind = fields.Selection(selection='_kind_selection', required=True, default='statement',
                            help="Which engine computes the report.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    custom = fields.Boolean(string='Designed here', default=True,
                            help="Built in the designer rather than shipped by a module.")
    description = fields.Text()
    company_id = fields.Many2one('res.company', help="Leave empty for every company.")
    line_ids = fields.One2many('ebshel.fin.report.line', 'report_id', string='Lines', copy=True)
    date_mode = fields.Selection([('range', 'A period'), ('single', 'As of a date')],
                                 default='range', required=True)
    default_preset = fields.Selection(PRESETS, default='this_month', required=True)
    allow_comparison = fields.Boolean(default=True)
    default_comparison = fields.Selection(COMPARISON_MODES, default='none')
    show_growth = fields.Boolean(string='Growth column', default=True)
    first_column = fields.Float(
        string='First column width', default=1.0,
        help="How wide the first column (the names) is, against its usual width: 1 is the "
             "usual width, 0.875 is seven eighths of it. What it gives up goes to the "
             "figures - on the screen, in the PDF and in the workbook alike.")
    share_code = fields.Char(string='Share of (line code)',
                             help="Common-size analysis: every amount can also be shown as a percentage of this line, "
                                  "e.g. REV on a profit and loss or ASSETS on a balance sheet.")
    allow_journals = fields.Boolean(string='Journal filter', default=True)
    allow_partners = fields.Boolean(string='Partner filter', default=False)
    allow_analytic = fields.Boolean(string='Analytic filter', default=True)
    allow_accounts = fields.Boolean(string='Account search', default=False)
    allow_hierarchy = fields.Boolean(string='Account groups', default=False)
    allow_posted_toggle = fields.Boolean(string='Draft entries toggle', default=True)
    unfold_all_default = fields.Boolean(string='Unfold all by default', default=False)
    ledger_account_types = fields.Char(
        default='asset_receivable,liability_payable',
        help="Partner ledger: the account types it walks (comma separated).")
    aged_interval = fields.Integer(string='Ageing interval (days)', default=30)
    aged_buckets = fields.Integer(string='Ageing buckets', default=5)
    action_id = fields.Many2one('ir.actions.client', readonly=True, copy=False)
    menu_id = fields.Many2one('ir.ui.menu', readonly=True, copy=False)
    line_count = fields.Integer(compute='_compute_line_count')

    _key_uniq = models.Constraint('UNIQUE(key)', 'Report keys must be unique.')

    @api.model
    def _kind_selection(self):
        out = []
        for name in sorted(self.env.registry):
            if name.startswith('ebshel.fin.handler.'):
                out.append((name[len('ebshel.fin.handler.'):], self.env[name]._kind_label))
        return out

    @api.depends('line_ids')
    def _compute_line_count(self):
        for report in self:
            report.line_count = len(report.line_ids)

    def _handler(self):
        self.ensure_one()
        name = 'ebshel.fin.handler.%s' % self.kind
        if name not in self.env:
            raise UserError(_("No engine is installed for '%s' reports.", self.kind))
        return self.env[name]

    def _prepare(self, options):
        """(engine, handler, options, columns), the display unit carried in the context
        so every figure - screen, workbook, PDF - is formatted the same way."""
        self.ensure_one()
        engine = self.env['ebshel.fin.engine']
        options = engine.normalize(self, options)
        company = self.env['ebshel.fin.engine'].lead_company(options).sudo()
        # one memo for this reading: the accounts, the sums and the writer of numbers are
        # worked out once and shared by everything the reading asks for
        carried = dict(fin_unit=options['unit'], fin_negative=company.ebshel_fin_negative or 'minus', fin_memo=Memo())
        handler = self._handler().with_context(**carried)
        options = handler.adjust_options(self, options)
        return engine.with_context(**carried), handler, options, handler.columns(self, options)

    @api.model
    def by_key(self, key):
        report = self.with_context(active_test=False).search([('key', '=', key)], limit=1)
        if not report:
            raise UserError(_("There is no report with the key '%s'.", key))
        return report

    # ------------------------------------------------------------------ what the screen calls
    @api.model
    def open_by_key(self, key, options=None):
        """The viewer's first call: the report behind a menu, with its data."""
        return self.by_key(key).get_report_data(options)

    def action_send_statements(self, options):
        """Statements of account: one PDF per partner, by email."""
        self.ensure_one()
        self.env.flush_all()                      # the SQL must see pending ORM writes (parent_state, balance)
        self._check_report_access()
        handler = self._handler()
        if not hasattr(handler, 'send_statements'):
            raise UserError(_("This report is not a statement of account."))
        return handler.send_statements(self, options)

    def _check_report_access(self):
        # the superuser (crons, scheduled sends, tests) is always let in
        if not self.env.su and not self.env.user.has_groups('account.group_account_readonly,account.group_account_invoice'):
            raise UserError(_("Financial reports are for the accounting team."))

    def get_report_data(self, options=None, lean=False):
        """Everything the viewer needs for one look at the report. `lean` is a reload
        under a report already on the screen: what the filters can be set to, the list
        of reports and the saved views are already there and are not sent again."""
        self.ensure_one()
        self.env.flush_all()                      # the SQL must see pending ORM writes (parent_state, balance)
        self._check_report_access()
        engine, handler, options, columns = self._prepare(options)
        lines = self._polish(handler.lines(self, options, columns))
        company = self.env['ebshel.fin.engine'].lead_company(options).sudo()
        if company.ebshel_fin_totals_last:
            lines = self._totals_last(lines)
        lines = self._slim(lines)
        currency = engine.currency(options)
        data = self._report_payload(engine, handler, options, columns, lines, currency, company)
        if lean:
            for key in ('choices', 'reports', 'saved_views'):
                data.pop(key, None)
        return data

    def _report_payload(self, engine, handler, options, columns, lines, currency, company):
        return {
            'report': {
                'id': self.id, 'name': self.name, 'kind': self.kind, 'key': self.key,
                'date_mode': self.date_mode, 'allow_comparison': self.allow_comparison,
                'allow_journals': self.allow_journals, 'allow_partners': self.allow_partners,
                'allow_analytic': self.allow_analytic, 'allow_accounts': self.allow_accounts,
                'allow_hierarchy': self.allow_hierarchy, 'allow_posted_toggle': self.allow_posted_toggle,
                'custom': self.custom, 'description': self.description or '',
                'extra_filters': handler.extra_filters(self, options),
                'share_code': self.share_code or '', 'wide_filters': self.kind != 'analytic',
                'first_column': self._first_column(),
                'totals_last': bool(company.ebshel_fin_totals_last),
                'negative': company.ebshel_fin_negative or 'minus',
            },
            'unit_label': engine.unit_label(options),
            # the period the reader chose, in words - a ledger's first column is "Debit", not a period
            'period_label': engine.period_label(fields.Date.to_date(options['date']['from']),
                                                fields.Date.to_date(options['date']['to']), self.date_mode == 'single'),
            'reports': [{'key': r.key, 'name': r.name, 'kind': r.kind} for r in self.search([])],
            'options': options,
            'columns': columns,
            'lines': lines,
            'choices': self._choices(options),
            'annotations': self.env['ebshel.fin.report.annotation'].for_report(self),
            'saved_views': self.env['ebshel.fin.report.view'].for_report(self),
            'currency': {'id': currency.id, 'symbol': currency.symbol,
                         'position': currency.position, 'decimals': currency.decimal_places},
            'can_design': self.env.user.has_group('account.group_account_manager'),
            'can_annotate': self.env.user.has_group('account.group_account_invoice'),
        }

    def _choices(self, options):
        Journal = self.env['account.journal'].sudo()
        journals = Journal.search([('company_id', 'in', options['companies'])], order='sequence, id')
        Analytic = self.env['account.analytic.account'].sudo()
        analytic = Analytic.search([('company_id', 'in', options['companies'] + [False])],
                                   order='plan_id, name', limit=500)
        partners = self.env['res.partner'].sudo().browse(options.get('partners') or [])
        return {
            'journals': [{'id': j.id, 'name': j.name, 'code': j.code, 'type': j.type} for j in journals],
            'analytic': [{'id': a.id, 'name': a.display_name, 'plan': a.plan_id.name or ''} for a in analytic],
            'partners': [{'id': p.id, 'name': p.display_name} for p in partners.exists()],
            'presets': PRESETS,
            'comparison_modes': COMPARISON_MODES,
            'account_types': ACCOUNT_TYPES,
            **self._chosen_wide(options),
        }

    def _chosen_wide(self, options):
        """The wider filters as far as a report needs them to be drawn: the names of what
        is chosen (for the chips), the journal types and the units. What they COULD be
        set to is asked for when the panel is opened - finding the salespeople of every
        invoice cost 50 ms on each load of each report, for a panel few readings open."""
        def named(model, key):
            ids = options.get(key) or []
            if not ids or model not in self.env:
                return []
            records = self.env[model].sudo().with_context(active_test=False).browse(ids).exists()
            return [{'id': r.id, 'name': r.display_name} for r in records]
        return {
            'journal_types': [[k, v] for k, v in self.env['account.journal']._fields['type']._description_selection(self.env)
                              if k in ('sale', 'purchase', 'bank', 'cash', 'credit', 'general')],
            'partner_categories': named('res.partner.category', 'partner_categories'),
            'salespeople': named('res.users', 'salespeople'),
            'product_categories': named('product.category', 'product_categories'),
            'teams': named('crm.team', 'teams'),
            'units': [[1, _('Exact')], [1000, _('Thousands')], [100000, _('Lakhs')],
                      [1000000, _('Millions')], [10000000, _('Crores')]],
            'wide_loaded': False,
        }

    def get_wide_choices(self, options=None):
        """What the wider filters can be set to, asked for when their panel is opened."""
        self.ensure_one()
        self._check_report_access()
        options = self.env['ebshel.fin.engine'].normalize(self, options)
        return dict(self._wide_choices(options), wide_loaded=True)

    def _wide_choices(self, options):
        """What the wider filters can be set to: only values the ledger really uses."""
        companies = tuple(options['companies'])
        users = self.env.execute_query(SQL(
            "SELECT DISTINCT invoice_user_id FROM account_move WHERE company_id IN %s AND invoice_user_id IS NOT NULL", companies))
        Users = self.env['res.users'].sudo().with_context(active_test=False)
        out = {
            'journal_types': [[k, v] for k, v in self.env['account.journal']._fields['type']._description_selection(self.env)
                              if k in ('sale', 'purchase', 'bank', 'cash', 'credit', 'general')],
            'partner_categories': [{'id': c.id, 'name': c.display_name}
                                   for c in self.env['res.partner.category'].sudo().search([], limit=200)],
            'salespeople': sorted(({'id': u.id, 'name': u.name} for u in Users.browse([r[0] for r in users]).exists()),
                                  key=lambda u: u['name'] or ''),
            'product_categories': [{'id': c.id, 'name': c.complete_name or c.name}
                                   for c in self.env['product.category'].sudo().search([], order='complete_name', limit=300)],
            'teams': [],
            'units': [[1, _('Exact')], [1000, _('Thousands')], [100000, _('Lakhs')],
                      [1000000, _('Millions')], [10000000, _('Crores')]],
        }
        if 'team_id' in self.env['account.move']._fields and 'crm.team' in self.env:
            out['teams'] = [{'id': t.id, 'name': t.name} for t in self.env['crm.team'].sudo().search([], limit=200)]
        return out

    # ------------------------------------------------------------------ the journal items behind a line
    ITEM_ORDERS = {
        'date desc': 'date desc, id desc', 'date': 'date, id', 'amount desc': 'balance desc, id', 'amount': 'balance, id',
        'partner': 'partner_id, date, id', 'account': 'account_id, date, id',
    }

    def _items_domain(self, options, line_id, column_key='p0', search=''):
        """(engine, options, domain, title, other_model) of the journal items behind one figure."""
        engine, handler, options, columns = self._prepare(options)
        target = handler.drill(self, options, columns, str(line_id), column_key or 'p0')
        if not target:
            return engine, options, None, '', False
        if isinstance(target, dict):
            if target.get('res_model') != 'account.move.line':
                return engine, options, None, target.get('name') or '', target.get('res_model')
            domain, title = list(target.get('domain') or []), target.get('name') or ''
        else:
            domain, title = list(target[0]), target[1]
        search = (search or '').strip()
        if search:
            try:
                amount = float(search.replace(',', ''))
                domain += ['|', '|', ('balance', '=', amount), ('balance', '=', -amount), ('move_name', 'ilike', search)]
            except ValueError:
                domain += ['|', '|', '|', '|', ('move_name', 'ilike', search), ('name', 'ilike', search),
                           ('ref', 'ilike', search), ('partner_id', 'ilike', search), ('account_id', 'ilike', search)]
        return engine, options, domain, title, False

    def get_items(self, options, line_id, column_key='p0', offset=0, limit=40, search='', order='date desc'):
        """The journal items that make one figure, a page at a time - so any line of any
        report can be opened in place, down to the entries."""
        self.ensure_one()
        self.env.flush_all()
        self._check_report_access()
        engine, options, domain, title, other_model = self._items_domain(options, line_id, column_key, search)
        empty = {'rows': [], 'total': 0, 'title': '', 'sums': {}, 'has_more': False, 'other_model': False}
        if other_model:
            return dict(empty, other_model=other_model, title=title)
        if domain is None:
            return empty
        AML = self.env['account.move.line']
        total = AML.search_count(domain)
        debit, credit, balance = AML._read_group(domain, [], ['debit:sum', 'credit:sum', 'balance:sum'])[0]
        limit = max(1, min(int(limit or 40), 200))
        offset = max(0, int(offset or 0))
        lines = AML.search(domain, order=self.ITEM_ORDERS.get(order, self.ITEM_ORDERS['date desc']), limit=limit, offset=offset)
        currency = engine.currency(options)
        plain = engine.with_context(fin_unit=1)          # an entry is always shown to the paisa
        analytic_ids = {int(k) for l in lines for key in (l.analytic_distribution or {}) for k in str(key).split(',') if k.isdigit()}
        names = {a.id: a.name for a in self.env['account.analytic.account'].sudo().browse(list(analytic_ids)).exists()}
        rows = []
        for l in lines:
            tags = [names.get(int(k), '') for key in (l.analytic_distribution or {}) for k in str(key).split(',') if k.isdigit()]
            rows.append({
                'id': l.id, 'move_id': l.move_id.id, 'date': fields.Date.to_string(l.date), 'move': l.move_name or l.move_id.name or '',
                'date_text': engine.fmt_date(l.date),
                'journal': l.journal_id.code or '', 'account': l.account_id.display_name or '',
                'partner': l.partner_id.display_name or '', 'label': l.name or '', 'ref': l.ref or '',
                'debit': l.debit, 'credit': l.credit, 'balance': l.balance,
                'debit_text': plain.fmt(l.debit, currency) if l.debit else '', 'credit_text': plain.fmt(l.credit, currency) if l.credit else '',
                'matching': l.matching_number or '', 'state': l.parent_state, 'analytic': ', '.join(t for t in tags if t),
                'due': fields.Date.to_string(l.date_maturity) if l.date_maturity else '',
            })
        return {
            'rows': rows, 'total': total, 'title': title, 'offset': offset, 'has_more': offset + len(rows) < total,
            'sums': {'debit': plain.fmt(debit or 0.0, currency), 'credit': plain.fmt(credit or 0.0, currency),
                     'balance': plain.fmt(balance or 0.0, currency), 'balance_value': balance or 0.0,
                     'net': plain.fmt(abs(balance or 0.0), currency),
                     'side': _('Dr') if (balance or 0.0) > 0.005 else _('Cr') if (balance or 0.0) < -0.005 else ''},
            'other_model': False,
        }

    def get_movers(self, options, limit=8):
        """What changed: the accounts that moved most between the period and the one it
        is compared with (the period before, when no comparison is set)."""
        self.ensure_one()
        self.env.flush_all()
        self._check_report_access()
        engine, handler, options, columns = self._prepare(options)
        # the columns of a statement are periods; those of a ledger are Debit, Credit and
        # Balance and carry no dates - asking one what changed raised a KeyError
        periods = [c for c in columns if c['type'] == 'amount' and c.get('from') and c.get('to')]
        d_from = fields.Date.to_date((periods[0] if periods else options['date'])['from'])
        d_to = fields.Date.to_date((periods[0] if periods else options['date'])['to'])
        single = self.date_mode == 'single'
        if len(periods) > 1:
            p_from, p_to = fields.Date.to_date(periods[1]['from']), fields.Date.to_date(periods[1]['to'])
        else:
            p_from, p_to = engine.shift_period(d_from, d_to, 'previous', 1)
        mode = 'cumulative' if single else 'flow'
        now = engine.sums_by_account(options, mode, d_from, d_to)
        before = engine.sums_by_account(options, mode, p_from, p_to)
        accounts = engine.accounts(options)
        currency = engine.currency(options)
        credit_side = ('income', 'income_other', 'liability_payable', 'liability_credit_card', 'liability_current',
                       'liability_non_current', 'equity', 'equity_unaffected')
        wanted = None if single else ('income', 'income_other', 'expense', 'expense_other', 'expense_depreciation',
                                      'expense_direct_cost')
        rows = []
        for aid in set(now) | set(before):
            meta = accounts.get(aid)
            if not meta or (wanted and meta['type'] not in wanted):
                continue
            sign = -1.0 if meta['type'] in credit_side else 1.0
            a = sign * now.get(aid, {}).get('balance', 0.0)
            b = sign * before.get(aid, {}).get('balance', 0.0)
            change = a - b
            if abs(change) < 0.005:
                continue
            rows.append({'account_id': aid, 'name': '%s %s' % (meta['code'], meta['name']), 'type': meta['type'],
                         'now': a, 'before': b, 'change': change,
                         'pct': round(change / abs(b) * 100, 1) if b else None,
                         'now_text': engine.fmt(a, currency), 'before_text': engine.fmt(b, currency),
                         'change_text': engine.fmt(change, currency),
                         'good': (change > 0) == (meta['type'] in ('income', 'income_other') or single and sign > 0)})
        rows.sort(key=lambda r: -abs(r['change']))
        return {'rows': rows[:max(1, int(limit or 8))], 'count': len(rows),
                'now': engine.period_label(d_from, d_to, single), 'before': engine.period_label(p_from, p_to, single),
                'domain_now': engine.domain(options, mode, d_from, d_to, None)}

    def expand_line(self, options, line_id, offset=0):
        """The children of one folded line, page by page."""
        self.ensure_one()
        self.env.flush_all()                      # the SQL must see pending ORM writes (parent_state, balance)
        self._check_report_access()
        engine, handler, options, columns = self._prepare(options)
        res = handler.expand(self, options, columns, str(line_id), int(offset or 0))
        self._polish(res.get('lines') or [])
        res['lines'] = self._slim(res.get('lines') or [])
        # the heading's figures move under its rows; the screen builds that total from
        # the heading it already has - it only needs to be told to
        company = self.env['ebshel.fin.engine'].lead_company(options).sudo()
        kids = res['lines']
        res['totals_last'] = bool(
            company.ebshel_fin_totals_last and not int(offset or 0) and kids
            and not any(l.get('kind') == 'total' and l.get('parent_id') == str(line_id) for l in kids))
        return res

    def get_drill_action(self, options, line_id, column_key='p0'):
        """The journal items behind one figure, as a list."""
        self.ensure_one()
        self.env.flush_all()                      # the SQL must see pending ORM writes (parent_state, balance)
        self._check_report_access()
        engine, handler, options, columns = self._prepare(options)
        target = handler.drill(self, options, columns, str(line_id), column_key)
        if not target:
            return False
        if isinstance(target, dict) and target.get('type'):
            return target
        domain, title = target
        return {
            'type': 'ir.actions.act_window',
            'name': title,
            'res_model': 'account.move.line',
            'view_mode': 'list,pivot,graph',
            'views': [(self.env.ref('account.view_move_line_tree').id, 'list'), (False, 'pivot'), (False, 'graph')],
            'domain': domain,
            'context': {'create': False, 'search_default_group_by_account': 0},
            'target': 'current',
        }

    def explain_cell(self, options, line_id, column_key='p0'):
        """What a figure is made of: by account, by partner, by month."""
        self.ensure_one()
        self.env.flush_all()                      # the SQL must see pending ORM writes (parent_state, balance)
        self._check_report_access()
        engine, handler, options, columns = self._prepare(options)
        return handler.explain(self, options, columns, str(line_id), column_key)

    def get_trends(self, options, line_ids):
        """Twelve months of each line, for the sparklines."""
        self.ensure_one()
        self.env.flush_all()                      # the SQL must see pending ORM writes (parent_state, balance)
        self._check_report_access()
        engine, handler, options, columns = self._prepare(options)
        return handler.trends(self, options, [str(i) for i in line_ids][:200])

    @api.model
    def _slim(self, lines):
        """Out of every cell, what says nothing: an empty class, a figure that opens
        nothing. A third of what an aged report of 800 partners sent to the screen."""
        for line in lines:
            for cell in line.get('columns') or ():
                if not cell.get('class'):
                    cell.pop('class', None)
                if not cell.get('drill'):
                    cell.pop('drill', None)
            if not line.get('class'):
                line.pop('class', None)
            if line.get('parent_id') is None:
                line.pop('parent_id', None)
        return lines

    @api.model
    def _totals_last(self, lines):
        """Close every section by its total: the heading stands alone, its lines follow,
        and 'Total ...' comes after the last of them. A section is a line followed by
        deeper ones; one that already ends in a total of its own is left as it is."""
        closed = {l.get('parent_id') for l in lines if l.get('kind') == 'total' and l.get('parent_id')}
        out, waiting = [], []                       # waiting: (level, the total to come)

        def close(level):
            while waiting and waiting[-1][0] >= level:
                out.append(waiting.pop()[1])

        for at, line in enumerate(lines):
            level = line.get('level') or 0
            close(level)
            out.append(line)
            after = lines[at + 1] if at + 1 < len(lines) else None
            if (after is None or (after.get('level') or 0) <= level or line['id'] in closed
                    or line.get('kind') in ('total', 'initial', 'more', 'move_line', 'open_item')
                    or not any(c.get('value') is not None for c in line.get('columns') or ())):
                continue
            total = dict(line, id='%s:total' % line['id'], name=_('Total %s', line['name']), total_of=line['id'],
                         parent_id=line['id'], bold=True, kind='total', unfoldable=False, unfolded=False,
                         columns=[dict(c) for c in line['columns']])
            total.pop('page_break', None)
            line['columns'] = [{'value': None, 'text': '', 'display': 'text'} for _c in line['columns']]
            line['total_last'] = True
            waiting.append((level, total))
        close(0)
        return out

    def _first_column(self):
        """The width asked for the first column, kept inside what a table can take."""
        self.ensure_one()
        return min(max(self.first_column or 1.0, 0.4), 1.6)

    def _polish(self, lines):
        """The last pass over detail rows, shared by the screen and every export: dates the
        way the reader writes them, and nothing said twice - an entry whose label is its own
        number, a partner repeated under the partner's own heading."""
        engine = self.env['ebshel.fin.engine']
        under_partner = self.kind in ('partner_ledger', 'aged', 'statement_of_account')
        under_account = self.kind == 'general_ledger'
        for line in lines:
            parts = line.get('parts')
            if not parts:
                continue
            parts['date_text'] = engine.fmt_date(parts.get('date'))
            if not under_partner:
                parts['due'] = ''               # a due date says something about what is owed, nothing about a ledger line
            parts['due_text'] = engine.fmt_date(parts.get('due'))
            if (parts.get('label') or '').strip() == (parts.get('move') or '').strip():
                parts['label'] = ''
            if line.get('parent_id'):
                if under_partner:
                    parts['partner'] = ''
                if under_account:
                    parts['account'] = ''
        return lines

    def _options_summary(self, options):
        parts = ['%s → %s' % (options['date']['from'], options['date']['to'])]
        if options['comparison']['mode'] != 'none':
            parts.append(_('compared with %s', dict(COMPARISON_MODES)[options['comparison']['mode']].lower()))
        if options.get('journals'):
            parts.append(_('%s journal(s)', len(options['journals'])))
        if options.get('partners'):
            parts.append(_('%s partner(s)', len(options['partners'])))
        if options.get('analytic'):
            parts.append(_('%s analytic account(s)', len(options['analytic'])))
        for key, label in (('journal_types', _('journal type(s)')), ('partner_categories', _('partner tag(s)')),
                           ('salespeople', _('salesperson(s)')), ('teams', _('sales team(s)')),
                           ('product_categories', _('product categor(ies)'))):
            if options.get(key):
                parts.append('%s %s' % (len(options[key]), label))
        if options.get('label'):
            parts.append(_('label contains "%s"', options['label']))
        if options.get('amount_min') is not None or options.get('amount_max') is not None:
            parts.append(_('amounts %(a)s to %(b)s', a=options.get('amount_min') or 0,
                           b=options.get('amount_max') if options.get('amount_max') is not None else '∞'))
        if options.get('unreconciled'):
            parts.append(_('unreconciled only'))
        parts.append(_('posted entries') if options['posted_only'] else _('posted and draft entries'))
        if options.get('unit') and options['unit'] != 1:
            parts.append(_('in %s', self.env['ebshel.fin.engine'].unit_label(options)))
        return ' · '.join(parts)

    # ------------------------------------------------------------------ designer
    def action_open_viewer(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'ebshel_fin_report',
            'name': self.name,
            'params': {'report_key': self.key},
        }

    def action_create_menu(self):
        """A menu entry under Reporting → Custom Reports for a designed report."""
        self.ensure_one()
        parent = self.env.ref('ebshel_account_reports.menu_custom_reports')
        if not self.action_id:
            self.action_id = self.env['ir.actions.client'].sudo().create({
                'name': self.name, 'tag': 'ebshel_fin_report',
                'params': {'report_key': self.key}})
        if not self.menu_id:
            self.menu_id = self.env['ir.ui.menu'].sudo().create({
                'name': self.name, 'parent_id': parent.id,
                'action': 'ir.actions.client,%d' % self.action_id.id,
                'sequence': self.sequence})
        return True

    def write(self, vals):
        res = super().write(vals)
        if 'name' in vals:
            for report in self:
                if report.menu_id:
                    report.menu_id.sudo().name = report.name
                if report.action_id:
                    report.action_id.sudo().name = report.name
        return res

    def unlink(self):
        (self.menu_id | self.env['ir.ui.menu']).sudo().unlink()
        (self.action_id | self.env['ir.actions.client']).sudo().unlink()
        return super().unlink()

    def copy_data(self, default=None):
        vals_list = super().copy_data(default=default)
        for report, vals in zip(self, vals_list):
            vals['key'] = '%s_copy_%s' % (report.key, self.env['ir.sequence'].sudo().next_by_code('ebshel.fin.report.copy') or fields.Datetime.now().strftime('%H%M%S'))
            vals['custom'] = True
            vals.pop('action_id', None)
            vals.pop('menu_id', None)
        return vals_list


class FinReportLine(models.Model):
    _name = 'ebshel.fin.report.line'
    _description = 'Financial report line'
    _order = 'sequence, id'
    _parent_name = 'parent_id'

    report_id = fields.Many2one('ebshel.fin.report', required=True, ondelete='cascade', index=True)
    parent_id = fields.Many2one('ebshel.fin.report.line', ondelete='cascade', index=True,
                                domain="[('report_id', '=', report_id)]")
    child_ids = fields.One2many('ebshel.fin.report.line', 'parent_id')
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True, translate=True)
    code = fields.Char(help="Short name other lines' formulas refer to, e.g. REV.")
    kind = fields.Selection([
        ('sum', 'Sum of accounts'),
        ('formula', 'Formula over other lines'),
        ('header', 'Heading only'),
    ], default='sum', required=True)
    balance_mode = fields.Selection(BALANCE_MODES, default='flow', required=True)
    sign = fields.Selection([('1', 'As booked (debit positive)'), ('-1', 'Reversed (credit positive)')],
                            default='1', required=True,
                            help="Revenue, liabilities and equity are credit balances: reverse "
                                 "them so the report reads them as positive figures.")
    account_prefixes = fields.Char(help="Comma-separated code prefixes, e.g. 4,5")
    exclude_prefixes = fields.Char(help="Prefixes to leave out, e.g. 499")
    account_types = fields.Char(help="Comma-separated account types, e.g. income,income_other")
    account_ids = fields.Many2many('account.account', string='Specific accounts')
    account_tag_ids = fields.Many2many('account.account.tag', string='Account tags')
    domain = fields.Char(help="An extra journal-item domain, e.g. [('partner_id.country_id.code', '=', 'IN')]")
    formula = fields.Char(help="e.g. REV - COGS, or GP / REV * 100")
    display = fields.Selection(DISPLAYS, default='amount', required=True)
    bold = fields.Boolean()
    hidden = fields.Boolean(help="Computed for other lines' formulas, never shown.")
    hide_if_zero = fields.Boolean()
    unfold_accounts = fields.Boolean(string='Unfold to accounts', default=True,
                                     help="Fold open to the accounts that make the figure.")
    level = fields.Integer(compute='_compute_level', recursive=True)
    note = fields.Char()

    @api.depends('parent_id.level')
    def _compute_level(self):
        for line in self:
            line.level = (line.parent_id.level + 1) if line.parent_id else 0

    @api.constrains('formula', 'kind', 'code', 'report_id')
    def _check_formula(self):
        for line in self.filtered(lambda l: l.kind == 'formula'):
            if not line.formula:
                raise ValidationError(_("A formula line needs a formula: %s", line.name))
            try:
                names = formula_names(line.formula)
            except SyntaxError as exc:
                raise ValidationError(_("The formula of '%s' does not parse: %s", line.name, exc)) from exc
            codes = set(line.report_id.line_ids.mapped('code')) - {False, ''}
            unknown = [n for n in names if n not in codes and n != 'DAYS']
            # Shipped reports load line by line, a formula before the lines it
            # names: the check waits for the designer, where order is no excuse.
            if unknown and not self.env.context.get('install_mode'):
                raise ValidationError(_("'%s' refers to lines that do not exist: %s",
                                        line.name, ', '.join(unknown)))
            if line.code and line.code in names:
                raise ValidationError(_("'%s' refers to itself.", line.name))

    @api.constrains('code', 'report_id')
    def _check_code(self):
        for line in self.filtered('code'):
            if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', line.code):
                raise ValidationError(_("A line code is letters, digits and underscores: %s", line.code))
            twins = line.report_id.line_ids.filtered(lambda l: l.code == line.code and l.id != line.id)
            if twins:
                raise ValidationError(_("The code %s is used twice in %s.", line.code, line.report_id.name))

    @api.constrains('parent_id')
    def _check_parent(self):
        if self._has_cycle():
            raise ValidationError(_("A line cannot be its own ancestor."))
