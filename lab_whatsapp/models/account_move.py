# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import api, fields, models


class AccountMove(models.Model):
    """A posted customer invoice reaches the doctor with the PDF on it."""
    _name = 'account.move'
    _inherit = ['account.move', 'lab.whatsapp.mixin']

    whatsapp_reminder_date = fields.Datetime(
        'Last Payment Reminder', readonly=True, copy=False, index='btree_not_null',
        help="When the overdue-payment reminder cron last picked up this invoice. "
             "Stamped on every attempt, including one that could not be delivered, so "
             "an unreachable doctor does not hold up the rest of the queue.")

    def _whatsapp_suggested_event(self):
        """A posted invoice sends the invoice; an overdue one, the reminder."""
        if self.state != 'posted' or self.move_type != 'out_invoice':
            return 'manual'
        if self.payment_state in ('not_paid', 'partial') and self.invoice_date_due \
                and self.invoice_date_due < fields.Date.context_today(self):
            return 'reminder'
        return 'invoice'

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        posted.filtered(lambda m: m.move_type == 'out_invoice') \
            ._whatsapp_send_event('invoice')
        return posted

    def action_whatsapp_reminder(self):
        """Chase an unpaid invoice, on demand from the invoice itself."""
        self.filtered(lambda m: m.payment_state != 'paid') \
            ._whatsapp_send_event('reminder')
        return True

    def _reminder_setting(self, key, default):
        try:
            return int(self.env['ir.config_parameter'].sudo().get_param(
                'lab_whatsapp.%s' % key, default))
        except (TypeError, ValueError):
            return default

    @api.model
    def _cron_whatsapp_payment_reminders(self):
        """Chase the invoices that have been overdue long enough.

        Called by the `WhatsApp: Overdue Payment Reminders` cron. Three settings
        shape it: an invoice is only chased once it is `reminder_days` past its due
        date, it is not chased again for `reminder_throttle_days` afterwards, and at
        most `reminder_batch_size` invoices go out per run.

        The throttle is what keeps the channel usable — a doctor who gets a daily
        reminder stops reading them, and the lab loses the line it uses for dispatch
        notices too. The batch cap is what keeps the cron finishing: each reminder
        renders the invoice PDF, so an unbounded run over a full receivables ledger
        never completes. Oldest due date goes first, so a backlog drains in order
        across successive runs.
        """
        params = self.env['ir.config_parameter'].sudo()
        if params.get_param('lab_whatsapp.notify_reminder', 'True') not in ('True', 'true', '1'):
            return

        grace = self._reminder_setting('reminder_days', 3)
        throttle = max(self._reminder_setting('reminder_throttle_days', 5), 0)
        batch = max(self._reminder_setting('reminder_batch_size', 200), 0)
        if not batch:
            return

        now = fields.Datetime.now()
        since = now - timedelta(days=throttle)
        overdue = self.search([
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'posted'),
            ('payment_state', 'not in', ('paid', 'reversed', 'invoicing_legacy')),
            ('invoice_date_due', '!=', False),
            ('invoice_date_due', '<=', fields.Date.context_today(self) - timedelta(days=grace)),
            '|', ('whatsapp_reminder_date', '=', False),
                 ('whatsapp_reminder_date', '<', since),
        ], order='invoice_date_due, id', limit=batch)

        for invoice in overdue:
            # Stamped before the send, and for a send that fails too: a doctor whose
            # number we cannot reach must not sit at the head of the queue re-selected
            # on every run while the rest of the backlog waits behind them.
            invoice.whatsapp_reminder_date = now
            invoice._whatsapp_send_event('reminder')
