# -*- coding: utf-8 -*-
"""One handler per kind of report. Each turns the engine's sums into lines the
viewer, the workbook and the PDF all read the same way:

    {'id', 'parent_id', 'name', 'level', 'unfoldable', 'unfolded', 'bold',
     'kind', 'columns': [{'value', 'text', 'display', 'drill', 'class'}], ...}

Line ids are stable strings ("ln:12", "ac:34", "pa:56", "ml:789") so notes,
saved views and the unfold state survive a reload.
"""
from ast import literal_eval
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.tools import SQL, date_utils

from .engine import PL_TYPES, BS_TYPES, LIQUIDITY_TYPES
from .report import evaluate_formula

PAGE = 80


class FinHandler(models.AbstractModel):
    _name = 'ebshel.fin.handler'
    _description = 'Financial report engine: base handler'
    _kind_label = 'Base'

    # ------------------------------------------------------------------ hooks
    def adjust_options(self, report, options):
        return options

    def extra_filters(self, report, options):
        """Handler-specific switches the viewer draws next to the standard ones:
        [{'key', 'label', 'type': 'toggle'|'int'|'select', 'value', 'choices'}]"""
        return []

    def columns(self, report, options):
        return self.env['ebshel.fin.engine'].period_columns(report, options)

    def lines(self, report, options, columns, for_export=False):
        return []

    def expand(self, report, options, columns, line_id, offset=0):
        return {'lines': [], 'has_more': False}

    def drill(self, report, options, columns, line_id, column_key):
        return None

    def explain(self, report, options, columns, line_id, column_key):
        return {}

    def trends(self, report, options, line_ids):
        return {}

    # ------------------------------------------------------------------ helpers
    @property
    def engine(self):
        return self.env['ebshel.fin.engine']

    def _dates(self, col):
        return fields.Date.to_date(col['from']), fields.Date.to_date(col['to'])

    def _cell(self, value, currency, display='amount', drill=False, css=''):
        return {'value': None if value is None else round(value, 2) if display == 'amount' else value,
                'text': self.engine.fmt(value, currency, display), 'display': display,
                'drill': bool(drill and value is not None), 'class': css}

    def _blank(self):
        return {'value': None, 'text': '', 'display': 'text', 'drill': False, 'class': ''}

    def _text_cell(self, text):
        return {'value': None, 'text': text, 'display': 'text', 'drill': False, 'class': ''}

    def _growth(self, cells):
        """The growth cell over the first two amount cells."""
        a = cells[0].get('value') if cells else None
        b = cells[1].get('value') if len(cells) > 1 else None
        if a is None or b is None:
            return self._blank()
        g = self.engine.growth(a, b)
        if g is None:
            return self._blank()
        css = 'up' if g > 0 else 'down' if g < 0 else ''
        return {'value': g, 'text': '%s%%' % ('%+.1f' % g), 'display': 'growth', 'drill': False, 'class': css}

    def _finish(self, cells, columns, options):
        """Append the growth cell when the columns ask for one."""
        if columns and columns[-1]['type'] == 'growth':
            cells = cells + [self._growth(cells)]
        return cells

    def _line(self, lid, name, level, cells, parent_id=None, unfoldable=False, unfolded=False,
              bold=False, kind='line', css='', **extra):
        line = {'id': lid, 'parent_id': parent_id, 'name': name, 'level': level, 'columns': cells,
                'unfoldable': unfoldable, 'unfolded': unfolded, 'bold': bold, 'kind': kind, 'class': css}
        line.update(extra)
        return line

    def _is_open(self, options, lid):
        return options.get('unfold_all') or lid in (options.get('expanded') or [])

    def _move_line_rows(self, options, where, order_dates, offset, limit, running_start=0.0,
                        parent_id=None, level=2, extra_select=None):
        """Journal items as detail rows with a running balance."""
        w = self.engine.weight_sql(options)
        base = SQL(" AND ").join([self.engine.base_where(options)] + list(where))
        if offset:
            skipped = self.env.execute_query(SQL("""
                SELECT COALESCE(SUM(s.bal), 0) FROM (
                    SELECT l.balance * %s AS bal FROM account_move_line l
                      JOIN account_account a ON a.id = l.account_id
                     WHERE %s ORDER BY l.date, l.id LIMIT %s) s""", w, base, offset))
            running_start += float(skipped[0][0] or 0)
        rows = self.env.execute_query(SQL("""
            SELECT l.id, l.date, m.name, m.id, p.name, l.name, l.ref, j.code, %s,
                   l.debit * %s, l.credit * %s, l.balance * %s, l.matching_number, l.date_maturity,
                   m.move_type
              FROM account_move_line l
              JOIN account_move m ON m.id = l.move_id
              JOIN account_account a ON a.id = l.account_id
              JOIN account_journal j ON j.id = l.journal_id
              LEFT JOIN res_partner p ON p.id = l.partner_id
             WHERE %s
             ORDER BY l.date, l.id
             LIMIT %s OFFSET %s
        """, self.engine.code_sql(options), w, w, w, base, limit + 1, offset))
        has_more = len(rows) > limit
        currency = self.engine.currency(options)
        out, running = [], running_start
        for r in rows[:limit]:
            (lid, day, move_name, move_id, partner, label, ref, jcode, acode,
             debit, credit, balance, matching, due, move_type) = r
            running += float(balance or 0)
            parts = {'date': fields.Date.to_string(day), 'move': move_name or '', 'partner': partner or '',
                     'label': label or ref or '', 'journal': jcode or '', 'account': acode or '',
                     'matching': matching or '', 'due': fields.Date.to_string(due) if due else ''}
            cells = [self._cell(float(debit or 0), currency), self._cell(float(credit or 0), currency),
                     self._cell(running, currency)]
            out.append(self._line('ml:%d' % lid, '%s · %s' % (parts['date'], move_name or ''), level, cells,
                                  parent_id=parent_id, kind='move_line', parts=parts,
                                  model='account.move', res_id=move_id))
        return out, has_more, running

    def _explain_sets(self, options, mode, date_from, date_to, account_ids, partner_ids=None):
        """Top accounts, top partners and the months inside one figure."""
        engine = self.engine
        currency = engine.currency(options)
        accounts = engine.accounts(options)
        extra = SQL("l.partner_id IN %s", tuple(partner_ids)) if partner_ids else None
        by_acc = engine.sums_by(options, SQL("l.account_id"), mode, date_from, date_to,
                                account_ids=account_ids, extra_where=extra,
                                order=SQL("ABS(SUM(l.balance)) DESC"), limit=8)
        by_partner = engine.sums_by(options, SQL("l.partner_id"), mode, date_from, date_to,
                                    account_ids=account_ids, extra_where=extra,
                                    order=SQL("ABS(SUM(l.balance)) DESC"), limit=8)
        by_month = engine.sums_by(options, SQL("date_trunc('month', l.date)::date"), 'flow',
                                  date_from if mode == 'flow' else min(date_from, date_to - relativedelta(months=11)),
                                  date_to, account_ids=account_ids, extra_where=extra)
        partners = {p.id: p.display_name for p in self.env['res.partner'].sudo().browse(
            [r[0] for r in by_partner if r[0]])}
        return {
            'accounts': [{'name': '%s %s' % (accounts.get(r[0], {}).get('code', ''), accounts.get(r[0], {}).get('name', '?')),
                          'value': float(r[1] or 0), 'text': engine.fmt(float(r[1] or 0), currency)} for r in by_acc],
            'partners': [{'name': partners.get(r[0], _('No partner')), 'value': float(r[1] or 0),
                          'text': engine.fmt(float(r[1] or 0), currency)} for r in by_partner],
            'months': [{'name': r[0].strftime('%b %Y'), 'value': float(r[1] or 0),
                        'text': engine.fmt(float(r[1] or 0), currency)} for r in by_month],
        }


# ---------------------------------------------------------------------------
class StatementHandler(models.AbstractModel):
    """Balance sheet, profit and loss, cash flow, executive summary - and every
    report designed in the designer: lines that sum accounts or compute over
    other lines."""
    _name = 'ebshel.fin.handler.statement'
    _inherit = 'ebshel.fin.handler'
    _description = 'Financial report engine: statement'
    _kind_label = 'Statement (designed lines)'

    MODE_OF_TREND = {'flow': 'flow', 'earnings_current': 'flow', 'cumulative': 'cumulative',
                     'opening': 'cumulative', 'earnings_previous': 'cumulative'}

    def _account_sets(self, report, options, accounts):
        sets = {}
        for line in report.line_ids:
            if line.kind != 'sum':
                continue
            domain = None
            if line.domain:
                try:
                    domain = literal_eval(line.domain)
                except (ValueError, SyntaxError):
                    domain = None
            sets[line.id] = self.engine.select_accounts(
                accounts, prefixes=line.account_prefixes or '', exclude=line.exclude_prefixes or '',
                types=line.account_types or '', account_ids=line.account_ids.ids,
                tag_ids=line.account_tag_ids.ids, domain=domain)
        return sets

    def _values(self, report, options, columns):
        """{line_id: {col_key: value}} for every line, sum and formula alike."""
        engine = self.engine
        accounts = engine.accounts(options)
        sets = self._account_sets(report, options, accounts)
        values = {}
        amount_cols = [c for c in columns if c['type'] == 'amount']
        sums = {}           # (mode, col_key) -> {account_id: balance}
        for col in amount_cols:
            d_from, d_to = self._dates(col)
            for mode in {l.balance_mode for l in report.line_ids if l.kind == 'sum'}:
                union = set().union(*[sets[l.id] for l in report.line_ids
                                      if l.kind == 'sum' and l.balance_mode == mode]) if sets else set()
                sums[(mode, col['key'])] = engine.sums_by_account(options, mode, d_from, d_to,
                                                                  account_ids=sorted(union)) if union else {}
        for line in report.line_ids:
            values[line.id] = {}
            if line.kind == 'sum':
                sign = float(line.sign)
                for col in amount_cols:
                    table = sums[(line.balance_mode, col['key'])]
                    values[line.id][col['key']] = sign * sum(
                        table.get(a, {}).get('balance', 0.0) for a in sets.get(line.id, ()))
        # Formulas: resolve in passes so a formula over formulas settles whatever the order.
        formula_lines = [l for l in report.line_ids if l.kind == 'formula']
        for col in amount_cols:
            d_from, d_to = self._dates(col)
            pending = list(formula_lines)
            for _pass in range(len(pending) + 1):
                if not pending:
                    break
                still = []
                for line in pending:
                    ctx = {l.code: values[l.id].get(col['key']) for l in report.line_ids
                           if l.code and col['key'] in values.get(l.id, {})}
                    ctx['DAYS'] = float(engine.days_in(d_from, d_to))
                    try:
                        values[line.id][col['key']] = evaluate_formula(line.formula, ctx)
                    except KeyError:
                        still.append(line)
                    except (ValueError, ZeroDivisionError, TypeError):
                        values[line.id][col['key']] = None
                pending = still
            for line in pending:
                values[line.id][col['key']] = None
        return values, sets, accounts

    def _cells(self, line, values, columns, currency, options):
        cells = []
        for col in columns:
            if col['type'] != 'amount':
                continue
            value = values[line.id].get(col['key'])
            display = line.display
            if display == 'check':
                ok = value is not None and abs(value) < 0.005
                cells.append({'value': 0.0 if ok else value, 'text': _('Balanced') if ok else self.engine.fmt(value, currency),
                              'display': 'check', 'drill': False, 'class': 'ok' if ok else 'danger'})
            elif line.kind == 'header':
                cells.append(self._blank())
            else:
                cells.append(self._cell(value, currency, display, drill=line.kind == 'sum'))
        if line.display in ('amount', 'percent', 'ratio', 'days', 'count') and line.kind != 'header':
            return self._finish(cells, columns, options)
        if columns and columns[-1]['type'] == 'growth':
            cells.append(self._blank())
        return cells

    def lines(self, report, options, columns, for_export=False):
        values, sets, accounts = self._values(report, options, columns)
        currency = self.engine.currency(options)
        out = []

        def walk(parent, level, parent_lid):
            children = report.line_ids.filtered(lambda l: l.parent_id == parent)
            for line in children:
                if line.hidden:
                    continue
                cells = self._cells(line, values, columns, currency, options)
                if line.hide_if_zero and all((c.get('value') or 0) == 0 for c in cells if c['display'] != 'text'):
                    continue
                lid = 'ln:%d' % line.id
                has_kids = bool(line.child_ids.filtered(lambda l: not l.hidden))
                can_unfold = line.kind == 'sum' and line.unfold_accounts and bool(sets.get(line.id))
                unfolded = self._is_open(options, lid) if can_unfold else True
                out.append(self._line(lid, line.name, level, cells, parent_id=parent_lid,
                                      unfoldable=can_unfold, unfolded=unfolded and can_unfold,
                                      bold=line.bold or line.kind == 'header' or has_kids,
                                      kind=line.kind, display=line.display, code=line.code or ''))
                if can_unfold and unfolded:
                    out.extend(self._account_rows(line, sets[line.id], accounts, options, columns, currency, level + 1, lid))
                walk(line, level + 1, lid)
        walk(self.env['ebshel.fin.report.line'], 0, None)
        return out

    def _account_rows(self, line, account_ids, accounts, options, columns, currency, level, parent_lid):
        sign = float(line.sign)
        per_col = {}
        for col in columns:
            if col['type'] != 'amount':
                continue
            d_from, d_to = self._dates(col)
            per_col[col['key']] = self.engine.sums_by_account(options, line.balance_mode, d_from, d_to,
                                                              account_ids=sorted(account_ids))
        rows = []
        for aid in sorted(account_ids, key=lambda i: accounts.get(i, {}).get('code', '')):
            cells = []
            for col in columns:
                if col['type'] != 'amount':
                    continue
                v = sign * per_col[col['key']].get(aid, {}).get('balance', 0.0)
                cells.append(self._cell(v, currency, line.display if line.display in ('amount',) else 'amount', drill=True))
            if not options.get('show_zero') and all(abs(c['value'] or 0) < 0.005 for c in cells):
                continue
            meta = accounts.get(aid, {})
            rows.append(self._line('ln:%d:ac:%d' % (line.id, aid), '%s %s' % (meta.get('code', ''), meta.get('name', '')),
                                   level, self._finish(cells, columns, options), parent_id=parent_lid,
                                   kind='account', account_id=aid))
        return rows

    def expand(self, report, options, columns, line_id, offset=0):
        if not line_id.startswith('ln:') or ':ac:' in line_id:
            return {'lines': [], 'has_more': False}
        line = self.env['ebshel.fin.report.line'].browse(int(line_id.split(':')[1]))
        accounts = self.engine.accounts(options)
        sets = self._account_sets(report, options, accounts)
        currency = self.engine.currency(options)
        rows = self._account_rows(line, sets.get(line.id, set()), accounts, options, columns, currency,
                                  line.level + 1, line_id)
        return {'lines': rows, 'has_more': False}

    def _target(self, report, options, line_id):
        """(line record, account ids) behind a line id."""
        parts = line_id.split(':')
        line = self.env['ebshel.fin.report.line'].browse(int(parts[1]))
        if len(parts) >= 4 and parts[2] == 'ac':
            return line, {int(parts[3])}
        accounts = self.engine.accounts(options)
        return line, self._account_sets(report, options, accounts).get(line.id, set())

    def drill(self, report, options, columns, line_id, column_key):
        if not line_id.startswith('ln:'):
            return None
        line, account_ids = self._target(report, options, line_id)
        if line.kind != 'sum' or not account_ids:
            return None
        col = next((c for c in columns if c['key'] == column_key and c['type'] == 'amount'), columns[0])
        d_from, d_to = self._dates(col)
        domain = self.engine.domain(options, line.balance_mode, d_from, d_to, account_ids)
        return domain, '%s — %s' % (line.name, col['label'])

    def explain(self, report, options, columns, line_id, column_key):
        if not line_id.startswith('ln:'):
            return {}
        line, account_ids = self._target(report, options, line_id)
        if line.kind != 'sum' or not account_ids:
            return {}
        col = next((c for c in columns if c['key'] == column_key and c['type'] == 'amount'), columns[0])
        d_from, d_to = self._dates(col)
        out = self._explain_sets(options, line.balance_mode, d_from, d_to, sorted(account_ids))
        sign = float(line.sign)
        for group in out.values():
            for item in group:
                item['value'] *= sign
                item['text'] = self.engine.fmt(item['value'], self.engine.currency(options))
        out['title'] = '%s — %s' % (line.name, col['label'])
        return out

    def trends(self, report, options, line_ids):
        out = {}
        for lid in line_ids:
            if not lid.startswith('ln:'):
                continue
            line, account_ids = self._target(report, options, lid)
            if line.kind != 'sum' or not account_ids:
                continue
            mode = self.MODE_OF_TREND.get(line.balance_mode, 'flow')
            series = self.engine.monthly(options, mode, sorted(account_ids))
            sign = float(line.sign)
            out[lid] = [{'label': m.strftime('%b %y'), 'value': round(sign * v, 2)} for m, v in series]
        return out


# ---------------------------------------------------------------------------
class LedgerBase(models.AbstractModel):
    """What the account-by-account reports share."""
    _name = 'ebshel.fin.handler.ledger_base'
    _inherit = 'ebshel.fin.handler'
    _description = 'Financial report engine: ledger base'
    _kind_label = 'Ledger base'

    def adjust_options(self, report, options):
        options['comparison'] = {'mode': 'none', 'periods': 1, 'from': None, 'to': None}
        options['growth'] = False
        return options

    def extra_filters(self, report, options):
        return [{'key': 'show_zero', 'label': _('Accounts without movement'), 'type': 'toggle',
                 'value': options.get('show_zero', False)}]

    def _period(self, options):
        return fields.Date.to_date(options['date']['from']), fields.Date.to_date(options['date']['to'])

    def _account_filter(self, options, accounts):
        if not options.get('accounts_query') and not options.get('account_types'):
            return None
        chosen = set(accounts)
        if options.get('accounts_query'):
            chosen &= self.engine.select_accounts(accounts, query=options['accounts_query'])
        if options.get('account_types'):
            chosen &= {a for a, m in accounts.items() if m['type'] in options['account_types']}
        return sorted(chosen)

    def _groups(self, options):
        """{group_id: {name, parent, start, end, level}} for the hierarchy."""
        rows = self.env.execute_query(SQL("""
            SELECT g.id, %s, g.parent_id, g.code_prefix_start, g.code_prefix_end
              FROM account_group g WHERE g.company_id IN %s ORDER BY g.code_prefix_start
        """, self.engine.name_sql('g'), tuple({self.env['res.company'].browse(c).root_id.id for c in options['companies']})))
        groups = {r[0]: {'id': r[0], 'name': r[1] or '', 'parent': r[2], 'start': r[3] or '', 'end': r[4] or r[3] or ''} for r in rows}
        for g in groups.values():
            level, p = 0, g['parent']
            while p and p in groups and level < 10:
                level, p = level + 1, groups[p]['parent']
            g['level'] = level
        return groups

    def _group_of(self, code, groups):
        best = None
        for g in groups.values():
            n = len(g['start'])
            if n and g['start'] <= code[:n] <= g['end']:
                if best is None or n > len(groups[best]['start']):
                    best = g['id']
        return best


class GeneralLedgerHandler(models.AbstractModel):
    _name = 'ebshel.fin.handler.general_ledger'
    _inherit = 'ebshel.fin.handler.ledger_base'
    _description = 'Financial report engine: general ledger'
    _kind_label = 'General ledger'

    def columns(self, report, options):
        return [{'key': 'debit', 'label': _('Debit'), 'type': 'amount'},
                {'key': 'credit', 'label': _('Credit'), 'type': 'amount'},
                {'key': 'balance', 'label': _('Balance'), 'type': 'amount'}]

    def _account_totals(self, options, account_ids=None):
        d_from, d_to = self._period(options)
        initial = self.engine.sums_by_account(options, 'initial', d_from, d_to, account_ids=account_ids)
        period = self.engine.sums_by_account(options, 'flow', d_from, d_to, account_ids=account_ids)
        return initial, period

    def lines(self, report, options, columns, for_export=False):
        engine = self.engine
        currency = engine.currency(options)
        accounts = engine.accounts(options)
        wanted = self._account_filter(options, accounts)
        initial, period = self._account_totals(options, wanted)
        ids = set(initial) | set(period)
        if options.get('show_zero'):
            ids |= set(wanted if wanted is not None else accounts)
        rows, tot_d, tot_c, tot_b = [], 0.0, 0.0, 0.0
        groups = self._groups(options) if options.get('hierarchy') else {}
        group_rows = {}
        for aid in sorted(ids, key=lambda i: accounts.get(i, {}).get('code', '')):
            meta = accounts.get(aid)
            if not meta:
                continue
            ini = initial.get(aid, {}).get('balance', 0.0)
            per = period.get(aid, {})
            d, c = per.get('debit', 0.0), per.get('credit', 0.0)
            bal = ini + per.get('balance', 0.0)
            if not options.get('show_zero') and abs(d) < 0.005 and abs(c) < 0.005 and abs(bal) < 0.005:
                continue
            tot_d, tot_c, tot_b = tot_d + d, tot_c + c, tot_b + bal
            lid = 'ac:%d' % aid
            unfolded = self._is_open(options, lid)
            parent = None
            level = 0
            if groups:
                gid = self._group_of(meta['code'], groups)
                if gid:
                    parent, level = 'grp:%d' % gid, groups[gid]['level'] + 1
                    group_rows.setdefault(gid, [0.0, 0.0, 0.0])
                    group_rows[gid][0] += d
                    group_rows[gid][1] += c
                    group_rows[gid][2] += bal
            rows.append(self._line(lid, '%s %s' % (meta['code'], meta['name']), level,
                                   [self._cell(d, currency, drill=True), self._cell(c, currency, drill=True),
                                    self._cell(bal, currency, drill=True)],
                                   parent_id=parent, unfoldable=True, unfolded=unfolded, kind='account',
                                   account_id=aid, initial=ini))
            if unfolded:
                detail, has_more, _running = self._detail(options, aid, ini, lid, level + 1, 0,
                                                          limit=PAGE if not for_export else 5000)
                rows.extend(detail)
                if has_more:
                    rows.append(self._line(lid + ':more', _('Load more…'), level + 1, [self._blank()] * 3,
                                           parent_id=lid, kind='more', offset=len(detail) - 1))
        if groups and group_rows:
            # Group rows come first in their own order; the account rows keep parent ids.
            heads = []
            for gid, (d, c, b) in group_rows.items():
                g = groups[gid]
                heads.append(self._line('grp:%d' % gid, '%s %s' % (g['start'], g['name']), g['level'],
                                        [self._cell(d, currency), self._cell(c, currency), self._cell(b, currency)],
                                        parent_id=('grp:%d' % g['parent']) if g['parent'] in group_rows else None,
                                        bold=True, kind='group'))
            heads.sort(key=lambda r: r['name'])
            ordered = []
            for head in heads:
                ordered.append(head)
                ordered.extend(r for r in rows if r['parent_id'] == head['id'] or
                               (r['parent_id'] and r['parent_id'].startswith('ac:') and any(
                                   a['id'] == r['parent_id'] and a['parent_id'] == head['id'] for a in rows)))
            rows = ordered
        unalloc = self._undistributed(options, accounts, wanted)
        if abs(unalloc) >= 0.005:
            tot_b += unalloc
            rows.append(self._line('unalloc', _("Undistributed profit and loss of previous years"), 0,
                                   [self._blank(), self._blank(), self._cell(unalloc, currency, drill=True)],
                                   kind='initial', css='muted'))
        rows.append(self._line('total', _('Total'), 0, [self._cell(tot_d, currency), self._cell(tot_c, currency),
                                                        self._cell(tot_b, currency)], bold=True, kind='total'))
        return rows

    def _undistributed(self, options, accounts, wanted):
        """Profit and loss of the years before the period's fiscal year: not on
        any account's initial balance (those restart each year) yet part of what
        the ledger must foot to."""
        pl_ids = [a for a, m in accounts.items() if m['type'] in PL_TYPES and (wanted is None or a in wanted)]
        d_from, d_to = self._period(options)
        sums = self.engine.sums_by_account(options, 'earnings_previous', d_from, d_from, account_ids=pl_ids)
        return sum(v['balance'] for v in sums.values())

    def _detail(self, options, account_id, initial, parent_lid, level, offset, limit=PAGE):
        d_from, d_to = self._period(options)
        currency = self.engine.currency(options)
        rows = []
        if offset == 0:
            rows.append(self._line(parent_lid + ':ini', _('Initial balance'), level,
                                   [self._blank(), self._blank(), self._cell(initial, currency, drill=True)],
                                   parent_id=parent_lid, kind='initial'))
        where = [self.engine.date_where('flow', d_from, d_to, options), SQL("l.account_id = %s", account_id)]
        detail, has_more, running = self._move_line_rows(options, where, None, offset, limit,
                                                         running_start=initial, parent_id=parent_lid, level=level)
        rows.extend(detail)
        return rows, has_more, running

    def expand(self, report, options, columns, line_id, offset=0):
        if not line_id.startswith('ac:'):
            return {'lines': [], 'has_more': False}
        aid = int(line_id.split(':')[1])
        d_from, d_to = self._period(options)
        initial = self.engine.sums_by_account(options, 'initial', d_from, d_to, account_ids=[aid]).get(aid, {}).get('balance', 0.0)
        rows, has_more, _r = self._detail(options, aid, initial, line_id, 1, offset)
        return {'lines': rows, 'has_more': has_more}

    def drill(self, report, options, columns, line_id, column_key):
        d_from, d_to = self._period(options)
        accounts = self.engine.accounts(options)
        if line_id.startswith('ac:'):
            aid = int(line_id.split(':')[1])
            mode = 'initial' if line_id.endswith(':ini') else 'flow'
            if line_id.endswith(':ini'):
                aid = int(line_id.split(':')[1])
            name = '%s %s' % (accounts.get(aid, {}).get('code', ''), accounts.get(aid, {}).get('name', ''))
            return self.engine.domain(options, mode, d_from, d_to, [aid]), name
        if line_id == 'total':
            return self.engine.domain(options, 'flow', d_from, d_to, None), _('All journal items')
        if line_id == 'unalloc':
            pl_ids = [a for a, m in accounts.items() if m['type'] in PL_TYPES]
            return self.engine.domain(options, 'earnings_previous', d_from, d_from, pl_ids), _("Previous years' profit and loss")
        return None

    def explain(self, report, options, columns, line_id, column_key):
        if not line_id.startswith('ac:'):
            return {}
        aid = int(line_id.split(':')[1])
        d_from, d_to = self._period(options)
        out = self._explain_sets(options, 'flow', d_from, d_to, [aid])
        accounts = self.engine.accounts(options)
        out['title'] = '%s %s' % (accounts.get(aid, {}).get('code', ''), accounts.get(aid, {}).get('name', ''))
        return out

    def trends(self, report, options, line_ids):
        out = {}
        for lid in line_ids:
            if lid.startswith('ac:') and lid.count(':') == 1:
                aid = int(lid.split(':')[1])
                out[lid] = [{'label': m.strftime('%b %y'), 'value': round(v, 2)}
                            for m, v in self.engine.monthly(options, 'flow', [aid])]
        return out


class TrialBalanceHandler(models.AbstractModel):
    _name = 'ebshel.fin.handler.trial_balance'
    _inherit = 'ebshel.fin.handler.ledger_base'
    _description = 'Financial report engine: trial balance'
    _kind_label = 'Trial balance'

    def columns(self, report, options):
        return [{'key': 'ini_d', 'label': _('Initial Debit'), 'type': 'amount'},
                {'key': 'ini_c', 'label': _('Initial Credit'), 'type': 'amount'},
                {'key': 'debit', 'label': _('Debit'), 'type': 'amount'},
                {'key': 'credit', 'label': _('Credit'), 'type': 'amount'},
                {'key': 'end_d', 'label': _('End Debit'), 'type': 'amount'},
                {'key': 'end_c', 'label': _('End Credit'), 'type': 'amount'}]

    def lines(self, report, options, columns, for_export=False):
        engine = self.engine
        currency = engine.currency(options)
        accounts = engine.accounts(options)
        wanted = self._account_filter(options, accounts)
        d_from, d_to = self._period(options)
        initial = engine.sums_by_account(options, 'initial', d_from, d_to, account_ids=wanted)
        period = engine.sums_by_account(options, 'flow', d_from, d_to, account_ids=wanted)
        ids = set(initial) | set(period)
        if options.get('show_zero'):
            ids |= set(wanted if wanted is not None else accounts)
        rows, totals = [], [0.0] * 6
        for aid in sorted(ids, key=lambda i: accounts.get(i, {}).get('code', '')):
            meta = accounts.get(aid)
            if not meta:
                continue
            ini = initial.get(aid, {}).get('balance', 0.0)
            per = period.get(aid, {})
            end = ini + per.get('balance', 0.0)
            vals = [max(ini, 0.0), max(-ini, 0.0), per.get('debit', 0.0), per.get('credit', 0.0),
                    max(end, 0.0), max(-end, 0.0)]
            if not options.get('show_zero') and all(abs(v) < 0.005 for v in vals):
                continue
            totals = [t + v for t, v in zip(totals, vals)]
            rows.append(self._line('ac:%d' % aid, '%s %s' % (meta['code'], meta['name']), 0,
                                   [self._cell(v, currency, drill=True) for v in vals], kind='account', account_id=aid))
        unalloc = self.env['ebshel.fin.handler.general_ledger']._undistributed(options, accounts, wanted)
        if abs(unalloc) >= 0.005:
            vals = [max(unalloc, 0.0), max(-unalloc, 0.0), 0.0, 0.0, max(unalloc, 0.0), max(-unalloc, 0.0)]
            totals = [t + v for t, v in zip(totals, vals)]
            rows.append(self._line('unalloc', _("Undistributed profit and loss of previous years"), 0,
                                   [self._cell(v, currency, drill=True) for v in vals], kind='initial', css='muted'))
        rows.append(self._line('total', _('Total'), 0, [self._cell(v, currency) for v in totals], bold=True, kind='total'))
        return rows

    def drill(self, report, options, columns, line_id, column_key):
        d_from, d_to = self._period(options)
        if line_id.startswith('ac:'):
            aid = int(line_id.split(':')[1])
            mode = 'initial' if column_key in ('ini_d', 'ini_c') else 'cumulative' if column_key in ('end_d', 'end_c') else 'flow'
            accounts = self.engine.accounts(options)
            return self.engine.domain(options, mode, d_from, d_to, [aid]), '%s %s' % (
                accounts.get(aid, {}).get('code', ''), accounts.get(aid, {}).get('name', ''))
        if line_id == 'total':
            return self.engine.domain(options, 'flow', d_from, d_to, None), _('All journal items')
        if line_id == 'unalloc':
            accounts = self.engine.accounts(options)
            pl_ids = [a for a, m in accounts.items() if m['type'] in PL_TYPES]
            return self.engine.domain(options, 'earnings_previous', d_from, d_from, pl_ids), _("Previous years' profit and loss")
        return None

    def explain(self, report, options, columns, line_id, column_key):
        return self.env['ebshel.fin.handler.general_ledger'].explain(report, options, columns, line_id, column_key)

    def trends(self, report, options, line_ids):
        return self.env['ebshel.fin.handler.general_ledger'].trends(report, options, line_ids)


class PartnerLedgerHandler(models.AbstractModel):
    _name = 'ebshel.fin.handler.partner_ledger'
    _inherit = 'ebshel.fin.handler.ledger_base'
    _description = 'Financial report engine: partner ledger'
    _kind_label = 'Partner ledger'

    def columns(self, report, options):
        return [{'key': 'debit', 'label': _('Debit'), 'type': 'amount'},
                {'key': 'credit', 'label': _('Credit'), 'type': 'amount'},
                {'key': 'balance', 'label': _('Balance'), 'type': 'amount'}]

    def extra_filters(self, report, options):
        return [{'key': 'account_types', 'label': _('Accounts'), 'type': 'multi',
                 'value': options.get('account_types') or [],
                 'choices': [('asset_receivable', _('Receivable')), ('liability_payable', _('Payable'))]},
                {'key': 'show_zero', 'label': _('Partners without movement'), 'type': 'toggle',
                 'value': options.get('show_zero', False)}]

    def _ledger_accounts(self, report, options):
        types = options.get('account_types') or [t.strip() for t in (report.ledger_account_types or '').split(',') if t.strip()]
        accounts = self.engine.accounts(options)
        return sorted(a for a, m in accounts.items() if m['type'] in types)

    def lines(self, report, options, columns, for_export=False):
        engine = self.engine
        currency = engine.currency(options)
        acc = self._ledger_accounts(report, options)
        d_from, d_to = self._period(options)
        initial = {r[0]: float(r[1] or 0) for r in engine.sums_by(options, SQL("l.partner_id"), 'initial', d_from, d_to, account_ids=acc)}
        period = {r[0]: (float(r[2] or 0), float(r[3] or 0), float(r[1] or 0))
                  for r in engine.sums_by(options, SQL("l.partner_id"), 'flow', d_from, d_to, account_ids=acc)}
        ids = set(initial) | set(period)
        names = {p.id: p.display_name for p in self.env['res.partner'].sudo().browse([i for i in ids if i]).exists()}
        rows, tot = [], [0.0, 0.0, 0.0]
        for pid in sorted(ids, key=lambda i: (names.get(i) or '~').lower()):
            ini = initial.get(pid, 0.0)
            d, c, b = period.get(pid, (0.0, 0.0, 0.0))
            bal = ini + b
            if not options.get('show_zero') and abs(d) < 0.005 and abs(c) < 0.005 and abs(bal) < 0.005:
                continue
            tot = [tot[0] + d, tot[1] + c, tot[2] + bal]
            lid = 'pa:%d' % (pid or 0)
            unfolded = self._is_open(options, lid)
            rows.append(self._line(lid, names.get(pid, _('Unknown partner')), 0,
                                   [self._cell(d, currency, drill=True), self._cell(c, currency, drill=True),
                                    self._cell(bal, currency, drill=True)],
                                   unfoldable=True, unfolded=unfolded, kind='partner', partner_id=pid or 0, initial=ini))
            if unfolded:
                detail, has_more = self._detail(options, acc, pid, ini, lid, 0, limit=PAGE if not for_export else 5000)
                rows.extend(detail)
                if has_more:
                    rows.append(self._line(lid + ':more', _('Load more…'), 1, [self._blank()] * 3, parent_id=lid,
                                           kind='more', offset=len(detail) - 1))
        rows.append(self._line('total', _('Total'), 0, [self._cell(v, currency) for v in tot], bold=True, kind='total'))
        return rows

    def _partner_where(self, pid):
        return SQL("l.partner_id = %s", pid) if pid else SQL("l.partner_id IS NULL")

    def _detail(self, options, acc, pid, initial, parent_lid, offset, limit=PAGE):
        d_from, d_to = self._period(options)
        currency = self.engine.currency(options)
        rows = []
        if offset == 0:
            rows.append(self._line(parent_lid + ':ini', _('Initial balance'), 1,
                                   [self._blank(), self._blank(), self._cell(initial, currency, drill=True)],
                                   parent_id=parent_lid, kind='initial'))
        where = [self.engine.date_where('flow', d_from, d_to, options), self._partner_where(pid)]
        if acc:
            where.append(SQL("l.account_id IN %s", tuple(acc)))
        detail, has_more, _r = self._move_line_rows(options, where, None, offset, limit, running_start=initial,
                                                    parent_id=parent_lid, level=1)
        rows.extend(detail)
        return rows, has_more

    def expand(self, report, options, columns, line_id, offset=0):
        if not line_id.startswith('pa:'):
            return {'lines': [], 'has_more': False}
        pid = int(line_id.split(':')[1]) or None
        acc = self._ledger_accounts(report, options)
        d_from, d_to = self._period(options)
        ini = {r[0]: float(r[1] or 0) for r in self.engine.sums_by(
            options, SQL("l.partner_id"), 'initial', d_from, d_to, account_ids=acc,
            extra_where=self._partner_where(pid))}.get(pid, 0.0)
        rows, has_more = self._detail(options, acc, pid, ini, line_id, offset)
        return {'lines': rows, 'has_more': has_more}

    def drill(self, report, options, columns, line_id, column_key):
        d_from, d_to = self._period(options)
        acc = self._ledger_accounts(report, options)
        if line_id.startswith('pa:'):
            pid = int(line_id.split(':')[1]) or None
            mode = 'initial' if line_id.endswith(':ini') else 'flow'
            domain = self.engine.domain(options, mode, d_from, d_to, acc)
            domain.append(('partner_id', '=', pid) if pid else ('partner_id', '=', False))
            name = self.env['res.partner'].browse(pid).display_name if pid else _('Unknown partner')
            return domain, name
        if line_id == 'total':
            return self.engine.domain(options, 'flow', d_from, d_to, acc), _('Partner ledger items')
        return None

    def explain(self, report, options, columns, line_id, column_key):
        if not line_id.startswith('pa:'):
            return {}
        pid = int(line_id.split(':')[1]) or None
        d_from, d_to = self._period(options)
        out = self._explain_sets(options, 'flow', d_from, d_to, self._ledger_accounts(report, options),
                                 partner_ids=[pid] if pid else None)
        out['title'] = self.env['res.partner'].browse(pid).display_name if pid else _('Unknown partner')
        return out


class AgedHandler(models.AbstractModel):
    """Aged receivable and aged payable: what was open on the date, bucketed by
    how long it had been due. Residuals are reconstructed AS OF the date from
    the partial reconciliations, so an old period reads as it did then."""
    _name = 'ebshel.fin.handler.aged'
    _inherit = 'ebshel.fin.handler'
    _description = 'Financial report engine: ageing'
    _kind_label = 'Ageing (receivable / payable)'

    def _account_type(self, report):
        return 'liability_payable' if 'payable' in (report.key or '') else 'asset_receivable'

    def adjust_options(self, report, options):
        options['comparison'] = {'mode': 'none', 'periods': 1, 'from': None, 'to': None}
        options['growth'] = False
        return options

    def extra_filters(self, report, options):
        return [{'key': 'aged_interval', 'label': _('Days per bucket'), 'type': 'int', 'value': options['aged_interval']},
                {'key': 'aged_buckets', 'label': _('Buckets'), 'type': 'int', 'value': options['aged_buckets']}]

    def _buckets(self, options):
        n, step = options['aged_buckets'], options['aged_interval']
        cols = [{'key': 'b0', 'label': _('Not due'), 'lo': None, 'hi': 0}]
        for i in range(n):
            lo, hi = i * step + 1, (i + 1) * step
            cols.append({'key': 'b%d' % (i + 1), 'label': '%d–%d' % (lo, hi), 'lo': lo, 'hi': hi})
        cols.append({'key': 'b%d' % (n + 1), 'label': _('Older'), 'lo': n * step + 1, 'hi': None})
        return cols

    def columns(self, report, options):
        cols = [dict(b, type='amount') for b in self._buckets(options)]
        cols.append({'key': 'total', 'label': _('Total'), 'type': 'amount'})
        return cols

    def _open_items(self, options, atype, partner_ids=None):
        """[(line_id, partner_id, date, due, move_name, label, open_amount)] as of date_to."""
        d_to = fields.Date.to_date(options['date']['to'])
        where = [self.engine.base_where(options), SQL("a.account_type = %s", atype), SQL("l.date <= %s", d_to)]
        if partner_ids:
            where.append(SQL("l.partner_id IN %s", tuple(partner_ids)))
        rows = self.env.execute_query(SQL("""
            SELECT s.id, s.partner_id, s.date, s.due, s.move_name, s.label, s.open_amount
              FROM (
                SELECT l.id, l.partner_id, l.date, COALESCE(l.date_maturity, l.date) AS due,
                       m.name AS move_name, COALESCE(l.name, l.ref, '') AS label,
                       l.balance
                       - COALESCE((SELECT SUM(p.amount) FROM account_partial_reconcile p
                                    WHERE p.debit_move_id = l.id AND p.max_date <= %s), 0)
                       + COALESCE((SELECT SUM(p.amount) FROM account_partial_reconcile p
                                    WHERE p.credit_move_id = l.id AND p.max_date <= %s), 0) AS open_amount
                  FROM account_move_line l
                  JOIN account_account a ON a.id = l.account_id
                  JOIN account_move m ON m.id = l.move_id
                 WHERE %s
              ) s
             WHERE ROUND(s.open_amount::numeric, 2) <> 0
             ORDER BY s.partner_id, s.due, s.id
        """, d_to, d_to, SQL(" AND ").join(where)))
        return rows

    def _bucket_of(self, buckets, days):
        for b in buckets:
            if b['lo'] is None and days <= b['hi']:
                return b['key']
            if b['lo'] is not None and days >= b['lo'] and (b['hi'] is None or days <= b['hi']):
                return b['key']
        return buckets[-1]['key']

    def lines(self, report, options, columns, for_export=False):
        atype = self._account_type(report)
        sign = -1.0 if atype == 'liability_payable' else 1.0
        d_to = fields.Date.to_date(options['date']['to'])
        buckets = self._buckets(options)
        currency = self.engine.currency(options)
        per_partner, items = {}, {}
        for lid, pid, day, due, move_name, label, open_amount in self._open_items(options, atype):
            key = self._bucket_of(buckets, (d_to - due).days)
            amount = sign * float(open_amount)
            per_partner.setdefault(pid, {})
            per_partner[pid][key] = per_partner[pid].get(key, 0.0) + amount
            items.setdefault(pid, []).append((lid, day, due, move_name, label, amount, key))
        names = {p.id: p.display_name for p in self.env['res.partner'].sudo().browse([i for i in per_partner if i]).exists()}
        rows, totals = [], {b['key']: 0.0 for b in buckets}
        for pid in sorted(per_partner, key=lambda i: (names.get(i) or '~').lower()):
            vals = per_partner[pid]
            cells = [self._cell(vals.get(b['key'], 0.0), currency, drill=True) for b in buckets]
            total = sum(vals.values())
            cells.append(self._cell(total, currency, drill=True))
            for k, v in vals.items():
                totals[k] += v
            lid = 'pa:%d' % (pid or 0)
            unfolded = self._is_open(options, lid)
            rows.append(self._line(lid, names.get(pid, _('Unknown partner')), 0, cells, unfoldable=True,
                                   unfolded=unfolded, kind='partner', partner_id=pid or 0))
            if unfolded:
                rows.extend(self._item_rows(items[pid], buckets, currency, lid, d_to))
        cells = [self._cell(totals[b['key']], currency) for b in buckets] + [self._cell(sum(totals.values()), currency)]
        rows.append(self._line('total', _('Total'), 0, cells, bold=True, kind='total'))
        return rows

    def _item_rows(self, items, buckets, currency, parent_lid, d_to):
        rows = []
        for lid, day, due, move_name, label, amount, key in items:
            cells = [self._cell(amount, currency) if b['key'] == key else self._blank() for b in buckets]
            cells.append(self._cell(amount, currency))
            parts = {'date': fields.Date.to_string(day), 'due': fields.Date.to_string(due), 'move': move_name or '',
                     'label': label or '', 'days': (d_to - due).days}
            rows.append(self._line('ml:%d' % lid, '%s · %s' % (parts['date'], move_name or ''), 1, cells,
                                   parent_id=parent_lid, kind='open_item', parts=parts,
                                   model='account.move.line', res_id=lid))
        return rows

    def expand(self, report, options, columns, line_id, offset=0):
        if not line_id.startswith('pa:'):
            return {'lines': [], 'has_more': False}
        pid = int(line_id.split(':')[1]) or None
        atype = self._account_type(report)
        sign = -1.0 if atype == 'liability_payable' else 1.0
        d_to = fields.Date.to_date(options['date']['to'])
        buckets = self._buckets(options)
        currency = self.engine.currency(options)
        items = [(lid, day, due, mv, lb, sign * float(o), self._bucket_of(buckets, (d_to - due).days))
                 for lid, p, day, due, mv, lb, o in self._open_items(options, atype, partner_ids=[pid] if pid else None)
                 if (p or None) == pid]
        return {'lines': self._item_rows(items, buckets, currency, line_id, d_to), 'has_more': False}

    def drill(self, report, options, columns, line_id, column_key):
        atype = self._account_type(report)
        d_to = fields.Date.to_date(options['date']['to'])
        buckets = self._buckets(options)
        if line_id.startswith('pa:') or line_id == 'total':
            pid = int(line_id.split(':')[1]) if line_id.startswith('pa:') else None
            items = self._open_items(options, atype, partner_ids=[pid] if pid else None)
            ids = [lid for lid, p, day, due, mv, lb, o in items
                   if (line_id == 'total' or (p or 0) == (pid or 0))
                   and (column_key == 'total' or self._bucket_of(buckets, (d_to - due).days) == column_key)]
            name = self.env['res.partner'].browse(pid).display_name if pid else _('Open items')
            return [('id', 'in', ids)], '%s — %s' % (name, next((c['label'] for c in columns if c['key'] == column_key), ''))
        return None


class TaxHandler(models.AbstractModel):
    _name = 'ebshel.fin.handler.tax'
    _inherit = 'ebshel.fin.handler'
    _description = 'Financial report engine: tax'
    _kind_label = 'Tax report'

    def adjust_options(self, report, options):
        options['growth'] = False
        return options

    def columns(self, report, options):
        cols = []
        for col in self.engine.period_columns(report, options):
            if col['type'] != 'amount':
                continue
            cols.append(dict(col, key=col['key'] + ':base', label=_('%s · Base', col['label'])))
            cols.append(dict(col, key=col['key'] + ':tax', label=_('%s · Tax', col['label'])))
        return cols

    def _amounts(self, options, d_from, d_to):
        """{tax_id: (base, tax)} for a window."""
        base_where = SQL(" AND ").join([self.engine.base_where(options),
                                        self.engine.date_where('flow', d_from, d_to, options)])
        w = self.engine.weight_sql(options)
        base_rows = self.env.execute_query(SQL("""
            SELECT r.account_tax_id, SUM(l.balance * %s)
              FROM account_move_line l
              JOIN account_move_line_account_tax_rel r ON r.account_move_line_id = l.id
              JOIN account_account a ON a.id = l.account_id
             WHERE %s GROUP BY r.account_tax_id""", w, base_where))
        tax_rows = self.env.execute_query(SQL("""
            SELECT l.tax_line_id, SUM(l.balance * %s)
              FROM account_move_line l
              JOIN account_account a ON a.id = l.account_id
             WHERE %s AND l.tax_line_id IS NOT NULL GROUP BY l.tax_line_id""", w, base_where))
        out = {}
        for tid, v in base_rows:
            out.setdefault(tid, [0.0, 0.0])[0] += float(v or 0)
        for tid, v in tax_rows:
            out.setdefault(tid, [0.0, 0.0])[1] += float(v or 0)
        return out

    def _grid_amounts(self, options, d_from, d_to):
        base_where = SQL(" AND ").join([self.engine.base_where(options),
                                        self.engine.date_where('flow', d_from, d_to, options)])
        w = self.engine.weight_sql(options)
        rows = self.env.execute_query(SQL("""
            SELECT r.account_account_tag_id, SUM(l.balance * %s)
              FROM account_move_line l
              JOIN account_account_tag_account_move_line_rel r ON r.account_move_line_id = l.id
              JOIN account_account a ON a.id = l.account_id
             WHERE %s GROUP BY r.account_account_tag_id""", w, base_where))
        return {r[0]: float(r[1] or 0) for r in rows}

    def lines(self, report, options, columns, for_export=False):
        currency = self.engine.currency(options)
        periods = [c for c in columns if c['key'].endswith(':base')]
        per_period = {}
        grids = {}
        for col in periods:
            d_from, d_to = self._dates(col)
            per_period[col['key'][:-5]] = self._amounts(options, d_from, d_to)
            grids[col['key'][:-5]] = self._grid_amounts(options, d_from, d_to)
        tax_ids = set().union(*[set(v) for v in per_period.values()]) if per_period else set()
        taxes = self.env['account.tax'].sudo().with_context(active_test=False).browse(list(tax_ids)).exists()
        rows = []
        for use, title, sign in (('sale', _('Sales'), -1.0), ('purchase', _('Purchases'), 1.0), ('none', _('Other'), 1.0)):
            group = taxes.filtered(lambda t: t.type_tax_use == use).sorted(lambda t: (t.sequence, t.name))
            if not group:
                continue
            section_cells, sums = [], {}
            lid_section = 'sec:%s' % use
            rows.append(self._line(lid_section, title, 0, [], bold=True, kind='header'))
            for tax in group:
                cells = []
                for col in periods:
                    key = col['key'][:-5]
                    base, amt = per_period[key].get(tax.id, [0.0, 0.0])
                    cells += [self._cell(sign * base, currency, drill=True), self._cell(sign * amt, currency, drill=True)]
                    sums.setdefault(key, [0.0, 0.0])
                    sums[key][0] += sign * base
                    sums[key][1] += sign * amt
                rows.append(self._line('tx:%d' % tax.id, tax.name, 1, cells, parent_id=lid_section, kind='tax', tax_id=tax.id))
            for col in periods:
                key = col['key'][:-5]
                section_cells += [self._cell(sums.get(key, [0, 0])[0], currency), self._cell(sums.get(key, [0, 0])[1], currency)]
            rows[[r['id'] for r in rows].index(lid_section)]['columns'] = section_cells
        tag_ids = set().union(*[set(v) for v in grids.values()]) if grids else set()
        tags = self.env['account.account.tag'].sudo().browse(list(tag_ids)).exists().sorted('name')
        if tags:
            rows.append(self._line('sec:grids', _('Tax grids'), 0, [self._blank()] * len(columns), bold=True, kind='header'))
            for tag in tags:
                cells = []
                for col in periods:
                    key = col['key'][:-5]
                    cells += [self._blank(), self._cell(-grids[key].get(tag.id, 0.0), currency, drill=True)]
                rows.append(self._line('tg:%d' % tag.id, tag.name, 1, cells, parent_id='sec:grids', kind='grid', tag_id=tag.id))
        return rows

    def drill(self, report, options, columns, line_id, column_key):
        col = next((c for c in columns if c['key'] == column_key), columns[0])
        d_from, d_to = self._dates(col)
        domain = self.engine.domain(options, 'flow', d_from, d_to, None)
        if line_id.startswith('tx:'):
            tid = int(line_id.split(':')[1])
            if column_key.endswith(':base'):
                domain.append(('tax_ids', 'in', [tid]))
            else:
                domain.append(('tax_line_id', '=', tid))
            return domain, self.env['account.tax'].browse(tid).name
        if line_id.startswith('tg:'):
            tid = int(line_id.split(':')[1])
            domain.append(('tax_tag_ids', 'in', [tid]))
            return domain, self.env['account.account.tag'].browse(tid).name
        return None


class JournalHandler(models.AbstractModel):
    _name = 'ebshel.fin.handler.journal'
    _inherit = 'ebshel.fin.handler.ledger_base'
    _description = 'Financial report engine: journal audit'
    _kind_label = 'Journal audit'

    def columns(self, report, options):
        return [{'key': 'debit', 'label': _('Debit'), 'type': 'amount'},
                {'key': 'credit', 'label': _('Credit'), 'type': 'amount'},
                {'key': 'count', 'label': _('Entries'), 'type': 'count'}]

    def extra_filters(self, report, options):
        return []

    def lines(self, report, options, columns, for_export=False):
        engine = self.engine
        currency = engine.currency(options)
        d_from, d_to = self._period(options)
        rows_sql = engine.sums_by(options, SQL("l.journal_id"), 'flow', d_from, d_to)
        moves = {r[0]: r[1] for r in self.env.execute_query(SQL("""
            SELECT l.journal_id, COUNT(DISTINCT l.move_id) FROM account_move_line l
              JOIN account_account a ON a.id = l.account_id
             WHERE %s AND %s GROUP BY l.journal_id""", engine.base_where(options),
            engine.date_where('flow', d_from, d_to, options)))}
        journals = {j.id: j for j in self.env['account.journal'].sudo().browse([r[0] for r in rows_sql])}
        rows, td, tc, tn = [], 0.0, 0.0, 0
        for jid, bal, d, c, n in sorted(rows_sql, key=lambda r: (journals[r[0]].sequence, journals[r[0]].code or '')):
            j = journals[jid]
            d, c, n = float(d or 0), float(c or 0), moves.get(jid, 0)
            td, tc, tn = td + d, tc + c, tn + n
            lid = 'jn:%d' % jid
            unfolded = self._is_open(options, lid)
            rows.append(self._line(lid, '%s · %s' % (j.code, j.name), 0,
                                   [self._cell(d, currency, drill=True), self._cell(c, currency, drill=True),
                                    self._cell(float(n), currency, 'count', drill=True)],
                                   unfoldable=True, unfolded=unfolded, kind='journal', journal_id=jid))
            if unfolded:
                detail, has_more = self._entries(options, jid, lid, 0, limit=PAGE if not for_export else 5000)
                rows.extend(detail)
                if has_more:
                    rows.append(self._line(lid + ':more', _('Load more…'), 1, [self._blank()] * 3, parent_id=lid,
                                           kind='more', offset=len(detail)))
        rows.append(self._line('total', _('Total'), 0, [self._cell(td, currency), self._cell(tc, currency),
                                                        self._cell(float(tn), currency, 'count')], bold=True, kind='total'))
        return rows

    def _entries(self, options, jid, parent_lid, offset, limit=PAGE):
        d_from, d_to = self._period(options)
        currency = self.engine.currency(options)
        w = self.engine.weight_sql(options)
        rows = self.env.execute_query(SQL("""
            SELECT m.id, m.date, m.name, p.name, m.ref, SUM(l.debit * %s), SUM(l.credit * %s), m.move_type
              FROM account_move_line l
              JOIN account_move m ON m.id = l.move_id
              JOIN account_account a ON a.id = l.account_id
              LEFT JOIN res_partner p ON p.id = m.partner_id
             WHERE %s AND %s AND l.journal_id = %s
             GROUP BY m.id, m.date, m.name, p.name, m.ref, m.move_type
             ORDER BY m.date, m.name LIMIT %s OFFSET %s
        """, w, w, self.engine.base_where(options), self.engine.date_where('flow', d_from, d_to, options),
             jid, limit + 1, offset))
        out = []
        for mid, day, name, partner, ref, d, c, mtype in rows[:limit]:
            parts = {'date': fields.Date.to_string(day), 'move': name or '', 'partner': partner or '',
                     'label': ref or '', 'journal': '', 'account': '', 'matching': '', 'due': ''}
            out.append(self._line('mv:%d' % mid, '%s · %s' % (parts['date'], name or ''), 1,
                                  [self._cell(float(d or 0), currency), self._cell(float(c or 0), currency), self._blank()],
                                  parent_id=parent_lid, kind='move_line', parts=parts, model='account.move', res_id=mid))
        return out, len(rows) > limit

    def expand(self, report, options, columns, line_id, offset=0):
        if not line_id.startswith('jn:'):
            return {'lines': [], 'has_more': False}
        rows, has_more = self._entries(options, int(line_id.split(':')[1]), line_id, offset)
        return {'lines': rows, 'has_more': has_more}

    def drill(self, report, options, columns, line_id, column_key):
        d_from, d_to = self._period(options)
        if line_id.startswith('jn:'):
            jid = int(line_id.split(':')[1])
            domain = self.engine.domain(options, 'flow', d_from, d_to, None) + [('journal_id', '=', jid)]
            return domain, self.env['account.journal'].browse(jid).display_name
        if line_id == 'total':
            return self.engine.domain(options, 'flow', d_from, d_to, None), _('All journal items')
        return None


class AnalyticHandler(models.AbstractModel):
    _name = 'ebshel.fin.handler.analytic'
    _inherit = 'ebshel.fin.handler'
    _description = 'Financial report engine: analytic'
    _kind_label = 'Analytic report'

    def _where(self, options, d_from, d_to):
        parts = [SQL("al.company_id IN %s", tuple(options['companies'])),
                 SQL("al.date >= %s AND al.date <= %s", d_from, d_to)]
        if options.get('partners'):
            parts.append(SQL("al.partner_id IN %s", tuple(options['partners'])))
        if options.get('analytic'):
            parts.append(SQL("al.account_id IN %s", tuple(options['analytic'])))
        return SQL(" AND ").join(parts)

    def lines(self, report, options, columns, for_export=False):
        currency = self.engine.currency(options)
        amount_cols = [c for c in columns if c['type'] == 'amount']
        per_col = {}
        for col in amount_cols:
            d_from, d_to = self._dates(col)
            per_col[col['key']] = {r[0]: float(r[1] or 0) for r in self.env.execute_query(SQL("""
                SELECT al.account_id, SUM(al.amount) FROM account_analytic_line al
                 WHERE %s GROUP BY al.account_id""", self._where(options, d_from, d_to)))}
        ids = set().union(*[set(v) for v in per_col.values()]) if per_col else set()
        accounts = self.env['account.analytic.account'].sudo().with_context(active_test=False).browse(list(ids)).exists()
        rows = []
        for plan in accounts.mapped('plan_id').sorted('complete_name'):
            group = accounts.filtered(lambda a: a.plan_id == plan).sorted('name')
            plan_cells = []
            for col in amount_cols:
                plan_cells.append(self._cell(sum(per_col[col['key']].get(a.id, 0.0) for a in group), currency))
            plan_lid = 'pl:%d' % plan.id
            rows.append(self._line(plan_lid, plan.complete_name, 0, self._finish(plan_cells, columns, options),
                                   bold=True, kind='plan'))
            for acc in group:
                cells = [self._cell(per_col[col['key']].get(acc.id, 0.0), currency, drill=True) for col in amount_cols]
                lid = 'an:%d' % acc.id
                unfolded = self._is_open(options, lid)
                rows.append(self._line(lid, acc.name, 1, self._finish(cells, columns, options), parent_id=plan_lid,
                                       unfoldable=True, unfolded=unfolded, kind='analytic', analytic_id=acc.id))
                if unfolded:
                    detail, has_more = self._detail(options, columns, acc.id, lid, 0, limit=PAGE if not for_export else 5000)
                    rows.extend(detail)
        return rows

    def _detail(self, options, columns, aid, parent_lid, offset, limit=PAGE):
        currency = self.engine.currency(options)
        col = next(c for c in columns if c['type'] == 'amount')
        d_from, d_to = self._dates(col)
        rows = self.env.execute_query(SQL("""
            SELECT al.id, al.date, al.name, p.name, %s, al.amount, al.move_line_id, m.name, m.id
              FROM account_analytic_line al
              LEFT JOIN res_partner p ON p.id = al.partner_id
              LEFT JOIN account_account a ON a.id = al.general_account_id
              LEFT JOIN account_move_line l ON l.id = al.move_line_id
              LEFT JOIN account_move m ON m.id = l.move_id
             WHERE %s AND al.account_id = %s
             ORDER BY al.date, al.id LIMIT %s OFFSET %s
        """, self.engine.code_sql(options), self._where(options, d_from, d_to), aid, limit + 1, offset))
        out = []
        for lid, day, name, partner, acode, amount, ml, mname, mid in rows[:limit]:
            parts = {'date': fields.Date.to_string(day), 'move': mname or '', 'partner': partner or '',
                     'label': name or '', 'journal': '', 'account': acode or '', 'matching': '', 'due': ''}
            cells = [self._cell(float(amount or 0), currency)] + [self._blank()] * (len([c for c in columns if c['type'] == 'amount']) - 1)
            out.append(self._line('al:%d' % lid, '%s · %s' % (parts['date'], name or ''), 2, self._finish(cells, columns, options),
                                  parent_id=parent_lid, kind='move_line', parts=parts,
                                  model='account.move' if mid else 'account.analytic.line', res_id=mid or lid))
        return out, len(rows) > limit

    def expand(self, report, options, columns, line_id, offset=0):
        if not line_id.startswith('an:'):
            return {'lines': [], 'has_more': False}
        rows, has_more = self._detail(options, columns, int(line_id.split(':')[1]), line_id, offset)
        return {'lines': rows, 'has_more': has_more}

    def drill(self, report, options, columns, line_id, column_key):
        if not line_id.startswith('an:'):
            return None
        aid = int(line_id.split(':')[1])
        col = next((c for c in columns if c['key'] == column_key and c['type'] == 'amount'), columns[0])
        d_from, d_to = self._dates(col)
        domain = [('account_id', '=', aid), ('date', '>=', d_from), ('date', '<=', d_to),
                  ('company_id', 'in', options['companies'])]
        return {'type': 'ir.actions.act_window', 'name': self.env['account.analytic.account'].browse(aid).name,
                'res_model': 'account.analytic.line', 'view_mode': 'list,pivot,graph',
                'views': [(False, 'list'), (False, 'pivot'), (False, 'graph')], 'domain': domain,
                'context': {'create': False}}


class StatementOfAccountHandler(models.AbstractModel):
    """A customer's or vendor's account over the period: opening balance, every
    item, closing balance - one block per partner, one page each on paper."""
    _name = 'ebshel.fin.handler.statement_of_account'
    _inherit = 'ebshel.fin.handler.ledger_base'
    _description = 'Financial report engine: statement of account'
    _kind_label = 'Statement of account (customer / vendor)'

    def columns(self, report, options):
        return [{'key': 'debit', 'label': _('Debit'), 'type': 'amount'},
                {'key': 'credit', 'label': _('Credit'), 'type': 'amount'},
                {'key': 'balance', 'label': _('Balance'), 'type': 'amount'}]

    def extra_filters(self, report, options):
        return []

    def _accounts(self, report, options):
        atype = 'liability_payable' if 'vendor' in (report.key or '') else 'asset_receivable'
        return sorted(a for a, m in self.engine.accounts(options).items() if m['type'] == atype)

    def lines(self, report, options, columns, for_export=False):
        engine = self.engine
        currency = engine.currency(options)
        acc = self._accounts(report, options)
        d_from, d_to = self._period(options)
        partners = options.get('partners') or [
            r[0] for r in engine.sums_by(options, SQL("l.partner_id"), 'flow', d_from, d_to, account_ids=acc,
                                         order=SQL("MAX(p.name)"), joins=SQL("LEFT JOIN res_partner p ON p.id = l.partner_id"),
                                         limit=200) if r[0]]
        names = {p.id: p.display_name for p in self.env['res.partner'].sudo().browse(partners).exists()}
        rows = []
        for pid in partners:
            ini = {r[0]: float(r[1] or 0) for r in engine.sums_by(options, SQL("l.partner_id"), 'initial', d_from, d_to,
                                                                   account_ids=acc, extra_where=SQL("l.partner_id = %s", pid))}.get(pid, 0.0)
            lid = 'pa:%d' % pid
            rows.append(self._line(lid, names.get(pid, '?'), 0, [self._blank(), self._blank(), self._cell(ini, currency)],
                                   bold=True, kind='partner', partner_id=pid, page_break=True, unfoldable=False))
            rows.append(self._line(lid + ':ini', _('Opening balance'), 1, [self._blank(), self._blank(), self._cell(ini, currency, drill=True)],
                                   parent_id=lid, kind='initial'))
            where = [engine.date_where('flow', d_from, d_to, options), SQL("l.partner_id = %s", pid)]
            if acc:
                where.append(SQL("l.account_id IN %s", tuple(acc)))
            detail, has_more, closing = self._move_line_rows(options, where, None, 0, 5000 if for_export else 400,
                                                             running_start=ini, parent_id=lid, level=1)
            rows.extend(detail)
            d_sum = sum(r['columns'][0]['value'] or 0 for r in detail)
            c_sum = sum(r['columns'][1]['value'] or 0 for r in detail)
            rows.append(self._line(lid + ':close', _('Closing balance'), 1,
                                   [self._cell(d_sum, currency), self._cell(c_sum, currency), self._cell(closing, currency)],
                                   parent_id=lid, bold=True, kind='total'))
        return rows

    def drill(self, report, options, columns, line_id, column_key):
        if not line_id.startswith('pa:'):
            return None
        pid = int(line_id.split(':')[1])
        d_from, d_to = self._period(options)
        mode = 'initial' if line_id.endswith(':ini') else 'flow'
        domain = self.engine.domain(options, mode, d_from, d_to, self._accounts(report, options)) + [('partner_id', '=', pid)]
        return domain, self.env['res.partner'].browse(pid).display_name

    def send_statements(self, report, options):
        """One PDF per partner, emailed to those with an address. Returns counts."""
        engine = self.engine
        options = self.adjust_options(report, engine.normalize(report, options))
        acc = self._accounts(report, options)
        d_from, d_to = self._period(options)
        partners = options.get('partners') or [
            r[0] for r in engine.sums_by(options, SQL("l.partner_id"), 'flow', d_from, d_to, account_ids=acc, limit=200) if r[0]]
        sent, skipped = 0, 0
        for partner in self.env['res.partner'].browse(partners).exists():
            if not partner.email:
                skipped += 1
                continue
            single = dict(options, partners=[partner.id])
            pdf, _kind = self.env['ir.actions.report']._render_qweb_pdf(
                'ebshel_account_reports.action_fin_report_pdf', res_ids=[report.id],
                data={'options': single, 'report_id': report.id})
            import base64
            att = self.env['ir.attachment'].create({
                'name': '%s - %s.pdf' % (report.name, partner.name), 'datas': base64.b64encode(pdf).decode(),
                'res_model': 'res.partner', 'res_id': partner.id})
            self.env['mail.mail'].create({
                'subject': _('%(report)s — %(from)s to %(to)s', report=report.name,
                             **{'from': options['date']['from'], 'to': options['date']['to']}),
                'body_html': _('<p>Dear %s,</p><p>Please find your statement of account attached.</p>', partner.name),
                'email_to': partner.email, 'recipient_ids': [(4, partner.id)],
                'attachment_ids': [(4, att.id)],
            }).send()
            sent += 1
        return {'sent': sent, 'skipped': skipped}


class DayBookHandler(models.AbstractModel):
    _name = 'ebshel.fin.handler.day_book'
    _inherit = 'ebshel.fin.handler.ledger_base'
    _description = 'Financial report engine: day book'
    _kind_label = 'Day book'

    def columns(self, report, options):
        return [{'key': 'debit', 'label': _('Debit'), 'type': 'amount'},
                {'key': 'credit', 'label': _('Credit'), 'type': 'amount'},
                {'key': 'count', 'label': _('Entries'), 'type': 'count'}]

    def extra_filters(self, report, options):
        return []

    def lines(self, report, options, columns, for_export=False):
        engine = self.engine
        currency = engine.currency(options)
        d_from, d_to = self._period(options)
        w = engine.weight_sql(options)
        rows_sql = self.env.execute_query(SQL("""
            SELECT l.date, SUM(l.debit * %s), SUM(l.credit * %s), COUNT(DISTINCT l.move_id)
              FROM account_move_line l JOIN account_account a ON a.id = l.account_id
             WHERE %s AND %s GROUP BY l.date ORDER BY l.date
        """, w, w, engine.base_where(options), engine.date_where('flow', d_from, d_to, options)))
        rows, td, tc, tn = [], 0.0, 0.0, 0
        for day, d, c, n in rows_sql:
            d, c = float(d or 0), float(c or 0)
            td, tc, tn = td + d, tc + c, tn + n
            lid = 'dt:%s' % fields.Date.to_string(day)
            unfolded = self._is_open(options, lid)
            rows.append(self._line(lid, day.strftime('%A %d %B %Y'), 0,
                                   [self._cell(d, currency, drill=True), self._cell(c, currency, drill=True),
                                    self._cell(float(n), currency, 'count')],
                                   unfoldable=True, unfolded=unfolded, kind='day', day=fields.Date.to_string(day)))
            if unfolded:
                detail, has_more, _r = self._move_line_rows(
                    options, [SQL("l.date = %s", day)], None, 0, PAGE if not for_export else 5000, parent_id=lid, level=1)
                for r in detail:
                    r['columns'][2] = self._blank()
                rows.extend(detail)
                if has_more:
                    rows.append(self._line(lid + ':more', _('Load more…'), 1, [self._blank()] * 3, parent_id=lid,
                                           kind='more', offset=len(detail)))
        rows.append(self._line('total', _('Total'), 0, [self._cell(td, currency), self._cell(tc, currency),
                                                        self._cell(float(tn), currency, 'count')], bold=True, kind='total'))
        return rows

    def expand(self, report, options, columns, line_id, offset=0):
        if not line_id.startswith('dt:'):
            return {'lines': [], 'has_more': False}
        day = fields.Date.to_date(line_id[3:])
        detail, has_more, _r = self._move_line_rows(options, [SQL("l.date = %s", day)], None, offset, PAGE,
                                                    parent_id=line_id, level=1)
        for r in detail:
            r['columns'][2] = self._blank()
        return {'lines': detail, 'has_more': has_more}

    def drill(self, report, options, columns, line_id, column_key):
        if line_id.startswith('dt:'):
            day = fields.Date.to_date(line_id[3:])
            return self.engine.domain(options, 'flow', day, day, None), day.strftime('%d %B %Y')
        if line_id == 'total':
            d_from, d_to = self._period(options)
            return self.engine.domain(options, 'flow', d_from, d_to, None), _('All journal items')
        return None


class CashBookHandler(models.AbstractModel):
    """Cash and bank books: every liquidity account with its receipts, payments,
    the account each entry went to, and a running balance."""
    _name = 'ebshel.fin.handler.cash_book'
    _inherit = 'ebshel.fin.handler.ledger_base'
    _description = 'Financial report engine: cash and bank book'
    _kind_label = 'Cash and bank book'

    def columns(self, report, options):
        return [{'key': 'debit', 'label': _('Receipts'), 'type': 'amount'},
                {'key': 'credit', 'label': _('Payments'), 'type': 'amount'},
                {'key': 'balance', 'label': _('Balance'), 'type': 'amount'}]

    def extra_filters(self, report, options):
        return []

    def _counterparts(self, options, move_ids, account_id):
        """{move_id: 'code name, code name'} of the other accounts on each entry."""
        if not move_ids:
            return {}
        rows = self.env.execute_query(SQL("""
            SELECT l.move_id, STRING_AGG(DISTINCT %s || ' ' || %s, ', ')
              FROM account_move_line l JOIN account_account a ON a.id = l.account_id
             WHERE l.move_id IN %s AND l.account_id <> %s AND l.display_type NOT IN ('line_section', 'line_note')
             GROUP BY l.move_id""", self.engine.code_sql(options), self.engine.name_sql(), tuple(move_ids), account_id))
        return {r[0]: r[1] for r in rows}

    def lines(self, report, options, columns, for_export=False):
        engine = self.engine
        currency = engine.currency(options)
        accounts = engine.accounts(options)
        liquid = sorted((a for a, m in accounts.items() if m['type'] in LIQUIDITY_TYPES),
                        key=lambda i: accounts[i]['code'])
        d_from, d_to = self._period(options)
        opening = engine.sums_by_account(options, 'opening', d_from, d_to, account_ids=liquid)
        period = engine.sums_by_account(options, 'flow', d_from, d_to, account_ids=liquid)
        rows = []
        for aid in liquid:
            ini = opening.get(aid, {}).get('balance', 0.0)
            per = period.get(aid, {})
            if not options.get('show_zero') and not per and abs(ini) < 0.005:
                continue
            lid = 'ac:%d' % aid
            unfolded = self._is_open(options, lid)
            rows.append(self._line(lid, '%s %s' % (accounts[aid]['code'], accounts[aid]['name']), 0,
                                   [self._cell(per.get('debit', 0.0), currency, drill=True),
                                    self._cell(per.get('credit', 0.0), currency, drill=True),
                                    self._cell(ini + per.get('balance', 0.0), currency, drill=True)],
                                   unfoldable=True, unfolded=unfolded, kind='account', account_id=aid, initial=ini))
            if unfolded:
                detail, has_more = self._detail(options, aid, ini, lid, 0, limit=PAGE if not for_export else 5000)
                rows.extend(detail)
                if has_more:
                    rows.append(self._line(lid + ':more', _('Load more…'), 1, [self._blank()] * 3, parent_id=lid,
                                           kind='more', offset=len(detail) - 1))
        return rows

    def _detail(self, options, aid, ini, parent_lid, offset, limit=PAGE):
        d_from, d_to = self._period(options)
        currency = self.engine.currency(options)
        rows = []
        if offset == 0:
            rows.append(self._line(parent_lid + ':ini', _('Opening balance'), 1,
                                   [self._blank(), self._blank(), self._cell(ini, currency, drill=True)],
                                   parent_id=parent_lid, kind='initial'))
        detail, has_more, _r = self._move_line_rows(
            options, [self.engine.date_where('flow', d_from, d_to, options), SQL("l.account_id = %s", aid)],
            None, offset, limit, running_start=ini, parent_id=parent_lid, level=1)
        counterparts = self._counterparts(options, [r['res_id'] for r in detail], aid)
        for r in detail:
            r['parts']['account'] = counterparts.get(r['res_id'], '')
        rows.extend(detail)
        return rows, has_more

    def expand(self, report, options, columns, line_id, offset=0):
        if not line_id.startswith('ac:'):
            return {'lines': [], 'has_more': False}
        aid = int(line_id.split(':')[1])
        d_from, d_to = self._period(options)
        ini = self.engine.sums_by_account(options, 'opening', d_from, d_to, account_ids=[aid]).get(aid, {}).get('balance', 0.0)
        rows, has_more = self._detail(options, aid, ini, line_id, offset)
        return {'lines': rows, 'has_more': has_more}

    def drill(self, report, options, columns, line_id, column_key):
        if not line_id.startswith('ac:'):
            return None
        aid = int(line_id.split(':')[1])
        d_from, d_to = self._period(options)
        mode = 'opening' if line_id.endswith(':ini') else 'flow'
        accounts = self.engine.accounts(options)
        return self.engine.domain(options, mode, d_from, d_to, [aid]), '%s %s' % (accounts[aid]['code'], accounts[aid]['name'])
