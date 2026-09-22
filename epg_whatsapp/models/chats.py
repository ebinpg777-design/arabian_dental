# -*- coding: utf-8 -*-
"""Chats: every conversation with a doctor, the way a chat app shows it.

The message log is a list of rows; the question at the desk is "what have we said
to Dr Anjali, and what did she say back". This reads one conversation as bubbles -
ours on the right with ticks, theirs on the left, the moments in between (opened
the invoice, tapped Received, says paid) as small notes - and lets the desk write
the next one. (client, 2026-09-17)
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

CHAT_ROWS = 80
THREAD_ROWS = 200


class EpgWhatsappDeskChats(models.AbstractModel):
    _inherit = 'epg.whatsapp.desk'

    @api.model
    def _chat_domain(self, search=''):
        domain = [('last_message_at', '!=', False)]
        search = (search or '').strip()
        if search:
            domain += ['|', ('partner_id.name', 'ilike', search), ('number', 'ilike', search)]
        return domain

    @api.model
    def get_chats(self, search='', partner_id=False):
        """The conversation list, newest first; `partner_id` also ensures the
        contact's conversation exists so it can be opened from their form."""
        self._check()
        Conversation = self.env['epg.whatsapp.conversation']
        opened = False
        if partner_id:
            opened = self._conversation_for_partner(int(partner_id))
        rows = Conversation.search(self._chat_domain(search),
                                   order='last_message_at desc, id desc', limit=CHAT_ROWS)
        if opened and opened not in rows:
            rows = opened | rows
        Message = self.env['epg.whatsapp.message'].sudo()
        best = Message._best_times(rows.mapped('number'))
        # One pass over every contact, not one per row: the engagement compute reads
        # the messages of all the partners it is given at once.
        rows.mapped('partner_id').mapped('whatsapp_engagement')
        # The last message of every conversation in one query, not one each: the
        # newest id is the newest message.
        last_ids = {conv.id: last_id for conv, last_id in Message._read_group(
            [('conversation_id', 'in', rows.ids)], ['conversation_id'], ['id:max'])}
        lasts = {m.conversation_id.id: m for m in Message.browse(list(last_ids.values()))}
        chats = []
        for conv in rows:
            # A conversation opened from a contact may hold nothing yet.
            last = lasts.get(conv.id) or Message.browse()
            chats.append({
                'id': conv.id,
                'partner': conv.sudo().partner_id.display_name or '',
                'partner_id': conv.sudo().partner_id.id or False,
                'number': conv.number,
                'last': (last.body or '')[:80],
                'last_inbound': last.direction == 'inbound' if last else False,
                'when': self._when(last.sent_at or last.create_date) if last else '',
                'awaiting': bool(last) and last.direction == 'inbound',
                'opt_out': conv.opt_out,
                'best_time': (best.get(conv.number) or {}).get('label', ''),
                'engagement': conv.partner_id.whatsapp_engagement if conv.partner_id else '',
            })
        return {'chats': chats, 'open_id': opened.id if opened else False}

    @api.model
    def _conversation_for_partner(self, partner_id):
        partner = self.env['res.partner'].browse(partner_id).exists()
        if not partner:
            return self.env['epg.whatsapp.conversation']
        Conversation = self.env['epg.whatsapp.conversation']
        conv = Conversation.search([('partner_id', '=', partner.id)],
                                   order='last_message_at desc', limit=1)
        if conv:
            return conv
        number = self.env['epg.whatsapp.campaign']._number_of(partner)
        account = self.env['epg.whatsapp.account']._default_account()
        if not (number and account):
            return Conversation
        return Conversation._get_or_create(account, number, partner)

    @api.model
    def _when(self, stamp):
        if not stamp:
            return ''
        local = fields.Datetime.context_timestamp(self, stamp)
        today = fields.Date.context_today(self)
        if local.date() == today:
            return local.strftime('%H:%M')
        return local.strftime('%d %b')

    @api.model
    def get_thread(self, conversation_id):
        """One conversation as bubbles, oldest first."""
        self._check()
        conv = self.env['epg.whatsapp.conversation'].browse(int(conversation_id)).exists()
        if not conv:
            raise UserError(_("This conversation no longer exists."))
        Message = self.env['epg.whatsapp.message'].sudo()
        rows = Message.search([('conversation_id', '=', conv.id)],
                              order='create_date desc, id desc', limit=THREAD_ROWS)
        bubbles = []
        last_day = None
        records = Message._records_of(rows)
        for message in reversed(rows):
            stamp = message.sent_at or message.create_date
            local = fields.Datetime.context_timestamp(self, stamp)
            day = local.date()
            if day != last_day:
                bubbles.append({'kind': 'day', 'id': 'd%s' % day,
                                'label': self._day_label(day)})
                last_day = day
            bubbles.append(self._bubble(message, local, records.get(message.id)))
        partner = conv.partner_id
        best = Message._best_times([conv.number]).get(conv.number)
        return {
            'id': conv.id,
            'partner': partner.display_name or '',
            'partner_id': partner.id or False,
            'number': conv.number,
            'opt_out': conv.opt_out,
            'best_time': best['label'] if best else '',
            'engagement': partner.whatsapp_engagement_label if partner else '',
            'account_channel': conv.account_id.channel,
            'record_model': 'res.partner' if partner else '',
            'bubbles': bubbles,
            'snippets': self.env['epg.whatsapp.snippet'].for_model('res.partner'),
        }

    @api.model
    def _day_label(self, day):
        today = fields.Date.context_today(self)
        if day == today:
            return _('Today')
        if (today - day).days == 1:
            return _('Yesterday')
        return day.strftime('%a %d %b %Y')

    @api.model
    def _bubble(self, message, local, record=None):
        events = []
        if message.seen_at and message.attachment_id:
            events.append(_("opened %s", message.sudo().attachment_id.name or _('the document')))
        if message.clicked_at:
            events.append(_("opened the link"))
        if message.pay_opened_at:
            events.append(_("opened the payment page"))
        if message.paid_claimed_at:
            events.append(_("says paid"))
        return {
            'kind': 'msg',
            'id': message.id,
            'inbound': message.direction == 'inbound',
            'text': message.body or '',
            'time': local.strftime('%H:%M'),
            'state': message.state,
            'ticks': 2 if message.seen_at or message.state == 'read' else
            1 if message.state in ('sent', 'delivered', 'simulated') else 0,
            'template': message.template_id.name or message.campaign_id.name or '',
            'document': message.sudo().attachment_id.name or '',
            'record': record.display_name if record is not None and
            record._name != 'res.partner' else '',
            'res_model': message.res_model or '',
            'res_id': message.res_id or 0,
            'replies': message._reply_labels(),
            'pay': self.env['epg.whatsapp.template']._money(message.pay_amount, message.currency_id) if message.pay_amount else '',
            'events': events,
            'sent_by': message.sent_by_id.name or '',
            'waiting': message.state in ('draft', 'ready', 'opened'),
            'cancelled': message.state == 'cancel',
        }

    @api.model
    def compose(self, conversation_id, text, snippet_id=False):
        """Write the next message. On a free sender it goes to the send dialog; on the
        Cloud API it is sent. Returns the message id and where it went."""
        self._check()
        conv = self.env['epg.whatsapp.conversation'].browse(int(conversation_id)).exists()
        if not conv:
            raise UserError(_("This conversation no longer exists."))
        if conv.opt_out:
            raise UserError(_("%s asked not to be messaged on WhatsApp.",
                              conv.sudo().partner_id.display_name or conv.number))
        text = (text or '').strip()
        if not text and snippet_id:
            text = self.env['epg.whatsapp.snippet'].browse(int(snippet_id)).render(
                'res.partner', conv.partner_id.id)
        if not text:
            raise UserError(_("Write the message first."))
        message = self.env['epg.whatsapp.message'].create({
            'account_id': conv.account_id.id,
            'conversation_id': conv.id,
            'partner_id': conv.partner_id.id,
            'number': conv.number,
            'body': text,
            'res_model': 'res.partner' if conv.partner_id else False,
            'res_id': conv.partner_id.id or False,
            'user_id': self.env.user.id,
        })
        message.action_send()
        if message.state == 'cancel':
            raise UserError(message.error or _("This message may not be sent."))
        return {'id': message.id, 'channel': conv.account_id.channel, 'state': message.state}

    @api.model
    def log_inbound(self, conversation_id, text):
        """Something the doctor wrote in WhatsApp, kept on the record."""
        self._check()
        conv = self.env['epg.whatsapp.conversation'].browse(int(conversation_id)).exists()
        text = (text or '').strip()
        if not conv or not text:
            raise UserError(_("Paste or type the reply first."))
        last = self.env['epg.whatsapp.message'].sudo().search(
            [('conversation_id', '=', conv.id), ('direction', '=', 'outbound')],
            order='create_date desc', limit=1)
        if last:
            return last.log_reply(text)
        reply = self.env['epg.whatsapp.message'].sudo().create({
            'account_id': conv.account_id.id, 'conversation_id': conv.id,
            'partner_id': conv.partner_id.id, 'number': conv.number, 'body': text,
            'direction': 'inbound', 'state': 'received', 'sent_at': fields.Datetime.now(),
            'res_model': 'res.partner' if conv.partner_id else False,
            'res_id': conv.partner_id.id or False, 'company_id': conv.company_id.id,
        })
        conv.sudo()._note_inbound()
        reply._chatter_post(reply._chatter_reply_markup(text, self.env.user.name))
        return reply.id
