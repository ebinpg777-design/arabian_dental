# -*- coding: utf-8 -*-
import base64
import io

import xlsxwriter

from odoo import fields, models
from odoo.tools import html2plaintext


class WizardPrintPartner(models.TransientModel):
    _name = "wizard.print.partner"
    _inherit = ['lab.report.mixin']
    _description = "Print Partner Reports"

    state = fields.Selection([('new', 'New'), ('done', 'Done')], 'State', default='new')
    report_data = fields.Binary('File')
    name = fields.Char('Name', size=252)
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company)

    def _logo(self, worksheet):
        if self.company_id.excel_logo:
            worksheet.insert_image(0, 0, "image.png", {
                'image_data': io.BytesIO(base64.b64decode(self.company_id.excel_logo))})

    def print_customer(self):
        self.ensure_one()
        partner_ids = self.env.context.get('active_ids', [])
        file_data = io.BytesIO()
        workbook = xlsxwriter.Workbook(file_data)
        worksheet = workbook.add_worksheet("Customer Details")
        worksheet.set_default_row(24)
        f = self._xlsx_formats(workbook, head2_align='center')
        doc_heading = 'Doc. Ref. : ORCR/R-12/00/' + self._doc_date()
        worksheet.merge_range(0, 0, 1, 1, '', f['title'])
        self._logo(worksheet)
        worksheet.merge_range(0, 2, 1, 4, 'LAB CREATION DENTAL LAB', f['main'])
        worksheet.merge_range(0, 5, 1, 7, doc_heading, f['common'])
        worksheet.merge_range(2, 0, 2, 7, 'DOCTOR / HOSPITAL DATA', f['title'])
        headers = ['Sl No.', 'CR No.', 'Name of the Doctor / Hospital', 'Contact Person',
                   'Address', 'Phone No.', 'Email ID', 'Handled By']
        for col, label in enumerate(headers):
            worksheet.write(3, col, label, f['head'])
        row = 4
        sl_no = 1
        for partner in self.env['res.partner'].browse(partner_ids).exists():
            worksheet.write(row, 0, str(sl_no), f['common'])
            worksheet.write(row, 1, partner.cr_number or '', f['common'])
            worksheet.write(row, 2, partner.name or '', f['common'])
            worksheet.write(row, 3, partner.contact_person or '', f['common'])
            worksheet.write(row, 4, partner._display_address() or '', f['common'])
            worksheet.write(row, 5, partner.phone or '', f['common'])
            worksheet.write(row, 6, partner.email or '', f['common'])
            worksheet.write(row, 7, partner.handled_by.name or '', f['common'])
            row += 1
            sl_no += 1
        workbook.close()
        file_data.seek(0)
        self.write({'state': 'done', 'report_data': base64.b64encode(file_data.read()),
                    'name': 'Customer Details.xlsx'})
        file_data.close()
        return self._xlsx_download()

    def print_customer_registration(self):
        self.ensure_one()
        partner_ids = self.env.context.get('active_ids', [])
        file_data = io.BytesIO()
        workbook = xlsxwriter.Workbook(file_data)
        f = self._xlsx_formats(workbook, head2_align='left')
        doc_heading = 'Doc. Ref. : ORCR/R-14/00/' + self._doc_date()
        used = set()
        for partner in self.env['res.partner'].browse(partner_ids).exists():
            worksheet = workbook.add_worksheet(self._safe_sheet_name(partner.name, used))
            worksheet.set_default_row(24)
            worksheet.merge_range(0, 0, 1, 0, '', f['title'])
            self._logo(worksheet)
            worksheet.merge_range(0, 1, 1, 2, 'LAB CREATION DENTAL LAB', f['main'])
            worksheet.merge_range(0, 3, 1, 3, doc_heading, f['common'])
            worksheet.merge_range(2, 0, 2, 3, 'CLIENT REGISTRATION FORM', f['title'])
            worksheet.merge_range(3, 0, 3, 1, 'CR No.', f['head'])
            worksheet.merge_range(4, 0, 4, 1, 'Name of the Hospital / Doctor', f['head'])
            worksheet.merge_range(5, 0, 7, 1, 'Address', f['head'])
            worksheet.merge_range(8, 0, 8, 1, 'Phone No.', f['head'])
            worksheet.merge_range(9, 0, 9, 1, 'Email ID', f['head'])
            worksheet.merge_range(10, 0, 10, 1, 'DCI No.', f['head'])
            worksheet.merge_range(11, 0, 11, 1, 'Hospital Registration No.', f['head'])
            worksheet.merge_range(12, 0, 12, 1, 'Sourced By', f['head'])
            worksheet.merge_range(3, 2, 3, 3, partner.cr_number or '', f['common'])
            worksheet.merge_range(4, 2, 4, 3, partner.name or '', f['common'])
            worksheet.merge_range(5, 2, 5, 3, partner.street or '', f['common'])
            worksheet.merge_range(6, 2, 6, 3, partner.street2 or '', f['common'])
            worksheet.merge_range(7, 2, 7, 3, partner.city or '', f['common'])
            worksheet.merge_range(8, 2, 8, 3, partner.phone or '', f['common'])
            worksheet.merge_range(9, 2, 9, 3, partner.email or '', f['common'])
            worksheet.merge_range(10, 2, 10, 3, partner.dci_number or '', f['common'])
            worksheet.merge_range(11, 2, 11, 3, partner.hospital_reg_no or '', f['common'])
            worksheet.merge_range(12, 2, 12, 3, partner.sourced_by.name or '', f['common'])
        workbook.close()
        file_data.seek(0)
        self.write({'state': 'done', 'report_data': base64.b64encode(file_data.read()),
                    'name': 'Customer Registration.xlsx'})
        file_data.close()
        return self._xlsx_download()

    def print_vendor(self):
        self.ensure_one()
        partner_ids = self.env.context.get('active_ids', [])
        file_data = io.BytesIO()
        workbook = xlsxwriter.Workbook(file_data)
        worksheet = workbook.add_worksheet("Vendor Details")
        worksheet.set_default_row(24)
        f = self._xlsx_formats(workbook, head2_align='center')
        doc_heading = 'Doc. Ref. : ORCR/R-19/00/' + self._doc_date()
        worksheet.merge_range(0, 0, 1, 1, '', f['title'])
        self._logo(worksheet)
        worksheet.merge_range(0, 2, 1, 10, 'LAB CREATION DENTAL LAB', f['main'])
        worksheet.merge_range(0, 11, 1, 12, doc_heading, f['common'])
        worksheet.merge_range(2, 0, 2, 12, 'APPROVED VENDOR LIST', f['title'])
        headers = ['Vendor Code', 'Name of the Vendor.', 'Address', 'Contact Person',
                   'Phone No.', 'Email ID', 'Supplier of', 'Status of Vendor.', 'GST',
                   'ISO Status', 'Rate', 'Credit Period', 'Remarks']
        for col, label in enumerate(headers):
            worksheet.write(3, col, label, f['head'])
        row = 4
        for partner in self.env['res.partner'].browse(partner_ids).exists():
            supplierinfo = self.env['product.supplierinfo'].search([('partner_id', '=', partner.id)])
            products = ",".join(supplierinfo.mapped('product_tmpl_id.name'))
            worksheet.write(row, 0, partner.vendor_code or '', f['common'])
            worksheet.write(row, 1, partner.name or '', f['common'])
            worksheet.write(row, 2, partner._display_address(without_company=True) or '', f['common'])
            worksheet.write(row, 3, partner.contact_person or '', f['common'])
            worksheet.write(row, 4, partner.phone or '', f['common'])
            worksheet.write(row, 5, partner.email or '', f['common'])
            worksheet.write(row, 6, products, f['common'])
            worksheet.write(row, 7, partner.vendor_status or '', f['common'])
            worksheet.write(row, 8, partner.gst_number or '', f['common'])
            worksheet.write(row, 9, partner.iso_status or '', f['common'])
            worksheet.write(row, 10, partner.rating or '', f['common'])
            worksheet.write(row, 11, partner.credit_period or '', f['common'])
            worksheet.write(row, 12, html2plaintext(partner.comment) if partner.comment else '', f['common'])
            row += 1
        workbook.close()
        file_data.seek(0)
        self.write({'state': 'done', 'report_data': base64.b64encode(file_data.read()),
                    'name': 'Vendor Details.xlsx'})
        file_data.close()
        return self._xlsx_download()

    def print_vendor_registration(self):
        self.ensure_one()
        partner_ids = self.env.context.get('active_ids', [])
        file_data = io.BytesIO()
        workbook = xlsxwriter.Workbook(file_data)
        f = self._xlsx_formats(workbook, head2_align='left')
        doc_heading = 'Doc. Ref. : ORCR/R-17/00/' + self._doc_date()
        used = set()
        for partner in self.env['res.partner'].browse(partner_ids).exists():
            worksheet = workbook.add_worksheet(self._safe_sheet_name(partner.name, used))
            worksheet.set_default_row(24)
            worksheet.merge_range(0, 0, 1, 0, '', f['title'])
            self._logo(worksheet)
            worksheet.merge_range(0, 1, 1, 2, 'LAB CREATION DENTAL LAB', f['main'])
            worksheet.merge_range(0, 3, 1, 3, doc_heading, f['common'])
            worksheet.merge_range(2, 0, 2, 3, 'VENDOR REGISTRATION FORM', f['title'])
            labels = ['Vendor Code.', 'Name of the Vendor', 'Address', '', '', 'Contact Person',
                      'Phone No.', 'Email ID', 'Type of Activity', 'GST No.', 'No.of Employees',
                      'Any Quality Certifications?', 'Major Clients', 'Approved By']
            worksheet.merge_range(3, 0, 3, 1, 'Vendor Code.', f['head'])
            worksheet.merge_range(4, 0, 4, 1, 'Name of the Vendor', f['head'])
            worksheet.merge_range(5, 0, 7, 1, 'Address', f['head'])
            worksheet.merge_range(8, 0, 8, 1, 'Contact Person', f['head'])
            worksheet.merge_range(9, 0, 9, 1, 'Phone No.', f['head'])
            worksheet.merge_range(10, 0, 10, 1, 'Email ID', f['head'])
            worksheet.merge_range(11, 0, 11, 1, 'Type of Activity', f['head'])
            worksheet.merge_range(12, 0, 12, 1, 'GST No.', f['head'])
            worksheet.merge_range(13, 0, 13, 1, 'No.of Employees', f['head'])
            worksheet.merge_range(14, 0, 14, 1, 'Any Quality Certifications?', f['head'])
            worksheet.merge_range(15, 0, 15, 1, 'Major Clients', f['head'])
            worksheet.merge_range(16, 0, 16, 1, 'Approved By', f['head'])
            worksheet.merge_range(3, 2, 3, 3, partner.vendor_code or '', f['common'])
            worksheet.merge_range(4, 2, 4, 3, partner.name or '', f['common'])
            worksheet.merge_range(5, 2, 5, 3, partner.street or '', f['common'])
            worksheet.merge_range(6, 2, 6, 3, partner.street2 or '', f['common'])
            worksheet.merge_range(7, 2, 7, 3, partner.city or '', f['common'])
            worksheet.merge_range(8, 2, 8, 3, partner.contact_person or '', f['common'])
            worksheet.merge_range(9, 2, 9, 3, partner.phone or '', f['common'])
            worksheet.merge_range(10, 2, 10, 3, partner.email or '', f['common'])
            worksheet.merge_range(11, 2, 11, 3, partner.activity_type or '', f['common'])
            worksheet.merge_range(12, 2, 12, 3, partner.gst_number or '', f['common'])
            worksheet.merge_range(13, 2, 13, 3, partner.no_employees or '', f['common'])
            worksheet.merge_range(14, 2, 14, 3, partner.quality_certificate or '', f['common'])
            worksheet.merge_range(15, 2, 15, 3, partner.major_clients or '', f['common'])
            worksheet.merge_range(16, 2, 16, 3, partner.approved_by or '', f['common'])
        workbook.close()
        file_data.seek(0)
        self.write({'state': 'done', 'report_data': base64.b64encode(file_data.read()),
                    'name': 'Vendor Registration.xlsx'})
        file_data.close()
        return self._xlsx_download()

    def print_vendor_evaluation(self):
        self.ensure_one()
        partner_ids = self.env.context.get('active_ids', [])
        file_data = io.BytesIO()
        workbook = xlsxwriter.Workbook(file_data)
        f = self._xlsx_formats(workbook, head2_align='left')
        doc_heading = 'Doc. Ref. : ORCR/R-18/00/' + self._doc_date()
        used = set()
        for partner in self.env['res.partner'].browse(partner_ids).exists():
            worksheet = workbook.add_worksheet(self._safe_sheet_name(partner.name, used))
            worksheet.set_default_row(24)
            worksheet.merge_range(0, 0, 1, 0, '', f['title'])
            self._logo(worksheet)
            worksheet.merge_range(0, 1, 1, 2, 'LAB CREATION DENTAL LAB', f['main'])
            worksheet.merge_range(0, 3, 1, 3, doc_heading, f['common'])
            worksheet.merge_range(2, 0, 2, 3, 'VENDOR EVALUATION', f['title'])
            worksheet.merge_range(3, 0, 3, 1, 'Vendor Code.', f['head'])
            worksheet.merge_range(4, 0, 4, 1, 'Name of the Vendor', f['head'])
            worksheet.merge_range(5, 0, 5, 1, 'Evaluation Period', f['head'])
            worksheet.merge_range(6, 0, 6, 1, 'Suppliers of', f['head'])
            worksheet.merge_range(7, 0, 7, 1, 'Total No. of supplies made', f['head'])
            worksheet.merge_range(8, 0, 8, 1, 'Qty. Rejected', f['head'])
            worksheet.merge_range(9, 0, 9, 1, 'No. of Rejected Qty per supply', f['head'])
            worksheet.merge_range(10, 0, 10, 1, 'Delivery Performance', f['head'])
            worksheet.merge_range(11, 0, 11, 1, 'Invoicing', f['head'])
            worksheet.merge_range(12, 0, 12, 1, 'Rating', f['head'])
            worksheet.merge_range(13, 0, 13, 1, 'Any Quality Certifications?', f['head'])
            worksheet.merge_range(14, 0, 14, 1, 'Major Clients', f['head'])
            worksheet.merge_range(15, 0, 15, 1, 'Approved By', f['head'])
            supplierinfo = self.env['product.supplierinfo'].search([('partner_id', '=', partner.id)])
            products = ",".join(supplierinfo.mapped('product_tmpl_id.name'))
            worksheet.merge_range(3, 2, 3, 3, partner.vendor_code or '', f['common'])
            worksheet.merge_range(4, 2, 4, 3, partner.name or '', f['common'])
            worksheet.merge_range(5, 2, 5, 3, partner.evaluation_period or '', f['common'])
            worksheet.merge_range(6, 2, 6, 3, products, f['common'])
            worksheet.merge_range(7, 2, 7, 3, partner.total_supplies or '', f['common'])
            worksheet.merge_range(8, 2, 8, 3, partner.qty_rejected or '', f['common'])
            worksheet.merge_range(9, 2, 9, 3, partner.no_qty_rejected or '', f['common'])
            worksheet.merge_range(10, 2, 10, 3, partner.delivery_performance or '', f['common'])
            worksheet.merge_range(11, 2, 11, 3, partner.invoicing or '', f['common'])
            worksheet.merge_range(12, 2, 12, 3, partner.rating or '', f['common'])
            worksheet.merge_range(13, 2, 13, 3, partner.quality_certificate or '', f['common'])
            worksheet.merge_range(14, 2, 14, 3, partner.major_clients or '', f['common'])
            worksheet.merge_range(15, 2, 15, 3, partner.approved_by or '', f['common'])
        workbook.close()
        file_data.seek(0)
        self.write({'state': 'done', 'report_data': base64.b64encode(file_data.read()),
                    'name': 'Vendor Evaluation.xlsx'})
        file_data.close()
        return self._xlsx_download()
