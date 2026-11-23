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
from datetime import date, datetime, timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
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
# summed measures: field -> key on the screen
MEASURES = {
    'case_count': 'cases',
    'work_count': 'works',
    'rework_count': 'reworks',
    'quantity': 'quantity',
    'teeth_count': 'teeth',
    'amount': 'value',
    'amount_share': 'share',
}
# the columns a screen (and its print and export) may show, in order
COLUMNS = [
    ('cases', 'Cases'),
    ('works', 'Works'),
    ('reworks', 'Reworks'),
    ('rework_rate', 'Rework %'),
    ('quantity', 'Quantity'),
    ('teeth', 'Teeth'),
    ('value', 'Value'),
    ('share', 'Value Share'),
    ('active_days', 'Active Days'),
    ('per_day', 'Works / Active Day'),
    ('works_pct', '% of Works'),
    ('trend', 'vs Previous'),
]
DEFAULT_COLUMNS = ['cases', 'works', 'reworks', 'quantity', 'teeth', 'share', 'trend']
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
    def _sums(self, domain, groupby=()):
        """{group key: {cases, works, ...}} - the key is the group's id (0 for none),
        or () without a grouping."""
        aggregates = ['%s:sum' % fname for fname in MEASURES]
        result = {}
        for *keys, values in [(*row[:len(groupby)], row[len(groupby):])
                              for row in self._read_group(domain, list(groupby), aggregates)]:
            sums = {MEASURES[fname]: value or 0 for fname, value in zip(MEASURES, values)}
            key = tuple(self._key(value) for value in keys)
            result[key[0] if len(key) == 1 else key] = sums
        return result

    @staticmethod
    def _key(value):
        if isinstance(value, models.BaseModel):
            return value.id or 0
        if isinstance(value, datetime):
            return value.date()
        return value

    @staticmethod
    def _derived(row, total_works):
        """The columns worked out from the sums."""
        row['rework_rate'] = round(100.0 * row['reworks'] / row['cases'], 1) if row['cases'] else 0.0
        row['per_day'] = round(row['works'] / row['active_days'], 1) if row.get('active_days') else 0.0
        row['works_pct'] = round(100.0 * row['works'] / total_works, 1) if total_works else 0.0
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

    # ------------------------------------------------------------------ the board
    @api.model
    def get_board(self, options=None):
        opts = self._board_options(options)
        domain = self._board_domain(opts)
        span = (opts['date_to'] - opts['date_from']).days + 1
        prev_from, prev_to = self._previous_period(opts['date_from'], opts['date_to'])
        prev_domain = self._board_domain(opts, prev_from, prev_to)
        group_by, field = opts['group_by'], opts['date_field']

        totals = self._sums(domain).get((), dict.fromkeys(MEASURES.values(), 0))
        previous = self._sums(prev_domain).get((), dict.fromkeys(MEASURES.values(), 0))
        people = len(self._read_group(domain, ['technician_id'], ['__count']))
        prev_people = len(self._read_group(prev_domain, ['technician_id'], ['__count']))

        groups = self._sums(domain, [group_by])
        prev_works = {key: sums['works'] for key, sums in self._sums(prev_domain, [group_by]).items()}
        unit, points = self._buckets(opts['date_from'], opts['date_to'])
        index = {point: i for i, point in enumerate(points)}
        series = defaultdict(lambda: [0] * len(points))
        active = defaultdict(int)
        daily_total = defaultdict(int)
        for (key, day), sums in self._sums(domain, [group_by, '%s:day' % field]).items():
            if not day:
                continue
            series[key][index.get(self._bucket_of(day, unit), 0)] += sums['works']
            active[key] += 1
            daily_total[day] += sums['works']

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
            'options': {**opts, 'date_from': fields.Date.to_string(opts['date_from']),
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
            'choices': self._choices(opts),
            'insights': self._insights(opts, rows, totals, daily_total),
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
    def _choices(self, opts):
        """What each filter offers: the values with work in the period, whatever that
        filter itself is set to, the busiest first."""
        choices = {}
        for key, fname in FILTERS.items():
            if key == 'product_ids':
                continue
            domain = self._board_domain(opts, skip=(key,))
            rows = self._read_group(domain, [fname], ['work_count:sum'], order='work_count:sum desc', limit=300)
            choices[key] = [{'id': record.id, 'name': record.display_name, 'works': works}
                            for record, works in rows if record]
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

    def _insights(self, opts, rows, totals, daily_total):
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
            shared = self._sums(self._board_domain(opts) + [('technician_count', '>', 1)]).get((), {})
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
        groups = self._sums(domain, [dimension])
        names = self._names(dimension, groups)
        total = sum(sums['works'] for sums in groups.values())
        rows = sorted(({**sums, 'key': key_, 'name': names.get(key_) or _('Not set')}
                       for key_, sums in groups.items()), key=lambda row: -row['works'])
        if len(rows) > TOP:
            rest = rows[TOP:]
            rows = rows[:TOP] + [{key_: sum(row[key_] for row in rest) for key_ in MEASURES.values()}
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
