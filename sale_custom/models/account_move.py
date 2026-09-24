# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.tools.misc import formatLang


class AccountMove(models.Model):
    _inherit = "account.move"

    comment = fields.Text(
        'Additional Information',
        default='We declare that this invoice shows the actual price of\n'
                'the goods described and that all particulars are true\n'
                'and correct.')
    invoice_signature = fields.Binary(
        'Invoice Signature', copy=False, related='create_uid.invoice_signature')

    # Every line of an invoice raised from one order carries the same patient, so the
    # name is worth showing once on the invoice itself — in the list, and on the
    # print-out in place of a Patient column repeating it down the page.
    # (client, 2026-08-18)
    patient_names = fields.Char(
        string='Patients', compute='_compute_patient_names', store=True,
        help="Patients named on the invoice lines, each listed once.")

    @api.depends('invoice_line_ids.patient')
    def _compute_patient_names(self):
        for move in self:
            move.patient_names = ', '.join(move._dental_patients())

    # What the invoice is FOR, in one cell.
    #
    # The statement of account showed a sales order number in its description column,
    # which is the one reference a clinic does not keep: they know the appliance and
    # the patient. Stored and computed the same way as `patient_names` above, so the
    # statement reads it in the same query as the rest of the line rather than walking
    # into the invoice per row. (client, 2026-08-27)
    product_names = fields.Char(
        string='Items', compute='_compute_product_names', store=True,
        help="The works billed on this invoice, each named once.")

    @api.depends('invoice_line_ids.product_id')
    def _compute_product_names(self):
        for move in self:
            move.product_names = ', '.join(move._dental_items())

    def _dental_items(self):
        """The distinct products on this invoice, in the order they appear on it."""
        self.ensure_one()
        names = []
        for line in self.invoice_line_ids:
            # `display_type` is 'product' on a real INVOICE line — unlike a sale order
            # line, where it is False and only sections and notes carry a value. Testing
            # it for truthiness (which is what the sale-order version does, correctly
            # for its own model) skipped every line and left the column empty on every
            # new invoice. Only sections and notes are skipped here. (client, 2026-08-27)
            if line.display_type in ('line_section', 'line_note') or not line.product_id:
                continue
            name = (line.product_id.name or '').strip()
            if name and name not in names:
                names.append(name)
        return names

    def _dental_patients(self):
        """The distinct patients of this invoice, in the order they appear on it."""
        self.ensure_one()
        names = []
        for line in self.invoice_line_ids:
            name = (line.patient or '').strip()
            if name and name not in names:
                names.append(name)
        return names

    # -- Sales Route / Salesperson from the clinic (client, 2026-08-17) -------------
    # An invoice made from an order inherits the order's route and salesperson (sale
    # passes them explicitly). One typed by hand takes the route from the clinic and
    # the salesperson from that route's Team Leader; Odoo's own rule (partner's
    # salesperson, else current user) only applies when the route has no leader.
    def _lab_partner_route(self):
        self.ensure_one()
        return self.partner_id.team_id or self.partner_id.commercial_partner_id.team_id

    @api.depends('partner_id')
    def _compute_invoice_default_sale_person(self):
        # The `not move.invoice_user_id` guard alone is NOT enough: in a batch
        # recompute (a module upgrade re-registering this compute did it on
        # 2026-08-18) the field being computed reads as empty on every record, the
        # guard waves everything through, and the leader is stamped over 324 real
        # salespeople. So the guard checks the DATABASE, not the cache: a value
        # already on file is always kept.
        saved = {}
        stored = self.filtered(lambda m: isinstance(m.id, int))
        if stored:
            self.env.cr.execute(
                "SELECT id, invoice_user_id FROM account_move WHERE id IN %s",
                [tuple(stored.ids)])
            saved = dict(self.env.cr.fetchall())
        for move in self:
            if saved.get(move.id):
                move.invoice_user_id = saved[move.id]
                continue
            if move.is_sale_document(include_receipts=True) and move.partner_id \
                    and not move.invoice_user_id:
                leader = move._lab_partner_route().user_id
                if leader:
                    move.invoice_user_id = leader
                    continue
            super(AccountMove, move)._compute_invoice_default_sale_person()

    @api.depends('partner_id', 'invoice_user_id')
    def _compute_team_id(self):
        by_partner = self.filtered(
            lambda m: m.is_sale_document(include_receipts=True) and m._lab_partner_route())
        for move in by_partner:
            move.team_id = move._lab_partner_route()
        super(AccountMove, self - by_partner)._compute_team_id()

    def button_draft(self):
        """Reset to draft throws away the cached print-out.

        Posted invoices are rendered once and kept as an attachment; if the invoice is
        reopened, edited and re-posted, that stored copy would still show the OLD figures
        and get served to the customer. Drop it so the next print re-renders.
        """
        res = super().button_draft()
        names = []
        for move in self:
            key = (move.name or '').replace('/', '_')
            names += ['Tax Invoice - %s.pdf' % key, 'Tax Invoice np - %s.pdf' % key]
        if names:
            self.env['ir.attachment'].sudo().search([
                ('res_model', '=', 'account.move'), ('name', 'in', names),
            ]).unlink()
        return res

    def _get_name_invoice_report(self):
        """Use the Odoo-10 dental invoice layout for every invoice / bill / refund.
        In Odoo 10 the custom template REPLACED account.report_invoice_document, so
        it was the one and only invoice printout; the v19 dispatch picks the report
        template by this method, so returning ours here makes the standard Print,
        Send & Print and portal all render the dental layout. Depending on l10n_in
        guarantees this override wins over l10n_in's own (which returns before super)."""
        self.ensure_one()
        if self.move_type in ('out_invoice', 'out_refund', 'in_invoice', 'in_refund'):
            return 'sale_custom.report_dental_invoice_document'
        return super()._get_name_invoice_report()

    def convert(self, amount, cur):
        """Amount in words in the Odoo-10 Indian 'X Rupees and Y Paise' format."""
        currency = self.env['res.currency'].search([('name', '=', cur)], limit=1) \
            or self.currency_id
        res = currency.amount_to_text(abs(amount)) or ''
        # Legacy Indian wording fallbacks (when the currency labels are not localised).
        res = res.replace('INR', 'Rupees').replace('Cents', 'Paise').replace('Cent', 'Paise')
        if res and 'Paise' not in res:   # match v10: always show the paise part
            res += ' and Zero Paise'
        return res

    def _dental_hsn_summary(self):
        """GST HSN summary: one row per HSN code with taxable value and tax split.

        A B2B tax invoice in India is expected to carry this table; building it from the
        posted tax lines (rather than re-deriving rates) keeps it equal to the totals.
        """
        self.ensure_one()
        rows = {}
        for line in self.invoice_line_ids.filtered(lambda l: l.display_type == 'product'):
            hsn = (line.product_id.hsn_number or '').strip() if 'hsn_number' in line.product_id._fields else ''
            key = hsn or '-'
            row = rows.setdefault(key, {'hsn': key, 'taxable': 0.0, 'tax': 0.0, 'rates': set()})
            row['taxable'] += line.price_subtotal
            for tax in line.tax_ids:
                row['rates'].add(tax.amount)
        # tax amount per HSN, prorated on the taxable value (invoices here use one rate set)
        total_taxable = sum(r['taxable'] for r in rows.values())
        total_tax = sum(abs(l.amount_currency) for l in self._dental_tax_lines())
        for row in rows.values():
            row['tax'] = (row['taxable'] / total_taxable * total_tax) if total_taxable else 0.0
            row['rate'] = sum(row['rates'])
            row['total'] = row['taxable'] + row['tax']
        return sorted(rows.values(), key=lambda r: r['hsn'])

    def _dental_money(self, amount):
        """Money the way the approved invoice design prints it: symbol first (₹ 1,234.00).

        The INR currency in this database is configured symbol-after for the UI; only the
        print-out is meant to lead with the symbol, so format here instead of touching
        res.currency and changing every screen.
        """
        self.ensure_one()
        currency = self.currency_id or self.company_id.currency_id
        number = formatLang(self.env, amount or 0.0, digits=currency.decimal_places)
        return u'%s\u00a0%s' % (currency.symbol or '', number)

    def _dental_payment_state_label(self):
        """Short status for the stamp on the print-out."""
        self.ensure_one()
        if self.state == 'draft':
            return ('DRAFT', 'draft')
        if self.state == 'cancel':
            return ('CANCELLED', 'cancel')
        if self.payment_state in ('paid', 'in_payment'):
            return ('PAID', 'paid')
        if self.payment_state == 'partial':
            return ('PART PAID', 'partial')
        if self.payment_state == 'reversed':
            return ('REVERSED', 'cancel')
        if self.invoice_date_due and self.invoice_date_due < fields.Date.context_today(self):
            return ('OVERDUE', 'overdue')
        return ('', '')

    def _dental_tax_lines(self):
        self.ensure_one()
        return self.line_ids.filtered(lambda l: l.tax_line_id)

    def _dental_tax_groups(self):
        """[(group_name, amount), ...] in the invoice currency, for the totals block."""
        self.ensure_one()
        groups = {}

        order = []
        for line in self._dental_tax_lines():
            group = line.tax_line_id.tax_group_id
            if group.id not in groups:
                groups[group.id] = {'name': group.name, 'amount': 0.0}
                order.append(group.id)
            groups[group.id]['amount'] += abs(line.amount_currency)
        return [(groups[k]['name'], groups[k]['amount']) for k in order]


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    patient = fields.Char(string='Patient')
    ul = fields.Selection(
        [('upper', 'U'), ('lower', 'L'), ('ul', 'UL')], string='Jaw')
    teeth = fields.Char('Teeth (FDI)', size=64)
    work_number = fields.Char(string='Work Number')
    # The invoice's patient list, read from the move that already stores it.
    #
    # This used to be a STORED column on account_move_line with
    # `@api.depends('move_id.invoice_line_ids', 'move_id.invoice_line_ids.patient')`.
    # That dependency runs through the parent, so editing one line marked every
    # line of the move - tax and receivable lines included - dirty, and rewrote the
    # lot; across 2,44,000 lines it was a duplicate of a string the move already
    # holds in `patient_names`. Related and unstored, it costs nothing to keep in
    # step and cannot drift from the invoice. (2026-08-21)
    patient_name = fields.Char(
        string='Patients', related='move_id.patient_names', readonly=True)
