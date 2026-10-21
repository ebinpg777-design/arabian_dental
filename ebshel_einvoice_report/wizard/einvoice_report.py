# Part of ebshel_einvoice_report. See LICENSE file for full copyright and licensing details.
"""The GST e-invoice report: a period's e-invoices as an Excel workbook in the column
layout of the e-invoice bulk upload (a Buyer Details sheet, one row per document, and
an E-Invoice Items sheet, one row per line).

The figures come from l10n_in's own helpers - `_l10n_in_prepare_tax_details` splits
every line's tax by the GST tax tags into SGST / CGST / IGST / cess - so they are the
values Odoo's e-invoicing would send to the portal, and each document's taxable value
plus its taxes plus its round off is its invoice total.
"""
import io
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_is_zero

try:
    import xlsxwriter
except ImportError:  # pragma: no cover - declared in external_dependencies
    xlsxwriter = None

# The customers an e-invoice is raised for: registered (B2B, composition, UN bodies),
# SEZ, deemed export and export.
EINVOICE_TREATMENTS = ('regular', 'composition', 'uin_holders', 'deemed_export',
                       'special_economic_zone', 'overseas')

BUYER_HEADERS = [
    'Supply Type Code', 'Igst On Intra', 'Document Type', 'Document Number',
    'Document Date (DD/MM/YYYY)', 'BUYER GSTIN', 'BUYER Name', 'BUYER POS', 'BUYER Addr1',
    'BUYER Addr2', 'BUYER Location', 'BUYER Pin Code', 'BUYER State Code',
    'BUYER Phone Number', 'BUYER EMAIL ID', 'Total Taxable Value', 'Sgst Amt', 'Cgst Amt',
    'Igst Amt', 'Cess Amt', 'State Cess Amt', 'TOTAL Discount', 'Other charges', 'Round off',
    'Total Invoice Value',
]
BUYER_KEYS = [
    'supply_type', 'igst_on_intra', 'doc_type', 'number', 'date', 'gstin', 'name', 'pos',
    'addr1', 'addr2', 'location', 'pin', 'state_code', 'phone', 'email', 'taxable', 'sgst',
    'cgst', 'igst', 'cess', 'state_cess', 'discount', 'other', 'round_off', 'total',
]
ITEM_HEADERS = [
    'Document Type', 'Document Number', 'Document Date', 'Sl.No.', 'Product Description',
    'Is_Service', 'HSN code', 'Quantity', 'Unit', 'Unit Price', 'Gross Amount',
    'Pre Tax Discount', 'Taxable value', 'GST Rate (%)', 'Sgst Amt(Rs)', 'Cgst Amt(Rs)',
    'Igst Amt(Rs)', 'Cess Rate (%)', 'Cess Amt', 'Cess Non Adval Amt(Rs)',
    'State Cess Rate (%)', 'State Cess Amt(Rs)', 'Item Total',
]
ITEM_KEYS = [
    'doc_type', 'number', 'date', 'sl_no', 'description', 'is_service', 'hsn', 'quantity',
    'unit', 'unit_price', 'gross', 'discount', 'taxable', 'gst_rate', 'sgst', 'cgst', 'igst',
    'cess_rate', 'cess', 'cess_non_advol', 'state_cess_rate', 'state_cess', 'total',
]
# columns summed on the Grand Total rows (a rate is never summed)
BUYER_SUMS = ('taxable', 'sgst', 'cgst', 'igst', 'cess', 'state_cess', 'discount', 'other',
              'round_off', 'total')
ITEM_SUMS = ('gross', 'discount', 'taxable', 'sgst', 'cgst', 'igst', 'cess', 'cess_non_advol',
             'state_cess', 'total')
TEXT_KEYS = {'gstin', 'pin', 'state_code', 'pos', 'phone', 'hsn', 'number', 'date'}


class EinvoiceReportWizard(models.TransientModel):
    _name = 'einvoice.report.wizard'
    _description = 'GST E-Invoice Report'

    date_from = fields.Date(string='From', required=True, default=fields.Date.context_today)
    date_to = fields.Date(string='To', required=True, default=fields.Date.context_today)
    company_ids = fields.Many2many(
        'res.company', string='Companies', required=True,
        domain=lambda self: [('id', 'in', self.env.companies.ids)],
        default=lambda self: self.env.companies)
    include_credit_notes = fields.Boolean(
        string='Include credit notes',
        help="Credit notes to these customers are e-invoiced too (document type CRN).")
    summary = fields.Char(compute='_compute_summary')

    # ------------------------------------------------------------------ the documents
    def _domain(self):
        self.ensure_one()
        move_types = ['out_invoice'] + (['out_refund'] if self.include_credit_notes else [])
        return [
            ('move_type', 'in', move_types),
            ('state', '=', 'posted'),
            ('invoice_date', '>=', self.date_from),
            ('invoice_date', '<=', self.date_to),
            ('company_id', 'in', self.company_ids.ids),
            ('l10n_in_gst_treatment', 'in', EINVOICE_TREATMENTS),
        ]

    def _moves(self):
        return self.env['account.move'].search(self._domain(), order='invoice_date, name, id')

    @api.depends('date_from', 'date_to', 'company_ids', 'include_credit_notes')
    def _compute_summary(self):
        for wizard in self:
            if not (wizard.date_from and wizard.date_to and wizard.company_ids):
                wizard.summary = False
                continue
            if wizard.date_from > wizard.date_to:
                wizard.summary = _("The period ends before it starts.")
                continue
            groups = self.env['account.move']._read_group(
                wizard._domain(), [], ['__count', 'amount_total_signed:sum'])
            count, value = groups[0] if groups else (0, 0.0)
            if not count:
                wizard.summary = _("No e-invoice documents in this period.")
                continue
            currency = wizard.company_ids[:1].currency_id
            wizard.summary = _(
                "%(count)s document(s) · %(value)s",
                count=count, value=currency.format(abs(value) if count else 0.0))

    def _check(self):
        self.ensure_one()
        if self.date_from > self.date_to:
            raise UserError(_("The period ends before it starts."))
        # one upload is one seller GSTIN: companies of different states cannot share it
        states = self.company_ids.mapped('state_id')
        if len(states) != 1 or any(not company.state_id for company in self.company_ids):
            raise UserError(_("Select companies of one state: an e-invoice upload is made "
                              "for one GSTIN."))
        if xlsxwriter is None:
            raise UserError(_("The Python package xlsxwriter is not installed."))

    # ------------------------------------------------------------------ the values
    @api.model
    def _buyer_name(self, move):
        """The buyer's legal name. A hook: a database that keeps a separate billing
        name can return it instead."""
        return move.partner_id.commercial_partner_id.name or ''

    @api.model
    def _supply_type(self, move, igst):
        if move.l10n_in_gst_treatment in ('overseas', 'special_economic_zone') and igst:
            return {'overseas': 'EXPWP', 'special_economic_zone': 'SEZWP'}[move.l10n_in_gst_treatment]
        return {
            'deemed_export': 'DEXP',
            'overseas': 'EXPWOP',
            'special_economic_zone': 'SEZWOP',
        }.get(move.l10n_in_gst_treatment, 'B2B')

    @api.model
    def _doc_type(self, move):
        if move.move_type == 'out_refund':
            return 'CRN'
        if 'debit_origin_id' in move._fields and move.debit_origin_id:
            return 'DBN'
        return 'INV'

    @api.model
    def _phone(self, move, partner):
        digits = move._l10n_in_extract_digits(partner.phone or '')
        return digits if re.match(r'^[0-9]{10,12}$', digits) else ''

    @api.model
    def _email(self, partner):
        email = (partner.email or '').strip()
        if re.match(r'^[a-zA-Z0-9+_.-]+@[a-zA-Z0-9.-]+$', email) and 6 <= len(email) <= 100:
            return email
        return ''

    def _document(self, move):
        """One document: its Buyer Details row and its item rows."""
        rnd = move._l10n_in_round_value
        details = move._l10n_in_prepare_tax_details()
        per_line = details['tax_details_per_record']
        codes = move._get_l10n_in_tax_details_by_line_code(details['tax_details'])
        sign = -1 if move.is_inbound() else 1
        doc_type = self._doc_type(move)
        date = move.invoice_date.strftime('%d/%m/%Y') if move.invoice_date else ''
        items = []
        lines = move.invoice_line_ids.filtered(lambda line: line.display_type == 'product')
        for index, line in enumerate(lines, start=1):
            line_details = per_line.get(line, {})
            by_code = move._get_l10n_in_tax_details_by_line_code(line_details.get('tax_details', {}))
            taxable = sign * line.balance                     # company currency, after discount
            quantity = line.quantity
            if line.discount == 100.0 or float_is_zero(quantity, precision_digits=3):
                unit_price = line.currency_id._convert(
                    line.price_unit, line.company_currency_id, line.company_id, line.date)
            else:
                unit_price = taxable / (1 - line.discount / 100.0) / quantity
            gross = unit_price * quantity
            hsn = line.l10n_in_hsn_code or line.product_id.l10n_in_hsn_code or ''
            items.append({
                'doc_type': doc_type, 'number': move.name, 'date': date, 'sl_no': index,
                'description': (line.product_id.name or (line.name or '').split('\n')[0]).strip(),
                'is_service': 'Y' if move._l10n_in_is_service_hsn(hsn) else 'N',
                'hsn': move._l10n_in_extract_digits(hsn),
                'quantity': rnd(quantity, 3),
                'unit': (line.product_uom_id.l10n_in_code or '').split('-')[0] or 'OTH',
                'unit_price': rnd(unit_price, 3),
                'gross': rnd(gross),
                'discount': rnd(gross * line.discount / 100.0),
                'taxable': rnd(taxable),
                'gst_rate': rnd(by_code.get('igst_rate', 0.0)
                                or (by_code.get('cgst_rate', 0.0) + by_code.get('sgst_rate', 0.0))),
                'sgst': rnd(by_code.get('sgst_amount', 0.0)),
                'cgst': rnd(by_code.get('cgst_amount', 0.0)),
                'igst': rnd(by_code.get('igst_amount', 0.0)),
                'cess_rate': rnd(by_code.get('cess_rate', 0.0), 3),
                'cess': rnd(by_code.get('cess_amount', 0.0)),
                'cess_non_advol': rnd(by_code.get('cess_non_advol_amount', 0.0)),
                'state_cess_rate': rnd(by_code.get('state_cess_rate', 0.0), 3),
                'state_cess': rnd(by_code.get('state_cess_amount', 0.0)
                                  + by_code.get('state_cess_non_advol_amount', 0.0)),
                'total': rnd(taxable + line_details.get('tax_amount', 0.0)),
            })
        partner = move.partner_id
        commercial = partner.commercial_partner_id
        overseas = move.l10n_in_gst_treatment == 'overseas'
        is_intra_state = move.l10n_in_state_id == move.company_id.state_id
        igst = codes.get('igst_amount', 0.0)
        taxable = sum(item['taxable'] for item in items)
        sgst = rnd(codes.get('sgst_amount', 0.0))
        cgst = rnd(codes.get('cgst_amount', 0.0))
        cess = rnd(codes.get('cess_amount', 0.0) + codes.get('cess_non_advol_amount', 0.0))
        state_cess = rnd(codes.get('state_cess_amount', 0.0)
                         + codes.get('state_cess_non_advol_amount', 0.0))
        round_off = rnd(sign * sum(line.balance for line in move.line_ids if line.display_type == 'rounding'))
        zip_digits = move._l10n_in_extract_digits(partner.zip or '')
        buyer = {
            'supply_type': self._supply_type(move, igst),
            'igst_on_intra': 'Y' if (move.l10n_in_gst_treatment not in ('special_economic_zone', 'overseas')
                                     and is_intra_state and igst) else 'N',
            'doc_type': doc_type, 'number': move.name, 'date': date,
            'gstin': 'URP' if overseas else (commercial.vat or ''),
            'name': self._buyer_name(move),
            'pos': '96' if overseas else (move.l10n_in_state_id.l10n_in_tin or ''),
            'addr1': partner.street or '',
            'addr2': partner.street2 or '',
            'location': partner.city or '',
            'pin': '999999' if overseas else zip_digits,
            'state_code': '96' if overseas else (partner.state_id.l10n_in_tin or ''),
            'phone': self._phone(move, partner),
            'email': self._email(partner),
            'taxable': rnd(taxable),
            'sgst': sgst, 'cgst': cgst, 'igst': rnd(igst), 'cess': cess, 'state_cess': state_cess,
            # line discounts are already out of each item's taxable value (Pre Tax
            # Discount on the items sheet): an invoice-level discount here would take
            # them off a second time
            'discount': 0.0,
            'other': 0.0,
            'round_off': round_off,
        }
        buyer['total'] = rnd(buyer['taxable'] + sgst + cgst + buyer['igst'] + cess + state_cess + round_off)
        return buyer, items

    def _documents(self):
        """[(buyer_row, item_rows)] for the period, in date and number order."""
        self.ensure_one()
        return [self._document(move) for move in self._moves()]

    # ------------------------------------------------------------------ the workbook
    def action_download(self):
        self._check()
        return {
            'type': 'ir.actions.act_url',
            'url': '/einvoice_report/xlsx/%s' % self.id,
            'target': 'self',
        }

    def _filename(self):
        period = self.date_from.strftime('%d-%m-%Y')
        if self.date_to != self.date_from:
            period += ' to ' + self.date_to.strftime('%d-%m-%Y')
        return 'E-Invoice Report %s.xlsx' % period

    def _xlsx(self):
        """The workbook, as bytes."""
        self._check()
        documents = self._documents()
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        base = {'font_name': 'Times New Roman', 'font_size': 10, 'valign': 'vcenter', 'border': 1}
        styles = {
            'title': workbook.add_format({'font_name': 'Times New Roman', 'font_size': 12,
                                          'bold': True, 'align': 'center'}),
            'period': workbook.add_format({'font_name': 'Times New Roman', 'font_size': 10,
                                           'align': 'center'}),
            'head': workbook.add_format(dict(base, bold=True, font_color='white', bg_color='#007acc',
                                             align='center', text_wrap=True)),
            'text': workbook.add_format(dict(base)),
            'center': workbook.add_format(dict(base, align='center')),
            'number': workbook.add_format(dict(base, num_format='#,##0.00')),
            'qty': workbook.add_format(dict(base, num_format='#,##0.###')),
            'total_label': workbook.add_format(dict(base, bold=True, font_color='white',
                                                    bg_color='#007acc', align='center')),
            'total': workbook.add_format(dict(base, bold=True, font_color='white',
                                              bg_color='#007acc', num_format='#,##0.00')),
        }
        buyers = [buyer for buyer, _items in documents]
        items = [item for _buyer, rows in documents for item in rows]
        period = '%s to %s' % (self.date_from.strftime('%d-%b-%Y'), self.date_to.strftime('%d-%b-%Y'))
        self._sheet(workbook, styles, 'Buyer Details', 'Buyer Details Report', period,
                    BUYER_HEADERS, BUYER_KEYS, BUYER_SUMS, buyers)
        self._sheet(workbook, styles, 'E-Invoice Items Sheet', 'E-Invoice Items Report', period,
                    ITEM_HEADERS, ITEM_KEYS, ITEM_SUMS, items)
        workbook.close()
        return output.getvalue()

    @api.model
    def _sheet(self, workbook, styles, name, title, period, headers, keys, sums, rows):
        sheet = workbook.add_worksheet(name)
        last = len(headers)                                   # columns B .. B+len-1
        sheet.merge_range(3, 1, 3, last, title, styles['title'])
        sheet.merge_range(4, 1, 4, last, period, styles['period'])
        header_row = 6
        for col, header in enumerate(headers, start=1):
            sheet.write(header_row, col, header, styles['head'])
        sheet.set_row(header_row, 32)
        sheet.set_column(1, last, 14)
        row = header_row + 1
        numeric = set(sums) | {'gst_rate', 'cess_rate', 'state_cess_rate', 'unit_price'}
        for record in rows:
            for col, key in enumerate(keys, start=1):
                value = record.get(key)
                if key in numeric:
                    sheet.write_number(row, col, value or 0.0, styles['number'])
                elif key in ('quantity', 'sl_no'):
                    sheet.write_number(row, col, value or 0, styles['qty'])
                elif key in TEXT_KEYS:
                    # codes stay text: a PIN or HSN must not turn into a number
                    sheet.write_string(row, col, str(value or ''), styles['center'])
                else:
                    sheet.write_string(row, col, str(value or ''), styles['text'])
            row += 1
        # Grand Total: the label runs up to the first summed column, never over it
        first_sum = min(keys.index(key) for key in sums) + 1
        if first_sum > 1:
            sheet.merge_range(row, 1, row, first_sum - 1, 'Grand Total', styles['total_label'])
        else:
            sheet.write(row, 1, 'Grand Total', styles['total_label'])
        for col, key in enumerate(keys, start=1):
            if col < first_sum:
                continue
            if key in sums:
                total = round(sum(record.get(key) or 0.0 for record in rows), 2)
                sheet.write_number(row, col, total, styles['total'])
            else:
                sheet.write_blank(row, col, None, styles['total'])
        sheet.freeze_panes(header_row + 1, 0)
        if rows:
            sheet.autofilter(header_row, 1, row - 1, last)
        return sheet
