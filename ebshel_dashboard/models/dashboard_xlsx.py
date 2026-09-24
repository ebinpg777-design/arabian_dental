# -*- coding: utf-8 -*-
"""Dynamic Dashboards - a board, or one card, as a spreadsheet.

A PDF is for reading and a chart is for looking at; a spreadsheet is for
carrying on. This writes what every card counted - the numbers behind the
picture, not a picture of the numbers - one sheet per card, with the board's
period and filters written at the top of each so a file that leaves the
screen still says what it was.

xlsxwriter is one of Odoo's own requirements, so nothing new has to be
installed; if a hand-built environment is missing it, only this download says
so and the rest of the module is untouched.
"""
import base64
import io
import re

from odoo import models
from odoo.exceptions import UserError

try:
    import xlsxwriter
except ImportError:  # pragma: no cover - only on an incomplete install
    xlsxwriter = None

from .dashboard_item import NUMBER_KINDS, PERIODS, STACKED_KINDS

# Excel refuses these in a sheet name, and truncates past 31 characters.
BAD_SHEET_CHARS = re.compile(r'[\[\]:*?/\\]')
SHEET_LIMIT = 31


class DashboardBoardXlsx(models.Model):
    _inherit = 'dashboard.board'

    def download_xlsx(self, period=None, focus=None, mine=False, item_ids=None, filters=None):
        """This dashboard as an .xlsx file, ready to download."""
        self.ensure_one()
        if xlsxwriter is None:
            raise UserError(self.env._(
                'This server has no xlsxwriter library, so it cannot build a '
                'spreadsheet. Odoo lists it among its own requirements: install '
                'it with "pip install xlsxwriter" and restart the service.'))
        data = self.read_board(self.id, period=period, focus=focus, mine=mine, filters=filters)
        items = [item for item in data['items'] if not item.get('hidden')]
        if item_ids:
            wanted = {int(one) for one in item_ids}
            items = [item for item in items if item['id'] in wanted]
        if not items:
            raise UserError(self.env._('There is nothing to export on this dashboard.'))

        stream = io.BytesIO()
        book = xlsxwriter.Workbook(stream, {'in_memory': True, 'default_date_format': 'yyyy-mm-dd'})
        styles = self._xlsx_styles(book)
        used = set()
        for item in items:
            sheet = book.add_worksheet(self._sheet_name(item['name'], used))
            self._write_sheet(sheet, styles, item, data)
        book.close()

        single = len(items) == 1
        stem = re.sub(r'[^A-Za-z0-9_-]+', '_',
                      items[0]['name'] if single else (self.name or 'dashboard')).strip('_')
        attachment = self.env['ir.attachment'].create({
            'name': '%s.xlsx' % (stem or 'dashboard'),
            'type': 'binary',
            'datas': base64.b64encode(stream.getvalue()),
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'res_model': 'dashboard.board',
            'res_id': self.id,
        })
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%s?download=true' % attachment.id,
            'target': 'self',
        }

    def _sheet_name(self, name, used):
        """A sheet name Excel will accept, and no two the same."""
        clean = BAD_SHEET_CHARS.sub(' ', name or 'Card').strip() or 'Card'
        clean = clean[:SHEET_LIMIT]
        candidate = clean
        suffix = 2
        while candidate.lower() in used:
            tail = ' (%s)' % suffix
            candidate = clean[:SHEET_LIMIT - len(tail)] + tail
            suffix += 1
        used.add(candidate.lower())
        return candidate

    def _xlsx_styles(self, book):
        return {
            'title': book.add_format({'bold': True, 'font_size': 13}),
            'note': book.add_format({'font_color': '#6b7280', 'font_size': 9}),
            'head': book.add_format({'bold': True, 'bg_color': '#eef2ff', 'border': 1}),
            'text': book.add_format({'border': 1}),
            'number': book.add_format({'border': 1, 'num_format': '#,##0.##'}),
            'percent': book.add_format({'border': 1, 'num_format': '0.0"%"'}),
            'big': book.add_format({'bold': True, 'font_size': 20}),
        }

    def _write_sheet(self, sheet, styles, item, data):
        """One card: what it is at the top, what it counted underneath."""
        sheet.set_column(0, 0, 38)
        sheet.set_column(1, 8, 16)
        sheet.write(0, 0, item['name'], styles['title'])
        period = data['board'].get('period')
        label = '%s – %s' % (period.get('start'), period.get('end')) if isinstance(period, dict) \
            else str(dict(PERIODS).get(period, period or ''))
        parts = [item.get('model') or '', label]
        if data.get('mine'):
            parts.append(self.env._('Only my records'))
        for entry in data.get('filters') or []:
            parts.append('%s = %s' % (entry.get('field'), entry.get('value')))
        sheet.write(1, 0, ' · '.join(part for part in parts if part), styles['note'])

        row = 3
        if item['kind'] in NUMBER_KINDS:
            sheet.write(row, 0, self.env._('Value'), styles['head'])
            sheet.write_number(row, 1, float(item.get('value') or 0), styles['big'])
            row += 1
            for key, title in (('count', self.env._('Records')),
                               ('previous', self.env._('Previous period')),
                               ('delta', self.env._('Change')),
                               ('target', self.env._('Target')),
                               ('ratio', self.env._('Share'))):
                if item.get(key) not in (None, False):
                    sheet.write(row, 0, title, styles['head'])
                    sheet.write_number(row, 1, float(item[key]), styles['number'])
                    row += 1
            for sub in item.get('subvalues') or []:
                sheet.write(row, 0, sub['label'], styles['head'])
                sheet.write_number(row, 1, float(sub['value'] or 0), styles['number'])
                if sub.get('share') is not None:
                    sheet.write_number(row, 2, float(sub['share']), styles['percent'])
                row += 1
            return

        if item['kind'] in STACKED_KINDS:
            categories = item.get('categories') or []
            series = item.get('series') or []
            sheet.write(row, 0, item.get('group_label') or self.env._('Group'), styles['head'])
            for index, entry in enumerate(series):
                sheet.write(row, index + 1, entry['label'], styles['head'])
            for line, category in enumerate(categories, start=row + 1):
                sheet.write(line, 0, category['label'], styles['text'])
                for index, entry in enumerate(series):
                    sheet.write_number(line, index + 1,
                                       float((entry.get('values') or [])[categories.index(category)] or 0),
                                       styles['number'])
            return

        if item['kind'] == 'list':
            columns = item.get('columns') or []
            head = [self.env._('Record')]
            if item.get('measure'):
                head.append(item.get('measure_label') or item['measure'])
            head += [column['label'] for column in columns]
            for index, title in enumerate(head):
                sheet.write(row, index, title, styles['head'])
            for line, record in enumerate(item.get('rows') or [], start=row + 1):
                sheet.write(line, 0, record['label'], styles['text'])
                offset = 1
                if item.get('measure'):
                    sheet.write_number(line, 1, float(record.get('value') or 0), styles['number'])
                    offset = 2
                for index, cell in enumerate(record.get('cells') or []):
                    sheet.write(line, offset + index, cell, styles['text'])
            return

        points = item.get('points') or []
        head = [item.get('group_label') or item.get('date_label') or self.env._('Group'),
                item.get('measure_label') or self.env._('Value')]
        if any(point.get('value2') is not None for point in points):
            head.append(item.get('measure2_label') or self.env._('Second measure'))
        head += [self.env._('Records'), self.env._('Share')]
        for index, title in enumerate(head):
            sheet.write(row, index, title, styles['head'])
        for line, point in enumerate(points, start=row + 1):
            sheet.write(line, 0, point.get('label') or '', styles['text'])
            sheet.write_number(line, 1, float(point.get('value') or 0), styles['number'])
            column = 2
            if len(head) == 5:
                sheet.write_number(line, 2, float(point.get('value2') or 0), styles['number'])
                column = 3
            sheet.write_number(line, column, float(point.get('count') or 0), styles['number'])
            if point.get('share') is not None:
                sheet.write_number(line, column + 1, float(point['share']), styles['percent'])
