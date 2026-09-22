# -*- coding: utf-8 -*-
"""The questions the lab puts to a doctor, ready to send.

A case stops at the bench for the same handful of reasons - the details are
missing, the impression will not do, the shade never came, nobody said which way
to go. Each is a message somebody writes again every time, in a hurry, at the
counter. Here they are written once: pick the question, add the case number if it
helps, and it opens in WhatsApp. (client, 2026-09-18)
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class LabWhatsappEnquiry(models.TransientModel):
    _name = 'lab.whatsapp.enquiry'
    _description = 'Ask the doctor'

    partner_id = fields.Many2one('res.partner', required=True, readonly=True)
    template_id = fields.Many2one(
        'epg.whatsapp.template', string='The Question', required=True,
        domain="[('id', 'in', enquiry_ids)]")
    enquiry_ids = fields.Many2many('epg.whatsapp.template', compute='_compute_enquiries')
    about = fields.Char(
        'About', help="The case, the patient, the invoice - whatever names what you "
                      "are asking about. Added to the message as its first line.")
    preview = fields.Text(compute='_compute_preview', readonly=True)

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        enquiries = self.env['epg.whatsapp.template']._enquiries()
        if 'template_id' in fields_list and enquiries and not values.get('template_id'):
            values['template_id'] = enquiries[0].id
        return values

    def _compute_enquiries(self):
        enquiries = self.env['epg.whatsapp.template']._enquiries()
        for wizard in self:
            wizard.enquiry_ids = enquiries

    @api.depends('template_id', 'about', 'partner_id')
    def _compute_preview(self):
        for wizard in self:
            wizard.preview = wizard._body() if wizard.template_id else ''

    def _body(self):
        """The message as it will go: the question, with what it is about on top."""
        self.ensure_one()
        template = self.template_id._for(self.partner_id, partner=self.partner_id)
        body = template.render(self.partner_id)
        about = (self.about or '').strip()
        if about:
            # Named where the doctor reads first, not buried under the question.
            lines = body.split('\n')
            heading = 1 if lines and lines[0].strip() else 0
            lines.insert(heading, ('' if heading else '') + _("*Re: %s*", about))
            if heading:
                lines.insert(heading, '')
            body = '\n'.join(lines)
        return body

    def action_send(self):
        """Into the send dialog, the same as every other one-click message."""
        self.ensure_one()
        partner = self.partner_id
        template = self.template_id
        account = template.account_id or self.env['epg.whatsapp.account']._default_account()
        number = template.phone_for(partner)
        if not number:
            raise UserError(_("%s has no WhatsApp number.", partner.display_name))
        if not account:
            raise UserError(_("No WhatsApp sender is configured."))
        message = self.env['epg.whatsapp.message'].create({
            'account_id': account.id, 'template_id': template.id,
            'res_model': 'res.partner', 'res_id': partner.id, 'partner_id': partner.id,
            'number': number, 'body': self._body(), 'user_id': self.env.uid,
        })
        message.action_send()
        if message.state == 'cancel':
            raise UserError(message.error or _("This message may not be sent."))
        return {'type': 'ir.actions.client', 'tag': 'epg_whatsapp_send',
                'params': {'message_id': message.id, 'close': True, 'reload': True,
                           'discard_on_close': True}}
