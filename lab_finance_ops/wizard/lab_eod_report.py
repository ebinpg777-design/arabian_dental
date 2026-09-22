# -*- coding: utf-8 -*-
from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models, _


class LabEodReport(models.TransientModel):
    """End-of-day consolidated report (gap 26).

    The Daily Sales entry (A9 / ``daily.sales``) is what a field officer submits.
    This is the other thing the proposal asks for and the lab does not have: what
    the *whole business* did today, in one page — registrations, dispatches,
    invoices, collections by mode and cheques taken in.
    """
    _name = 'lab.eod.report'
    _description = 'End-of-Day Report'

    date = fields.Date(required=True, default=fields.Date.context_today)
    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company, required=True)
    currency_id = fields.Many2one(related='company_id.currency_id', readonly=True)

    # --- sales -----------------------------------------------------------------
    registered_count = fields.Integer('Cases Registered', compute='_compute_figures')
    registered_amount = fields.Monetary(
        'Registered Value', compute='_compute_figures', currency_field='currency_id')
    confirmed_count = fields.Integer('Orders Confirmed', compute='_compute_figures')
    confirmed_amount = fields.Monetary(
        'Confirmed Value', compute='_compute_figures', currency_field='currency_id')
    urgent_count = fields.Integer('Urgent Cases', compute='_compute_figures')
    dispatched_count = fields.Integer('Cases Dispatched', compute='_compute_figures')

    # --- Invoicing ---------------------------------------------------------------
    invoice_count = fields.Integer('Invoices Raised', compute='_compute_figures')
    invoice_amount = fields.Monetary(
        'Invoiced', compute='_compute_figures', currency_field='currency_id')
    credit_note_count = fields.Integer('Credit Notes', compute='_compute_figures')
    credit_note_amount = fields.Monetary(
        'Credited', compute='_compute_figures', currency_field='currency_id')

    # --- collections -----------------------------------------------------------
    collection_total = fields.Monetary(
        'Total Collected', compute='_compute_figures', currency_field='currency_id')
    collection_cash = fields.Monetary(
        'Cash', compute='_compute_figures', currency_field='currency_id')
    collection_bank = fields.Monetary(
        'Bank / Online', compute='_compute_figures', currency_field='currency_id')
    cheque_count = fields.Integer('Cheques Received', compute='_compute_figures')
    cheque_amount = fields.Monetary(
        'Cheque Value', compute='_compute_figures', currency_field='currency_id')
    cheque_cleared_amount = fields.Monetary(
        'Cheques Cleared', compute='_compute_figures', currency_field='currency_id')

    # --- receivables -----------------------------------------------------------
    outstanding_total = fields.Monetary(
        'Total Outstanding', compute='_compute_figures', currency_field='currency_id')
    overdue_total = fields.Monetary(
        'Overdue', compute='_compute_figures', currency_field='currency_id')

    @api.depends('date', 'company_id')
    def _compute_figures(self):
        for report in self:
            report._fill_figures()

    def _lab_tz(self):
        """Context tz, then the user's, then the company's, then Kolkata - 129 of
        156 users here have no tz, so the user's alone decides nothing."""
        name = (self.env.context.get('tz') or self.env.user.tz
                or self.company_id.partner_id.tz or 'Asia/Kolkata')
        try:
            return pytz.timezone(name)
        except pytz.UnknownTimeZoneError:
            return pytz.timezone('Asia/Kolkata')

    def _day_bounds(self):
        """The lab's day as a naive-UTC [start, end) pair for datetime columns.

        Midnight UTC is 05:30 here: the old bounds counted the first five and a
        half hours of the next morning into today and lost today's own.
        """
        self.ensure_one()
        tz = self._lab_tz()

        def utc(day):
            return tz.localize(datetime.combine(day, time.min)).astimezone(
                pytz.utc).replace(tzinfo=None)
        return utc(self.date), utc(self.date + timedelta(days=1))

    def _fill_figures(self):
        self.ensure_one()
        company = self.company_id
        day_start, day_end = self._day_bounds()
        base_company = [('company_id', '=', company.id)]

        Order = self.env['sale.order']
        registered = Order.search(base_company + [
            ('create_date', '>=', day_start), ('create_date', '<', day_end)])
        confirmed = Order.search(base_company + [
            ('state', 'in', ('sale', 'done')),
            ('date_order', '>=', day_start), ('date_order', '<', day_end)])
        self.registered_count = len(registered)
        self.registered_amount = sum(registered.mapped('amount_total'))
        self.confirmed_count = len(confirmed)
        self.confirmed_amount = sum(confirmed.mapped('amount_total'))
        self.urgent_count = len(registered.filtered(lambda o: o.priority == 'urgent'))

        # Dispatch is the outgoing transfer being validated — the lab's own
        # definition of "it left the building".
        self.dispatched_count = self.env['stock.picking'].search_count(base_company + [
            ('picking_type_code', '=', 'outgoing'), ('state', '=', 'done'),
            ('date_done', '>=', day_start), ('date_done', '<', day_end)])

        Move = self.env['account.move']
        invoices = Move.search(base_company + [
            ('move_type', '=', 'out_invoice'), ('state', '=', 'posted'),
            ('invoice_date', '=', self.date)])
        refunds = Move.search(base_company + [
            ('move_type', '=', 'out_refund'), ('state', '=', 'posted'),
            ('invoice_date', '=', self.date)])
        self.invoice_count = len(invoices)
        self.invoice_amount = sum(invoices.mapped('amount_total'))
        self.credit_note_count = len(refunds)
        self.credit_note_amount = sum(refunds.mapped('amount_total'))

        payments = self.env['account.payment'].search(base_company + [
            ('payment_type', '=', 'inbound'), ('partner_type', '=', 'customer'),
            ('state', 'in', ('in_process', 'paid')), ('date', '=', self.date)])
        self.collection_total = sum(payments.mapped('amount'))
        cash = payments.filtered(lambda p: p.journal_id.type == 'cash')
        self.collection_cash = sum(cash.mapped('amount'))
        self.collection_bank = self.collection_total - self.collection_cash

        Cheque = self.env['lab.cheque']
        received = Cheque.search(base_company + [
            ('received_date', '=', self.date), ('state', '!=', 'cancel')])
        cleared = Cheque.search(base_company + [('clear_date', '=', self.date)])
        self.cheque_count = len(received)
        self.cheque_amount = sum(received.mapped('amount'))
        self.cheque_cleared_amount = sum(cleared.mapped('amount'))

        outstanding = self.env['lab.outstanding.report'].search(base_company)
        self.outstanding_total = sum(outstanding.mapped('amount_residual'))
        self.overdue_total = sum(
            outstanding.filtered(lambda o: o.age_bucket != 'not_due').mapped('amount_residual'))

    def action_print(self):
        self.ensure_one()
        return self.env.ref('lab_finance_ops.action_report_lab_eod').report_action(self)

    def action_view_collections(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Collections — %s', self.date),
            'res_model': 'account.payment',
            'view_mode': 'list,form',
            'domain': [('company_id', '=', self.company_id.id),
                       ('payment_type', '=', 'inbound'),
                       ('partner_type', '=', 'customer'),
                       ('date', '=', self.date)],
        }
