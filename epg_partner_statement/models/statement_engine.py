# -*- coding: utf-8 -*-
"""The statement, computed once and rendered many ways.

Everything - the PDF, the Excel workbook, the e-mail, the monthly job and the portal -
asks this model for the same data structure, so a figure on the PDF is by construction
the figure in the spreadsheet and on the portal.

Data comes from posted ``account.move.line`` on receivable / payable accounts - the same
lines behind Odoo's Partner Ledger - so the statement always agrees with the books.
"""
import base64
import io
from collections import defaultdict
from datetime import date, timedelta

from odoo import _, api, fields, models
from odoo.tools import date_utils
from odoo.tools.misc import format_amount, format_date

STATEMENT_TYPES = [
    ('receivable', 'Receivable (customer)'),
    ('payable', 'Payable (vendor)'),
    ('both', 'Receivable and Payable'),
]
ACCOUNT_TYPES = {
    'receivable': ('asset_receivable',),
    'payable': ('liability_payable',),
    'both': ('asset_receivable', 'liability_payable'),
}
PERIOD_PRESETS = [
    ('this_month', 'This month'),
    ('last_month', 'Last month'),
    ('this_quarter', 'This quarter'),
    ('fiscal_year', 'Financial year to date'),
    ('last_12', 'Last 12 months'),
    ('custom', 'Custom'),
]
AGEING_BUCKETS = [
    ('not_due', 'Not due', None, 0),
    ('d1_30', '1-30 days', 1, 30),
    ('d31_60', '31-60 days', 31, 60),
    ('d61_90', '61-90 days', 61, 90),
    ('d90p', '90+ days', 91, None),
]


class PartnerStatement(models.AbstractModel):
    _name = 'epg.partner.statement'
    _description = 'Partner Statement Engine'

    # ------------------------------------------------------------------ periods
    @api.model
    def period_dates(self, preset, company=None, today=None):
        """(date_from, date_to) for a preset. `custom` returns (None, None)."""
        today = today or fields.Date.context_today(self)
        company = company or self.env.company
        if preset == 'this_month':
            return date_utils.start_of(today, 'month'), date_utils.end_of(today, 'month')
        if preset == 'last_month':
            last = date_utils.start_of(today, 'month') - timedelta(days=1)
            return date_utils.start_of(last, 'month'), last
        if preset == 'this_quarter':
            return date_utils.start_of(today, 'quarter'), date_utils.end_of(today, 'quarter')
        if preset == 'fiscal_year':
            start, end = date_utils.get_fiscal_year(
                today, day=int(company.fiscalyear_last_day), month=int(company.fiscalyear_last_month))
            return start, today
        if preset == 'last_12':
            return today - timedelta(days=365), today
        return None, None

    # ------------------------------------------------------------------ core
    @api.model
    def _statement_accounts(self, options):
        """The accounts a statement is built from, as ids.

        Named explicitly rather than filtered through `account_id.account_type`:
        that path makes the planner join account_account and then probe
        account_move once per line - the same shape that cost the collections
        countback 5,70,000 buffer reads before it was measured out (2026-08-21).
        """
        return self.env['account.account'].sudo().search([
            ('account_type', 'in', ACCOUNT_TYPES[options['statement_type']]),
            ('company_ids', 'in', options['company'].id),
        ]).ids

    @api.model
    def _line_domain(self, partner, options, accounts=None):
        company = options['company']
        domain = [
            ('partner_id', 'in', partner.ids),
            ('parent_state', '=', 'posted'),
            ('account_id', 'in',
             accounts if accounts is not None else self._statement_accounts(options)),
            ('company_id', '=', company.id),
            # Filtered on the date the statement SHOWS, not the accounting date:
            # a migrated opening item carries its original document date and must
            # fall in the period it reads as. (client, 2026-08-27)
            ('statement_date', '<=', options['date_to']),
        ]
        if options.get('open_items_only'):
            domain += [('reconciled', '=', False)]
        domain += self._entered_domain(options)
        return domain

    @api.model
    def _entered_domain(self, options):
        """When the ENTRY WAS TYPED, as opposed to what it is dated.

        Two different questions live on this ledger. `statement_date` is the
        date the document claims; `create_date` on the move is the moment
        somebody (or a migration) actually put it into Odoo. They are years
        apart on migrated paper, and only the second one can answer "what has
        been entered since Friday" or "leave the import out of this".

        On the MOVE, not the line: an entry is created as a whole, the client
        asked for the journal entry's own stamp, and a line inherits its move's
        create_date anyway unless somebody edited the entry later - in which
        case the entry's stamp is still the honest one. (client, 2026-09-02)
        """
        domain = []
        if options.get('created_from'):
            domain.append(('move_id.create_date', '>=', options['created_from']))
        if options.get('created_to'):
            domain.append(('move_id.create_date', '<=', options['created_to']))
        return domain

    @api.model
    def _line_kind(self, line):
        move = line.move_id
        if move.move_type in ('out_invoice', 'in_invoice', 'out_receipt', 'in_receipt'):
            return _('Invoice')
        if move.move_type in ('out_refund', 'in_refund'):
            return _('Credit Note')
        if line.payment_id or ('origin_payment_id' in move._fields and move.origin_payment_id):
            return _('Payment')
        if 'statement_line_id' in move._fields and move.statement_line_id:
            return _('Bank')
        # receipts posted straight in a cash / bank journal (e.g. migrated ones) are payments too
        if line.journal_id.type in ('cash', 'bank'):
            return _('Payment')
        return _('Journal Entry')

    @api.model
    def compute(self, partners, options):
        """Build the statement for `partners`.

        options: statement_type, date_from, date_to, company (record), open_items_only,
                 show_ageing.
        Returns {partner_id: {'partner', 'blocks': [block], 'ageing': [...], 'has_data'}}
        where block = {'currency', 'opening', 'lines', 'closing', 'total_debit',
                       'total_credit', 'ageing'}.
        """
        company = options['company']
        date_from, date_to = options['date_from'], options['date_to']
        sign = -1 if options['statement_type'] == 'payable' else 1
        Line = self.env['account.move.line']
        has_patient = 'patient_name' in Line._fields
        # sale_custom's doing, and optional: the statement must still render on
        # a database without the dental layer installed.
        has_items = 'product_names' in self.env['account.move']._fields
        result = {}
        # Deliberately ONE SEARCH PER CLINIC, not one for the whole batch. Batching
        # was tried and measured 7.7x SLOWER on the real month-end set (251 clinics,
        # 97,737 receivable lines: 426 ms per-partner vs 3,281 ms batched). A single
        # `partner_id IN (251 ids)` with `ORDER BY date, id` sorts the whole set,
        # while each per-partner search is a small indexed read. Do not "optimise"
        # this into one query. (measured 2026-08-21)
        accounts = self._statement_accounts(options)
        for partner in partners:
            lines = Line.search(
                self._line_domain(partner, options, accounts),
                order='statement_date, date, id')
            buckets = {}
            for line in lines:
                if line.currency_id and line.currency_id != company.currency_id:
                    currency = line.currency_id
                    debit = line.amount_currency if line.amount_currency > 0 else 0.0
                    credit = -line.amount_currency if line.amount_currency < 0 else 0.0
                    residual = line.amount_residual_currency
                else:
                    currency = company.currency_id
                    debit, credit = line.debit, line.credit
                    residual = line.amount_residual
                bucket = buckets.setdefault(currency.id, {
                    'currency': currency, 'opening': 0.0, 'lines': [],
                    'total_debit': 0.0, 'total_credit': 0.0, 'credits_open': 0.0,
                    'open_count': 0, 'overdue_amount': 0.0,
                    'ageing': {key: 0.0 for key, *_ in AGEING_BUCKETS}})
                delta = sign * (debit - credit)
                # ageing on what is still open at the statement date, by due date.
                # Open *credits* (unallocated payments / credit notes) are kept apart so the
                # buckets only ever show what is owed; they net off in the total.
                if not line.reconciled and residual:
                    signed = sign * residual
                    if signed < 0:
                        bucket['credits_open'] += -signed
                    else:
                        due = line.date_maturity or line.date
                        overdue = (date_to - due).days
                        key = 'not_due'
                        for k, _label, lo, hi in AGEING_BUCKETS:
                            if lo is None:
                                continue
                            if overdue >= lo and (hi is None or overdue <= hi):
                                key = k
                        bucket['ageing'][key] += signed
                        bucket['open_count'] += 1
                        if overdue > 0:
                            bucket['overdue_amount'] += signed
                # Compared on the same date the filter and the sort use, or a line
                # could be listed in the period AND counted into the opening balance.
                shown_date = line.statement_date or line.date
                if shown_date < date_from and not options.get('open_items_only'):
                    bucket['opening'] += delta
                    continue
                if shown_date < date_from and options.get('open_items_only'):
                    pass  # open items ignore the period start: anything unpaid is listed
                move = line.move_id
                due = line.date_maturity or line.date
                origin = move.invoice_origin if 'invoice_origin' in move._fields else ''
                # WHAT THE INVOICE WAS FOR, first.
                #
                # A clinic reconciling a statement recognises the appliance and the
                # patient; the sales order number this column used to show is the one
                # reference they never keep. Falls back to the old label wherever there
                # are no items to name — opening balances, payments, journal entries.
                # (client, 2026-08-27)
                # THE NUMBER THE CLINIC KNOWS, in the Document column.
                #
                # Every item migrated from Odoo 10 sits on one journal entry per
                # clinic - MISC/26-27/04/4601 - and the number the clinic recognises,
                # their old invoice, is on the line itself. Printing the batch entry
                # made forty rows of a statement read as one document, and pushed the
                # real number into Description. The migrated lines are exactly the
                # ones carrying original_date. (client, 2026-09-18)
                old_number = (line.name or '') if (
                    line.original_date and line.name and line.name != move.name) else ''
                items = move.product_names if has_items else ''
                label = items or ''
                if not label:
                    own = line.name or ''
                    # Anything but the number now printed beside it, and never the
                    # document number over again.
                    label = own if own not in ('', move.name, old_number) else ''
                if not label and not old_number:
                    # receivable/payable lines often carry no label of their own: say
                    # something useful instead of repeating the document number
                    label = move.invoice_payment_ref if 'invoice_payment_ref' in move._fields and move.invoice_payment_ref else ''
                    # never repeat the Type column: leave it blank rather than say "Invoice"
                    label = label or origin or move.ref or ''
                bucket['lines'].append({
                    'date': line.date,
                    # what the statement SHOWS. An opening-balance line is dated the
                    # migration date, which tells the reader nothing; original_date
                    # carries the date the document had in Odoo 10. The accounting
                    # date stays in 'date' - only the presentation changes.
                    'doc_date': line.statement_date or line.date,
                    # What the reader looks for: their own document number. The
                    # journal entry it was booked on stays in 'entry'.
                    'move': old_number or move.name,
                    'entry': move.name,
                    'old_number': old_number,
                    'ref': line.ref or move.ref or origin or '',
                    'journal': line.journal_id.code,
                    'kind': self._line_kind(line),
                    'name': label,
                    'patient': line.patient_name if has_patient else '',
                    'due': due,
                    'days_overdue': max((date_to - due).days, 0) if residual and not line.reconciled else 0,
                    'debit': debit,
                    'credit': credit,
                    'delta': delta,
                    'residual': sign * residual if not line.reconciled else 0.0,
                    'paid': line.reconciled or not residual,
                })
                bucket['total_debit'] += debit
                bucket['total_credit'] += credit
            blocks = []
            for bucket in buckets.values():
                running = bucket['opening']
                for row in bucket['lines']:
                    running += row['delta']
                    row['balance'] = running
                bucket['closing'] = running
                bucket['ageing_rows'] = [
                    (label, bucket['ageing'][key], key != 'not_due') for key, label, *_ in AGEING_BUCKETS]
                bucket['ageing_gross'] = sum(bucket['ageing'].values())
                bucket['ageing_total'] = bucket['ageing_gross'] - bucket['credits_open']
                blocks.append(bucket)
            result[partner.id] = {
                'partner': partner,
                'address': self._address_lines(partner),
                'blocks': blocks,
                'has_data': any(b['lines'] or b['opening'] for b in blocks),
                'identifiers': self._partner_identifiers(partner),
            }
        return result

    @api.model
    def _address_lines(self, partner):
        """Address without repeating the partner's own name (QWeb's contact widget needs
        a dotted t-field path, which `o` - the partner itself - cannot provide)."""
        company_country = self.env.company.country_id
        parts = [
            partner.street, partner.street2,
            ' '.join(x for x in (partner.city, partner.state_id.name, partner.zip) if x),
            partner.country_id.name if partner.country_id and partner.country_id != company_country else '',
        ]
        return [p.strip() for p in parts if p and p.strip()]

    @api.model
    def _partner_identifiers(self, partner):
        """Statutory numbers to print under the address, when the fields exist."""
        rows = []
        # the route, so a page pulled out of a route-wise run says which route it is from
        route = partner.team_id if 'team_id' in partner._fields else False
        route = route or (partner.commercial_partner_id.team_id
                          if 'team_id' in partner.commercial_partner_id._fields else False)
        if route:
            rows.append((_('Sales Route'), route.name))
        for field, label in (('vat', _('Tax ID')), ('gst_number', 'GSTIN'), ('pan_number', 'PAN'),
                             ('dci_number', 'DCI No')):
            if field not in partner._fields:
                continue
            value = partner[field] or (partner.parent_id[field] if partner.parent_id else False)
            if value:
                rows.append((label, value))
        return rows

    # ------------------------------------------------------------------ wording
    @api.model
    def labels(self, statement_type):
        """What the money columns are called on a document a CUSTOMER reads.

        "Debit" and "Credit" are bookkeeping words: on a receivable statement the debits
        are what we charged and the credits are what they paid, so that is what the
        columns say. A payable statement (sent to a vendor) reads the other way round.
        """
        if statement_type == 'payable':
            return {'debit': _('Paid'), 'credit': _('Billed'),
                    'debit_total': _('Payments made'), 'credit_total': _('Bills received'),
                    'due': _('Amount payable'), 'open': _('Outstanding')}
        if statement_type == 'both':
            return {'debit': _('Charges'), 'credit': _('Payments'),
                    'debit_total': _('Charges'), 'credit_total': _('Payments'),
                    'due': _('Balance due'), 'open': _('Outstanding')}
        return {'debit': _('Charges'), 'credit': _('Payments & Credits'),
                'debit_total': _('Charges in period'), 'credit_total': _('Payments received'),
                'due': _('Amount due'), 'open': _('Outstanding')}

    # ------------------------------------------------------------------ helpers for renderers
    @api.model
    def normalize_options(self, data):
        """Options dict from wizard / URL / cron data (dates as strings or dates)."""
        company = self.env['res.company'].browse(data.get('company_id')) if data.get('company_id') else self.env.company
        date_from = fields.Date.to_date(data.get('date_from')) if data.get('date_from') else None
        date_to = fields.Date.to_date(data.get('date_to')) if data.get('date_to') else None
        preset = data.get('period') or 'custom'
        if preset != 'custom' or not (date_from and date_to):
            pf, pt = self.period_dates(preset if preset != 'custom' else 'this_month', company)
            date_from, date_to = date_from or pf, date_to or pt
        return {
            'statement_type': data.get('statement_type') or 'receivable',
            'date_from': date_from,
            'date_to': date_to,
            'company': company,
            'open_items_only': bool(data.get('open_items_only')),
            'show_ageing': data.get('show_ageing', True),
            # Optional, and None when absent: a falsy value must mean "no
            # bound", never "the epoch".
            'created_from': fields.Datetime.to_datetime(data['created_from'])
                            if data.get('created_from') else None,
            'created_to': fields.Datetime.to_datetime(data['created_to'])
                          if data.get('created_to') else None,
        }

    @api.model
    def layout_template(self):
        xmlid = self.env['ir.config_parameter'].sudo().get_param('epg_partner_statement.layout')
        if xmlid and self.env.ref(xmlid, raise_if_not_found=False):
            return xmlid
        return 'web.external_layout'

    @api.model
    def entered_data(self, options):
        """The entered-between bounds as report `data`, or nothing at all.

        Every path that hands options to a report goes through this, so a new
        caller cannot quietly drop the filter and print a document that
        disagrees with the screen it came from.
        """
        out = {}
        if options.get('created_from'):
            out['created_from'] = fields.Datetime.to_string(options['created_from'])
        if options.get('created_to'):
            out['created_to'] = fields.Datetime.to_string(options['created_to'])
        return out

    @api.model
    def render_pdf(self, partners, options):
        report = self.env.ref('epg_partner_statement.action_report_partner_statement')
        data = dict({
            'ids': partners.ids,
            'statement_type': options['statement_type'],
            'date_from': str(options['date_from']),
            'date_to': str(options['date_to']),
            'company_id': options['company'].id,
            'open_items_only': options['open_items_only'],
            'show_ageing': options['show_ageing'],
        }, **self.entered_data(options))
        pdf, _ext = report.sudo()._render_qweb_pdf(report.report_name, partners.ids, data=data)
        return pdf

    @api.model
    def render_xlsx(self, partners, options):
        import xlsxwriter  # bundled with Odoo's requirements
        statements = self.compute(partners, options)
        buf = io.BytesIO()
        wb = xlsxwriter.Workbook(buf, {'in_memory': True})
        bold = wb.add_format({'bold': True})
        head = wb.add_format({'bold': True, 'bg_color': '#DDDDDD', 'border': 1})
        money = wb.add_format({'num_format': '#,##0.00'})
        money_b = wb.add_format({'num_format': '#,##0.00', 'bold': True})
        datef = wb.add_format({'num_format': 'yyyy-mm-dd'})
        used = set()
        for partner in partners:
            st = statements[partner.id]
            title = ''.join(ch for ch in (partner.name or 'Statement')[:28] if ch not in '[]:*?/\\')
            name, n = title, 1
            while name.lower() in used:
                n += 1; name = f"{title[:25]} {n}"
            used.add(name.lower())
            ws = wb.add_worksheet(name)
            ws.set_column(0, 0, 11); ws.set_column(1, 2, 16); ws.set_column(3, 3, 8); ws.set_column(4, 4, 36)
            ws.set_column(5, 5, 11); ws.set_column(6, 9, 13)
            ws.write(0, 0, partner.display_name, bold)
            ws.write(1, 0, _('Statement %(type)s  %(start)s - %(end)s',
                             type=dict(STATEMENT_TYPES)[options['statement_type']],
                             start=format_date(self.env, options['date_from']), end=format_date(self.env, options['date_to'])))
            r = 3
            for block in st['blocks']:
                cur = block['currency'].name
                ws.write(r, 0, _('Currency: %s', cur), bold); r += 1
                lbl = self.labels(options['statement_type'])
                headers = [_('Date'), _('Document'), _('Reference'), _('Jrnl'), _('Description'), _('Due'),
                           lbl['debit'], lbl['credit'], _('Balance'), lbl['open']]
                for c, h in enumerate(headers):
                    ws.write(r, c, h, head)
                r += 1
                if not options['open_items_only']:
                    ws.write(r, 4, _('Opening balance'), bold); ws.write(r, 8, block['opening'], money_b); r += 1
                for l in block['lines']:
                    ws.write_datetime(r, 0, fields.Datetime.to_datetime(l['doc_date']), datef)
                    ws.write(r, 1, l['move']); ws.write(r, 2, l['ref']); ws.write(r, 3, l['journal'])
                    ws.write(r, 4, l['name'] + (f" ({l['patient']})" if l['patient'] else ''))
                    ws.write_datetime(r, 5, fields.Datetime.to_datetime(l['due']), datef)
                    ws.write(r, 6, l['debit'], money); ws.write(r, 7, l['credit'], money)
                    ws.write(r, 8, l['balance'], money); ws.write(r, 9, l['residual'], money)
                    r += 1
                ws.write(r, 4, lbl['due'], bold); ws.write(r, 8, block['closing'], money_b); r += 2
                if options.get('show_ageing'):
                    ws.write(r, 4, _('Ageing (open items by due date)'), bold); r += 1
                    for label, amount, _late in block['ageing_rows']:
                        ws.write(r, 4, label); ws.write(r, 8, amount, money); r += 1
                    if block['credits_open']:
                        ws.write(r, 4, _('Less: unallocated payments / credits')); ws.write(r, 8, -block['credits_open'], money); r += 1
                    ws.write(r, 4, _('Total open'), bold); ws.write(r, 8, block['ageing_total'], money_b); r += 2
            if not st['blocks']:
                ws.write(r, 0, _('No entries for this period.'))
        wb.close()
        return buf.getvalue()

    # ------------------------------------------------------------------ e-mail
    @api.model
    def send_by_email(self, partners, options, template=None):
        """E-mail each partner its statement (PDF attached); log in the chatter.

        Returns the partners actually mailed (those with an e-mail address).
        """
        template = template or self.env.ref('epg_partner_statement.mail_template_statement')
        sent = self.env['res.partner']
        for partner in partners:
            if not partner.email:
                continue
            pdf = self.render_pdf(partner, options)
            fname = _('Statement %(name)s %(start)s to %(end)s.pdf', name=(partner.name or '').replace('/', '-'),
                      start=options['date_from'], end=options['date_to'])
            attachment = self.env['ir.attachment'].create({
                'name': fname, 'type': 'binary', 'datas': base64.b64encode(pdf),
                'res_model': 'res.partner', 'res_id': partner.id, 'mimetype': 'application/pdf',
            })
            ctx = {
                'statement_from': format_date(self.env, options['date_from']),
                'statement_to': format_date(self.env, options['date_to']),
                'statement_type': dict(STATEMENT_TYPES)[options['statement_type']],
            }
            template.with_context(**ctx).send_mail(
                partner.id, force_send=False,
                email_values={'attachment_ids': [attachment.id]},
                email_layout_xmlid='mail.mail_notification_light')
            partner.message_post(
                body=_("Statement of account %(start)s to %(end)s e-mailed to %(email)s.",
                       start=ctx['statement_from'], end=ctx['statement_to'], email=partner.email),
                attachment_ids=[attachment.id], message_type='comment', subtype_xmlid='mail.mt_note')
            partner.sudo().write({'statement_last_sent': fields.Date.context_today(self)})
            sent |= partner
        return sent

    # ------------------------------------------------------------------ cron
    @api.model
    def _cron_auto_send(self):
        """Monthly: e-mail last month's statement to every partner that opted in."""
        params = self.env['ir.config_parameter'].sudo()
        day = int(params.get_param('epg_partner_statement.auto_send_day', '1') or 1)
        today = fields.Date.context_today(self)
        if today.day != day:
            return 0
        partners = self.env['res.partner'].search([
            ('statement_auto_send', '=', True), ('email', '!=', False),
            '|', ('statement_last_sent', '=', False), ('statement_last_sent', '<', date_utils.start_of(today, 'month'))])
        total = 0
        for company in self.env['res.company'].search([]):
            date_from, date_to = self.period_dates('last_month', company, today)
            for partner in partners:
                options = {
                    'statement_type': partner.statement_send_type or 'receivable',
                    'date_from': date_from, 'date_to': date_to, 'company': company,
                    'open_items_only': False, 'show_ageing': True,
                }
                st = self.with_company(company).compute(partner, options)[partner.id]
                if not st['has_data']:
                    continue
                total += len(self.with_company(company).send_by_email(partner, options))
        return total
