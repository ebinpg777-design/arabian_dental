# -*- coding: utf-8 -*-
import base64
import io
from datetime import datetime, time, timedelta

import pytz
import xlsxwriter
from dateutil.relativedelta import relativedelta

from odoo import fields, models

# source-location-usage (incoming) → movement column
_IN_MAP = {
    'supplier': 'purchase',
    'production': 'production',
    'internal': 'transfer',
    'inventory': 'inventory_loss',
    'customer': 'sale',        # sale return (subtracted below)
}
# destination-location-usage (outgoing) → movement column
_OUT_MAP = {
    'supplier': 'purchase',    # purchase return (subtracted)
    'production': 'itp',
    'internal': 'transfer',
    'inventory': 'inventory_loss',
    'customer': 'sale',
}
LAB_TZ = 'Asia/Kolkata'
_COLS = ['opening_bal', 'purchase', 'production', 'transfer',
         'inventory_loss', 'itp', 'sale', 'closing_bal']


class StockMovementReportWizard(models.TransientModel):
    _name = 'stock.movement.report.wizard'
    _description = 'Stock Movement Summary'

    warehouse_id = fields.Many2one(
        'stock.warehouse', string='Warehouse', required=True,
        default=lambda self: self.env['stock.warehouse'].search([], limit=1))
    categ_ids = fields.Many2many('product.category', string='Product Categories')
    location_ids = fields.Many2many('stock.location', string='Locations')
    product_ids = fields.Many2many('product.product', string='Products')
    value_wise = fields.Boolean(string='Value Wise Report')
    date_from = fields.Date(
        string='Date From', required=True,
        default=lambda self: fields.Date.today().replace(day=1))
    date_to = fields.Date(
        string='Date To', required=True,
        default=lambda self: fields.Date.today().replace(day=1) + relativedelta(months=2, days=-1))
    report_data = fields.Binary('File')
    report_name = fields.Char('File Name')

    def _internal_locations(self):
        if self.location_ids:
            return self.location_ids
        return self.env['stock.location'].search([
            ('usage', '=', 'internal'),
            ('warehouse_id', '=', self.warehouse_id.id)])

    def _local_tz(self):
        """Context tz, else user tz, else company tz, else Kolkata."""
        name = (self.env.context.get('tz') or self.env.user.tz
                or self.env.company.partner_id.tz or LAB_TZ)
        try:
            return pytz.timezone(name)
        except pytz.UnknownTimeZoneError:
            return pytz.timezone(LAB_TZ)

    def _day_start_utc(self, day):
        """Local midnight at the start of `day`, as the naive UTC datetime moves store.

        UTC midnight put the lab's first 5h30 of every period on the previous one.
        """
        local = self._local_tz().localize(datetime.combine(day, time.min))
        return local.astimezone(pytz.utc).replace(tzinfo=None)

    def _get_lines(self, category, internal_ids):
        """One row per product of `category` that had any movement/stock."""
        product_domain = [('categ_id', '=', category.id)]
        if self.product_ids:
            product_domain.append(('id', 'in', self.product_ids.ids))
        # Archived products still moved stock in the period and still hold it.
        products = self.env['product.product'].with_context(active_test=False).search(product_domain)
        if not products:
            return []
        Move = self.env['stock.move']
        dt_from = self._day_start_utc(self.date_from)
        dt_to = self._day_start_utc(self.date_to + timedelta(days=1))
        base = [('state', '=', 'done'), ('product_id', 'in', products.ids),
                ('date', '<', dt_to)]
        moves_in = Move.search(base + [('location_dest_id', 'in', internal_ids.ids)])
        moves_out = Move.search(base + [('location_id', 'in', internal_ids.ids)])

        data = {p.id: dict.fromkeys(_COLS, 0.0) for p in products}
        for move in moves_in:
            cost = move.product_id.standard_price if self.value_wise else 1.0
            qty = move.quantity * cost  # done quantity (product_uom_qty is only demand)
            row = data[move.product_id.id]
            if move.date < dt_from:
                row['opening_bal'] += qty
            row['closing_bal'] += qty
            if move.date >= dt_from:
                col = _IN_MAP.get(move.location_id.usage)
                if col == 'sale':
                    row['sale'] -= qty            # sale return
                elif col:
                    row[col] += qty
        for move in moves_out:
            cost = move.product_id.standard_price if self.value_wise else 1.0
            qty = move.quantity * cost  # done quantity (product_uom_qty is only demand)
            row = data[move.product_id.id]
            if move.date < dt_from:
                row['opening_bal'] -= qty
            row['closing_bal'] -= qty
            if move.date >= dt_from:
                col = _OUT_MAP.get(move.location_dest_id.usage)
                if col in ('purchase', 'transfer', 'inventory_loss'):
                    row[col] -= qty               # returns / issues out
                elif col:
                    row[col] += qty

        lines = []
        for product in products:
            row = data[product.id]
            if any(abs(row[c]) > 1e-9 for c in _COLS):
                lines.append(dict(row, code=product.default_code or '', name=product.display_name))
        return lines

    def print_report(self):
        self.ensure_one()
        internal_ids = self._internal_locations()
        categories = self.categ_ids or self.env['product.category'].search([])

        file_data = io.BytesIO()
        workbook = xlsxwriter.Workbook(file_data)
        sheet = workbook.add_worksheet('Stock Movement')
        title = workbook.add_format({'font_size': 12, 'bold': True, 'align': 'center', 'border': True})
        period = workbook.add_format({'font_size': 10, 'align': 'center', 'border': True})
        head = workbook.add_format({'font_size': 11, 'bold': True, 'align': 'center', 'border': True})
        cell = workbook.add_format({'font_size': 10, 'align': 'right', 'border': True, 'num_format': '#,##0.000'})
        text = workbook.add_format({'font_size': 10, 'align': 'left', 'border': True})
        subtotal = workbook.add_format({'font_size': 10, 'bold': True, 'align': 'right', 'border': True, 'num_format': '#,##0.000'})

        sheet.merge_range(0, 0, 0, 9, 'Brand Wise Summary of Stock Movement', title)
        sheet.merge_range(1, 0, 1, 9, 'Period from: %s to %s' % (
            self.date_from.strftime('%d/%m/%y'), self.date_to.strftime('%d/%m/%y')), period)
        headers = ['Code', 'Name', 'Opening Balance', 'Purchase', 'Production', 'Transfers',
                   'Adjustments (+/-)', 'Issue to Production', 'Sale', 'Closing Balance']

        row = 5
        grand = dict.fromkeys(_COLS, 0.0)
        sheet.merge_range(row - 3, 0, row - 3, 2, self.warehouse_id.name, head)
        for category in categories:
            lines = self._get_lines(category, internal_ids)
            if not lines:
                continue
            sheet.write(row - 2, 0, 'Category', head)
            sheet.merge_range(row - 2, 1, row - 2, 2, category.name, head)
            for col, label in enumerate(headers):
                sheet.write(row - 1, col, label, head)
            totals = dict.fromkeys(_COLS, 0.0)
            for line in lines:
                sheet.write(row, 0, line['code'], text)
                sheet.write(row, 1, line['name'], text)
                for i, col in enumerate(_COLS):
                    sheet.write(row, 2 + i, line[col], cell)
                    totals[col] += line[col]
                    grand[col] += line[col]
                row += 1
            sheet.write(row, 1, 'Sub Total', subtotal)
            for i, col in enumerate(_COLS):
                sheet.write(row, 2 + i, totals[col], subtotal)
            row += 4

        sheet.write(row, 1, 'Grand Total', subtotal)
        for i, col in enumerate(_COLS):
            sheet.write(row, 2 + i, grand[col], subtotal)

        workbook.close()
        file_data.seek(0)
        self.write({
            'report_data': base64.b64encode(file_data.read()),
            'report_name': 'Stock Movement Summary.xlsx',
        })
        file_data.close()
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content?model=%s&id=%s&field=report_data&filename_field=report_name&download=true' % (
                self._name, self.id),
            'target': 'self',
        }
