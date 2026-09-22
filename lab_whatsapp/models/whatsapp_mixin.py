# -*- coding: utf-8 -*-
import logging
import re

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class LabWhatsappMixin(models.AbstractModel):
    """Send a lab event to whoever the record belongs to.

    Inherited by every record that notifies a doctor, so the trigger on a sale order,
    an invoice, a payment and a picking is literally the same code — the only thing that
    differs is which event name is passed.
    """
    _name = 'lab.whatsapp.mixin'
    _description = 'Lab WhatsApp notifications'

    whatsapp_message_count = fields.Integer(compute='_compute_whatsapp_messages')
    # The record's WhatsApp story in three words - "Opened 2 h ago" - on the button
    # a person looks at before deciding whether to send again. (client, 2026-09-17)
    whatsapp_status = fields.Char(compute='_compute_whatsapp_status')
    # Where a message from here would go: the doctor's WhatsApp number, or nothing.
    whatsapp_to = fields.Char(compute='_compute_whatsapp_to')

    def _compute_whatsapp_messages(self):
        counts = dict(self.env['epg.whatsapp.message'].sudo()._read_group(
            [('res_model', '=', self._name), ('res_id', 'in', self.ids)],
            ['res_id'], ['__count']))
        for record in self:
            record.whatsapp_message_count = counts.get(record.id, 0)

    def _whatsapp_partner(self):
        """Whose WhatsApp a message from this record goes to."""
        self.ensure_one()
        if self._name == 'res.partner':
            return self
        return self.partner_id if 'partner_id' in self._fields else self.env['res.partner']

    def _compute_whatsapp_to(self):
        for record in self:
            partner = record._whatsapp_partner()
            record.whatsapp_to = (partner.whatsapp_number or '') if partner else ''

    def _ago(self, stamp):
        delta = fields.Datetime.now() - stamp
        minutes = int(delta.total_seconds() // 60)
        if minutes < 2:
            return self.env._("just now")
        if minutes < 60:
            return self.env._("%s min ago", minutes)
        if minutes < 60 * 24:
            return self.env._("%s h ago", minutes // 60)
        return self.env._("%s d ago", minutes // (60 * 24))

    def _whatsapp_status_of(self, message):
        if message is None:
            return _("None yet")
        if message.reply_ids:
            return _("Replied %s", self._ago(message.reply_ids[-1].create_date))
        if message.seen_at or message.state == 'read':
            return _("Opened %s", self._ago(message.seen_at or message.sent_at
                                            or message.write_date))
        if message.state in ('sent', 'delivered', 'simulated'):
            return _("Sent %s", self._ago(message.sent_at or message.write_date))
        if message.state in ('ready', 'opened'):
            return _("On the Desk")
        if message.state == 'draft':
            return _("Queued")
        if message.state == 'error':
            return _("Failed")
        return _("Not sent")

    def _compute_whatsapp_status(self):
        Message = self.env['epg.whatsapp.message'].sudo()
        last_ids = {res_id: last for res_id, last in Message._read_group(
            [('res_model', '=', self._name), ('res_id', 'in', self.ids),
             ('direction', '=', 'outbound')], ['res_id'], ['id:max'])}
        lasts = {m.res_id: m for m in Message.browse(list(last_ids.values()))}
        for record in self:
            record.whatsapp_status = self._whatsapp_status_of(lasts.get(record.id))

    # ------------------------------------------------------------------ the button
    def _whatsapp_suggested_event(self):
        """The lab moment this record is at - what the WhatsApp button offers first.
        Each record type says its own; the generic message otherwise."""
        return 'manual'

    def _whatsapp_suggested_template(self):
        self.ensure_one()
        Template = self.env['epg.whatsapp.template']
        return (Template._find(self._name, self._whatsapp_suggested_event())
                or Template._find(self._name, 'manual'))

    def action_whatsapp_quick(self):
        """One click: the record's own message, straight into the send dialog -
        read it, change a word if needed, Open in WhatsApp. The composer only when
        there is nothing ready-made to offer. (client, 2026-09-18)"""
        return self._whatsapp_quick(self._whatsapp_suggested_template())

    def _whatsapp_quick_event(self, event):
        """The same one click, for a moment asked for by name."""
        self.ensure_one()
        Template = self.env['epg.whatsapp.template']
        return self._whatsapp_quick(Template._find(self._name, event))

    def _whatsapp_quick(self, template):
        self.ensure_one()
        partner = self._whatsapp_partner()
        account = template.account_id or self.env['epg.whatsapp.account']._default_account()
        if not template or not partner.whatsapp_number or not account \
                or account.channel != 'link':
            return self.action_whatsapp_send()
        message = template.send(self, account=account, now=True)
        message = message.filtered(lambda m: m.state == 'ready')
        if not message:
            return self.action_whatsapp_send()
        return {'type': 'ir.actions.client', 'tag': 'epg_whatsapp_send',
                'params': {'message_id': message.id, 'close': False, 'reload': True,
                           'discard_on_close': True}}

    def action_whatsapp_add_number(self):
        """The doctor has no WhatsApp number: ask for it here, not three screens away.
        The contact's mobile is offered when it looks like one."""
        self.ensure_one()
        partner = self._whatsapp_partner()
        suggested = ''
        # Odoo 19 keeps one phone on a contact; older data may still carry a mobile.
        candidates = (getattr(partner, 'mobile', ''), partner.phone) if partner else ()
        for candidate in candidates:
            digits = re.sub(r'\D', '', candidate or '')
            if len(digits) >= 10:
                suggested = candidate
                break
        return {
            'type': 'ir.actions.act_window', 'name': _('WhatsApp number'),
            'res_model': 'lab.whatsapp.number', 'view_mode': 'form', 'target': 'new',
            'context': {'default_partner_id': partner.id or False,
                        'default_number': suggested},
        }

    def action_whatsapp_send_batch(self):
        """The WhatsApp action on a list: each record's own moment, all at once. On the
        free channel they land on the Desk, to be sent in one sitting."""
        Message = self.env['epg.whatsapp.message']
        created = Message
        by_event = {}
        for record in self:
            by_event.setdefault(record._whatsapp_suggested_event(), self.browse())
            by_event[record._whatsapp_suggested_event()] |= record
        Template = self.env['epg.whatsapp.template']
        for event, records in by_event.items():
            template = Template._find(self._name, event) or Template._find(self._name, 'manual')
            if template:
                created |= template.send(records)
        usable = created.filtered(lambda m: m.state != 'cancel')
        missing = len(self) - len(created)
        parts = [_("%s message(s) ready.", len(usable))]
        if missing:
            parts.append(_("%s record(s) have no WhatsApp number.", missing))
        if len(created) - len(usable):
            parts.append(_("%s refused (opted out).", len(created) - len(usable)))
        on_desk = usable.filtered(lambda m: m.channel == 'link')
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'type': 'success' if usable else 'warning',
                       'title': _("WhatsApp"), 'message': ' '.join(parts),
                       'next': {'type': 'ir.actions.client', 'tag': 'epg_whatsapp_desk'}
                       if on_desk else None},
        }

    def action_view_whatsapp(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': 'WhatsApp',
            'res_model': 'epg.whatsapp.message', 'view_mode': 'list,form',
            'domain': [('res_model', '=', self._name), ('res_id', '=', self.id)],
        }

    def action_whatsapp_chat(self):
        """The doctor's WhatsApp conversation, opened from the record, with the
        messages about this record picked out. (client, 2026-09-17)"""
        self.ensure_one()
        partner = self.partner_id if 'partner_id' in self._fields else self.env['res.partner']
        return {
            'type': 'ir.actions.client', 'tag': 'epg_whatsapp_chats', 'name': 'WhatsApp',
            'params': {'partner_id': partner.id or False,
                       'res_model': self._name, 'res_id': self.id},
        }

    def action_whatsapp_send(self):
        """Open the composer on this record, with the record's own moment filled in."""
        self.ensure_one()
        template = self._whatsapp_suggested_template()
        return {
            'type': 'ir.actions.act_window', 'name': 'Send WhatsApp',
            'res_model': 'epg.whatsapp.composer', 'view_mode': 'form', 'target': 'new',
            'context': {'default_res_model': self._name, 'default_res_id': self.id,
                        'default_template_id': template.id or False},
        }

    # ------------------------------------------------------------------ events
    def _whatsapp_enabled(self, event):
        return self.env['ir.config_parameter'].sudo().get_param(
            'lab_whatsapp.notify_%s' % event, 'True') in ('True', 'true', '1')

    def _whatsapp_send_event(self, event):
        """Fire one lab event.

        Never raises. A notification is a side effect of confirming an order or posting
        an invoice; letting it fail the transaction would mean a doctor's message
        outranking the lab's books.

        Silent under ``migration_replay``. A migration re-posts documents the lab
        already sent months ago, and a doctor must not be told twice about an
        invoice they have long since paid — quite apart from the cost, which is a
        full invoice PDF rendered per record.
        """
        if self.env.context.get('migration_replay'):
            return
        for record in self:
            if not record._whatsapp_enabled(event):
                continue
            try:
                template = self.env['epg.whatsapp.template'].sudo()._find(
                    record._name, event)
                if not template:
                    continue
                # Deferred: the click that raised the event (Validate, Confirm, Post)
                # must not wait on a PDF render and two Meta round-trips.
                messages = template.sudo().send(record, defer=True)
                # Refused now, with the reason on the log, rather than when the queue
                # reaches them: an opted-out doctor's invoice must not even be rendered.
                messages._cancel_blocked()
            except Exception as exc:                                   # noqa: BLE001
                _logger.warning("WhatsApp %s failed on %s: %s",
                                event, record.display_name, exc)
