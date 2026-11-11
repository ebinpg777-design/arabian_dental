# Part of ebshel_gst_report. See LICENSE file for full copyright and licensing details.
"""The GSTR-1 register of a period as an Excel workbook.

Every section is built from the same list of invoice lines (`_lines`): one row per
product line of every posted customer invoice and credit note of the period, with
its tax split by l10n_in's own tax tags (`_l10n_in_prepare_tax_details`), in the
company currency. The sections only group those rows differently - by customer, by
invoice and rate, by place of supply, by HSN - so a figure can never differ between
two sheets of the same workbook.

The layout follows the register the lab filed from before (a sheet per section, the
section's title and period at the top, a Grand Total line), with numbers written as
numbers so Excel can add them.
"""
import io
from collections import OrderedDict, defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError

try:
    import xlsxwriter
except ImportError:  # pragma: no cover - declared in external_dependencies
    xlsxwriter = None

# GST treatments by section of the return
REGISTERED = ('regular', 'composition', 'uin_holders')
UNREGISTERED = ('unregistered', 'consumer')
EXPORTS = ('overseas', 'special_economic_zone', 'deemed_export')
B2CL_LIMIT = 250000.0          # an inter-state invoice above this is B2CL, not B2CS
NIL_TYPES = ('nil_rated', 'exempt', 'non_gst')

REPORT_TYPES = [
    ('b2b', 'B2B'), ('b2b_detailed', 'B2B Detailed'),
    ('b2cs', 'B2CS'), ('b2cs_detailed', 'B2CS Detailed'),
    ('hsn', 'HSN'), ('hsn_gst', 'HSN B2C-B2B'),
    ('exp', 'EXP'), ('cdnr', 'CDNR'), ('cdnur', 'CDNUR'),
    ('docs', 'Docs'), ('nil_rated', 'Nil Rated'), ('invcr', 'HSN Wise INV-CR'),
    ('tax_check', 'Tax Check'),
    ('all', 'All in one'),
]
ALL_SECTIONS = [code for code, _label in REPORT_TYPES if code != 'all']

TAX_HEADERS = ['Taxable \n Amount', 'Integrated Tax \n Amount', 'Central Tax \n Amount',
               'State Tax \n Amount', 'Cess \n Amount', 'Total Tax \n Amount']
TAX_KEYS = ['taxable', 'igst', 'cgst', 'sgst', 'cess', 'tax']


class GstReportWizard(models.TransientModel):
    _name = 'gst.report.wizard'
    _description = 'GSTR-1 Register'

    date_from = fields.Date(string='From', required=True, default=fields.Date.context_today)
    date_to = fields.Date(string='To', required=True, default=fields.Date.context_today)
    report_type = fields.Selection(REPORT_TYPES, string='Section', required=True, default='all')
    company_ids = fields.Many2many(
        'res.company', string='Companies', required=True,
        domain=lambda self: [('id', 'in', self.env.companies.ids)],
        default=lambda self: self.env.companies)
    summary = fields.Char(compute='_compute_summary')

    # ------------------------------------------------------------------ the documents
    def _domain(self, move_types=('out_invoice', 'out_refund'), states=('posted',)):
        self.ensure_one()
        return [
            ('move_type', 'in', list(move_types)),
            ('state', 'in', list(states)),
            ('invoice_date', '>=', self.date_from),
            ('invoice_date', '<=', self.date_to),
            ('company_id', 'in', self.company_ids.ids),
        ]

    def _moves(self):
        return self.env['account.move'].search(self._domain(), order='invoice_date, name, id')

    @api.depends('date_from', 'date_to', 'company_ids')
    def _compute_summary(self):
        for wizard in self:
            if not (wizard.date_from and wizard.date_to and wizard.company_ids):
                wizard.summary = False
                continue
            if wizard.date_from > wizard.date_to:
                wizard.summary = _("The period ends before it starts.")
                continue
            groups = self.env['account.move']._read_group(
                wizard._domain(), ['move_type'], ['__count'])
            counts = dict(groups)
            invoices, refunds = counts.get('out_invoice', 0), counts.get('out_refund', 0)
            if not invoices and not refunds:
                wizard.summary = _("No posted invoices or credit notes in this period.")
                continue
            wizard.summary = _("%(invoices)s invoice(s) and %(refunds)s credit note(s) in the period.",
                               invoices=invoices, refunds=refunds)

    def _check(self):
        self.ensure_one()
        if self.date_from > self.date_to:
            raise UserError(_("The period ends before it starts."))
        # inter / intra state is judged against the seller's state: one state per register
        states = self.company_ids.mapped('state_id')
        if len(states) != 1 or any(not company.state_id for company in self.company_ids):
            raise UserError(_("Select companies of one state with a state set on each: the "
                              "register tells inter-state from intra-state supplies by it."))
        if xlsxwriter is None:
            raise UserError(_("The Python package xlsxwriter is not installed."))

    def _home_state(self):
        return self.company_ids[:1].state_id

    # ------------------------------------------------------------------ the values
    @api.model
    def _customer_name(self, move):
        """The customer's name on the register. A hook: a database that keeps a
        separate billing name can return it instead."""
        return move.partner_id.commercial_partner_id.name or move.partner_id.name or ''

    @api.model
    def _klass(self, treatment):
        if treatment in UNREGISTERED:
            return 'B2C'
        if treatment == 'overseas':
            return 'Export'
        return 'B2B'

    @api.model
    def _taxes_of(self, line):
        """The line's taxes, group taxes opened up."""
        taxes = self.env['account.tax']
        for tax in line.tax_ids:
            taxes |= tax.children_tax_ids or tax
        return taxes

    @api.model
    def _supply_state(self, move):
        """The place of supply: the one on the invoice (Odoo sets it from the customer's
        state), else the customer's state for a document that has none."""
        return move.l10n_in_state_id or move.partner_id.commercial_partner_id.state_id

    @api.model
    def _place_of_supply(self, move):
        state = self._supply_state(move)
        if not state:
            return ''
        return '-'.join(part for part in (state.l10n_in_tin or '', state.name or '') if part)

    def _lines(self):
        """One dict per product line of the period's documents, taxes split."""
        home = self._home_state()
        rows = []
        for move in self._moves():
            details = move._l10n_in_prepare_tax_details()
            per_line = details['tax_details_per_record']
            refund = move.move_type == 'out_refund'
            treatment = move.l10n_in_gst_treatment or 'consumer'
            partner = move.partner_id.commercial_partner_id
            pos_state = self._supply_state(move)
            for line in move.invoice_line_ids.filtered(lambda l: l.display_type == 'product'):
                line_details = per_line.get(line, {})
                codes = move._get_l10n_in_tax_details_by_line_code(
                    line_details.get('tax_details', {}))

                def amount(*names):
                    return sum(abs(codes.get('%s_amount' % name, 0.0)) for name in names)

                igst = amount('igst', 'igst_rc')
                cgst = amount('cgst', 'cgst_rc')
                sgst = amount('sgst', 'sgst_rc')
                cess = amount('cess', 'cess_non_advol', 'state_cess', 'state_cess_non_advol')
                rate = (codes.get('igst_rate') or codes.get('igst_rc_rate')
                        or ((codes.get('cgst_rate') or codes.get('cgst_rc_rate') or 0.0)
                            + (codes.get('sgst_rate') or codes.get('sgst_rc_rate') or 0.0)))
                taxable = abs(line.balance)                   # company currency, after discount
                tax = igst + cgst + sgst + cess
                nil = next((t.l10n_in_tax_type for t in self._taxes_of(line)
                            if t.l10n_in_tax_type in NIL_TYPES), False)
                hsn = line.l10n_in_hsn_code or line.product_id.l10n_in_hsn_code or ''
                uom = line.product_uom_id
                rows.append({
                    'move': move, 'name': move.name, 'date': move.invoice_date,
                    'refund': refund, 'treatment': treatment, 'klass': self._klass(treatment),
                    'partner': self._customer_name(move),
                    'vat': partner.vat or move.partner_id.vat or '',
                    'pos': self._place_of_supply(move), 'pos_state': pos_state.name or '',
                    'intra': bool(pos_state) and pos_state == home,
                    'journal': move.journal_id,
                    'amount_total': abs(move.amount_total_signed),
                    'hsn': move._l10n_in_extract_digits(hsn) if hsn else '',
                    'description': line.product_id.product_tmpl_id.name or (line.name or '').split('\n')[0],
                    'uqc': (uom.l10n_in_code or '').split('-')[0] or uom.name or '',
                    'quantity': line.quantity or 0.0,
                    'rate': round(rate or 0.0, 2),
                    'taxable': round(taxable, 2), 'igst': round(igst, 2), 'cgst': round(cgst, 2),
                    'sgst': round(sgst, 2), 'cess': round(cess, 2), 'tax': round(tax, 2),
                    'total': round(taxable + tax, 2),
                    'nil': nil,
                })
        return rows

    # ------------------------------------------------------------------ the sections
    @staticmethod
    def _add(target, row, keys=TAX_KEYS + ['total'], sign=1):
        for key in keys:
            target[key] = round(target.get(key, 0.0) + sign * row[key], 2)

    def _grouped(self, rows, key_of, start, keys=TAX_KEYS + ['total'], sign_of=None):
        groups = OrderedDict()
        for row in rows:
            key = key_of(row)
            if key not in groups:
                groups[key] = start(row)
                groups[key]['_moves'] = set()
            self._add(groups[key], row, keys, sign_of(row) if sign_of else 1)
            groups[key]['_moves'].add(row['name'])
        for group in groups.values():
            group['count'] = len(group.pop('_moves'))
        # a group of free lines (nothing taxable, no tax) says nothing on a value sheet
        return [g for g in groups.values() if g.get('taxable') or g.get('tax') or g.get('quantity')]

    def _b2b(self, rows):
        rows = [r for r in rows if not r['refund'] and r['treatment'] in REGISTERED]
        out = self._grouped(rows, lambda r: (r['vat'], r['partner']),
                            lambda r: {'partner': r['partner'], 'vat': r['vat']})
        return sorted(out, key=lambda g: g['partner'].lower())

    def _b2b_detailed(self, rows):
        rows = [r for r in rows if not r['refund'] and r['treatment'] in REGISTERED]
        return self._grouped(rows, lambda r: (r['move'].id, r['rate']), lambda r: {
            'vat': r['vat'], 'partner': r['partner'], 'pos': r['pos'], 'name': r['name'],
            'date': r['date'], 'rate': r['rate']})

    def _b2cs_rows(self, rows):
        return [r for r in rows if not r['refund'] and r['treatment'] in UNREGISTERED
                and (r['intra'] or r['amount_total'] <= B2CL_LIMIT)]

    def _b2cs(self, rows):
        out = self._grouped(self._b2cs_rows(rows), lambda r: (r['pos_state'], r['rate']),
                            lambda r: {'pos_state': r['pos_state'], 'rate': r['rate']})
        return sorted(out, key=lambda g: (g['pos_state'], g['rate']))

    def _b2cs_detailed(self, rows):
        return self._grouped(self._b2cs_rows(rows), lambda r: (r['move'].id, r['rate']), lambda r: {
            'pos': r['pos'], 'partner': r['partner'], 'name': r['name'], 'date': r['date'],
            'rate': r['rate']})

    def _hsn(self, rows):
        rows = [r for r in rows if r['hsn']]
        out = self._grouped(rows, lambda r: (r['hsn'], r['uqc'], r['rate']), lambda r: {
            'hsn': r['hsn'], 'description': r['description'], 'uqc': r['uqc'], 'rate': r['rate'],
            'quantity': 0.0}, keys=TAX_KEYS + ['total', 'quantity'],
            sign_of=lambda r: -1 if r['refund'] else 1)
        return sorted(out, key=lambda g: (g['hsn'], g['uqc'], g['rate']))

    def _hsn_gst(self, rows):
        rows = [r for r in rows if r['hsn']]
        out = self._grouped(rows, lambda r: (r['hsn'], r['uqc'], r['rate'], r['klass']), lambda r: {
            'hsn': r['hsn'], 'uqc': r['uqc'], 'rate': r['rate'], 'klass': r['klass'], 'quantity': 0.0},
            keys=TAX_KEYS + ['total', 'quantity'], sign_of=lambda r: -1 if r['refund'] else 1)
        return sorted(out, key=lambda g: (g['klass'], g['hsn'], g['uqc'], g['rate']))

    def _exp(self, rows):
        rows = [r for r in rows if not r['refund'] and r['treatment'] in EXPORTS]
        return self._grouped(rows, lambda r: r['move'].id, lambda r: {
            'date': r['date'], 'partner': r['partner'], 'kind': 'Export Invoice', 'name': r['name'],
            'shipping_no': r['move'].l10n_in_shipping_bill_number or '',
            'shipping_date': r['move'].l10n_in_shipping_bill_date,
            'port': r['move'].l10n_in_shipping_port_code_id.code or ''})

    def _cdn(self, rows, treatments):
        rows = [r for r in rows if r['refund'] and r['treatment'] in treatments]
        return self._grouped(rows, lambda r: (r['move'].id, r['rate']), lambda r: {
            'date': r['date'], 'partner': r['partner'], 'vat': r['vat'], 'kind': 'Credit Note',
            'name': r['name'], 'rate': r['rate']})

    def _cdnr(self, rows):
        return self._cdn(rows, REGISTERED)

    def _cdnur(self, rows):
        return self._cdn(rows, UNREGISTERED + ('overseas',))

    def _invcr(self, rows):
        rows = [r for r in rows if r['hsn']]
        groups = OrderedDict()
        for r in rows:
            key = (r['hsn'], r['uqc'], r['rate'], r['klass'])
            g = groups.setdefault(key, {
                'hsn': r['hsn'], 'uqc': r['uqc'], 'rate': r['rate'], 'klass': r['klass'],
                'quantity': 0.0, 'total': 0.0, 'taxable': 0.0, 'tax': 0.0,
                'cr_quantity': 0.0, 'cr_total': 0.0, 'cr_taxable': 0.0, 'cr_tax': 0.0})
            prefix = 'cr_' if r['refund'] else ''
            for key_ in ('quantity', 'total', 'taxable', 'tax'):
                g[prefix + key_] = round(g[prefix + key_] + r[key_], 2)
        return sorted(groups.values(), key=lambda g: (g['klass'], g['hsn'], g['uqc'], g['rate']))

    def _tax_check(self, rows):
        """Documents whose tax does not fit their place of supply: IGST charged inside
        the seller's state, CGST/SGST charged outside it, or a line worth something that
        carries no tax at all (neither GST nor a nil-rated / exempt / non-GST tax), which
        the other sheets would show as a supply at 0 %. One row per document and kind."""
        out = OrderedDict()
        for r in rows:
            if r['treatment'] in EXPORTS:
                continue
            expected = 'CGST + SGST' if r['intra'] else 'IGST'
            if r['igst'] or r['cgst'] or r['sgst']:
                kind = 'IGST' if r['igst'] else 'CGST + SGST'
                if (kind == 'IGST') != r['intra']:
                    continue
            elif r['nil'] or not r['taxable']:
                continue
            else:
                kind = 'No tax'
                expected += ' or a nil-rated tax'
            g = out.setdefault((r['move'].id, kind), {
                'date': r['date'], 'name': r['name'], 'partner': r['partner'], 'vat': r['vat'],
                'pos': r['pos'], 'kind': kind, 'taxable': 0.0, 'tax': 0.0, 'total': 0.0,
                'expected': expected})
            self._add(g, r, ('taxable', 'tax', 'total'))
        return list(out.values())

    def _nil_rated(self, rows):
        """The 4 x 3 matrix: inter / intra x registered / unregistered, by nil-rated,
        exempt and non-GST supplies (invoices less credit notes)."""
        matrix = {}
        for who in ('registered', 'unregistered'):
            for where in ('inter', 'intra'):
                matrix[(where, who)] = {t: 0.0 for t in NIL_TYPES}
        for r in rows:
            if not r['nil']:
                continue
            if r['treatment'] in REGISTERED:
                who = 'registered'
            elif r['treatment'] in UNREGISTERED:
                who = 'unregistered'
            else:
                continue
            where = 'intra' if r['intra'] else 'inter'
            sign = -1 if r['refund'] else 1
            matrix[(where, who)][r['nil']] = round(matrix[(where, who)][r['nil']] + sign * r['taxable'], 2)
        return matrix

    def _docs(self):
        """The document series of the period, per journal: first and last number,
        how many were issued, how many of them cancelled."""
        moves = self.env['account.move'].search(
            self._domain(('out_invoice', 'out_refund', 'in_refund'), ('posted', 'cancel'))
            + [('name', '!=', '/')], order='journal_id, name')
        out = OrderedDict()
        for move in moves:
            key = (move.move_type, move.journal_id.id)
            g = out.setdefault(key, {'move_type': move.move_type, 'journal': move.journal_id.name,
                                     'first': move.name, 'last': move.name, 'total': 0, 'cancelled': 0})
            g['first'] = min(g['first'], move.name)
            g['last'] = max(g['last'], move.name)
            g['total'] += 1
            g['cancelled'] += move.state == 'cancel'
        for g in out.values():
            g['net'] = g['total'] - g['cancelled']
        return list(out.values())

    def _sections(self):
        """{code: rows} for the sections wanted, from one pass over the lines."""
        wanted = ALL_SECTIONS if self.report_type == 'all' else [self.report_type]
        rows = self._lines() if wanted != ['docs'] else []
        builders = {
            'b2b': self._b2b, 'b2b_detailed': self._b2b_detailed, 'b2cs': self._b2cs,
            'b2cs_detailed': self._b2cs_detailed, 'hsn': self._hsn, 'hsn_gst': self._hsn_gst,
            'exp': self._exp, 'cdnr': self._cdnr, 'cdnur': self._cdnur, 'invcr': self._invcr,
            'nil_rated': self._nil_rated, 'tax_check': self._tax_check,
        }
        out = OrderedDict()
        for code in wanted:
            out[code] = self._docs() if code == 'docs' else builders[code](rows)
        out['_vouchers'] = len({r['name'] for r in rows if r['hsn']})
        return out

    # ------------------------------------------------------------------ the download
    def action_download(self):
        self._check()
        return {
            'type': 'ir.actions.act_url',
            'url': '/gst_report/xlsx/%s' % self.id,
            'target': 'self',
        }

    def _filename(self):
        period = self.date_from.strftime('%d-%m-%Y')
        if self.date_to != self.date_from:
            period += ' to ' + self.date_to.strftime('%d-%m-%Y')
        return 'GST Report %s.xlsx' % period

    def _period(self):
        return '%s  to  %s' % (self.date_from.strftime('%d-%b-%Y'), self.date_to.strftime('%d-%b-%Y'))

    def _xlsx(self):
        """The workbook, as bytes."""
        self._check()
        sections = self._sections()
        vouchers = sections.pop('_vouchers')
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        base = {'font_name': 'Times New Roman', 'font_size': 10, 'valign': 'vcenter', 'border': 1}
        styles = {
            'title': workbook.add_format({'font_name': 'Times New Roman', 'font_size': 12,
                                          'bold': True, 'align': 'center', 'valign': 'vcenter'}),
            'period': workbook.add_format({'font_name': 'Times New Roman', 'font_size': 10,
                                           'align': 'center', 'valign': 'vcenter'}),
            'band': workbook.add_format(dict(base, bold=True, font_color='white', bg_color='#007acc',
                                             align='center')),
            'head': workbook.add_format(dict(base, bold=True, font_color='white', bg_color='#007acc',
                                             align='center', text_wrap=True)),
            'text': workbook.add_format(dict(base)),
            'center': workbook.add_format(dict(base, align='center')),
            'date': workbook.add_format(dict(base, align='center', num_format='dd-mmm-yyyy')),
            'number': workbook.add_format(dict(base, num_format='#,##0.00')),
            'qty': workbook.add_format(dict(base, num_format='#,##0.00')),
            'int': workbook.add_format(dict(base, align='center', num_format='0')),
            'total_label': workbook.add_format(dict(base, bold=True, font_color='white',
                                                    bg_color='#007acc', align='center')),
            'total': workbook.add_format(dict(base, bold=True, font_color='white',
                                              bg_color='#007acc', num_format='#,##0.00')),
        }
        period = self._period()
        for code, rows in sections.items():
            if code == 'docs':
                self._docs_sheet(workbook, styles, period, rows)
            elif code == 'nil_rated':
                self._nil_sheet(workbook, styles, period, rows)
            else:
                name, title, section, headers, keys, sums, ints = self._layout(code, vouchers)
                self._sheet(workbook, styles, name, title, section, period, headers, keys, sums, ints, rows)
        workbook.close()
        return output.getvalue()

    @api.model
    def _layout(self, code, vouchers):
        """Sheet name, title, section label, headers, keys, summed keys, integer keys."""
        register = 'GSTR-1  -  Voucher Register'
        summary = 'GSTR-1  -  HSN/SAC Summary'
        return {
            'b2b': ('B2B', register, 'B2B Invoices - 4A, 4B, 4C, 6B, 6C',
                    ['Particulars', 'GSTIN/UIN', 'Voucher \n Count'] + TAX_HEADERS + ['Invoice \n Amount'],
                    ['partner', 'vat', 'count'] + TAX_KEYS + ['total'], TAX_KEYS + ['total'], ['count']),
            'b2b_detailed': ('B2B Detailed', register, 'B2B Invoices - 4A, 4B, 4C, 6B, 6C',
                             ['GSTIN/UIN of Recipient', 'Receiver Name', 'Place Of Supply', 'Invoice Number',
                              'Invoice Date', 'Tax Rate'] + TAX_HEADERS + ['Invoice \n Amount'],
                             ['vat', 'partner', 'pos', 'name', 'date', 'rate'] + TAX_KEYS + ['total'],
                             TAX_KEYS + ['total'], []),
            'b2cs': ('B2CS', register, 'B2C(Small) Invoices - 7',
                     ['Particulars', 'Taxable \n Amount', 'Rate Of \n Tax', 'Integrated Tax \n Amount',
                      'Central Tax \n Amount', 'State Tax \n Amount', 'Cess \n Amount', 'Total Tax \n Amount'],
                     ['pos_state', 'taxable', 'rate', 'igst', 'cgst', 'sgst', 'cess', 'tax'],
                     ['taxable', 'igst', 'cgst', 'sgst', 'cess', 'tax'], []),
            'b2cs_detailed': ('B2CS Detailed', register, 'B2C(Small) Invoices - 7',
                              ['Place Of Supply', 'Particulars', 'Invoice Number', 'Invoice Date', 'Tax Rate']
                              + TAX_HEADERS + ['Invoice \n Amount'],
                              ['pos', 'partner', 'name', 'date', 'rate'] + TAX_KEYS + ['total'],
                              TAX_KEYS + ['total'], []),
            'hsn': ('HSN', summary, 'Voucher Count - %s' % vouchers,
                    ['HSN/SAC', 'Description', 'UQC', 'Total \n Quantity', 'Total \n Value', 'Tax Rate']
                    + TAX_HEADERS,
                    ['hsn', 'description', 'uqc', 'quantity', 'total', 'rate'] + TAX_KEYS,
                    ['quantity', 'total'] + TAX_KEYS, []),
            'hsn_gst': ('HSN B2C-B2B', 'GSTR-1  -  HSN GST Summary', 'Voucher Count - %s' % vouchers,
                        ['HSN/SAC', 'UQC', 'Total \n Quantity', 'Total \n Value', 'Tax Rate'] + TAX_HEADERS
                        + ['Gst Treatment'],
                        ['hsn', 'uqc', 'quantity', 'total', 'rate'] + TAX_KEYS + ['klass'],
                        ['quantity', 'total'] + TAX_KEYS, []),
            'exp': ('EXP', register, 'Exports Invoices - 6A',
                    ['Date', 'Particulars', 'Vch Type', 'Vch No.', 'Shipping \n No.', 'Shipping \n Date',
                     'Port \n Code'] + TAX_HEADERS + ['Invoice \n Amount'],
                    ['date', 'partner', 'kind', 'name', 'shipping_no', 'shipping_date', 'port']
                    + TAX_KEYS + ['total'], TAX_KEYS + ['total'], []),
            'cdnr': ('CDNR', register, 'Credit/Debit Notes(Registered) - 9B',
                     ['Date', 'Particulars', 'GSTIN/UIN', 'Vch Type', 'Vch No.', 'Tax Rate'] + TAX_HEADERS
                     + ['Invoice \n Amount'],
                     ['date', 'partner', 'vat', 'kind', 'name', 'rate'] + TAX_KEYS + ['total'],
                     TAX_KEYS + ['total'], []),
            'cdnur': ('CDNUR', register, 'Credit/Debit Notes(Unregistered) - 9B',
                      ['Date', 'Particulars', 'Vch Type', 'Vch No.', 'Tax Rate'] + TAX_HEADERS
                      + ['Invoice \n Amount'],
                      ['date', 'partner', 'kind', 'name', 'rate'] + TAX_KEYS + ['total'],
                      TAX_KEYS + ['total'], []),
            'tax_check': ('Tax Check', 'GSTR-1  -  Tax Check', 'Tax charged against the place of supply, and lines with no tax',
                          ['Date', 'Vch No.', 'Particulars', 'GSTIN/UIN', 'Place Of Supply', 'Tax Charged',
                           'Tax Expected', 'Taxable \n Amount', 'Total Tax \n Amount', 'Invoice \n Amount'],
                          ['date', 'name', 'partner', 'vat', 'pos', 'kind', 'expected', 'taxable', 'tax', 'total'],
                          ['taxable', 'tax', 'total'], []),
            'invcr': ('HSN INV-CR', 'GSTR-1  -  HSN Wise INV-CR', 'Invoices and Credit Notes',
                      ['HSN/SAC', 'UQC', 'Total \n Quantity', 'Total \n Value', 'Tax Rate',
                       'Taxable \n Amount', 'Total Tax \n Amount', 'Total \n Quantity(CR)',
                       'Total \n Value(CR)', 'Taxable \n Amount(CR)', 'Total Tax \n Amount(CR)',
                       'Gst Treatment'],
                      ['hsn', 'uqc', 'quantity', 'total', 'rate', 'taxable', 'tax', 'cr_quantity',
                       'cr_total', 'cr_taxable', 'cr_tax', 'klass'],
                      ['quantity', 'total', 'taxable', 'tax', 'cr_quantity', 'cr_total', 'cr_taxable',
                       'cr_tax'], []),
        }[code]

    @api.model
    def _head(self, sheet, styles, title, section, period, last):
        """Rows 4-6 of every sheet: the register's name, the period, the section."""
        sheet.merge_range(3, 1, 3, last, title, styles['title'])
        sheet.merge_range(4, 1, 4, last, period, styles['period'])
        mid = max(2, last // 2)
        sheet.merge_range(5, 1, 5, mid, section, styles['band'])
        if mid < last:
            sheet.merge_range(5, mid + 1, 5, last, period, styles['band'])
        sheet.set_row(3, 25)
        sheet.set_row(4, 20)
        sheet.set_row(5, 22)

    @api.model
    def _sheet(self, workbook, styles, name, title, section, period, headers, keys, sums, ints, rows):
        sheet = workbook.add_worksheet(name)
        last = len(headers)                                   # columns B .. B+len-1
        self._head(sheet, styles, title, section, period, last)
        header_row = 6
        for col, header in enumerate(headers, start=1):
            sheet.write(header_row, col, header, styles['head'])
        sheet.set_row(header_row, 32)
        sheet.set_column(0, 0, 2)
        sheet.set_column(1, last, 15)
        if keys and keys[0] in ('partner', 'vat', 'date', 'hsn', 'pos'):
            sheet.set_column(1, 1, 22)
        if 'partner' in keys:
            sheet.set_column(keys.index('partner') + 1, keys.index('partner') + 1, 36)
        if 'description' in keys:
            sheet.set_column(keys.index('description') + 1, keys.index('description') + 1, 30)
        row = header_row + 1
        for record in rows:
            for col, key in enumerate(keys, start=1):
                value = record.get(key)
                if key in ints:
                    sheet.write_number(row, col, value or 0, styles['int'])
                elif key in sums or key == 'rate':
                    sheet.write_number(row, col, value or 0.0, styles['number'])
                elif key in ('date', 'shipping_date'):
                    if value:
                        sheet.write_datetime(row, col, fields.Date.to_date(value), styles['date'])
                    else:
                        sheet.write_blank(row, col, None, styles['center'])
                elif key in ('vat', 'hsn', 'uqc', 'name', 'pos', 'klass', 'kind', 'port', 'expected'):
                    # codes stay text: an HSN or a GSTIN must not turn into a number
                    sheet.write_string(row, col, str(value or ''), styles['center'])
                else:
                    sheet.write_string(row, col, str(value or ''), styles['text'])
            row += 1
        # Grand Total: the label runs up to the first summed column, never over it
        first_sum = min(keys.index(key) for key in list(sums) + list(ints)) + 1
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
            elif key in ints:
                sheet.write_number(row, col, sum(record.get(key) or 0 for record in rows), styles['total'])
            else:
                sheet.write_blank(row, col, None, styles['total'])
        sheet.freeze_panes(header_row + 1, 0)
        if rows:
            sheet.autofilter(header_row, 1, row - 1, last)
        return sheet

    @api.model
    def _docs_sheet(self, workbook, styles, period, groups):
        sheet = workbook.add_worksheet('DOCS')
        last = 6
        self._head(sheet, styles, 'GSTR-1  -  Document Summary', 'Documents issued', period, last)
        sheet.set_column(0, 0, 2)
        sheet.set_column(1, 1, 40)
        sheet.set_column(2, last, 16)
        sheet.merge_range(6, 1, 7, 1, 'Nature of Document', styles['head'])
        sheet.merge_range(6, 2, 6, 3, 'Serial No.', styles['head'])
        sheet.write(7, 2, 'From', styles['head'])
        sheet.write(7, 3, 'To', styles['head'])
        sheet.merge_range(6, 4, 7, 4, 'Total No.', styles['head'])
        sheet.merge_range(6, 5, 7, 5, 'Cancelled', styles['head'])
        sheet.merge_range(6, 6, 7, 6, 'Net Issued', styles['head'])
        row = 8
        natures = [('out_invoice', 'Invoices for outward supply (Sales)'),
                   ('out_refund', 'Credit Note'), ('in_refund', 'Debit Note')]
        for move_type, label in natures:
            sheet.merge_range(row, 1, row, last, label, styles['band'])
            row += 1
            mine = [g for g in groups if g['move_type'] == move_type]
            if not mine:
                sheet.write_string(row, 1, '  (none)', styles['text'])
                for col in range(2, last + 1):
                    sheet.write_blank(row, col, None, styles['text'])
                row += 1
            for g in mine:
                sheet.write_string(row, 1, '  ' + g['journal'], styles['text'])
                sheet.write_string(row, 2, g['first'], styles['center'])
                sheet.write_string(row, 3, g['last'], styles['center'])
                sheet.write_number(row, 4, g['total'], styles['int'])
                sheet.write_number(row, 5, g['cancelled'], styles['int'])
                sheet.write_number(row, 6, g['net'], styles['int'])
                row += 1
        sheet.merge_range(row, 1, row, 3, 'Grand Total', styles['total_label'])
        for col, key in ((4, 'total'), (5, 'cancelled'), (6, 'net')):
            sheet.write_number(row, col, sum(g[key] for g in groups), styles['total'])
        return sheet

    @api.model
    def _nil_sheet(self, workbook, styles, period, matrix):
        sheet = workbook.add_worksheet('Nil Rated')
        last = 4
        self._head(sheet, styles, 'GSTR-1  -  Voucher Register', 'NilRated Invoices - 8A, 8B, 8C, 8D',
                   period, last)
        sheet.set_column(0, 0, 2)
        sheet.set_column(1, 1, 44)
        sheet.set_column(2, last, 18)
        sheet.write(6, 1, 'Description', styles['head'])
        for col, label in enumerate(('Nil Rated', 'Exempted', 'Non GST'), start=2):
            sheet.write(6, col, label, styles['head'])
        sheet.set_row(6, 24)
        lines = [(('inter', 'registered'), 'Inter-State supplies to registered person'),
                 (('intra', 'registered'), 'Intra-State supplies to registered person'),
                 (('inter', 'unregistered'), 'Inter-State supplies to unregistered person'),
                 (('intra', 'unregistered'), 'Intra-State supplies to unregistered person')]
        row = 7
        for key, label in lines:
            sheet.write_string(row, 1, '  ' + label, styles['text'])
            for col, kind in enumerate(NIL_TYPES, start=2):
                sheet.write_number(row, col, matrix[key][kind], styles['number'])
            row += 1
        sheet.write(row, 1, 'Grand Total', styles['total_label'])
        for col, kind in enumerate(NIL_TYPES, start=2):
            sheet.write_number(row, col, round(sum(matrix[key][kind] for key, _l in lines), 2),
                               styles['total'])
        return sheet
