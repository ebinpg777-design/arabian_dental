# -*- coding: utf-8 -*-
"""What the doctor says back, and what it makes somebody do.

A tapped answer used to be a line on the chatter and nothing more: "⚠️ Something
is missing" sat in a conversation nobody had open. Two things change that. An
answer named on the template raises a to-do on the case itself, for the person
whose case it is; and every reply waits in a **To answer** tray on the Desk until
somebody answers it or ticks it off. (client, 2026-09-18)
"""
from markupsafe import Markup

from odoo import _, api, fields, models

INBOX_ROWS = 60


class EpgWhatsappTemplate(models.Model):
    _inherit = 'epg.whatsapp.template'

    alert_replies = fields.Text(
        'Answers That Need a Person',
        help="One answer per line, written exactly as in Quick Replies. Tapping one "
             "raises a to-do on the record - the doctor is asking for something, and "
             "a chatter note is not an answer.")
    alert_user_id = fields.Many2one(
        'res.users', string='To-do For',
        help="Who gets it. Empty: whoever the record belongs to - the salesperson on "
             "an order, the accountant on an invoice - and the sender otherwise.")

    def _alert_labels(self):
        """The answers that need a person - on one template, or on none at all: a
        campaign's message has no template and asks nothing of anybody."""
        return [line.strip() for template in self
                for line in (template.alert_replies or '').splitlines() if line.strip()]


class EpgWhatsappMessage(models.Model):
    _inherit = 'epg.whatsapp.message'

    handled_at = fields.Datetime('Answered', readonly=True, copy=False,
                                 index='btree_not_null')
    handled_by_id = fields.Many2one('res.users', string='Answered By', readonly=True,
                                    copy=False)

    # ------------------------------------------------------------------ to-dos
    def _todo_owner(self, record):
        """Whose to-do this is: the template's choice, else whoever the record
        belongs to, else whoever sent the message."""
        self.ensure_one()
        chosen = self.template_id.alert_user_id
        if chosen:
            return chosen
        for field in ('user_id', 'invoice_user_id'):
            owner = record and getattr(record, field, False)
            if owner and owner._name == 'res.users':
                return owner
        return self.sent_by_id or self.user_id or self.create_uid

    def _raise_todo(self, label):
        """An answer the template says needs a person: a to-do where the case is."""
        self.ensure_one()
        message = self.sudo()
        if not label or label.strip() not in message.template_id._alert_labels():
            return False
        record = message._record()
        if record is None or not hasattr(record, 'activity_schedule'):
            record = message.partner_id
        if not record or not hasattr(record, 'activity_schedule'):
            return False
        who = message.sudo().partner_id.display_name or message.number
        owner = message._todo_owner(record)
        record.activity_schedule(
            'mail.mail_activity_data_todo',
            summary=_("WhatsApp: %(answer)s — %(who)s", answer=label.strip(), who=who),
            note=_("%(who)s tapped “%(answer)s” on the WhatsApp message sent on "
                   "%(when)s. Answer them on WhatsApp.",
                   who=who, answer=label.strip(),
                   when=fields.Datetime.context_timestamp(
                       self, message.sent_at or message.create_date).strftime('%d %b %H:%M')),
            user_id=owner.id if owner else self.env.uid)
        return True

    def _on_quick_reply(self, label, index):
        result = super()._on_quick_reply(label, index)
        self._raise_todo(label)
        return result

    # ------------------------------------------------------------------ the tray
    def action_mark_handled(self):
        """Somebody has dealt with this reply: it leaves the tray."""
        self.filtered(lambda m: m.direction == 'inbound' and not m.handled_at).write({
            'handled_at': fields.Datetime.now(), 'handled_by_id': self.env.uid})
        return True

    def action_mark_sent(self):
        """Answering the doctor is answering their message: what they said before
        this one leaves the tray on its own."""
        result = super().action_mark_sent()
        waiting = self.sudo().search([
            ('direction', '=', 'inbound'), ('handled_at', '=', False),
            ('number', 'in', self.mapped('number')),
            ('create_date', '<=', fields.Datetime.now())])
        if waiting:
            waiting.write({'handled_at': fields.Datetime.now(),
                           'handled_by_id': self.env.uid})
        return result

    @api.model
    def _inbox(self, limit=INBOX_ROWS):
        """Everything the doctors have said that nobody has answered yet."""
        return self.search([('direction', '=', 'inbound'), ('handled_at', '=', False)],
                           order='create_date desc', limit=limit)

    def _chatter_reply_note(self, label):
        """Kept for business modules that want the wording."""
        self.ensure_one()
        return Markup('<p>💬 <b>%s</b></p>') % label
