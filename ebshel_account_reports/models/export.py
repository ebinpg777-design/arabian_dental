# -*- coding: utf-8 -*-
"""The report leaving the screen: a workbook, a PDF, rows for a CSV.

One reading (`_export_lines`) feeds every way out, so the paper can never say
something the workbook does not. What goes in is the reader's choice, not the
format's: the report as it stands on the screen, its summary, or everything
unfolded. A workbook is built to be worked in - real dates and numbers, the
parts of an entry in columns of their own, rows that fold the way the screen
does; a PDF is built to be read - its own slim heading instead of the letter
paper, tables cut in slices the renderer can carry, and an honest line where
a ledger was too long for paper."""
import base64
import io
import math

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import split_every
from odoo.tools.misc import format_datetime

SCOPES = ('screen', 'summary', 'full')
ORIENTATIONS = ('auto', 'portrait', 'landscape')
PDF_LINES = 4000            # beyond this a PDF is cut, and says so
PDF_SLICE = 250             # rows per table: the renderer slows down sharply on long tables
ITEMS_CAP = 50000           # journal items in one workbook
# the parts of an entry, in the order they are laid out
DETAILS = (('date', 'Date'), ('move', 'Entry'), ('journal', 'Journal'), ('partner', 'Partner'),
           ('label', 'Label'), ('account', 'Account'), ('due', 'Due'), ('matching', 'Match'))
NAVY, INK, MUTED, RULE = '#1F3A5F', '#1F2937', '#6B7280', '#E6EAF0'
GOOD, BAD = '#067647', '#B42318'
MONEY = '#,##0.00;[Red]-#,##0.00;"-"'     # nothing is a dash, the way a ledger is ruled


class FinReportExport(models.Model):
    _inherit = 'ebshel.fin.report'

    # ------------------------------------------------------------------ what goes in
    @api.model
    def _export_layout(self, layout):
        layout = dict(layout or {})
        return {
            'scope': layout.get('scope') if layout.get('scope') in SCOPES else 'full',
            'orientation': layout.get('orientation') if layout.get('orientation') in ORIENTATIONS else 'auto',
            'filters': bool(layout.get('filters', True)),
            'notes': bool(layout.get('notes', True)),
        }

    def _export_lines(self, options, layout=None):
        """(engine, options, columns, lines, layout): the one reading every export is made from."""
        self.ensure_one()
        layout = self._export_layout(layout)
        given = dict(options or {})
        if layout['scope'] == 'summary':
            given.update(unfold_all=False, expanded=[])
        elif layout['scope'] == 'full':
            given['unfold_all'] = True
        engine, handler, options, columns = self._prepare(given)
        lines = [l for l in handler.lines(self, options, columns, for_export=True) if l.get('kind') != 'more']
        return engine, options, columns, self._polish(lines), layout

    @api.model
    def _is_empty(self, lines):
        """A report whose only line is its total of nothing has nothing to say."""
        return not any(l.get('kind') != 'total' for l in lines)

    @api.model
    def _pdf_cap(self):
        try:
            cap = int(self.env['ir.config_parameter'].sudo().get_param('ebshel_account_reports.pdf_max_lines') or PDF_LINES)
        except ValueError:
            cap = PDF_LINES
        return max(200, cap)

    @api.model
    def _wants_landscape(self, layout, columns, lines):
        if layout['orientation'] != 'auto':
            return layout['orientation'] == 'landscape'
        return len(columns) > 4 or any(l.get('parts') for l in lines)

    def get_export_preview(self, options, layout=None):
        """How much the chosen export holds, before it is made: lines, the pages they
        come to, and whether the paper would have to be cut."""
        self.ensure_one()
        self.env.flush_all()
        self._check_report_access()
        engine, options, columns, lines, layout = self._export_lines(options, layout)
        cap = self._pdf_cap()
        landscape = self._wants_landscape(layout, columns, lines)
        per_page = 33 if landscape else 48
        return {
            'scope': layout['scope'], 'lines': len(lines), 'columns': len(columns),
            'details': sum(1 for l in lines if l.get('parts')),
            'landscape': landscape, 'pages': max(1, math.ceil(min(len(lines), cap) / per_page)),
            'cut': len(lines) > cap, 'cap': cap, 'empty': self._is_empty(lines),
        }

    def _filters_in_words(self, options):
        """Every filter that shaped the figures, by name - what a reader of the export
        needs to trust it without the screen."""
        self.ensure_one()
        engine = self.env['ebshel.fin.engine']
        choices = self._choices(options)
        single = self.date_mode == 'single'

        def named(key, limit=6):
            table = {}
            for item in choices.get(key) or []:
                if isinstance(item, (list, tuple)):
                    table[item[0]] = item[1]
                else:
                    table[item['id']] = item['name']
            ids = list(options.get(key) or [])
            text = ', '.join(str(table.get(i, '#%s' % i)) for i in ids[:limit])
            return text + (_(' and %s more', len(ids) - limit) if len(ids) > limit else '')

        out = [(_('Period'), engine.period_label(fields.Date.to_date(options['date']['from']),
                                                 fields.Date.to_date(options['date']['to']), single))]
        comparison = options.get('comparison') or {}
        if comparison.get('mode') and comparison['mode'] != 'none':
            out.append((_('Compared with'), dict(choices['comparison_modes']).get(comparison['mode'], comparison['mode'])))
        if len(options['companies']) > 1:
            out.append((_('Companies'), ', '.join(self.env['res.company'].sudo().browse(options['companies']).mapped('name'))))
        for key, label in (('journals', _('Journals')), ('journal_types', _('Journal type')), ('partners', _('Partners')),
                           ('partner_categories', _('Partner tag')), ('salespeople', _('Salesperson')),
                           ('teams', _('Sales team')), ('product_categories', _('Product category')),
                           ('analytic', _('Analytic'))):
            if options.get(key):
                out.append((label, named(key)))
        if options.get('accounts_query'):
            out.append((_('Accounts'), options['accounts_query']))
        if options.get('label'):
            out.append((_('Label contains'), options['label']))
        if options.get('amount_min') is not None or options.get('amount_max') is not None:
            out.append((_('Amount of each item'), _('%(a)s to %(b)s', a=options.get('amount_min') or 0,
                                                   b=options['amount_max'] if options.get('amount_max') is not None else _('no limit'))))
        if options.get('unreconciled'):
            out.append((_('Items'), _('unreconciled only')))
        out.append((_('Entries'), _('posted') if options.get('posted_only', True) else _('posted and draft')))
        if (options.get('unit') or 1) != 1:
            out.append((_('Figures in'), engine.unit_label(options)))
        return out

    @api.model
    def _excel_date_format(self):
        """The reader's date format, written the way a spreadsheet writes it."""
        lang = self.env['res.lang']._get_data(code=self.env.lang or self.env.user.lang or 'en_US')
        pattern = (lang and lang.date_format) or '%m/%d/%Y'
        for strf, excel in (('%d', 'dd'), ('%m', 'mm'), ('%Y', 'yyyy'), ('%y', 'yy'), ('%b', 'mmm'), ('%B', 'mmmm'),
                            ('%a', 'ddd'), ('%A', 'dddd'), ('%e', 'd'), ('%-d', 'd'), ('%-m', 'm')):
            pattern = pattern.replace(strf, excel)
        return pattern if '%' not in pattern else 'yyyy-mm-dd'

    def _export_stamp(self):
        return _('%(when)s by %(who)s', when=format_datetime(self.env, fields.Datetime.now(), dt_format='short'),
                 who=self.env.user.name)

    # ------------------------------------------------------------------ rows, for a CSV or the clipboard
    def _detail_keys(self, lines):
        return [key for key, _label in DETAILS if any((l.get('parts') or {}).get(key) for l in lines)]

    def export_rows(self, options, layout=None):
        """The export as plain rows: what a CSV and the clipboard carry."""
        self.ensure_one()
        self.env.flush_all()
        self._check_report_access()
        engine, options, columns, lines, layout = self._export_lines(options, layout)
        unit = options.get('unit') or 1
        keys = self._detail_keys(lines)
        labels = dict(DETAILS)
        names = {l['id']: l['name'] for l in lines}
        rows = [[_('Line')] + [_(labels[k]) for k in keys] + [c['label'] for c in columns]]
        for line in lines:
            parts = line.get('parts') or {}
            name = names.get(line.get('parent_id'), '') if parts else line['name']
            row = [name] + [parts.get(k + '_text') or parts.get(k) or '' for k in keys]
            for cell in line['columns']:
                value = cell.get('value')
                if value is None:
                    row.append(cell.get('text') or '')
                elif cell.get('display') == 'amount':
                    row.append(round(value / unit, 2))
                else:
                    row.append(value)
            rows.append(row)
        return {'rows': rows, 'filename': '%s - %s' % (self.name, options['date']['to']),
                'levels': [0] + [l.get('level') or 0 for l in lines]}

    # ------------------------------------------------------------------ the workbook
    def _workbook(self):
        try:
            import xlsxwriter
        except ImportError:                                           # pragma: no cover
            raise UserError(_("The xlsxwriter library is not installed on the server."))
        output = io.BytesIO()
        book = xlsxwriter.Workbook(output, {'in_memory': True, 'remove_timezone': True})
        cache = {}

        def fmt(**spec):
            spec.setdefault('font_name', 'Calibri')
            spec.setdefault('font_size', 10)
            spec.setdefault('valign', 'vcenter')
            key = tuple(sorted(spec.items()))
            if key not in cache:
                cache[key] = book.add_format(spec)
            return cache[key]
        return output, book, fmt

    def _sheet_heading(self, book, sheet, fmt, title, company, facts, last_col):
        """Company, title and the filters in words; returns the first free row."""
        sheet.set_row(0, 16)
        sheet.write(0, 0, company.name or '', fmt(bold=True, font_color=MUTED, font_size=10))
        sheet.set_row(1, 26)
        sheet.write(1, 0, title, fmt(bold=True, font_size=18, font_color=NAVY))
        row = 2
        for label, value in facts:
            sheet.write_rich_string(row, 0, fmt(bold=True, font_color=MUTED, font_size=9), '%s: ' % label,
                                    fmt(font_color=INK, font_size=9), str(value) or ' ',
                                    fmt(font_size=9, text_wrap=False))
            row += 1
        sheet.write(row, 0, _('Exported %s', self._export_stamp()), fmt(italic=True, font_color=MUTED, font_size=8))
        self._sheet_logo(sheet, company, last_col)
        return row + 2

    def _sheet_logo(self, sheet, company, col):
        logo = company.sudo().logo
        if not logo:
            return
        try:
            from PIL import Image
            raw = base64.b64decode(logo)
            width, height = Image.open(io.BytesIO(raw)).size
            scale = 44.0 / max(height, 1)
            if width * scale > 150:
                scale = 150.0 / width
            sheet.insert_image(0, max(col, 1), 'logo.png', {'image_data': io.BytesIO(raw), 'x_scale': scale, 'y_scale': scale,
                                                            'x_offset': 4, 'y_offset': 2, 'object_position': 3})
        except Exception:                                             # a logo that cannot be read is left out
            return

    def export_xlsx(self, options, layout=None):
        """The report as a workbook made to be worked in."""
        self.ensure_one()
        self.env.flush_all()                      # the SQL must see pending ORM writes (parent_state, balance)
        self._check_report_access()
        output, book, fmt = self._workbook()
        engine, options, columns, lines, layout = self._export_lines(options, layout)
        unit = options.get('unit') or 1
        company = self.env['res.company'].browse(options['companies'][0])
        notes = self.env['ebshel.fin.report.annotation'].for_report(self) if layout['notes'] else {}
        keys = self._detail_keys(lines)
        labels = dict(DETAILS)
        first_value = 1 + len(keys)
        last_col = first_value + len(columns) - 1
        names = {l['id']: l['name'] for l in lines}
        day_format = self._excel_date_format()
        book.set_properties({'title': self.name, 'author': self.env.user.name, 'company': company.name or '',
                             'comments': self._options_summary(options)})
        sheet = book.add_worksheet(self.name[:31])
        sheet.hide_gridlines(2)
        sheet.outline_settings(True, False, False, False)      # a group's heading sits above its rows, as on the screen

        facts = self._filters_in_words(options) if layout['filters'] else self._filters_in_words(options)[:1]
        row = self._sheet_heading(book, sheet, fmt, self.name, company, facts, last_col)
        head_row = row
        head = dict(bold=True, bg_color=NAVY, font_color='#FFFFFF', border=1, border_color=NAVY, text_wrap=True, font_size=9)
        sheet.set_row(head_row, 30)
        sheet.write(head_row, 0, _('Line'), fmt(align='left', **head))
        widths = {0: len(_('Line'))}
        for i, key in enumerate(keys, start=1):
            sheet.write(head_row, i, _(labels[key]), fmt(align='left', **head))
            widths[i] = len(labels[key])
        for i, col in enumerate(columns, start=first_value):
            sheet.write(head_row, i, col['label'], fmt(align='right', **head))
            widths[i] = min(len(col['label']), 24)

        def wide(col, text, extra=0):
            widths[col] = max(widths.get(col, 0), len(str(text or '')) + extra)

        def look(line):
            kind = line.get('kind') or 'line'
            spec = {'bottom': 1, 'bottom_color': RULE, 'font_color': INK}
            if line.get('bold') or kind in ('header', 'total'):
                spec['bold'] = True
            if kind == 'header':
                spec.update(bg_color='#EEF3FA', font_color=NAVY)
            elif kind == 'total':
                spec.update(top=2, top_color=NAVY, bg_color='#F7F9FC')
            elif kind in ('move_line', 'open_item'):
                spec.update(font_color='#374151', font_size=9)
            elif kind == 'initial':
                spec.update(italic=True, font_color=MUTED)
            return spec

        for line in lines:
            row += 1
            spec = look(line)
            level = min(line.get('level') or 0, 7)
            parts = line.get('parts') or {}
            if level:
                sheet.set_row(row, None, None, {'level': level})
            if parts:
                # the heading it belongs to, so a filter on the first column keeps an account's entries together
                name = names.get(line.get('parent_id'), '')
                sheet.write(row, 0, name, fmt(indent=level, **dict(spec, font_color=MUTED)))
            else:
                name = line['name']
                sheet.write(row, 0, name, fmt(indent=level, **spec))
            wide(0, name, level * 2)
            if notes.get(line['id']):
                sheet.write_comment(row, 0, '\n'.join('%s - %s' % (n['user'], n['text']) for n in notes[line['id']]),
                                    {'x_scale': 2, 'y_scale': 1.5})
            for i, key in enumerate(keys, start=1):
                value = parts.get(key) or ''
                if key in ('date', 'due') and value:
                    sheet.write_datetime(row, i, fields.Date.to_date(value), fmt(num_format=day_format, align='left', **spec))
                    wide(i, '00/00/0000')
                else:
                    sheet.write(row, i, value, fmt(align='left', **spec))
                    wide(i, value)
            for i, (col, cell) in enumerate(zip(columns, line['columns']), start=first_value):
                value = cell.get('value')
                kind = 'growth' if col['type'] == 'growth' else (cell.get('display') or 'amount')
                text = cell.get('text') or ''
                if value is None or kind in ('check', 'text'):
                    sheet.write(row, i, text, fmt(align='right', **spec))
                elif kind == 'amount':
                    sheet.write_number(row, i, value / unit, fmt(num_format=MONEY, align='right', **spec))
                elif kind == 'growth':
                    colour = GOOD if cell.get('class') == 'up' else BAD if cell.get('class') == 'down' else INK
                    if abs(value) > 999.9:
                        sheet.write(row, i, text, fmt(align='right', **dict(spec, font_color=colour)))
                    else:
                        sheet.write_number(row, i, value / 100.0,
                                           fmt(num_format='+0.0%;-0.0%;0.0%', align='right', **dict(spec, font_color=colour)))
                elif kind == 'percent':
                    sheet.write_number(row, i, value / 100.0, fmt(num_format='0.0%', align='right', **spec))
                elif kind == 'days':
                    sheet.write_number(row, i, value, fmt(num_format='0 "%s"' % _('days'), align='right', **spec))
                elif kind == 'count':
                    sheet.write_number(row, i, value, fmt(num_format='#,##0', align='right', **spec))
                else:
                    sheet.write_number(row, i, value, fmt(num_format='0.00', align='right', **spec))
                wide(i, text, 2)
        last_row = row
        if self._is_empty(lines):
            row += 1
            sheet.write(row, 0, _('Nothing in this period with these filters.'), fmt(italic=True, font_color=MUTED))

        # the sheet, ready to be read and to be printed
        sheet.set_column(0, 0, max(30, min(widths.get(0, 30) + 2, 62)))
        for i, key in enumerate(keys, start=1):
            sheet.set_column(i, i, max(9, min(widths.get(i, 10) + 2, 46 if key == 'label' else 34)))
        for i in range(first_value, last_col + 1):
            sheet.set_column(i, i, max(14, min(widths.get(i, 14) + 2, 26)))
        sheet.freeze_panes(head_row + 1, 1)
        if keys and lines:
            sheet.autofilter(head_row, 0, last_row, last_col)
        if self._wants_landscape(layout, columns, lines):
            sheet.set_landscape()
        sheet.set_paper(9)
        sheet.fit_to_pages(1, 0)
        sheet.repeat_rows(head_row)
        sheet.set_margins(left=0.4, right=0.4, top=0.5, bottom=0.6)
        sheet.set_footer('&L&8%s · %s&R&8%s &P / &N' % (self.name, facts[0][1], _('Page')))

        flat = [dict(n, line_key=key) for key, items in notes.items() for n in items]
        if flat:
            page = book.add_worksheet(_('Notes')[:31])
            page.hide_gridlines(2)
            for i, label in enumerate((_('Line'), _('Note'), _('By'), _('On'))):
                page.write(0, i, label, fmt(align='left', **head))
            for r, note in enumerate(flat, start=1):
                page.write(r, 0, note.get('line_name') or note['line_key'], fmt(bold=True, font_color=INK))
                page.write(r, 1, note['text'], fmt(text_wrap=True, font_color=INK))
                page.write(r, 2, note['user'], fmt(font_color=MUTED))
                page.write(r, 3, str(note['date']), fmt(font_color=MUTED))
            page.set_column(0, 0, 36)
            page.set_column(1, 1, 80)
            page.set_column(2, 3, 20)
        book.close()
        return {
            'filename': '%s - %s.xlsx' % (self.name, options['date']['to']),
            'content': base64.b64encode(output.getvalue()).decode(),
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'lines': len(lines),
        }

    # ------------------------------------------------------------------ the journal items of one figure
    def export_items_xlsx(self, options, line_id, column_key='p0', search='', order='date desc'):
        """The journal items behind one figure as a flat, filterable sheet. Its totals are
        SUBTOTALs: filter the sheet and they follow."""
        self.ensure_one()
        self.env.flush_all()
        self._check_report_access()
        engine, options, domain, title, other_model = self._items_domain(options, line_id, column_key, search)
        if domain is None:
            raise UserError(_("There are no journal items behind this figure."))
        AML = self.env['account.move.line']
        total = AML.search_count(domain)
        ids = AML.search(domain, order=self.ITEM_ORDERS.get(order, self.ITEM_ORDERS['date desc']), limit=ITEMS_CAP).ids
        company = self.env['res.company'].browse(options['companies'][0])
        output, book, fmt = self._workbook()
        book.set_properties({'title': title or self.name, 'author': self.env.user.name, 'company': company.name or ''})
        sheet = book.add_worksheet(_('Journal items')[:31])
        sheet.hide_gridlines(2)
        heads = [(_('Date'), 12, 'left'), (_('Journal'), 9, 'left'), (_('Entry'), 22, 'left'), (_('Account'), 34, 'left'),
                 (_('Partner'), 34, 'left'), (_('Label'), 46, 'left'), (_('Reference'), 22, 'left'),
                 (_('Debit'), 16, 'right'), (_('Credit'), 16, 'right'), (_('Balance'), 16, 'right'),
                 (_('Match'), 10, 'left'), (_('Analytic'), 24, 'left'), (_('Due'), 12, 'left'), (_('Status'), 10, 'left')]
        facts = [(_('Report'), self.name)] + self._filters_in_words(options)
        if search:
            facts.append((_('Search'), search))
        facts.append((_('Items'), _('%(shown)s of %(total)s', shown=len(ids), total=total) if total > len(ids) else str(total)))
        row = self._sheet_heading(book, sheet, fmt, title or self.name, company, facts, len(heads) - 1)
        total_row, head_row = row, row + 1
        first, last = head_row + 1, head_row + max(len(ids), 1)
        head = dict(bold=True, bg_color=NAVY, font_color='#FFFFFF', border=1, border_color=NAVY, font_size=9)
        for i, (label, width, align) in enumerate(heads):
            sheet.write(head_row, i, label, fmt(align=align, **head))
            sheet.set_column(i, i, width)
        cell = {'bottom': 1, 'bottom_color': RULE, 'font_color': INK, 'font_size': 9}
        text, money = fmt(align='left', **cell), fmt(align='right', num_format=MONEY, **cell)
        day = fmt(align='left', num_format=self._excel_date_format(), **cell)
        draft = fmt(align='left', italic=True, **dict(cell, font_color='#9A3412'))
        names = {}
        sums = [0.0, 0.0, 0.0]
        row = head_row
        for batch in split_every(2000, ids):
            records = AML.browse(batch)
            wanted = {int(k) for l in records for key in (l.analytic_distribution or {}) for k in str(key).split(',') if k.isdigit()}
            missing = wanted - set(names)
            if missing:
                names.update({a.id: a.name for a in self.env['account.analytic.account'].sudo().browse(list(missing)).exists()})
            for l in records:
                row += 1
                tags = [names.get(int(k), '') for key in (l.analytic_distribution or {}) for k in str(key).split(',') if k.isdigit()]
                sheet.write_datetime(row, 0, l.date, day)
                sheet.write(row, 1, l.journal_id.code or '', text)
                sheet.write(row, 2, l.move_name or l.move_id.name or '', text)
                sheet.write(row, 3, l.account_id.display_name or '', text)
                sheet.write(row, 4, l.partner_id.display_name or '', text)
                sheet.write(row, 5, l.name or '', text)
                sheet.write(row, 6, l.ref or '', text)
                sheet.write_number(row, 7, l.debit, money)
                sheet.write_number(row, 8, l.credit, money)
                sheet.write_number(row, 9, l.balance, money)
                sheet.write(row, 10, l.matching_number or '', text)
                sheet.write(row, 11, ', '.join(t for t in tags if t), text)
                if l.date_maturity:
                    sheet.write_datetime(row, 12, l.date_maturity, day)
                else:
                    sheet.write(row, 12, '', text)
                sheet.write(row, 13, _('Draft') if l.parent_state == 'draft' else _('Posted'), draft if l.parent_state == 'draft' else text)
                sums[0] += l.debit
                sums[1] += l.credit
                sums[2] += l.balance
            records.invalidate_recordset()
        strong = dict(bold=True, bg_color='#F7F9FC', top=2, top_color=NAVY, font_color=INK)
        sheet.write(total_row, 0, _('Total of the rows shown'), fmt(align='left', **strong))
        for i in range(1, 7):
            sheet.write(total_row, i, '', fmt(**strong))
        for i, amount in zip((7, 8, 9), sums):
            letter = chr(ord('A') + i)
            sheet.write_formula(total_row, i, '=SUBTOTAL(109,%s%d:%s%d)' % (letter, first + 1, letter, last + 1),
                                fmt(align='right', num_format=MONEY, **strong), round(amount, 2))
        for i in range(10, len(heads)):
            sheet.write(total_row, i, '', fmt(**strong))
        if total > len(ids):
            sheet.write(last + 2, 0, _('Cut at %(shown)s items of %(total)s. Narrow the period or the filters for the rest.',
                                       shown=len(ids), total=total), fmt(italic=True, font_color=BAD))
        sheet.freeze_panes(head_row + 1, 3)
        sheet.autofilter(head_row, 0, last, len(heads) - 1)
        sheet.set_landscape()
        sheet.set_paper(9)
        sheet.fit_to_pages(1, 0)
        sheet.repeat_rows(head_row)
        sheet.set_margins(left=0.4, right=0.4, top=0.5, bottom=0.6)
        sheet.set_footer('&L&8%s&R&8%s &P / &N' % (title or self.name, _('Page')))
        book.close()
        safe = ''.join(ch if ch.isalnum() or ch in ' -_.' else ' ' for ch in (title or self.name)).strip()[:80]
        return {
            'filename': '%s - %s.xlsx' % (safe or self.name, _('journal items')),
            'content': base64.b64encode(output.getvalue()).decode(),
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'items': len(ids), 'total': total,
        }

    # ------------------------------------------------------------------ the PDF
    def export_pdf(self, options, layout=None):
        """The PDF itself, named after the report and its date - the report action names
        every download after itself."""
        self.ensure_one()
        self.env.flush_all()
        self._check_report_access()
        action = self.get_pdf_action(options, layout)
        pdf, _kind = self.env['ir.actions.report']._render_qweb_pdf(
            'ebshel_account_reports.action_fin_report_pdf', res_ids=[self.id], data=action['data'])
        day = self.env['ebshel.fin.engine'].normalize(self, dict(options or {}))['date']['to']
        return {'filename': '%s - %s.pdf' % (self.name, day), 'content': base64.b64encode(pdf).decode(),
                'mimetype': 'application/pdf'}

    def get_pdf_action(self, options, layout=None):
        self.ensure_one()
        self._check_report_access()
        action = self.env.ref('ebshel_account_reports.action_fin_report_pdf').read()[0]
        action['data'] = {'options': options, 'report_id': self.id,
                          'layout': self._export_layout(layout) if layout else False}
        action['context'] = {'active_ids': [self.id]}
        return action
