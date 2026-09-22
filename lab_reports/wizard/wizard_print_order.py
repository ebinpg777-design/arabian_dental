# -*- coding: utf-8 -*-
import base64
import io
from datetime import timedelta

import xlsxwriter

from odoo import fields, models


class WizardPrintOrder(models.TransientModel):
    _name = "wizard.print.order"
    _inherit = ['lab.report.mixin']
    _description = "Print Order Reports (BoM / Daily Production)"

    state = fields.Selection([('new', 'New'), ('done', 'Done')], 'State', default='new')
    report_data = fields.Binary('File')
    name = fields.Char('Name', size=252)
    checked_by = fields.Char('Checked By', size=252)
    prepared_by = fields.Char('Prepared By', size=252)
    approved_by = fields.Char('Approved By', size=252)
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company)
    workcenter_id = fields.Many2one('mrp.workcenter', string='Work center')

    def _logo(self, worksheet):
        if self.company_id.excel_logo:
            worksheet.insert_image(0, 0, "image.png", {
                'image_data': io.BytesIO(base64.b64decode(self.company_id.excel_logo))})

    def print_bill_of_material(self):
        self.ensure_one()
        order_ids = self.env.context.get('active_ids', [])
        file_data = io.BytesIO()
        workbook = xlsxwriter.Workbook(file_data)
        worksheet = workbook.add_worksheet("Bill of materials")
        worksheet.set_default_row(24)
        f = self._xlsx_formats(workbook, head2_align='center')
        doc_heading = 'Doc. Ref. : ORCR/R-15/00/' + self._doc_date()
        worksheet.merge_range(0, 0, 1, 1, '', f['title'])
        self._logo(worksheet)
        worksheet.merge_range(0, 2, 1, 5, 'LAB CREATION DENTAL LAB', f['main'])
        worksheet.merge_range(0, 6, 1, 7, doc_heading, f['common'])
        worksheet.merge_range(2, 0, 2, 7, 'BILL OF MATERIAL', f['title'])
        worksheet.merge_range(3, 0, 4, 0, 'DO No.', f['head'])
        worksheet.merge_range(3, 1, 4, 1, 'Product', f['head'])
        worksheet.merge_range(3, 2, 3, 4, 'Raw Material Requirement', f['head'])
        worksheet.write(4, 2, 'Raw Material', f['head'])
        worksheet.write(4, 3, 'Qty', f['head'])
        worksheet.write(4, 4, 'UoM', f['head'])
        worksheet.merge_range(3, 5, 4, 5, 'Prepared By', f['head'])
        worksheet.merge_range(3, 6, 4, 6, 'Approved By', f['head'])
        worksheet.merge_range(3, 7, 4, 7, 'Remarks', f['head'])
        row = 5
        for order in self.env['sale.order'].browse(order_ids).exists():
            row_order_start = row
            if not order.order_line:
                continue
            for line in order.order_line:
                row_start = row
                boms = self.env['mrp.bom'].search(
                    [('product_tmpl_id', '=', line.product_id.product_tmpl_id.id)])
                if not boms:
                    continue
                for bom in boms:
                    for bom_line in bom.bom_line_ids:
                        worksheet.write(row, 2, bom_line.product_id.name or '', f['common'])
                        worksheet.write(row, 3, str(bom_line.product_qty), f['common'])
                        worksheet.write(row, 4, bom_line.product_uom_id.name or '', f['common'])
                        row += 1
                if row_start >= row - 1:
                    worksheet.write(row_start, 1, line.product_id.display_name or '', f['vcenter'])
                else:
                    worksheet.merge_range(row_start, 1, row - 1, 1, line.product_id.display_name or '', f['vcenter'])
            if row_order_start >= row - 1:
                worksheet.write(row_order_start, 0, order.name or '', f['vcenter'])
                worksheet.write(row_order_start, 5, self.prepared_by or '', f['vcenter'])
                worksheet.write(row_order_start, 6, self.approved_by or '', f['vcenter'])
                worksheet.write(row_order_start, 7, '', f['vcenter'])
            else:
                worksheet.merge_range(row_order_start, 0, row - 1, 0, order.name or '', f['vcenter'])
                worksheet.merge_range(row_order_start, 5, row - 1, 5, self.prepared_by or '', f['vcenter'])
                worksheet.merge_range(row_order_start, 6, row - 1, 6, self.approved_by or '', f['vcenter'])
                worksheet.merge_range(row_order_start, 7, row - 1, 7, '', f['vcenter'])
        workbook.close()
        file_data.seek(0)
        self.write({'state': 'done', 'report_data': base64.b64encode(file_data.read()),
                    'name': 'Bill of Material.xlsx'})
        file_data.close()
        return self._xlsx_download()

    def print_bom_detail(self):
        self.ensure_one()
        bom_ids = self.env.context.get('active_ids', [])
        file_data = io.BytesIO()
        workbook = xlsxwriter.Workbook(file_data)
        worksheet = workbook.add_worksheet("Bill Of materal")
        worksheet.set_default_row(24)
        f = self._xlsx_formats(workbook, head2_align='center')
        doc_heading = 'Doc. Ref. : ORCR/R-15/00/' + self._doc_date()
        worksheet.merge_range(0, 0, 1, 1, '', f['title'])
        self._logo(worksheet)
        worksheet.merge_range(0, 2, 1, 5, 'LAB CREATION DENTAL LAB', f['main'])
        worksheet.merge_range(0, 6, 1, 7, doc_heading, f['common'])
        worksheet.merge_range(2, 0, 2, 7, 'BILL OF MATERIAL', f['title'])
        worksheet.merge_range(3, 0, 4, 0, 'Sl No.', f['head'])
        worksheet.merge_range(3, 1, 4, 1, 'Product', f['head'])
        worksheet.merge_range(3, 2, 3, 4, 'Raw Material Requirement', f['head'])
        worksheet.write(4, 2, 'Raw Material', f['head'])
        worksheet.write(4, 3, 'Qty', f['head'])
        worksheet.write(4, 4, 'UoM', f['head'])
        worksheet.merge_range(3, 5, 4, 5, 'Prepared By', f['head'])
        worksheet.merge_range(3, 6, 4, 6, 'Approved By', f['head'])
        worksheet.merge_range(3, 7, 4, 7, 'Remarks', f['head'])
        row = 5
        sl_no = 1
        for bom in self.env['mrp.bom'].browse(bom_ids).exists():
            row_start = row
            for bom_line in bom.bom_line_ids:
                worksheet.write(row, 2, bom_line.product_id.name or '', f['common'])
                worksheet.write(row, 3, str(bom_line.product_qty), f['common'])
                worksheet.write(row, 4, bom_line.product_uom_id.name or '', f['common'])
                row += 1
            if row_start >= row - 1:
                worksheet.write(row_start, 1, bom.product_tmpl_id.display_name or '', f['vcenter'])
                worksheet.write(row_start, 0, str(sl_no), f['vcenter'])
                worksheet.write(row_start, 5, self.prepared_by or '', f['vcenter'])
                worksheet.write(row_start, 6, self.approved_by or '', f['vcenter'])
                worksheet.write(row_start, 7, '', f['vcenter'])
            else:
                worksheet.merge_range(row_start, 1, row - 1, 1, bom.product_tmpl_id.display_name or '', f['vcenter'])
                worksheet.merge_range(row_start, 0, row - 1, 0, str(sl_no), f['vcenter'])
                worksheet.merge_range(row_start, 5, row - 1, 5, self.prepared_by or '', f['vcenter'])
                worksheet.merge_range(row_start, 6, row - 1, 6, self.approved_by or '', f['vcenter'])
                worksheet.merge_range(row_start, 7, row - 1, 7, '', f['vcenter'])
            sl_no += 1
        workbook.close()
        file_data.seek(0)
        self.write({'state': 'done', 'report_data': base64.b64encode(file_data.read()),
                    'name': 'Bill of Material.xlsx'})
        file_data.close()
        return self._xlsx_download()

    def print_daily_production(self):
        self.ensure_one()
        order_ids = self.env.context.get('active_ids', [])
        file_data = io.BytesIO()
        workbook = xlsxwriter.Workbook(file_data)
        worksheet = workbook.add_worksheet(self.workcenter_id.name or 'Production')
        worksheet.set_default_row(24)
        f = self._xlsx_formats(workbook, head2_align='center')
        doc_heading = 'Doc. Ref. : ORCR/00/' + self._doc_date()
        worksheet.merge_range(0, 0, 1, 1, '', f['title'])
        self._logo(worksheet)
        worksheet.merge_range(0, 2, 1, 6, 'LAB CREATION DENTAL LAB', f['main'])
        worksheet.merge_range(0, 7, 1, 7, doc_heading, f['common'])
        worksheet.merge_range(2, 0, 2, 7, 'Daily Production Report - ' + str(self.workcenter_id.name or ''), f['title'])
        headers = ['Sl No', 'Date', 'Order No.', 'MO No.', 'Due Date', 'Completed on',
                   'Reason of Delay', 'Remarks']
        for col, label in enumerate(headers):
            worksheet.merge_range(3, col, 4, col, label, f['head'])
        row = 5
        sl_no = 1
        for order in self.env['sale.order'].browse(order_ids).exists().sorted(key=lambda o: o.id, reverse=True):
            for mo in order.mrp_production_ids:
                for workorder in mo.workorder_ids:
                    if workorder.workcenter_id.id != self.workcenter_id.id:
                        continue
                    worksheet.write(row, 0, str(sl_no), f['common'])
                    worksheet.write(row, 1, self._local_str(order.create_date), f['common'])
                    worksheet.write(row, 2, order.name or '', f['common'])
                    worksheet.write(row, 3, mo.name or '', f['common'])
                    due = self._local_str(order.date_order and order.date_order + timedelta(days=2))
                    worksheet.write(row, 4, due, f['common'])
                    worksheet.write(row, 5, self._local_str(workorder.date_finished), f['common'])
                    worksheet.write(row, 6, workorder.delay_reason or '', f['common'])
                    worksheet.write(row, 7, workorder.remarks or '', f['common'])
                    row += 1
                    sl_no += 1
        workbook.close()
        file_data.seek(0)
        report_name = 'Daily Production Report-' + str(self.workcenter_id.name or '') + '.xlsx'
        self.write({'state': 'done', 'report_data': base64.b64encode(file_data.read()), 'name': report_name})
        file_data.close()
        return self._xlsx_download()
