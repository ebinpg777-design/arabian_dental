# -*- coding: utf-8 -*-
"""The open receivable, one row per item, as something you can actually open.

The Collections cards report open and overdue money from a FIFO countback (see
``collection_performance``): per clinic, receipts pay off receivable debits
oldest-first, and whatever the money has not reached is open. That figure is right -
it equals to the paisa what the clinics who owe actually owe - but clicking it used
to open a list of *documents*, and a document list cannot state it:

* ``amount_residual`` is meaningless here (4 reconciled lines out of 1,88,462), so
  the Amount Due column footed to 83.5 lakh against a card reading 99 lakh;
* the document Total footed to 2.32 crore, because an item the money has partly
  reached still shows its whole face value - 1,243 of the 2,719 owing clinics have
  such a boundary item;
* 1,485 of the open items are journal entries rather than invoices, so they showed
  a blank Invoice Date in an invoice-shaped list.

So the open items get their own rows, filled from the countback itself when a
figure is clicked. The list foots to the card that opened it, and the money can be
grouped by route, clinic or age like any other Odoo list.
(client, 2026-08-22: "check Open receivable, Overdue are calculated properly with
open items")

Why a TransientModel and not a SQL view: as a view, every list query wrapped the
countback in an outer filter, and Postgres re-planned the whole window aggregate
per query - a plain ``SELECT count(*) ... WHERE company_id = 1`` took 32 SECONDS
against 0.44 s for the view alone, and the list never finished loading. Filling
rows from the parameterised countback (~190 ms) and reading them back as an
ordinary table keeps the list instant. The rows are throw-away: Odoo vacuums them.
"""
from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError


class CollectionOpenItem(models.TransientModel):
    _name = 'lab.collection.open.item'
    _description = 'Open Receivable Item'
    _order = 'days desc, partner_id'
    _rec_name = 'move_id'

    move_id = fields.Many2one('account.move', string='Document', readonly=True,
                              index=True)
    move_type = fields.Selection(
        [('out_invoice', 'Invoice'), ('out_refund', 'Credit Note'),
         ('entry', 'Journal Entry')],
        string='Type', readonly=True,
        help="Whether Print Invoice below has anything to print - 1,485 of these "
             "items are opening-balance journal entries with no invoice behind "
             "them (see the module docstring).")
    patient = fields.Char(
        string='Patient', readonly=True,
        help="From the invoice's own patient_names - blank on a journal entry, "
             "which names no patient.")
    product_names = fields.Char(
        string='Products', readonly=True,
        help="What the item billed, from the invoice's own product_names.")
    partner_id = fields.Many2one('res.partner', string='Customer', readonly=True,
                                 index=True)
    team_id = fields.Many2one('crm.team', string='Sales Route', readonly=True)
    company_id = fields.Many2one('res.company', string='Company', readonly=True)
    currency_id = fields.Many2one('res.currency', readonly=True)
    description = fields.Char(
        string='Description', readonly=True,
        help="What the item is: the ledger line's own label, or the order it came "
             "from when the label only repeats the document number. On migrated "
             "opening balances this is the original invoice number.")
    date = fields.Date(string='Item Date', readonly=True,
                       help="The date the item is aged from - the document's own "
                            "accounting date.")
    days = fields.Integer(
        string='Age (days)', readonly=True,
        # AVERAGED, never summed. Grouped by customer — which is how this list now
        # opens — an integer column totals by default, and "13,126 days" across a
        # clinic's 206 open items is not a number that means anything. The average age
        # of what a clinic owes is. (client, 2026-08-27)
        aggregator='avg')
    open_amount = fields.Monetary(
        string='Open', readonly=True, currency_field='currency_id',
        help="What the clinic's receipts have not yet reached on this item. This "
             "is the figure the Collections cards add up.")
    billed_amount = fields.Monetary(
        string='Billed', readonly=True, currency_field='currency_id',
        help="The item's own value. Larger than Open where the clinic's payments "
             "have partly covered it.")

    # ------------------------------------------------------------------ printing
    def action_print_invoice(self):
        """The document itself, for whichever row is actually an invoice.

        1,485 of these items are opening-balance journal entries with no invoice
        behind them at all (see the module docstring) - printing one would mean
        printing a blank or nonsensical sheet, so those say so instead of trying.
        (client, 2026-08-29)
        """
        self.ensure_one()
        if not self.move_id or self.move_type not in ('out_invoice', 'out_refund'):
            raise UserError(_(
                "%s is not an invoice - it is a ledger entry with no document to "
                "print.", self.description or self.move_id.name or _('This item')))
        report = self.env.ref('sale_custom.action_report_dental_invoice')
        Perf = self.env['lab.collection.performance']
        try:
            self.move_id.check_access('read')
            readable = True
        except AccessError:
            readable = False
        if not readable:
            # Open receivable is reachable from My Day and the visit, and the
            # report reads the invoice as the person asking - a field executive
            # who cannot read it (no accounting role, not their own invoice) got
            # an access error. Rendered elevated for this one invoice, after the
            # same check the buttons that led here make. Anyone who CAN read it
            # keeps the ordinary report. (client, 2026-09-17)
            # Everything in this branch reads the invoice elevated - reading even
            # its name as the person asking is the error this branch avoids.
            move = self.move_id.sudo()
            if not Perf._partner_in_viewer_scope(move.commercial_partner_id):
                raise AccessError(_("That clinic is not on your round."))
            pdf, _kind = report.sudo()._render_qweb_pdf(report.report_name, move.ids)
            return Perf._pdf_download(pdf, _('Invoice - %s.pdf', move.name))
        return report.report_action(self.move_id)

    def action_print_statement(self):
        """This clinic's Statement of Account, for a period the reader chooses.

        It used to go straight to the PDF on the wizard's defaults (this month).
        That was one tap, but it answered only one question - and a clinic that
        stopped paying in June is not explained by June's own statement. The
        executive standing in front of the doctor needs to say "since April",
        so the wizard opens with the clinic already filled in and the cursor on
        the period; everything else keeps its default and Print is one click
        away. (client, 2026-09-01)
        """
        self.ensure_one()
        if not self.partner_id:
            raise UserError(_("This item has no customer to print a statement for."))
        Perf = self.env['lab.collection.performance']
        if not Perf._is_accounting_user():
            # The dialog opens for a field executive too: it is fixed to this
            # clinic, and the render runs elevated once the clinic is confirmed
            # to be on their round. (client, 2026-09-18)
            Perf._check_collections_access()
            if not Perf._partner_in_viewer_scope(self.partner_id.commercial_partner_id):
                raise UserError(_("That clinic is not on your round."))
            return self.env['epg.partner.statement.wizard'].open_for_partner(
                self.partner_id)
        # Every default is the wizard's own, so this is the SAME document the
        # accounts office prints from Accounting - "Statement of Account", the
        # full ledger for the period - and not the shorter "Open Items
        # Statement" this button used to force. A doctor being shown a
        # statement at the door and a doctor posted one from the office were
        # getting two different papers with two different headings and two
        # different totals, and only one of them was the statement anybody
        # meant. The clinic is still filled in, so it stays one click to
        # Print. (client, 2026-09-02)
        return self.env['epg.partner.statement.wizard'].open_for_partner(self.partner_id)
