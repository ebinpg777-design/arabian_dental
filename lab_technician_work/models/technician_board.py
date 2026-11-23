# -*- coding: utf-8 -*-
"""The Technician Work screen's data (client, 2026-11-23: "instead of a pivot, an
attractive widget; one menu; filters in the widget; more fields can be added; print
or export it; more innovative features").

Everything the screen shows comes from one call, ``get_board(options)``, computed on
the same rows the analysis lists (``sale.technician.work``): the totals of the period
and of the period before it, one row per technician (or department, product, clinic...)
with its daily series, the choices each filter offers, and plain-sentence insights.
"""
import io
from collections import defaultdict
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import SQL
from odoo.tools.misc import format_date

from odoo.addons.sale_custom.wizard.order_list_wizard import local_midnight_utc

# what a row can be
DIMENSIONS = [
    ('technician_id', 'Technician'),
    ('department_id', 'Department'),
    ('product_id', 'Product'),
    ('categ_id', 'Product Category'),
    ('partner_id', 'Clinic'),
    ('team_id', 'Sales Route'),
]
# the filters a screen sends: option key -> field
FILTERS = {
    'technician_ids': 'technician_id',
    'department_ids': 'department_id',
    'categ_ids': 'categ_id',
    'team_ids': 'team_id',
    'partner_ids': 'partner_id',
    'product_ids': 'product_id',
}
# what every group carries (see _distinct_sums)
MEASURE_KEYS = ('cases', 'works', 'reworks', 'quantity', 'teeth', 'value',
                'cases_share', 'works_share', 'quantity_share', 'teeth_share', 'share')
# the columns a screen (and its print and export) may show, in order
COLUMNS = [
    ('cases', 'Cases'),
    ('cases_share', 'Cases (Share)'),
    ('works', 'Works'),
    ('works_share', 'Works (Share)'),
    ('reworks', 'Reworks'),
    ('rework_rate', 'Rework %'),
    ('quantity', 'Quantity'),
    ('quantity_share', 'Quantity (Share)'),
    ('teeth', 'Teeth'),
    ('teeth_share', 'Teeth (Share)'),
    ('value', 'Value'),
    ('share', 'Value Share'),
    ('active_days', 'Active Days'),
    ('per_day', 'Works / Active Day'),
    ('works_pct', '% of Works'),
    ('trend', 'vs Previous'),
]
DEFAULT_COLUMNS = ['cases', 'works', 'works_share', 'quantity', 'teeth', 'share', 'trend']
TOP = 25  # rows of a breakdown before "others"


class SaleTechnicianWork(models.Model):
    _inherit = 'sale.technician.work'

    # ------------------------------------------------------------------ options
    @api.model
    def _board_options(self, options):
        """The screen's options, checked and with defaults: the current month by the
        order date, every technician, rows by technician."""
        options = dict(options or {})
        today = fields.Date.context_today(self)
        date_from = fields.Date.to_date(options.get('date_from')) or today.replace(day=1)
        date_to = fields.Date.to_date(options.get('date_to')) or today
        if date_from > date_to:
            raise UserError(_('The start date is after the end date.'))
        if (date_to - date_from).days > 731:
            raise UserError(_('Choose a period of two years at most.'))
        dims = dict(DIMENSIONS)
        group_by = options.get('group_by') if options.get('group_by') in dims else 'technician_id'
        breakdown = options.get('breakdown') if options.get('breakdown') in dims else 'product_id'
        if breakdown == group_by:
            breakdown = 'technician_id' if group_by != 'technician_id' else 'product_id'
        clean = {
            'date_from': date_from,
            'date_to': date_to,
            'date_field': 'delivered_date' if options.get('date_field') == 'delivered_date' else 'date',
            'group_by': group_by,
            'breakdown': breakdown,
            'rework': options.get('rework') if options.get('rework') in ('exclude', 'only') else 'all',
            'cases': options.get('cases') if options.get('cases') in ('shared', 'alone') else 'all',
        }
        for key in FILTERS:
            clean[key] = [int(value) for value in (options.get(key) or []) if value]
        return clean

    @api.model
    def _board_domain(self, opts, date_from=None, date_to=None, skip=()):
        date_from = date_from or opts['date_from']
        date_to = date_to or opts['date_to']
        field = opts['date_field']
        domain = [
            (field, '>=', local_midnight_utc(self, date_from)),
            (field, '<', local_midnight_utc(self, date_to + timedelta(days=1))),
            ('company_id', 'in', self.env.companies.ids),
        ]
        for key, fname in FILTERS.items():
            if opts[key] and key not in skip:
                domain.append((fname, 'in', opts[key]))
        if opts['rework'] == 'exclude':
            domain.append(('is_rework', '=', False))
        elif opts['rework'] == 'only':
            domain.append(('is_rework', '=', True))
        if opts['cases'] == 'shared':
            domain.append(('technician_count', '>', 1))
        elif opts['cases'] == 'alone':
            domain.append(('technician_count', '=', 1))
        return domain

    @api.model
    def _distinct_sums(self, domain, group_field=None, day_field=None):
        """{key: {cases, works, ..., *_share}} of the rows matching ``domain``, by
        ``group_field`` and/or the local day of ``day_field``; the key is the group's
        id (0 for none), the day, ``(id, day)``, or ``()`` with neither.

        A row of the view is a technician on an order line, so a line worked by three
        people is three rows. Here every case and line counts ONCE in its group: the
        cases are the distinct orders, the works the distinct lines, and the quantity,
        teeth and value those of the distinct lines (client, 2026-11-23: "most of the
        lines are shared with multiple technicians - no duplicates"). The share sums
        divide each line between its technicians instead.
        """
        query = self._search(domain)
        if query.is_empty():
            return {}
        table = self._table

        def col(name):
            return SQL.identifier(table, name)

        tz = (self.env.context.get('tz') or self.env.user.tz
              or self.env.company.partner_id.tz or 'Asia/Kolkata')
        group_sql = col(group_field) if group_field else SQL('NULL::int')
        day_sql = SQL("(%s AT TIME ZONE 'UTC' AT TIME ZONE %s)::date", col(day_field), tz) \
            if day_field else SQL('NULL::date')
        rows_sql = query.select(
            SQL('%s AS g', group_sql), SQL('%s AS d', day_sql),
            *(SQL('%s AS %s', col(name), SQL.identifier(name)) for name in (
                'order_id', 'order_line_id', 'is_rework', 'quantity', 'teeth_count', 'amount',
                'work_share', 'case_share', 'quantity_share', 'teeth_share', 'amount_share')))
        self.env.cr.execute(SQL("""
            SELECT g, d,
                   COUNT(DISTINCT order_id), COUNT(*),
                   COUNT(DISTINCT order_id) FILTER (WHERE rework),
                   SUM(quantity), SUM(teeth), SUM(amount),
                   SUM(case_share), SUM(work_share), SUM(quantity_share), SUM(teeth_share), SUM(amount_share)
              FROM (SELECT g, d, order_line_id, MAX(order_id) AS order_id, BOOL_OR(is_rework) AS rework,
                           MAX(quantity) AS quantity, MAX(teeth_count) AS teeth, MAX(amount) AS amount,
                           SUM(case_share) AS case_share, SUM(work_share) AS work_share,
                           SUM(quantity_share) AS quantity_share, SUM(teeth_share) AS teeth_share,
                           SUM(amount_share) AS amount_share
                      FROM (%s) technician_lines
                     GROUP BY g, d, order_line_id) lines
             GROUP BY g, d
        """, rows_sql))
        result = {}
        for g, d, *values in self.env.cr.fetchall():
            sums = dict(zip(MEASURE_KEYS, (float(value or 0) for value in values)))
            for key in ('cases', 'works', 'reworks', 'teeth'):
                sums[key] = int(sums[key])
            if group_field and day_field:
                key = (g or 0, d)
            elif group_field:
                key = g or 0
            elif day_field:
                key = d
            else:
                key = ()
            result[key] = sums
        return result

    @staticmethod
    def _derived(row, total_works):
        """The columns worked out from the sums."""
        row['rework_rate'] = round(100.0 * row['reworks'] / row['cases'], 1) if row['cases'] else 0.0
        row['per_day'] = round(row['works'] / row['active_days'], 1) if row.get('active_days') else 0.0
        # a share of the lab's works: the row's part of each line it shares, so the
        # percentages of the technicians add up to 100
        row['works_pct'] = round(100.0 * row['works_share'] / total_works, 1) if total_works else 0.0
        row['trend'] = row['works'] - row.get('prev_works', 0)
        return row

    @staticmethod
    def _previous_period(date_from, date_to):
        """What the period is compared with: the same days of the month(s) before for
        a period starting on the 1st (this month so far against the same days last
        month, a whole month against the whole month before), else the same number of
        days just before it."""
        if date_from.day == 1:
            months = (date_to.year - date_from.year) * 12 + date_to.month - date_from.month + 1
            prev_from = date_from - relativedelta(months=months)
            if date_to == date_to + relativedelta(day=31):  # the period ends a month
                prev_to = date_from - timedelta(days=1)
            else:
                prev_to = min(date_to - relativedelta(months=months), date_from - timedelta(days=1))
            return prev_from, prev_to
        span = (date_to - date_from).days + 1
        prev_to = date_from - timedelta(days=1)
        return prev_to - timedelta(days=span - 1), prev_to

    @staticmethod
    def _buckets(date_from, date_to):
        """The points of a row's series: days for up to two months, weeks (from
        Monday) for up to a year, months beyond."""
        days = (date_to - date_from).days + 1
        unit = 'day' if days <= 62 else 'week' if days <= 370 else 'month'
        points, current = [], date_from
        if unit == 'week':
            current = date_from - timedelta(days=date_from.weekday())
        elif unit == 'month':
            current = date_from.replace(day=1)
        while current <= date_to:
            points.append(current)
            current += timedelta(days=1) if unit == 'day' else timedelta(days=7) if unit == 'week' \
                else relativedelta(months=1)
        return unit, points

    @staticmethod
    def _bucket_of(day, unit):
        if unit == 'day':
            return day
        if unit == 'week':
            return day - timedelta(days=day.weekday())
        return day.replace(day=1)

    # ------------------------------------------------------------------ one read, counted
    # The screen's figures come from the period's rows read ONCE (and the previous
    # period's once more), counted here in every grouping it needs: twelve queries on
    # the view took three seconds for a month, each of them computing the people of
    # every order. (2026-11-23)
    ROW_FIELDS = ('technician_id', 'order_id', 'order_line_id', 'is_rework', 'quantity', 'teeth_count',
                  'amount', 'case_share', 'work_share', 'quantity_share', 'teeth_share', 'amount_share',
                  'technician_count', 'department_id', 'categ_id', 'team_id', 'partner_id', 'product_id')

    @api.model
    def _board_rows(self, opts, date_from, date_to):
        """[{field: value, 'day': local date}] of the period, with every filter but the
        dimension ones (technicians, departments...), which are applied in Python so the
        filters can still offer what they leave out."""
        base = dict(opts, **{key: [] for key in FILTERS})
        query = self._search(self._board_domain(base, date_from, date_to))
        if query.is_empty():
            return []
        tz = (self.env.context.get('tz') or self.env.user.tz
              or self.env.company.partner_id.tz or 'Asia/Kolkata')
        table = self._table
        self.env.cr.execute(query.select(
            SQL("(%s AT TIME ZONE 'UTC' AT TIME ZONE %s)::date", SQL.identifier(table, opts['date_field']), tz),
            *(SQL.identifier(table, name) for name in self.ROW_FIELDS)))
        names = ('day',) + self.ROW_FIELDS
        return [dict(zip(names, row)) for row in self.env.cr.fetchall()]

    @staticmethod
    def _passes(row, opts, skip=None):
        for key, fname in FILTERS.items():
            if key != skip and opts[key] and row[fname] not in opts['_sets'][key]:
                return False
        return True

    @staticmethod
    def _count(rows, key_of):
        """{key: sums} as _distinct_sums gives them, from rows read once: each case and
        line once in its group, the shares summed."""
        groups = {}
        for row in rows:
            key = key_of(row)
            group = groups.get(key)
            if group is None:
                group = groups[key] = {'orders': set(), 'reworks': set(), 'lines': {},
                                       'cases_share': 0.0, 'works_share': 0.0, 'quantity_share': 0.0,
                                       'teeth_share': 0.0, 'share': 0.0}
            group['orders'].add(row['order_id'])
            if row['is_rework']:
                group['reworks'].add(row['order_id'])
            group['lines'][row['order_line_id']] = (row['quantity'] or 0, row['teeth_count'] or 0, row['amount'] or 0)
            group['cases_share'] += row['case_share'] or 0
            group['works_share'] += row['work_share'] or 0
            group['quantity_share'] += float(row['quantity_share'] or 0)
            group['teeth_share'] += row['teeth_share'] or 0
            group['share'] += float(row['amount_share'] or 0)
        result = {}
        for key, group in groups.items():
            lines = group['lines'].values()
            result[key] = {
                'cases': len(group['orders']),
                'works': len(group['lines']),
                'reworks': len(group['reworks']),
                'quantity': float(sum(line[0] for line in lines)),
                'teeth': int(sum(line[1] for line in lines)),
                'value': float(sum(line[2] for line in lines)),
                'cases_share': round(group['cases_share'], 2),
                'works_share': round(group['works_share'], 2),
                'quantity_share': round(group['quantity_share'], 2),
                'teeth_share': round(group['teeth_share'], 2),
                'share': round(group['share'], 2),
            }
        return result

    # ------------------------------------------------------------------ the board
    @api.model
    def get_board(self, options=None):
        opts = self._board_options(options)
        opts['_sets'] = {key: set(opts[key]) for key in FILTERS}
        span = (opts['date_to'] - opts['date_from']).days + 1
        prev_from, prev_to = self._previous_period(opts['date_from'], opts['date_to'])
        group_by = opts['group_by']

        all_rows = self._board_rows(opts, opts['date_from'], opts['date_to'])
        rows_now = [row for row in all_rows if self._passes(row, opts)]
        rows_prev = [row for row in self._board_rows(opts, prev_from, prev_to) if self._passes(row, opts)]

        zeros = dict.fromkeys(MEASURE_KEYS, 0)
        totals = self._count(rows_now, lambda row: ()).get((), zeros)
        previous = self._count(rows_prev, lambda row: ()).get((), zeros)
        people = len({row['technician_id'] for row in rows_now})
        prev_people = len({row['technician_id'] for row in rows_prev})

        def group_of(row):
            return row[group_by] or 0

        groups = self._count(rows_now, group_of)
        prev_works = {key: sums['works'] for key, sums in self._count(rows_prev, group_of).items()}
        unit, points = self._buckets(opts['date_from'], opts['date_to'])
        index = {point: i for i, point in enumerate(points)}
        series = defaultdict(lambda: [0] * len(points))
        active = defaultdict(int)
        for (key, day), sums in self._count(rows_now, lambda row: (group_of(row), row['day'])).items():
            if not day:
                continue
            series[key][index.get(self._bucket_of(day, unit), 0)] += sums['works']
            active[key] += 1
        daily_total = {day: sums['works'] for day, sums in self._count(rows_now, lambda row: row['day']).items() if day}

        names = self._names(group_by, groups)
        rows = []
        for key, sums in groups.items():
            row = dict(sums, key=key, name=names.get(key) or _('Not set'), active_days=active[key],
                       prev_works=prev_works.get(key, 0), series=series[key])
            rows.append(self._derived(row, totals['works']))
        rows.sort(key=lambda row: (-row['works'], row['name']))
        for rank, row in enumerate(rows, 1):
            row['rank'] = rank

        currency = self.env.company.currency_id
        return {
            'options': {**{k: v for k, v in opts.items() if k != '_sets'},
                        'date_from': fields.Date.to_string(opts['date_from']),
                        'date_to': fields.Date.to_string(opts['date_to'])},
            'period': {
                'label': self._period_label(opts['date_from'], opts['date_to']),
                'previous_label': self._period_label(prev_from, prev_to),
                'days': span,
                'unit': unit,
                'points': [fields.Date.to_string(point) for point in points],
                'point_labels': [self._point_label(point, unit) for point in points],
            },
            'totals': dict(totals, people=people, active_days=len(daily_total)),
            'previous': dict(previous, people=prev_people),
            'rows': rows,
            'dimension': dict(DIMENSIONS)[group_by],
            'choices': self._choices(opts, all_rows),
            'insights': self._insights(opts, rows, totals, daily_total, rows_now),
            'columns': [{'key': key, 'label': self.env._(label)} for key, label in COLUMNS],
            'default_columns': DEFAULT_COLUMNS,
            'dimensions': [{'key': key, 'label': self.env._(label)} for key, label in DIMENSIONS],
            'currency': {'symbol': currency.symbol or '', 'position': currency.position,
                         'digits': currency.decimal_places},
        }

    @api.model
    def _names(self, group_by, groups):
        comodel = self._fields[group_by].comodel_name
        records = self.env[comodel].sudo().browse([key for key in groups if key]).exists()
        return {record.id: record.display_name for record in records}

    @api.model
    def _choices(self, opts, all_rows):
        """What each filter offers: the values with work in the period, whatever that
        filter itself is set to, the busiest first."""
        choices = {}
        for key, fname in FILTERS.items():
            if key == 'product_ids':
                continue
            groups = self._count([row for row in all_rows if self._passes(row, opts, skip=key)],
                                 lambda row, fname=fname: row[fname] or 0)
            names = self._names(fname, groups)
            ranked = sorted(((sums['works'], key_) for key_, sums in groups.items() if key_), reverse=True)[:300]
            choices[key] = [{'id': key_, 'name': names.get(key_) or '', 'works': works} for works, key_ in ranked]
        return choices

    def _period_label(self, date_from, date_to):
        if date_from == date_to:
            return format_date(self.env, date_from, date_format='d MMM y')
        if date_from.day == 1 and date_to == date_from + relativedelta(months=1, days=-1):
            return format_date(self.env, date_from, date_format='MMMM y')
        return '%s – %s' % (format_date(self.env, date_from, date_format='d MMM y'),
                            format_date(self.env, date_to, date_format='d MMM y'))

    def _point_label(self, point, unit):
        if unit == 'month':
            return format_date(self.env, point, date_format='MMM y')
        if unit == 'week':
            return _('Week of %s', format_date(self.env, point, date_format='d MMM'))
        return format_date(self.env, point, date_format='EEE d MMM')

    def _insights(self, opts, rows, totals, daily_total, rows_now=()):
        """A few sentences worth reading before the table."""
        out = []
        rows = [row for row in rows if row['key']]  # "Not set" is nobody to name
        if not rows:
            return out
        top = rows[0]
        if totals['works'] and top['works']:
            out.append({'icon': 'fa-trophy', 'tone': 'main', 'text': _(
                '%(name)s did the most: %(works)s works, %(pct)s%% of all.',
                name=top['name'], works=top['works'], pct=top['works_pct'])})
        risers = [row for row in rows if row['trend'] > 0 and row['prev_works']]
        if risers:
            riser = max(risers, key=lambda row: row['trend'])
            out.append({'icon': 'fa-line-chart', 'tone': 'ok', 'text': _(
                '%(name)s rose the most: +%(delta)s works on the previous period.',
                name=riser['name'], delta=riser['trend'])})
        fallers = [row for row in rows if row['trend'] < 0]
        if fallers:
            faller = min(fallers, key=lambda row: row['trend'])
            out.append({'icon': 'fa-arrow-down', 'tone': 'warn', 'text': _(
                '%(name)s fell the most: %(delta)s works on the previous period.',
                name=faller['name'], delta=faller['trend'])})
        reworked = [row for row in rows if row['cases'] >= 5 and row['reworks']]
        if reworked:
            worst = max(reworked, key=lambda row: row['rework_rate'])
            out.append({'icon': 'fa-repeat', 'tone': 'bad', 'text': _(
                '%(name)s has the highest rework rate: %(rate)s%% of %(cases)s cases.',
                name=worst['name'], rate=worst['rework_rate'], cases=worst['cases'])})
        if daily_total:
            day, works = max(daily_total.items(), key=lambda item: item[1])
            out.append({'icon': 'fa-calendar-check-o', 'tone': 'main', 'text': _(
                'Busiest day: %(day)s, with %(works)s works.',
                day=format_date(self.env, day, date_format='EEEE d MMM'), works=works)})
        if opts['group_by'] == 'technician_id' and opts['cases'] == 'all' and totals['cases']:
            shared = self._count([row for row in rows_now if row['technician_count'] > 1], lambda row: ()).get((), {})
            if shared.get('cases'):
                out.append({'icon': 'fa-users', 'tone': 'main', 'text': _(
                    '%(pct)s%% of the cases were shared between technicians.',
                    pct=round(100.0 * shared['cases'] / totals['cases']))})
        return out

    # ------------------------------------------------------------------ one row, opened
    @api.model
    def get_breakdown(self, options, key, dimension=None):
        """A row of the board, by another dimension: the technician's products,
        departments, clinics... the busiest first, the rest as "others"."""
        opts = self._board_options(dict(options or {}, breakdown=dimension or (options or {}).get('breakdown')))
        dimension = opts['breakdown']
        domain = self._board_domain(opts) + [(opts['group_by'], '=', key or False)]
        groups = self._distinct_sums(domain, dimension)
        names = self._names(dimension, groups)
        total = sum(sums['works'] for sums in groups.values())
        rows = sorted(({**sums, 'key': key_, 'name': names.get(key_) or _('Not set')}
                       for key_, sums in groups.items()), key=lambda row: -row['works'])
        if len(rows) > TOP:
            rest = rows[TOP:]
            rows = rows[:TOP] + [{key_: sum(row[key_] for row in rest) for key_ in MEASURE_KEYS}
                                 | {'key': None, 'name': _('%s others', len(rest))}]
        for row in rows:
            row['works_pct'] = round(100.0 * row['works'] / total, 1) if total else 0.0
        return {'dimension': dict(DIMENSIONS)[dimension], 'rows': rows}

    @api.model
    def action_open_lines(self, options, key=None):
        """The order lines behind a figure: the whole board, or one of its rows."""
        opts = self._board_options(options)
        domain = self._board_domain(opts)
        name = _('Technician Work')
        if key is not None:
            domain.append((opts['group_by'], '=', key or False))
            names = self._names(opts['group_by'], {key: None})
            name = _('Work - %s', names.get(key) or _('Not set'))
        return {
            'type': 'ir.actions.act_window', 'name': name, 'res_model': 'sale.technician.work',
            'views': [(self.env.ref('lab_technician_work.view_technician_work_list').id, 'list')],
            'view_mode': 'list', 'domain': domain, 'target': 'current',
            'context': {'create': False},
        }

    # ------------------------------------------------------------------ print, export
    @api.model
    def action_print(self, options, columns=None, with_breakdown=False):
        opts = self._board_options(options)
        summary = self.env['sale.technician.work.summary'].create({
            'date_from': opts['date_from'], 'date_to': opts['date_to'],
            'options': {**opts, 'date_from': fields.Date.to_string(opts['date_from']),
                        'date_to': fields.Date.to_string(opts['date_to'])},
            'columns': [key for key in (columns or DEFAULT_COLUMNS) if key in dict(COLUMNS)],
            'with_breakdown': bool(with_breakdown),
        })
        return self.env.ref('lab_technician_work.action_report_technician_work').report_action(summary)

    @api.model
    def action_export(self, options, columns=None):
        """An Excel workbook of the board: the summary as on the screen, and every
        order line behind it."""
        import xlsxwriter  # noqa: PLC0415 - Odoo's own export uses it; loaded on demand

        board = self.get_board(options)
        opts = self._board_options(options)
        columns = [key for key in (columns or DEFAULT_COLUMNS) if key in dict(COLUMNS)]
        labels = dict(COLUMNS)
        stream = io.BytesIO()
        book = xlsxwriter.Workbook(stream, {'in_memory': True})
        bold = book.add_format({'bold': True})
        head = book.add_format({'bold': True, 'bg_color': '#E9F2F3', 'border': 1})
        title = book.add_format({'bold': True, 'font_size': 14})
        money = book.add_format({'num_format': '#,##0.00'})
        number = book.add_format({'num_format': '#,##0.##'})
        when = book.add_format({'num_format': 'dd-mm-yyyy hh:mm'})

        sheet = book.add_worksheet(_('Summary'))
        sheet.write(0, 0, _('Technician Work - %s', board['period']['label']), title)
        sheet.write(1, 0, _('Counted by: %s', _('delivered date') if opts['date_field'] == 'delivered_date'
                                              else _('order date')))
        sheet.write(3, 0, board['dimension'], head)
        for col, key in enumerate(columns, 1):
            sheet.write(3, col, self.env._(labels[key]), head)
        for line, row in enumerate(board['rows'], 4):
            sheet.write(line, 0, row['name'])
            for col, key in enumerate(columns, 1):
                sheet.write_number(line, col, row[key] or 0,
                                   money if key in ('value', 'share') else number)
        total_line = 4 + len(board['rows'])
        sheet.write(total_line, 0, _('Total'), bold)
        for col, key in enumerate(columns, 1):
            if key in board['totals']:
                sheet.write_number(total_line, col, board['totals'][key] or 0, bold)
        sheet.set_column(0, 0, 32)
        sheet.set_column(1, len(columns), 14)
        sheet.freeze_panes(4, 1)

        lines = book.add_worksheet(_('Order Lines'))
        heads = [_('Technician'), _('Order Date'), _('Delivered On'), _('Order'), _('Clinic'), _('Product'),
                 _('Department'), _('Teeth'), _('Teeth Count'), _('Quantity'), _('Value'), _('Value Share'),
                 _('Technicians on the Case'), _('Rework')]
        for col, text in enumerate(heads):
            lines.write(0, col, text, head)
        records = self.search_fetch(self._board_domain(opts), [
            'technician_id', 'date', 'delivered_date', 'order_id', 'partner_id', 'product_id', 'department_id',
            'teeth', 'teeth_count', 'quantity', 'amount', 'amount_share', 'technician_count', 'is_rework',
        ], order='technician_id, date', limit=200000)
        tz_dates = lambda value: fields.Datetime.context_timestamp(self, value).replace(tzinfo=None) if value else None  # noqa: E731
        for line, record in enumerate(records, 1):
            lines.write(line, 0, record.technician_id.display_name or '')
            for col, value in ((1, tz_dates(record.date)), (2, tz_dates(record.delivered_date))):
                if value:
                    lines.write_datetime(line, col, value, when)
            lines.write(line, 3, record.order_id.name or '')
            lines.write(line, 4, record.partner_id.display_name or '')
            lines.write(line, 5, record.product_id.display_name or '')
            lines.write(line, 6, record.department_id.display_name or '')
            lines.write(line, 7, record.teeth or '')
            lines.write_number(line, 8, record.teeth_count)
            lines.write_number(line, 9, record.quantity, number)
            lines.write_number(line, 10, record.amount, money)
            lines.write_number(line, 11, record.amount_share, money)
            lines.write_number(line, 12, record.technician_count)
            lines.write(line, 13, _('Yes') if record.is_rework else '')
        lines.set_column(0, 13, 16)
        lines.freeze_panes(1, 0)
        book.close()

        filename = 'Technician Work %s.xlsx' % board['period']['label'].replace('/', '-')
        Attachment = self.env['ir.attachment'].sudo()
        Attachment.search([('res_model', '=', 'sale.technician.work.summary'), ('res_id', '=', 0),
                           ('create_uid', '=', self.env.uid)]).unlink()
        attachment = Attachment.create({
            'name': filename, 'raw': stream.getvalue(), 'res_model': 'sale.technician.work.summary',
            'res_id': 0, 'public': False,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })
        return {'type': 'ir.actions.act_url', 'target': 'download',
                'url': '/web/content/%s?download=true' % attachment.id}
