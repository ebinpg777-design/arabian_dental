# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class EpgWhatsappComposer(models.TransientModel):
    """Send from any record: pick a template (or none), read what will go, send it.

    The preview is not decoration. A template is written once and fired hundreds of
    times; seeing the resolved text against THIS record is the only moment anyone can
    catch a placeholder that silently resolved to nothing.

    On a WhatsApp app / Web sender the button opens WhatsApp with the text typed in,
    and the message is logged on the record once it is confirmed sent. A template is
    optional there: a quick note to a doctor needs no template. (client, 2026-09-17)
    """
    _name = 'epg.whatsapp.composer'
    _description = 'Send WhatsApp'

    res_model = fields.Char(required=True)
    res_id = fields.Integer(required=True)
    template_id = fields.Many2one(
        'epg.whatsapp.template', string='Template',
        domain="[('model', '=', res_model)]")
    account_id = fields.Many2one('epg.whatsapp.account', string='Send From',
                                 compute='_compute_account', store=True, readonly=False)
    channel = fields.Selection(related='account_id.channel')
    number = fields.Char('To', compute='_compute_preview', readonly=False, store=True)
    body = fields.Text('Message', compute='_compute_preview', readonly=False, store=True)
    record_name = fields.Char(compute='_compute_record_name')
    snippet_id = fields.Many2one(
        'epg.whatsapp.snippet', string='Saved Reply',
        domain="['|', ('model', '=', False), ('model', '=', res_model)]",
        help="Drops the saved text in, with the record's values filled in.")

    @api.onchange('snippet_id')
    def _onchange_snippet(self):
        if self.snippet_id:
            text = self.snippet_id.render(self.res_model, self.res_id)
            self.body = ('%s\n\n%s' % (self.body.rstrip(), text)) if (self.body or '').strip() \
                else text
            self.snippet_id = False

    @api.depends('template_id')
    def _compute_account(self):
        Account = self.env['epg.whatsapp.account']
        for wizard in self:
            wizard.account_id = wizard.template_id.account_id or wizard.account_id \
                or Account._default_account()

    def _compute_record_name(self):
        for wizard in self:
            record = wizard._record()
            wizard.record_name = record.display_name if record else ''

    @api.depends('template_id')
    def _compute_preview(self):
        for wizard in self:
            record = wizard._record()
            if not record:
                wizard.number = wizard.number or ''
                wizard.body = wizard.body or ''
                continue
            if wizard.template_id:
                wizard.number = wizard.template_id.phone_for(record)
                wizard.body = wizard.template_id.render(record)
            else:
                wizard.number = wizard.number or wizard._default_number(record)
                wizard.body = wizard.body or ''

    def _default_number(self, record):
        """With no template: the record's WhatsApp number, never its phone.

        A clinic's phone is usually a landline, and a WhatsApp chat opened on a
        landline is lost without an error. (client, 2026-09-15)
        """
        Template = self.env['epg.whatsapp.template']
        for path in ('whatsapp_number', 'partner_id.whatsapp_number'):
            ok, _why = Template._check_path(record._name, path)
            if ok:
                number = Template._normalise_number(Template._resolve(record, path), record)
                if number:
                    return number
        return ''

    def _record(self):
        self.ensure_one()
        if not (self.res_model and self.res_id) or self.res_model not in self.env:
            return None
        return self.env[self.res_model].browse(self.res_id).exists()

    def _create_message(self):
        self.ensure_one()
        if not self.number:
            raise UserError(_(
                "There is no WhatsApp number to send to. Add one to the contact, or "
                "type it here."))
        if not (self.body or '').strip():
            raise UserError(_("Write the message first."))
        account = self.account_id or self.template_id.account_id or \
            self.env['epg.whatsapp.account']._default_account()
        if not account:
            raise UserError(_(
                "No WhatsApp sender is configured. Add one under WhatsApp → "
                "Configuration → Senders."))
        record = self._record()
        partner = False
        if record is not None:
            if record._name == 'res.partner':
                partner = record.id
            elif 'partner_id' in record._fields and record.partner_id:
                partner = record.partner_id.id
        number = self.env['epg.whatsapp.template']._normalise_number(self.number, record)
        message = self.env['epg.whatsapp.message'].create({
            'account_id': account.id,
            'template_id': self.template_id.id or False,
            'res_model': self.res_model,
            'res_id': self.res_id,
            'partner_id': partner,
            'number': number,
            'body': self.body,
            'user_id': self.env.user.id,
        })
        if record is not None and self.template_id:
            message._attach_report(record)
        return message

    def action_send(self):
        """Cloud API: send now. WhatsApp app / Web: open the send dialog."""
        self.ensure_one()
        message = self._create_message()
        message.action_send()
        if message.channel == 'link':
            if message.state == 'cancel':
                raise UserError(message.error or _("This message may not be sent."))
            return {'type': 'ir.actions.client', 'tag': 'epg_whatsapp_send',
                    'params': {'message_id': message.id, 'close': True}}
        return {'type': 'ir.actions.act_window_close'}

    def action_queue(self):
        """WhatsApp app / Web: leave it on the Desk to send later."""
        self.ensure_one()
        message = self._create_message()
        message.action_send()
        if message.state == 'cancel':
            raise UserError(message.error or _("This message may not be sent."))
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'type': 'success', 'title': _("On the WhatsApp Desk"),
                       'message': _("The message to %s is waiting to be sent.",
                                    message.partner_id.display_name or message.number),
                       'next': {'type': 'ir.actions.act_window_close'}},
        }
