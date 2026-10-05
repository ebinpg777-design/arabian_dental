# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
"""Common machinery of the report wizards.

A report declares its columns (``_asr_columns``) and produces its rows
(``_asr_rows``) as plain dicts; the mixin turns them into transient lines
for the screen, into an Excel file and into a PDF, and removes every value
column for users outside the See Values group.
"""
import io
import json
from datetime import date, datetime

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_date

try:
    import xlsxwriter
except ImportError:  # pragma: no cover - xlsxwriter ships with Odoo
    xlsxwriter = None

VALUE_GROUP = 'ebshel_stock_reports.group_see_values'


def col(key, label, ctype='char', value=False, total=False, width=None, help_text=None):
    """Column descriptor.

    :param ctype: char, int, float, qty, monetary, percent, date, datetime, many2one, bool.
    :param value: True when the column is a cost or a value (hidden without See Values).
    :param total: True when the column is summed on the total row.
    """
    return {'key': key, 'label': label, 'type': ctype, 'value': value, 'total': total,
            'width': width, 'help': help_text}


class AsrReportMixin(models.AbstractModel):
    _name = 'asr.report.mixin'
    _description = 'Advanced Stock Report'

    _asr_title = 'Report'
    _asr_line_model = None
    _asr_uses_engine = True  # reads the daily tables: refresh the queue first
    _asr_default_months = 1

    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company,
        domain=lambda self: [('id', 'in', self.env.companies.ids)])
    currency_id = fields.Many2one(related='company_id.currency_id')
    date_from = fields.Date(default=lambda self: self._default_date_from())
    date_to = fields.Date(default=fields.Date.context_today)
    warehouse_ids = fields.Many2many('stock.warehouse', string='Warehouses', check_company=True)
    location_ids = fields.Many2many(
        'stock.location', string='Locations', check_company=True,
        domain="[('usage', 'in', ('internal', 'transit')), ('company_id', '=', company_id)]")
    product_ids = fields.Many2many('product.product', string='Products', domain=[('is_storable', '=', True)])
    categ_ids = fields.Many2many('product.category', string='Product Categories')
    row_limit = fields.Integer(
        'Rows on Screen', default=5000,
        help="The screen stops after this many rows; Excel exports everything.")
    show_values = fields.Boolean(compute='_compute_show_values')
    engine_warning = fields.Char(compute='_compute_engine_warning')
    line_count = fields.Integer(compute='_compute_line_count')

    @api.model
    def _default_date_from(self):
        today = fields.Date.context_today(self)
        return today.replace(day=1) - relativedelta(months=self._asr_default_months - 1)

    def _compute_show_values(self):
        has_group = self.env.user.has_group(VALUE_GROUP)
        for wizard in self:
            wizard.show_values = has_group

    @api.depends('company_id')
    def _compute_engine_warning(self):
        Dirty = self.env['asr.stock.dirty'].sudo()
        for wizard in self:
            warning = False
            if self._asr_uses_engine and wizard.company_id:
                if wizard.company_id.asr_engine_state == 'empty':
                    warning = self.env._("The daily stock summary has not been built yet; figures will be empty "
                                         "until the cron runs or a manager recomputes it from the settings.")
                else:
                    pending = Dirty.search_count([('company_id', '=', wizard.company_id.id)])
                    if pending > 200:
                        warning = self.env._(
                            "%s products are waiting to be recomputed; recent movements may be missing.", pending)
            wizard.engine_warning = warning

    def _compute_line_count(self):
        for wizard in self:
            wizard.line_count = self.env[self._asr_line_model].search_count([('wizard_id', '=', wizard.id)]) \
                if self._asr_line_model and wizard.id else 0

    @api.onchange('warehouse_ids')
    def _onchange_warehouse_ids(self):
        if self.warehouse_ids and self.location_ids:
            self.location_ids = self.location_ids.filtered(lambda loc: loc.warehouse_id in self.warehouse_ids)

    # ------------------------------------------------------------------
    # To implement per report
    # ------------------------------------------------------------------
    def _asr_columns(self):
        return []

    def _asr_rows(self):
        return []

    # ------------------------------------------------------------------
    # Helpers available to every report
    # ------------------------------------------------------------------
    def _asr_check(self):
        self.ensure_one()
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise UserError(self.env._("The start date must be before the end date."))

    def _asr_prepare(self):
        """Make sure the daily tables are current for this company before reading them."""
        self._asr_check()
        if self._asr_uses_engine:
            self.env['asr.stock.dirty']._ensure_fresh(self.company_id, self.product_ids or None)

    def _asr_locations(self):
        """Internal/transit locations selected by the warehouse and location filters (child_of), or empty for all."""
        self.ensure_one()
        Location = self.env['stock.location'].with_context(active_test=False)
        domain = [('company_id', '=', self.company_id.id), ('usage', 'in', ('internal', 'transit'))]
        if self.location_ids:
            return Location.search(domain + [('id', 'child_of', self.location_ids.ids)])
        if self.warehouse_ids:
            return Location.search(domain + [('warehouse_id', 'in', self.warehouse_ids.ids)])
        return Location

    def _asr_product_domain(self):
        domain = [('is_storable', '=', True)]
        if self.product_ids:
            domain.append(('id', 'in', self.product_ids.ids))
        if self.categ_ids:
            domain.append(('categ_id', 'child_of', self.categ_ids.ids))
        return domain

    def _asr_products(self):
        return self.env['product.product'].with_context(active_test=False).search(
            self._asr_product_domain(), order='default_code, name, id')

    def _asr_visible_columns(self):
        columns = self._asr_columns()
        if not self.env.user.has_group(VALUE_GROUP):
            columns = [c for c in columns if not c['value']]
        return columns

    def _asr_filter_text(self):
        parts = []
        if self.date_from:
            parts.append(self.env._("From %s", format_date(self.env, self.date_from)))
        if self.date_to:
            parts.append(self.env._("To %s", format_date(self.env, self.date_to)))
        if self.warehouse_ids:
            parts.append(self.env._("Warehouses: %s", ', '.join(self.warehouse_ids.mapped('name'))))
        if self.location_ids:
            parts.append(self.env._("Locations: %s", ', '.join(self.location_ids.mapped('complete_name'))))
        if self.categ_ids:
            parts.append(self.env._("Categories: %s", ', '.join(self.categ_ids.mapped('complete_name'))))
        if self.product_ids:
            parts.append(self.env._("Products: %s", ', '.join(self.product_ids[:5].mapped('display_name'))
                                    + (' …' if len(self.product_ids) > 5 else '')))
        return ' | '.join(parts)

    def _asr_totals(self, rows, columns):
        totals = {}
        for column in columns:
            if column['total']:
                totals[column['key']] = sum((r.get(column['key']) or 0.0) for r in rows if not r.get('_group'))
        return totals

    # ------------------------------------------------------------------
    # Screen
    # ------------------------------------------------------------------
    def _asr_line_vals(self, row):
        Line = self.env[self._asr_line_model]
        vals = {'wizard_id': self.id}
        for key, value in row.items():
            if key.startswith('_') or key not in Line._fields or key in ('id', 'wizard_id'):
                continue
            if isinstance(value, models.BaseModel):
                value = value.id
            vals[key] = value
        return vals

    def action_view(self):
        self.ensure_one()
        self._asr_prepare()
        # Lines are written with sudo: a user outside the See Values group may not
        # write value fields, yet the rows are theirs; the field groups still hide
        # those columns when the lines are read.
        Line = self.env[self._asr_line_model].sudo()
        Line.search([('wizard_id', '=', self.id)]).unlink()
        rows = [r for r in self._asr_rows() if not r.get('_group')]
        truncated = len(rows) > self.row_limit if self.row_limit else False
        if truncated:
            rows = rows[:self.row_limit]
        Line.create([self._asr_line_vals(row) for row in rows])
        view_modes = ['list']
        for mode in ('pivot', 'graph'):
            if self.env['ir.ui.view'].sudo().search_count([('model', '=', self._asr_line_model), ('type', '=', mode)]):
                view_modes.append(mode)
        name = self.env._(self._asr_title)
        if self._asr_filter_text():
            name = f"{name} ({self._asr_filter_text()})"
        action = {
            'type': 'ir.actions.act_window',
            'name': name,
            'res_model': self._asr_line_model,
            'view_mode': ','.join(view_modes),
            'domain': [('wizard_id', '=', self.id)],
            'context': {'create': False, 'edit': False, 'delete': False, **self._asr_view_context()},
            'target': 'current',
        }
        if truncated:
            action['help'] = self.env._(
                "Only the first %s rows are shown. Export to Excel for the full report.", self.row_limit)
        return action

    def _asr_view_context(self):
        return {}

    # ------------------------------------------------------------------
    # Excel
    # ------------------------------------------------------------------
    def action_xlsx(self):
        self.ensure_one()
        self._asr_check()
        return {
            'type': 'ir.actions.act_url',
            'url': f'/ebshel_stock_reports/xlsx/{self._name}/{self.id}',
            'target': 'self',
        }

    def _asr_xlsx_filename(self):
        stamp = fields.Date.to_string(self.date_to or fields.Date.context_today(self))
        return f"{self._asr_title.replace(' ', '_').replace('/', '-')}_{stamp}.xlsx"

    def _asr_xlsx(self):
        """Build the Excel file: returns bytes."""
        self.ensure_one()
        if xlsxwriter is None:
            raise UserError(self.env._("The Python package xlsxwriter is not installed."))
        self._asr_prepare()
        columns = self._asr_visible_columns()
        rows = self._asr_rows()
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True, 'constant_memory': True})
        sheet = workbook.add_worksheet(self._asr_title[:31])
        bold = workbook.add_format({'bold': True})
        header = workbook.add_format(
            {'bold': True, 'bg_color': '#DDDDDD', 'border': 1, 'text_wrap': True, 'valign': 'top'})
        group = workbook.add_format({'bold': True, 'bg_color': '#F2F2F2'})
        total = workbook.add_format({'bold': True, 'top': 1})
        decimals = self.currency_id.decimal_places or 2
        formats = {
            'int': workbook.add_format({'num_format': '0'}),
            'float': workbook.add_format({'num_format': '#,##0.00'}),
            'qty': workbook.add_format({'num_format': '#,##0.00##'}),
            'monetary': workbook.add_format({'num_format': '#,##0.' + '0' * decimals}),
            'percent': workbook.add_format({'num_format': '0.00"%"'}),
            'date': workbook.add_format({'num_format': 'yyyy-mm-dd'}),
            'datetime': workbook.add_format({'num_format': 'yyyy-mm-dd hh:mm'}),
        }
        total_formats = {
            key: workbook.add_format({'bold': True, 'top': 1, 'num_format': fmt.num_format})
            for key, fmt in formats.items()
        }
        sheet.write(0, 0, self.env._(self._asr_title), bold)
        sheet.write(1, 0, self.company_id.name)
        sheet.write(2, 0, self._asr_filter_text())
        sheet.write(3, 0, self.env._("Printed on %(date)s by %(user)s",
                                     date=fields.Datetime.now().strftime('%Y-%m-%d %H:%M'), user=self.env.user.name))
        row_idx = 5
        for c, column in enumerate(columns):
            sheet.write(row_idx, c, column['label'], header)
            sheet.set_column(c, c, column['width'] or (14 if column['type'] in ('char', 'many2one') else 12))
        sheet.freeze_panes(row_idx + 1, 0)
        row_idx += 1
        for row in rows:
            if row.get('_group'):
                sheet.merge_range(row_idx, 0, row_idx, max(len(columns) - 1, 1), str(row.get('_group')), group)
                row_idx += 1
                continue
            for c, column in enumerate(columns):
                self._asr_xlsx_cell(sheet, row_idx, c, row.get(column['key']), column, formats, None)
            row_idx += 1
        totals = self._asr_totals(rows, columns)
        if totals:
            sheet.write(row_idx, 0, self.env._("Total"), total)
            for c, column in enumerate(columns):
                if column['key'] in totals:
                    self._asr_xlsx_cell(sheet, row_idx, c, totals[column['key']], column, total_formats, total)
                elif c:
                    sheet.write_blank(row_idx, c, None, total)
        workbook.close()
        return output.getvalue()

    def _asr_xlsx_cell(self, sheet, r, c, value, column, formats, fallback):
        ctype = column['type']
        if isinstance(value, models.BaseModel):
            value = value.display_name if value else ''
        if value is None or value is False and ctype not in ('bool',):
            sheet.write_blank(r, c, None, fallback)
            return
        if ctype in ('int', 'float', 'qty', 'monetary', 'percent'):
            sheet.write_number(r, c, float(value or 0.0), formats[ctype])
        elif ctype == 'date':
            if isinstance(value, str):
                value = fields.Date.to_date(value)
            sheet.write_datetime(r, c, datetime.combine(value, datetime.min.time()), formats['date'])
        elif ctype == 'datetime':
            if isinstance(value, str):
                value = fields.Datetime.to_datetime(value)
            local = fields.Datetime.context_timestamp(self, value).replace(tzinfo=None)
            sheet.write_datetime(r, c, local, formats['datetime'])
        elif ctype == 'bool':
            sheet.write(r, c, self.env._("Yes") if value else self.env._("No"), fallback)
        else:
            sheet.write(r, c, str(value), fallback)

    # ------------------------------------------------------------------
    # PDF
    # ------------------------------------------------------------------
    def action_pdf(self):
        self.ensure_one()
        self._asr_prepare()
        columns = self._asr_visible_columns()
        rows = self._asr_rows()
        cap = self.company_id.asr_pdf_row_cap or 2000
        truncated = len(rows) > cap
        totals = self._asr_totals(rows, columns)
        if truncated:
            rows = rows[:cap]
        serialised = [self._asr_serialise_row(r, columns) for r in rows]
        document = self.env['asr.report.print'].create({
            'name': self.env._(self._asr_title),
            'company_id': self.company_id.id,
            'subtitle': self._asr_filter_text(),
            'res_model': self._name,
            'res_id': self.id,
            'columns_json': json.dumps([{k: v for k, v in c.items() if k != 'help'} for c in columns]),
            'rows_json': json.dumps(serialised, default=str),
            'totals_json': json.dumps(totals, default=str),
            'row_count': len(rows),
            'truncated': truncated,
            'row_cap': cap,
        })
        return self.env.ref('ebshel_stock_reports.action_report_document').report_action(document, config=False)

    def _asr_serialise_row(self, row, columns):
        if row.get('_group'):
            return {'_group': str(row['_group'])}
        out = {}
        for column in columns:
            value = row.get(column['key'])
            if isinstance(value, models.BaseModel):
                value = value.display_name if value else ''
            elif isinstance(value, datetime):
                value = fields.Datetime.context_timestamp(self, value).strftime('%Y-%m-%d %H:%M')
            elif isinstance(value, date):
                value = format_date(self.env, value)
            out[column['key']] = value
        return out
