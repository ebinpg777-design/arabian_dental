# -*- coding: utf-8 -*-
"""The Collected basis: an executive's incentive on the money their invoices brought in.

Client, 2026-11-23: "the uncleared PDCs should not be considered as payments. Also the
payments in previous months whose PDC is cleared in this month are to be considered as
payments of this month. The incentive report of each sales person should contain all the
details of payment and PDCs without any confusion."

A receipt counts in the month the money became the lab's:

* cash or a bank transfer: on the receipt's date;
* a cheque: on the day it cleared, whatever day it came in - received in October and
  cleared in November is November's money. One still in hand or at the bank is listed
  on the sheet and not counted; one cleared after the month is listed there with the
  month it counts in;
* a bounced cheque is listed in the month it bounced. If it had already cleared in an
  earlier month, where it was counted, it is taken back in the month it bounced.

What a receipt paid is read from the matching (the payment reconciled with the invoice,
as the lab's receipts are since 2026-10-27), not guessed by date. Only the executive's
share of an invoice counts - its product lines sold by them - and before tax, so the
figure is comparable with the billed amounts the slabs were written for.
"""
from collections import defaultdict

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.tools.misc import format_date

from .incentive_sheet import EXCLUDED_STATES

COLLECTION_STATUSES = [
    ('counted', 'Counted'),
    ('taken_back', 'Taken back'),
    ('pending', 'Cheque not cleared'),
    ('later', 'Clears in a later month'),
    ('bounced', 'Bounced'),
]
# the order the sheet lists them in: what counts first, then what does not and why
STATUS_SEQUENCE = {'counted': 1, 'taken_back': 2, 'pending': 3, 'later': 4, 'bounced': 5}
COLLECTION_MODES = [('cash', 'Cash'), ('bank', 'Bank'), ('cheque', 'Cheque')]


class LabIncentiveCollection(models.Model):
    """One receipt's part on one invoice, as a sheet saw it when it was computed.

    Kept on the sheet rather than read live, so an approved sheet keeps the receipts it
    was approved on - a cheque that bounces after the approval does not quietly change a
    figure someone signed off; it is taken back in the month it bounced.
    """
    _name = 'lab.incentive.collection'
    _description = 'Incentive Sheet Receipt'
    _order = 'sheet_id, sequence, counted_date, receipt_date, id'

    sheet_id = fields.Many2one('lab.incentive.sheet', required=True, ondelete='cascade',
                               index=True)
    sequence = fields.Integer(default=10)
    status = fields.Selection(COLLECTION_STATUSES, required=True, readonly=True)
    mode = fields.Selection(COLLECTION_MODES, string='Paid By', required=True, readonly=True)
    note = fields.Char('Why', readonly=True,
                       help="Why this receipt counts this month, or why it does not.")

    payment_id = fields.Many2one('account.payment', string='Payment', readonly=True)
    receipt_ref = fields.Char('Receipt', readonly=True)
    receipt_date = fields.Date('Received On', readonly=True)
    journal_name = fields.Char('Paid Into', readonly=True)

    cheque_id = fields.Many2one('lab.cheque', string='Cheque Register', readonly=True)
    cheque_number = fields.Char('Cheque No.', readonly=True)
    cheque_bank = fields.Char('Drawee Bank', readonly=True)
    cheque_date = fields.Date('Cheque Date', readonly=True)
    deposit_date = fields.Date('Deposited On', readonly=True)
    clear_date = fields.Date('Cleared On', readonly=True)
    bounce_date = fields.Date('Bounced On', readonly=True)

    invoice_id = fields.Many2one('account.move', string='Invoice', readonly=True)
    invoice_ref = fields.Char('Invoice No.', readonly=True)
    invoice_date = fields.Date(readonly=True)
    partner_id = fields.Many2one('res.partner', string='Clinic', readonly=True)

    amount = fields.Monetary(
        'Received', currency_field='currency_id', readonly=True,
        help="The part of the receipt that paid this invoice, tax included.")
    executive_amount = fields.Monetary(
        "Executive's Part", currency_field='currency_id', readonly=True,
        help="Of that, the part for this executive's lines on the invoice, before tax - "
             "what counts once the money is in.")
    counted_amount = fields.Monetary(
        'Counted', currency_field='currency_id', readonly=True,
        help="What this month's incentive is calculated on from this receipt: the "
             "executive's part when it counts, minus it when a cheque counted earlier "
             "bounced, nothing otherwise.")
    counted_date = fields.Date('Counted On', readonly=True)
    from_earlier_month = fields.Boolean(
        'Received Earlier', readonly=True,
        help="Received in an earlier month and counted in this one, because the cheque "
             "cleared in it.")
    currency_id = fields.Many2one(related='sheet_id.currency_id')

    def action_open_cheque(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'lab.cheque',
                'res_id': self.cheque_id.id, 'view_mode': 'form',
                'views': [(False, 'form')]}


class LabIncentiveSheet(models.Model):
    _inherit = 'lab.incentive.sheet'

    # ------------------------------------------------------------------ reading
    @api.model
    def _invoice_parts(self, invoice, executives=None):
        """{executive: product lines} of `invoice` that count for an executive: sold on
        their order, the order neither cancelled nor a rework (as on the billed basis)."""
        parts = defaultdict(lambda: self.env['account.move.line'])
        for line in invoice.invoice_line_ids:
            if line.display_type != 'product':
                continue
            order = line.sale_line_ids.order_id[:1]
            user = order.user_id
            if not user or order.state in EXCLUDED_STATES or order.is_rework:
                continue
            if executives is not None and user not in executives:
                continue
            parts[user] |= line
        return parts

    @api.model
    def _bounce_date(self, cheque):
        """The day `cheque` bounced: the date of the reversals the bounce booked; for a
        cheque that had nothing in the books, the day it was last changed."""
        reversals = cheque.move_ids.filtered(
            lambda m: m.reversed_entry_id and m.state == 'posted')
        if reversals:
            return max(reversals.mapped('date'))
        return fields.Date.to_date(fields.Datetime.context_timestamp(
            self, cheque.write_date)) if cheque.write_date else False

    @api.model
    def _cheque_was_cleared(self, cheque):
        if cheque.accounting == 'holding':
            return bool(cheque.deposit_move_id)
        return bool(cheque.clear_date and cheque.payment_id)

    @api.model
    def _collection_rows(self, company, period, executives=None):
        """{executive id: [row, ...]}: every receipt that reached an executive's invoices
        and matters to the month holding `period` - counted in it, or still waiting at its
        end, or bounced in it. `executives` (res.users) narrows it; None reads everyone.

        A row is the values of a lab.incentive.collection plus 'lines': [(invoice line,
        amount, quantity)] - the executive's lines' share of what was counted, signed.
        """
        first = fields.Date.to_date(period).replace(day=1)
        after = first + relativedelta(months=1)
        rows = defaultdict(list)
        currency = company.currency_id
        fmt = lambda day: format_date(self.env, day, date_format='d MMM y') if day else ''

        # -- receipts matched to an invoice
        receipt = 'credit_move_id.move_id.origin_payment_id.'
        domain = [
            ('debit_move_id.move_id.move_type', '=', 'out_invoice'),
            ('debit_move_id.move_id.state', '=', 'posted'),
            ('debit_move_id.move_id.company_id', '=', company.id),
            ('credit_move_id.move_id.origin_payment_id', '!=', False),
            ('credit_move_id.move_id.state', '=', 'posted'),
            '|', '|', '|',
            # cash and bank: on their own date
            '&', '&', (receipt + 'cheque_id', '=', False),
            ('credit_move_id.date', '>=', first), ('credit_move_id.date', '<', after),
            # a cheque that cleared in the month, received whenever
            '&', '&', (receipt + 'cheque_id.state', '=', 'cleared'),
            (receipt + 'cheque_id.clear_date', '>=', first),
            (receipt + 'cheque_id.clear_date', '<', after),
            # a cheque received by the month's end and still not cleared
            '&', (receipt + 'cheque_id.state', 'in', ('received', 'deposited')),
            ('credit_move_id.date', '<', after),
            # a cheque received by the month's end that cleared after it
            '&', '&', (receipt + 'cheque_id.state', '=', 'cleared'),
            (receipt + 'cheque_id.clear_date', '>=', after),
            ('credit_move_id.date', '<', after),
        ]
        if executives is not None:
            domain.append(('debit_move_id.move_id.invoice_line_ids.sale_line_ids.order_id.user_id',
                           'in', executives.ids))
        partials = self.env['account.partial.reconcile'].sudo().search(domain)
        for partial in partials:
            invoice = partial.debit_move_id.move_id
            payment = partial.credit_move_id.move_id.origin_payment_id
            cheque = payment.cheque_id
            received = partial.credit_move_id.date
            vals = self._receipt_vals(payment, received, cheque)
            if not cheque:
                vals.update(status='counted', counted_date=received)
            elif cheque.state == 'cleared' and cheque.clear_date and cheque.clear_date < after:
                earlier = received < first
                vals.update(status='counted', counted_date=cheque.clear_date,
                            from_earlier_month=earlier)
                if earlier:
                    vals['note'] = _("Received %(received)s, cleared %(cleared)s: counted "
                                     "this month, when it cleared.",
                                     received=fmt(received), cleared=fmt(cheque.clear_date))
                else:
                    vals['note'] = _("Cleared %s.", fmt(cheque.clear_date))
            elif cheque.state == 'cleared':
                vals.update(status='later', note=_(
                    "Cleared %(cleared)s: counted in %(month)s.", cleared=fmt(cheque.clear_date),
                    month=format_date(self.env, cheque.clear_date, date_format='MMMM y')))
            else:
                where = (_("deposited %s", fmt(cheque.deposit_date))
                         if cheque.state == 'deposited' else _("in hand"))
                vals.update(status='pending', note=_(
                    "Received %(received)s, %(where)s, not cleared yet: counted in the month "
                    "it clears.", received=fmt(received), where=where))
            self._add_rows(rows, vals, invoice, partial.amount, currency, executives)

        # -- bounced cheques: their matching is undone, so read what they were allocated to
        cheques = self.env['lab.cheque'].sudo().search([
            ('state', '=', 'bounced'), ('company_id', '=', company.id),
            ('received_date', '<', after), ('allocation_ids', '!=', False)])
        for cheque in cheques:
            bounced = self._bounce_date(cheque)
            cleared = self._cheque_was_cleared(cheque) and cheque.clear_date
            if not bounced or bounced < first:
                continue        # bounced before the month: nothing of it is this month's
            if cleared and cleared < first and bounced >= after:
                continue        # counted in its own month, taken back in a later one
            payment = cheque.payment_id
            vals = self._receipt_vals(payment, payment.date or cheque.received_date, cheque)
            vals['bounce_date'] = bounced
            if cleared and first <= cleared < after:
                if bounced < after:
                    vals.update(status='bounced', note=_(
                        "Cleared %(cleared)s and bounced %(bounced)s: not counted.",
                        cleared=fmt(cleared), bounced=fmt(bounced)))
                else:
                    vals.update(status='counted', counted_date=cleared, note=_(
                        "Cleared %(cleared)s, bounced later on %(bounced)s: taken back that "
                        "month.", cleared=fmt(cleared), bounced=fmt(bounced)))
            elif cleared and cleared < first:
                vals.update(status='taken_back', counted_date=bounced, note=_(
                    "Counted in %(month)s when it cleared (%(cleared)s); bounced %(bounced)s, "
                    "so it is taken back this month.",
                    month=format_date(self.env, cleared, date_format='MMMM y'),
                    cleared=fmt(cleared), bounced=fmt(bounced)))
            elif cleared:
                vals.update(status='later', note=_(
                    "Cleared %(cleared)s, then bounced %(bounced)s.",
                    cleared=fmt(cleared), bounced=fmt(bounced)))
            elif bounced < after:
                vals.update(status='bounced', note=_(
                    "Bounced %s before it cleared: never counted.", fmt(bounced)))
            else:
                vals.update(status='pending', note=_(
                    "Not cleared at the month's end; bounced later, on %s.", fmt(bounced)))
            for allocation in cheque.allocation_ids:
                invoice = allocation.move_id
                if invoice.state != 'posted' or invoice.move_type != 'out_invoice':
                    continue
                self._add_rows(rows, dict(vals), invoice, allocation.amount, currency, executives)
        return rows

    @api.model
    def _receipt_vals(self, payment, received, cheque):
        journal = payment.journal_id or cheque.journal_id
        vals = {
            'payment_id': payment.id,
            'receipt_ref': payment.name or payment.move_id.name or '',
            'receipt_date': received,
            'journal_name': journal.name or '',
            'mode': 'cheque' if cheque else ('cash' if journal.type == 'cash' else 'bank'),
            'note': '',
        }
        if cheque:
            vals.update({
                'cheque_id': cheque.id, 'cheque_number': cheque.cheque_number,
                'cheque_bank': cheque.bank_name or '', 'cheque_date': cheque.cheque_date,
                'deposit_date': cheque.deposit_date, 'clear_date': cheque.clear_date
                if cheque.state in ('cleared', 'bounced') else False,
            })
        return vals

    @api.model
    def _add_rows(self, rows, vals, invoice, amount, currency, executives):
        """One row per executive with lines on `invoice`, for `amount` of it received."""
        total = invoice.amount_total_signed
        if currency.is_zero(total) or currency.is_zero(amount):
            return
        ratio = amount / total
        sign = {'counted': 1, 'taken_back': -1}.get(vals['status'], 0)
        for user, lines in self._invoice_parts(invoice, executives).items():
            parts = [(line, currency.round(-line.balance * ratio), line.quantity * ratio)
                     for line in lines]
            share = sum(part for _line, part, _qty in parts)
            row = dict(vals, sequence=STATUS_SEQUENCE[vals['status']],
                       invoice_id=invoice.id, invoice_ref=invoice.name,
                       invoice_date=invoice.invoice_date,
                       partner_id=invoice.partner_id.id,
                       amount=currency.round(amount), executive_amount=share,
                       counted_amount=sign * share)
            row['lines'] = [(line, sign * part, sign * qty) for line, part, qty in parts] \
                if sign else []
            rows[user.id].append(row)

    # ------------------------------------------------------------------ the sheet's part
    def _collected_values(self, default_margin, rows=None):
        """[(collection vals, [line vals])] of this sheet on the Collected basis."""
        self.ensure_one()
        if rows is None:
            rows = self._collection_rows(self.company_id, self.period,
                                         self.executive_id)[self.executive_id.id]
        result = []
        for row in rows:
            row = dict(row)
            line_vals = []
            for aml, part, qty in row.pop('lines'):
                product = aml.product_id
                base = product._incentive_base(part, qty, default_margin) if product else part
                line_vals.append({
                    'sale_order_id': aml.sale_line_ids.order_id[:1].id,
                    'invoice_id': aml.move_id.id,
                    'invoice_date': aml.move_id.invoice_date,
                    'counted_date': row.get('counted_date'),
                    'product_id': product.id,
                    'quantity': qty,
                    'sale_amount': part,
                    'incentive_base': base,
                    'incentive_basis': product.incentive_basis or 'sale',
                    'used_incentive_price': bool(product) and product._uses_own_incentive_base(),
                })
            result.append((row, line_vals))
        return result

    @api.depends('collection_ids.status', 'collection_ids.counted_amount',
                 'collection_ids.executive_amount', 'collection_ids.mode',
                 'collection_ids.from_earlier_month', 'collection_ids.cheque_id')
    def _compute_collection_totals(self):
        for sheet in self:
            rows = sheet.collection_ids
            counted = rows.filtered(lambda r: r.status == 'counted')
            cheques = counted.filtered(lambda r: r.mode == 'cheque')
            waiting = rows.filtered(lambda r: r.status in ('pending', 'later'))
            bounced = rows.filtered(lambda r: r.status in ('bounced', 'taken_back'))
            sheet.collected_cash_bank = sum((counted - cheques).mapped('counted_amount'))
            sheet.collected_cheques = sum(cheques.mapped('counted_amount'))
            sheet.cheques_cleared_count = len(cheques.cheque_id)
            sheet.collected_earlier = sum(
                cheques.filtered('from_earlier_month').mapped('counted_amount'))
            sheet.cheques_waiting_amount = sum(waiting.mapped('executive_amount'))
            sheet.cheques_waiting_count = len(waiting.cheque_id)
            sheet.cheques_bounced_amount = sum(bounced.mapped('executive_amount'))
            sheet.cheques_bounced_count = len(bounced.cheque_id)
            sheet.taken_back_amount = -sum(
                rows.filtered(lambda r: r.status == 'taken_back').mapped('counted_amount'))
