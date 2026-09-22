# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    wa_account_id = fields.Many2one(
        'epg.whatsapp.account', string='Default WhatsApp Sender',
        config_parameter='lab_whatsapp.default_account_id')
    wa_notify_order_confirm = fields.Boolean(
        'Notify on Case Registered', default=True,
        config_parameter='lab_whatsapp.notify_order_confirm')
    wa_notify_dispatch = fields.Boolean(
        'Notify on Dispatch', default=True,
        config_parameter='lab_whatsapp.notify_dispatch')
    wa_notify_invoice = fields.Boolean(
        'Notify on Invoice', default=True,
        config_parameter='lab_whatsapp.notify_invoice')
    wa_notify_payment = fields.Boolean(
        'Notify on Payment Received', default=True,
        config_parameter='lab_whatsapp.notify_payment')
    wa_notify_reminder = fields.Boolean(
        'Send Payment Reminders', default=True,
        config_parameter='lab_whatsapp.notify_reminder')
    wa_reminder_days = fields.Integer(
        'Start Reminder After (days overdue)', default=3,
        config_parameter='lab_whatsapp.reminder_days')
    wa_reminder_throttle_days = fields.Integer(
        'Re-remind Every (days)', default=5,
        config_parameter='lab_whatsapp.reminder_throttle_days')
    wa_reminder_batch_size = fields.Integer(
        'Reminders Per Run', default=200,
        help="How many overdue invoices the daily reminder cron chases in one run. "
             "Each reminder renders the invoice PDF, so a backlog is drained oldest "
             "first over successive runs rather than in one pass.",
        config_parameter='lab_whatsapp.reminder_batch_size')

    # An event switched on with no template behind it sends nothing, silently.
    # Named here, on the screen where the switch is. (client, 2026-08-28)
    wa_missing_templates = fields.Char(compute='_compute_wa_missing_templates')

    EVENT_MODELS = (
        ('order_confirm', 'sale.order', 'Case Registered'),
        ('dispatch', 'stock.picking', 'Dispatched'),
        ('invoice', 'account.move', 'Invoice Ready'),
        ('payment', 'account.payment', 'Payment Received'),
        ('reminder', 'account.move', 'Payment Reminder'),
    )

    @api.depends('wa_notify_order_confirm', 'wa_notify_dispatch', 'wa_notify_invoice',
                 'wa_notify_payment', 'wa_notify_reminder')
    def _compute_wa_missing_templates(self):
        Template = self.env['epg.whatsapp.template'].sudo()
        for settings in self:
            missing = []
            for event, model, label in self.EVENT_MODELS:
                if not settings['wa_notify_%s' % event]:
                    continue
                if not Template.search_count([('model', '=', model), ('event', '=', event)]):
                    missing.append(label)
            settings.wa_missing_templates = ', '.join(missing)

    def action_open_whatsapp_templates(self):
        return {
            'type': 'ir.actions.act_window', 'name': 'WhatsApp Templates',
            'res_model': 'epg.whatsapp.template', 'view_mode': 'list,form',
        }

    def action_open_whatsapp_accounts(self):
        return {
            'type': 'ir.actions.act_window', 'name': 'WhatsApp Senders',
            'res_model': 'epg.whatsapp.account', 'view_mode': 'list,form',
        }
