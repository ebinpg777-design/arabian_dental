# -*- coding: utf-8 -*-
"""Finance Ops screens that name money owed, counted back.

lab_finance_ops cannot ask the countback itself - this module depends on it -
so the two places it read invoice residuals are corrected from here. On this
ledger receipts are journal entries that never reconcile, so a paid invoice
keeps its whole residual: the EOD page and the cheque allocation both saw money
owed that had long been paid. (2026-09-15)
"""
from odoo import _, models
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_is_zero


class LabEodReport(models.TransientModel):
    _inherit = 'lab.eod.report'

    def _fill_figures(self):
        super()._fill_figures()
        # sudo: the EOD menu is already confined to the cheque desk, and the
        # summary's own gate knows nothing of that group.
        summary = self.env['lab.collection.performance'].sudo().outstanding_summary(
            self.company_id)
        self.outstanding_total = summary['total']
        self.overdue_total = summary['overdue']


class LabCheque(models.Model):
    _inherit = 'lab.cheque'

    def action_load_open_invoices(self):
        """The clinic's invoices still open after the countback, oldest first."""
        self.ensure_one()
        if self.state != 'received':
            raise UserError(_("Invoices can only be allocated while the cheque is in hand."))
        commercial = self.partner_id.commercial_partner_id
        partner_ids = self.env['res.partner'].with_context(active_test=False).search(
            [('id', 'child_of', commercial.id)]).ids
        debits = self.env['lab.collection.performance'].sudo()._open_debits(
            self.company_id, partner_ids=partner_ids)
        open_by_move = {}
        for debit in debits:
            open_by_move[debit['move_id']] = open_by_move.get(debit['move_id'], 0.0) + debit['open']
        # Only invoices: an open opening-balance journal entry has no invoice
        # line to reconcile the cheque against.
        moves = self.env['account.move'].search([
            ('id', 'in', list(open_by_move)),
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'posted'),
        ], order='invoice_date, id')
        self.allocation_ids.unlink()
        remaining = self.amount
        lines = []
        rounding = self.currency_id.rounding or 0.01
        for move in moves:
            if float_is_zero(remaining, precision_rounding=rounding):
                break
            due = open_by_move[move.id]
            if float_compare(due, 0.0, precision_rounding=rounding) <= 0:
                continue
            take = min(due, remaining)
            lines.append((0, 0, {'move_id': move.id, 'amount': take}))
            remaining -= take
        if not lines:
            raise UserError(_("%s has no open invoices to allocate this cheque against.",
                              self.partner_id.display_name))
        self.allocation_ids = lines
        return True
