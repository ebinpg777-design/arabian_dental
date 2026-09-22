# -*- coding: utf-8 -*-
"""The Bank Reconciliation Statement as an Excel file, for the auditor's workpaper."""
from io import BytesIO

import xlsxwriter

from odoo import http
from odoo.http import content_disposition, request


class BankReconciliationXlsx(http.Controller):

    @http.route('/bank_reconciliation/<int:rec_id>/xlsx', type='http', auth='user')
    def download(self, rec_id, **kw):
        rec = request.env['bank.reconciliation'].browse(rec_id).exists()
        if not rec:
            raise request.not_found()
        rec.check_access('read')
        data = rec._brs_data()

        buf = BytesIO()
        book = xlsxwriter.Workbook(buf, {'in_memory': True})
        sheet = book.add_worksheet('BRS')
        bold = book.add_format({'bold': True})
        title = book.add_format({'bold': True, 'font_size': 14})
        money = book.add_format({'num_format': '#,##0.00'})
        money_bold = book.add_format({'num_format': '#,##0.00', 'bold': True})
        day = book.add_format({'num_format': 'dd-mm-yyyy'})
        head = book.add_format({'bold': True, 'bottom': 1})
        stale = book.add_format({'font_color': '#B3261E', 'bold': True})

        sheet.set_column(0, 0, 12)
        sheet.set_column(1, 1, 18)
        sheet.set_column(2, 2, 34)
        sheet.set_column(3, 3, 28)
        sheet.set_column(4, 4, 10)
        sheet.set_column(5, 5, 16)

        sheet.write(0, 0, 'Bank Reconciliation Statement', title)
        sheet.write(1, 0, '%s · %s' % (rec.journal_id.display_name, rec.account_id.display_name))
        sheet.write(2, 0, 'As on')
        sheet.write_datetime(2, 1, rec.date, day)
        sheet.write(3, 0, rec.company_id.name)

        row = 5
        for label, value, fmt in (
                ('Balance as per Books', data['book'], money_bold),
                ('Less: Deposits not yet credited by bank', data['deposits_not_credited'], money),
                ('Add: Payments not yet presented to bank', data['payments_not_presented'], money),
                ('Balance per Books, adjusted', data['adjusted'], money_bold),
                ('Balance as per Bank', data['bank'], money_bold),
                ('Difference', data['difference'], money_bold)):
            sheet.write(row, 0, label, bold if fmt is money_bold else None)
            sheet.write_number(row, 5, value, fmt)
            row += 1

        row += 1
        sheet.write(row, 0, 'Outstanding items by age', bold)
        row += 1
        for col, text in enumerate(('Age', '', 'Deposits', 'Amount', 'Payments', 'Amount')):
            sheet.write(row, col, text, head)
        row += 1
        for bucket in data['ageing']:
            sheet.write(row, 0, bucket['label'])
            sheet.write_number(row, 2, bucket['deposits']['count'])
            sheet.write_number(row, 3, bucket['deposits']['amount'], money)
            sheet.write_number(row, 4, bucket['payments']['count'])
            sheet.write_number(row, 5, bucket['payments']['amount'], money)
            row += 1

        if data['queries']:
            row += 1
            sheet.write(row, 0, 'Entries under query with the bank', bold)
            row += 1
            for col, text in enumerate(('Date', 'Entry', 'Particulars', 'Query', '', 'Amount')):
                sheet.write(row, col, text, head)
            row += 1
            for line in data['queries']:
                sheet.write_datetime(row, 0, line['date'], day)
                sheet.write(row, 1, line['move'])
                sheet.write(row, 2, line['label'] or line['ref'])
                sheet.write(row, 3, line['query'])
                sheet.write_number(row, 5, line['amount'], money)
                row += 1

        for heading, lines in (('Deposits not yet credited by bank', data['deposits']),
                               ('Payments not yet presented to bank', data['payments'])):
            row += 1
            sheet.write(row, 0, heading, bold)
            row += 1
            for col, text in enumerate(('Date', 'Entry', 'Particulars', 'Party', 'Age (days)', 'Amount')):
                sheet.write(row, col, text, head)
            row += 1
            total = 0.0
            for line in lines:
                sheet.write_datetime(row, 0, line['date'], day)
                sheet.write(row, 1, line['move'])
                sheet.write(row, 2, line['label'] or line['ref'])
                sheet.write(row, 3, line['partner'])
                sheet.write_number(row, 4, line['age'], stale if line['stale'] else None)
                sheet.write_number(row, 5, line['amount'], money)
                total += line['amount']
                row += 1
            sheet.write(row, 4, 'Total', bold)
            sheet.write_number(row, 5, total, money_bold)
            row += 1

        book.close()
        name = 'BRS %s %s.xlsx' % (rec.journal_id.name.replace('/', '-'), rec.date)
        return request.make_response(buf.getvalue(), headers=[
            ('Content-Type',
             'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('Content-Disposition', content_disposition(name)),
        ])
