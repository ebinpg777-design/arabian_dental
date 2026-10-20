# -*- coding: utf-8 -*-
import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.misc import formatLang


class AccountJournal(models.Model):
    _inherit = "account.journal"

    # v17 dental_sale: a clinic with no GSTIN (consumer / unregistered) is invoiced in
    # B2C, a GST-registered one (regular / composition) in B2B - 20,302 invoices and
    # refunds since April 2026 followed it without exception. Any other treatment
    # (overseas, SEZ, ...) keeps Odoo's default journal.
    _LAB_JOURNAL_BY_GST_TREATMENT = {
        'consumer': 'B2C', 'unregistered': 'B2C',
        'regular': 'B2B', 'composition': 'B2B',
    }

    @api.model
    def _lab_gst_journal(self, treatment, company):
        """The B2B or B2C sales journal of `company` for a GST treatment; empty when the
        treatment has no series of its own or the journal does not exist."""
        code = self._LAB_JOURNAL_BY_GST_TREATMENT.get(treatment)
        if not code:
            return self.browse()
        return self.search([('code', '=', code), ('type', '=', 'sale'),
                            *self._check_company_domain(company)], limit=1)


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

    # -- B2B / B2C by GST treatment (v17 dental_sale, client 2026-10-20: "Invoice
    # sequences are not correct") ---------------------------------------------------
    # Without it Odoo 20 numbered new invoices in the first sales journal, which on the
    # live book was Opening Invoices (OB/2026/00001).
    @api.onchange('l10n_in_gst_treatment', 'partner_id')
    def _onchange_gst_treatment_lab_journal(self):
        # only while it has never been numbered: Odoo refuses to move a posted-once
        # invoice to another journal
        if (self.state != 'draft' or self.posted_before or (self.name and self.name != '/')
                or not self.is_sale_document(include_receipts=False)):
            return
        journal = self.env['account.journal']._lab_gst_journal(
            self.l10n_in_gst_treatment, self.company_id)
        if journal:
            self.journal_id = journal

    def action_post(self):
        # v17 refused a customer invoice in the wrong series. Only a B2B / B2C mix-up is
        # refused here: an invoice deliberately put in another sales journal (Opening
        # Invoices holds the migrated opening drafts) still posts. The migration replays
        # the lab's history as it was and is not checked.
        if not self.env.context.get('migration_replay'):
            Journal = self.env['account.journal']
            for move in self.filtered(lambda m: m.move_type == 'out_invoice'):
                expected = Journal._lab_gst_journal(move.l10n_in_gst_treatment, move.company_id)
                other = Journal._lab_gst_journal(
                    'unregistered' if expected.code == 'B2B' else 'regular', move.company_id)
                if expected and other and move.journal_id == other:
                    raise ValidationError(_(
                        "Journal mismatch: %(invoice)s is for a clinic with GST treatment "
                        "\"%(treatment)s\", which is invoiced in the %(expected)s journal, not %(journal)s.",
                        invoice=move.display_name,
                        treatment=dict(move._fields['l10n_in_gst_treatment']._description_selection(self.env)).get(
                            move.l10n_in_gst_treatment, move.l10n_in_gst_treatment),
                        expected=expected.name, journal=move.journal_id.name))
        return super().action_post()

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

    def _dental_label(self, line):
        """What the print-out calls the line: the product, then any extra description.

        Odoo 20 no longer repeats the product name in `line.name` - it holds only the
        product's sales description, so it is blank for most of the lab's products and
        the invoice printed an empty Description column for every line made on Odoo 20
        (client, 2026-10-20). Migrated lines still carry the product name from v17
        (sometimes with a trailing space, or an old spelling behind the same [code]),
        so a name that already starts with the product is printed as it is.
        """
        name = (line.name or '').strip()
        product = line.product_id
        if not product:
            return name
        shown = product.display_name
        if not name:
            return shown
        first = name.splitlines()[0].strip()
        code = (product.default_code or '').strip()
        if first.lower() == shown.lower() or (code and first.startswith('[%s]' % code)):
            return name
        return shown + '\n' + name

    def _card_orders(self):
        """The orders these invoices bill - one authentication card each (client,
        2026-10-20). Read off the invoice lines; an invoice typed in by hand with
        only the order's name as its origin still finds it."""
        orders = self.env['sale.order']
        for move in self:
            found = move.invoice_line_ids.sale_line_ids.order_id
            if not found and move.invoice_origin:
                # the invoice's own partner: order numbers were not unique on the
                # migrated book, and a clinic is never billed another clinic's case
                names = [name.strip() for name in move.invoice_origin.split(',') if name.strip()]
                found = self.env['sale.order'].search([
                    ('name', 'in', names),
                    ('partner_id.commercial_partner_id', '=', move.commercial_partner_id.id),
                ])
            orders |= found
        return orders.sorted('name')

    def convert(self, amount, cur):
        """Amount in words in the Odoo-10 Indian 'X Rupees and Y Paise' format."""
        currency = self.env['res.currency'].search([('name', '=', cur)], limit=1) \
            or self.currency_id
        res = currency.amount_to_text(abs(amount)) or ''
        # Legacy Indian wording fallbacks (when the currency labels are not localised).
        res = res.replace('INR', 'Rupees').replace('Cents', 'Paise').replace('Cent', 'Paise')
        # match v10: always show the paise part. Case-blind: Odoo writes "fifty paise"
        # in lower case, and a case-sensitive test printed "Fifty Paise and Zero
        # Paise" on every amount with paise in it (found 2026-10-19). Odoo 20 says
        # "One Paisa" for a single paisa - matched on the stem, or 650.01 printed
        # "One Paisa and Zero Paise" (live invoice 20001, 2026-10-20).
        if res and 'pais' not in res.lower():
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

    # -- GST in columns, round off, old balance (client, 2026-10-19) ---------------
    # The lab's Odoo 17 invoice showed each line's GST beside it, the round-off on
    # its own row, and the clinic's old balance and total balance under the bill
    # amount. "GST should show in columns", "needs to show old balance and total
    # balance", "also round off".

    def _dental_gst_table(self):
        """(group names, {line id: {group name: (rate, amount)}}) for the line table.

        A line's GST is a group tax ("GST 5%") whose children post to separate tax
        groups (SGST, CGST - or IGST across states). Each child becomes a column.
        Computed with the company's rounding method, so with round-per-line (the lab's
        setting) the columns add up to the posted tax lines to the paisa.
        """
        self.ensure_one()
        groups, table = [], {}
        rounding = self.company_id.tax_calculation_rounding_method
        for line in self.invoice_line_ids.filtered(lambda l: l.display_type == 'product'):
            cells = {}
            if line.tax_ids:
                res = line.tax_ids.compute_all(
                    line.price_unit * (1 - (line.discount or 0.0) / 100.0),
                    currency=self.currency_id, quantity=line.quantity,
                    product=line.product_id, partner=self.partner_id,
                    is_refund=self.move_type in ('out_refund', 'in_refund'),
                    rounding_method=rounding)
                for data in res['taxes']:
                    tax = self.env['account.tax'].browse(data['id'])
                    name = tax.tax_group_id.name or tax.name
                    if name not in groups:
                        groups.append(name)
                    rate, amount = cells.get(name, (0.0, 0.0))
                    if tax.amount_type == 'percent':
                        rate += tax.amount
                    cells[name] = (rate, amount + data['amount'])
            table[line.id] = cells
        return groups, table

    # -- the lab's invoice numbers: B2C/2026-27/19892 (2026-10-19) -------------------
    # Odoo reads "2026-27" as a yearly sequence with the year "27" (the range check
    # wants both years written at the same length), so every invoice refused to be
    # re-posted after a reset to draft - "The Date isn't aligned with the existing
    # sequence number" - and a new invoice would have been numbered B2C/2026-26/....
    # A short end year is matched on its own length: 2026 -> 27 is one fiscal year on.
    def _validate_fiscalyear_difference(self, start_year, end_year):
        if len(end_year) < len(start_year):
            return self._truncate_year_to_length(int(start_year) + 1, len(end_year)) == int(end_year)
        return super()._validate_fiscalyear_difference(start_year, end_year)

    def _dental_column_totals(self):
        """The foot of the line table: quantity, taxable value, each GST column and the
        line totals summed (client, 2026-10-19: "add total in each column of item")."""
        self.ensure_one()
        names, table = self._dental_gst_table()
        lines = self.invoice_line_ids.filtered(lambda l: l.display_type == 'product')
        gst = {name: 0.0 for name in names}
        for line in lines:
            for name, (_rate, amount) in table.get(line.id, {}).items():
                gst[name] = gst.get(name, 0.0) + amount
        qty = sum(lines.mapped('quantity'))
        digits = self.env['account.move.line']._fields['quantity'].get_digits(self.env)
        return {
            'qty': qty,
            'qty_text': formatLang(self.env, qty, digits=digits[1] if digits else 2),
            'taxable': sum(lines.mapped('price_subtotal')),
            'gst': gst,
            'total': sum(lines.mapped('price_total')),
        }

    def _dental_words(self, amount):
        """The amount in words with every word capitalised but "and" - "Four Hundred
        Rupees and Zero Paise" (client, 2026-10-19). `convert` itself is left as it is
        for the other print-outs that use it."""
        self.ensure_one()
        return re.sub(r'\b[A-Za-z]+\b',
                      lambda m: 'and' if m.group().lower() == 'and' else m.group()[:1].upper() + m.group()[1:],
                      self.convert(amount, self.currency_id.name))

    def _dental_taxable(self):
        """What the lines charge before GST. Not `amount_untaxed`: Odoo counts the
        cash-rounding line in that, and the round-off has a row of its own."""
        self.ensure_one()
        return sum(self.invoice_line_ids.filtered(
            lambda l: l.display_type == 'product').mapped('price_subtotal'))

    def _dental_round_off(self):
        self.ensure_one()
        return self.currency_id.round(
            self.amount_total - self._dental_taxable() - self.amount_tax)

    def _dental_balances(self):
        """(old balance, total balance) of the clinic, as the lab's Odoo 17 invoice
        printed them: every posted receivable entry dated up to the invoice date,
        this invoice left out, then this invoice added on top. None on bills.

        The Odoo 17 version read only the partner's own receivable account and the
        invoice's own partner; the clinic's account is the commercial partner's, and
        migrated or hand-made entries may sit on another receivable account, so both
        are widened here.
        """
        self.ensure_one()
        if self.move_type not in ('out_invoice', 'out_refund'):
            return None
        date = self.invoice_date or self.date or fields.Date.context_today(self)
        [(old,)] = self.env['account.move.line'].sudo()._read_group([
            ('partner_id', 'child_of', self.commercial_partner_id.id),
            ('account_id.account_type', '=', 'asset_receivable'),
            ('parent_state', '=', 'posted'),
            ('company_id', '=', self.company_id.id),
            ('date', '<=', date),
            ('move_id', '!=', self.id or 0),
        ], aggregates=['balance:sum'])
        mine = sum(self.line_ids.filtered(
            lambda l: l.account_id.account_type == 'asset_receivable').mapped('balance'))
        return (old or 0.0, (old or 0.0) + mine)


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    patient = fields.Char(string='Patient')
    ul = fields.Selection(
        [('upper', 'U'), ('lower', 'L'), ('ul', 'UL')], string='Jaw')
    teeth = fields.Char('Teeth (FDI)')  # no size: 19 silently cut it at 64 characters
    work_number = fields.Char(string='Work Number')

    # The teeth as the lab's invoices always drew them: a Palmer cross, each tooth's
    # number in its quadrant (client, 2026-10-19, "the teeth map in the column").
    # Drawn as the dentist faces the patient: the patient's RIGHT on the left of the
    # cross. Numbers run outward from the midline, so the right-hand quadrants read
    # 8..1 towards the centre line and the left-hand ones 1..8 away from it. Milk
    # teeth (FDI 5x-8x) take Palmer's letters A-E.
    _DENTAL_PALMER_CELL = {1: 'ur', 2: 'ul', 3: 'll', 4: 'lr', 5: 'ur', 6: 'ul', 7: 'll', 8: 'lr'}

    def _dental_palmer(self):
        """{'ur', 'ul', 'lr', 'll': text, 'rest': text} for the cross, or None when the
        teeth text holds no FDI number at all (it is then printed as written).

        Anything that is not a tooth - a lone digit, a word - is kept in `rest` and
        printed under the cross, never dropped: the note is the lab's own writing.
        """
        self.ensure_one()
        cells = {'ur': [], 'ul': [], 'lr': [], 'll': []}
        rest = []
        for token in re.split(r'[\s,;/]+', (self.teeth or '').strip()):
            if not token:
                continue
            match = re.fullmatch(r'([1-8])([1-8])', token)
            quadrant, tooth = (int(match.group(1)), int(match.group(2))) if match else (0, 0)
            if not match or (quadrant >= 5 and tooth > 5):
                rest.append(token)
                continue
            cell = self._DENTAL_PALMER_CELL[quadrant]
            if (tooth, quadrant) not in cells[cell]:
                cells[cell].append((tooth, quadrant))
        if not any(cells.values()):
            return None
        result = {'rest': ' '.join(rest)}
        for cell, teeth in cells.items():
            teeth = sorted(teeth, reverse=cell in ('ur', 'lr'))
            result[cell] = ' '.join(
                'ABCDE'[tooth - 1] if quadrant >= 5 else str(tooth) for tooth, quadrant in teeth)
        return result
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
