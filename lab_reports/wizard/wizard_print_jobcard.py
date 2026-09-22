# -*- coding: utf-8 -*-
import base64
import io
from datetime import timedelta

import xlsxwriter

from odoo import fields, models


class WizardPrintJobCard(models.TransientModel):
    _name = "wizard.print.jobcard"
    _inherit = ['lab.report.mixin']
    _description = "Print Job Card"

    state = fields.Selection([('new', 'New'), ('done', 'Done')], 'State', default='new')
    report_data = fields.Binary('File')
    name = fields.Char('Name', size=252)
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company)

    def _dt(self, value, fmt='%d-%m-%Y %H:%M'):
        return self._local_str(value, fmt)

    def print_job_card(self):
        self.ensure_one()
        order_ids = self.env.context.get('active_ids', [])
        file_data = io.BytesIO()
        workbook = xlsxwriter.Workbook(file_data)
        f = self._xlsx_formats(workbook, head2_align='left')
        used = set()
        for order in self.env['sale.order'].browse(order_ids).exists():
            for mrp in order.mrp_production_ids:
                worksheet = workbook.add_worksheet(self._safe_sheet_name(mrp.name, used))
                worksheet.set_default_row(24)
                if self.company_id.excel_logo:
                    worksheet.insert_image(0, 0, "image.png", {
                        'image_data': io.BytesIO(base64.b64decode(self.company_id.excel_logo))})
                worksheet.merge_range(0, 0, 1, 1, '', f['title'])
                worksheet.merge_range(0, 2, 1, 5, 'LAB CREATION DENTAL LAB', f['main'])
                worksheet.merge_range(0, 6, 1, 8, '', f['common'])
                worksheet.merge_range(2, 0, 2, 8, 'JOB CARD', f['title'])
                worksheet.write(3, 0, 'Order No.', f['head'])
                worksheet.merge_range(3, 1, 3, 2, order.name or '', f['common'])
                worksheet.write(3, 3, 'Date', f['head'])
                worksheet.merge_range(3, 4, 3, 5, self._dt(order.create_date, '%d-%m-%Y'), f['common'])
                worksheet.write(3, 6, 'Due Date', f['head'])
                due = self._dt(order.date_order and order.date_order + timedelta(days=2), '%d-%m-%Y')
                worksheet.merge_range(3, 7, 3, 8, due, f['common'])
                worksheet.merge_range(4, 0, 4, 2, 'Job Card No:', f['head'])
                worksheet.merge_range(4, 3, 4, 8, mrp.name or '', f['common'])
                worksheet.merge_range(5, 0, 5, 2, 'Patient Name', f['head'])
                worksheet.merge_range(5, 3, 5, 8, order.patient or '', f['common'])
                worksheet.merge_range(6, 0, 6, 2, 'Prosthesis', f['head'])
                worksheet.merge_range(6, 3, 6, 8, mrp.product_id.name or '', f['common'])
                worksheet.merge_range(7, 0, 7, 2, 'Appliance Style', f['head'])
                worksheet.merge_range(7, 3, 7, 8, mrp.product_id.appliance_style.name or '', f['common'])
                worksheet.merge_range(8, 0, 8, 2, 'Upper/Lower', f['head'])
                worksheet.merge_range(8, 3, 8, 8, mrp.ul or '', f['common'])
                worksheet.merge_range(9, 0, 9, 2, 'Special Instructions if any', f['head'])
                worksheet.merge_range(9, 3, 9, 8, order.instruction or '', f['common'])
                worksheet.merge_range(10, 0, 10, 2, 'Impression Type', f['head'])
                worksheet.merge_range(10, 3, 10, 8, order.impression_type or '', f['common'])
                worksheet.merge_range(11, 0, 11, 2, 'Scanned Impression', f['head'])
                worksheet.merge_range(11, 3, 11, 8, order.scanned_impression or '', f['common'])
                worksheet.merge_range(12, 0, 12, 2, 'Impression Tray', f['head'])
                worksheet.merge_range(12, 3, 12, 8, order.impression_tray or '', f['common'])
                worksheet.merge_range(13, 0, 13, 2, 'Wax Bite', f['head'])
                worksheet.merge_range(13, 3, 13, 8, order.wax_bite or '', f['common'])
                worksheet.merge_range(14, 0, 14, 2, 'Registration Done by', f['head'])
                worksheet.merge_range(14, 3, 14, 8, order.register_person_id.name or '', f['common'])
                worksheet.merge_range(15, 0, 15, 8, 'Departmentwise Details', f['title'])
                row_no = 16
                for workorder in mrp.workorder_ids.filtered(lambda w: w.state != 'cancel'):
                    worksheet.merge_range(row_no, 0, row_no, 2, workorder.name or '', f['common'])
                    worksheet.write(row_no, 3, "In Time", f['head'])
                    worksheet.merge_range(row_no, 4, row_no, 5, self._dt(workorder.date_start), f['common'])
                    worksheet.write(row_no, 6, "Out Time", f['head'])
                    worksheet.merge_range(row_no, 7, row_no, 8, self._dt(workorder.date_finished), f['common'])
                    row_no += 1
                    worksheet.merge_range(row_no, 0, row_no, 2, "Remarks", f['head'])
                    worksheet.merge_range(row_no, 3, row_no, 8, workorder.remarks or '', f['common'])
                    row_no += 1
                    worksheet.merge_range(row_no, 0, row_no, 2, "Material used", f['head'])
                    workcenter = workorder.workcenter_id
                    bom_line_ids = mrp.bom_id.bom_line_ids.filtered(
                        lambda l: l.operation_id.workcenter_id == workcenter)
                    materials = ', '.join(
                        '%s(%s %s)' % (bl.product_id.name, bl.product_qty, bl.product_uom_id.name)
                        for bl in bom_line_ids)
                    worksheet.merge_range(row_no, 3, row_no, 8, materials or '', f['common'])
                    row_no += 1
                    worksheet.merge_range(row_no, 0, row_no, 2, "Taken from Stores on", f['head'])
                    worksheet.merge_range(row_no, 3, row_no, 8, self._dt(workorder.date_start, '%d-%m-%Y'), f['common'])
                    row_no += 1
                    lot_ids = self.env['stock.lot'].search([
                        ('mrp_ids', 'in', [mrp.id]),
                        ('product_id', 'in', bom_line_ids.mapped('product_id').ids)])
                    internal_ref = ', '.join(lot_ids.mapped('name'))
                    worksheet.merge_range(row_no, 0, row_no, 2, "Internal Material Ref.", f['head'])
                    worksheet.merge_range(row_no, 3, row_no, 8, internal_ref, f['common'])
                    row_no += 1
                    worksheet.merge_range(row_no, 0, row_no, 2, "Technician", f['head'])
                    worksheet.merge_range(row_no, 3, row_no, 8, workorder.user_id.name or '', f['common'])
                    row_no += 2
        workbook.close()
        file_data.seek(0)
        self.write({'state': 'done', 'report_data': base64.b64encode(file_data.read()),
                    'name': 'Job Card.xlsx'})
        file_data.close()
        return self._xlsx_download()
