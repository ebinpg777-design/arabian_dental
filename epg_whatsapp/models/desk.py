# -*- coding: utf-8 -*-
"""The WhatsApp Desk: the one screen where free WhatsApp messages get sent.

Everything waiting for a person is here, newest news first, with one button each.
"Send them all" walks the queue a message at a time - open in WhatsApp, press Send,
confirm, next - because that is how a front desk actually clears forty dispatch
notices after lunch. (client, 2026-09-17)
"""
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from .res_users import OPEN_WITH

QUEUE_ROWS = 200
RECENT_ROWS = 60


class EpgWhatsappDesk(models.AbstractModel):
    _name = 'epg.whatsapp.desk'
    _description = 'WhatsApp Desk'

    @api.model
    def _check(self):
        if not (self.env.su or self.env.user.has_group('epg_whatsapp.group_whatsapp_user')):
            raise AccessError(_("The WhatsApp Desk needs the 'WhatsApp: send' right."))

    @api.model
    def _scope_domain(self, scope):
        if scope == 'mine':
            return ['|', ('user_id', '=', self.env.user.id), ('user_id', '=', False)]
        return []

    @api.model
    def _today_start(self):
        tz = pytz.timezone(self.env.context.get('tz') or self.env.user.tz
                           or self.env.company.partner_id.tz or 'Asia/Kolkata')
        local = tz.localize(datetime.combine(fields.Date.context_today(self), time.min))
        return local.astimezone(pytz.utc).replace(tzinfo=None)

    @api.model
    def _due_domain(self):
        return ['|', ('scheduled_at', '=', False), ('scheduled_at', '<=', fields.Datetime.now())]

    @api.model
    def pending_count(self):
        """For the top bar: what is waiting for this person to send now."""
        if not self.env.user.has_group('epg_whatsapp.group_whatsapp_user'):
            return 0
        return self.env['epg.whatsapp.message'].search_count(
            [('direction', '=', 'outbound'), ('state', 'in', ('ready', 'opened'))]
            + self._due_domain() + self._scope_domain('mine'))

    @api.model
    def get_desk(self, scope='mine'):
        self._check()
        Message = self.env['epg.whatsapp.message']
        scope_domain = self._scope_domain(scope)
        # Drafts on a link sender are prepared before the Desk shows them, so a
        # message queued a second ago is already sendable. Prepared, not rendered:
        # the PDF costs seconds each and is made when the message is opened.
        drafts = Message.search([('direction', '=', 'outbound'), ('state', '=', 'draft'),
                                 ('channel', '=', 'link')], limit=50)
        if drafts:
            drafts.action_send()

        queue = Message.search(
            [('direction', '=', 'outbound'), ('state', 'in', ('ready', 'opened'))]
            + self._due_domain() + scope_domain, order='state desc, ready_at, id',
            limit=QUEUE_ROWS)
        later = Message.search(
            [('direction', '=', 'outbound'), ('state', '=', 'ready'),
             ('scheduled_at', '>', fields.Datetime.now())] + scope_domain,
            order='scheduled_at, id', limit=QUEUE_ROWS)
        since = fields.Datetime.now() - timedelta(hours=48)
        recent = Message.search(
            [('direction', '=', 'outbound'), ('channel', '=', 'link'),
             ('state', 'in', ('sent', 'read')), ('sent_at', '>=', since)]
            + scope_domain, order='sent_at desc', limit=RECENT_ROWS)
        inbox = Message._inbox()
        shown = queue | later | recent | inbox
        best = Message._best_times(shown.mapped('number'))
        records = Message._records_of(shown)
        return {
            'scope': scope,
            'open_with': self.env.user.whatsapp_open_with or 'auto',
            'open_with_options': OPEN_WITH,
            'counts': dict(self._counts(scope_domain), later=len(later)),
            'savings': self._savings(),
            'safety': Message._safety(),
            'groups': self._groups(queue, best, records),
            'queue_count': len(queue),
            'later': [self._card(m, best, records) for m in later],
            'recent': [self._card(m, best, records) for m in recent],
            'inbox': [self._reply_card(m, records) for m in inbox],
            'can_configure': self.env.user.has_group('epg_whatsapp.group_whatsapp_admin'),
            # Offering "Everyone" to somebody whose rules show them only their own
            # is offering a button that does nothing. (client, 2026-09-19)
            'can_see_all': self.env.user.has_group('epg_whatsapp.group_whatsapp_admin'),
            'has_link_sender': bool(self.env['epg.whatsapp.account'].search_count(
                [('channel', '=', 'link')])),
            'link_problem': next((a._link_problem() for a in self.env['epg.whatsapp.account']
                                  .sudo().search([('channel', '=', 'link')])
                                  if a._link_problem()), ''),
        }

    @api.model
    def _counts(self, scope_domain):
        Message = self.env['epg.whatsapp.message']
        today = self._today_start()
        base = [('direction', '=', 'outbound')] + scope_domain
        return {
            'ready': Message.search_count(
                base + [('state', '=', 'ready')] + self._due_domain()),
            'opened': Message.search_count(base + [('state', '=', 'opened')]),
            'sent_today': Message.search_count(
                base + [('state', 'in', ('sent', 'read')), ('sent_at', '>=', today)]),
            'seen_today': Message.search_count(base + [('seen_at', '>=', today)]),
            'replies_today': Message.search_count(
                [('direction', '=', 'inbound'), ('create_date', '>=', today)]),
        }

    @api.model
    def _savings(self):
        """Messages sent free this month, and what the Cloud API would have charged."""
        Message = self.env['epg.whatsapp.message'].sudo()
        month_start = fields.Date.context_today(self).replace(day=1)
        rows = Message._read_group(
            [('channel', '=', 'link'), ('direction', '=', 'outbound'),
             ('state', 'in', ('sent', 'read')),
             ('sent_at', '>=', fields.Datetime.to_datetime(month_start))],
            ['account_id'], ['__count'])
        count = sum(n for _account, n in rows)
        amount = sum(n * (account.api_cost_per_message or 0.0) for account, n in rows)
        currency = self.env.company.currency_id
        return {'count': count, 'amount': amount,
                'amount_label': currency.format(amount) if amount else '',
                'priced': any(account.api_cost_per_message for account, _n in rows)}

    @api.model
    def _groups(self, messages, best, records=None):
        """The queue by doctor: several messages for one number go as one WhatsApp.

        Kept in queue order - the group sits where its oldest message would.
        """
        # sudo for the NAME only. The message is one this person is entitled to
        # see - the rules on the message itself decided that - but the doctor it
        # is addressed to may be a clinic they cannot read: a field executive
        # sees clinics on their route, visited, or carried to, and nothing else.
        # Rendering the name of the contact on a message already granted is not
        # granting the contact. Without this the whole Desk died on an
        # AccessError the moment one such message was in view.
        # (client, 2026-09-19)
        groups = {}
        for message in messages:
            key = message.number
            group = groups.setdefault(key, {
                'key': key, 'number': message.number,
                'partner': message.sudo().partner_id.display_name or '',
                'best_time': (best.get(message.number) or {}).get('label', ''),
                'cards': [],
            })
            group['cards'].append(self._card(message, best, records))
        result = list(groups.values())
        for group in result:
            group['ids'] = [card['id'] for card in group['cards']]
            group['count'] = len(group['cards'])
        return result

    @api.model
    @api.model
    def _reply_card(self, message, records=None):
        """One thing a doctor said, waiting for an answer."""
        record = records.get(message.id) if records is not None else message._record()
        asked = message.reply_to_id
        return {
            'id': message.id,
            'partner': message.sudo().partner_id.display_name or '',
            'partner_id': message.sudo().partner_id.id or False,
            'number': message.number,
            'text': message.body or '',
            'when': self._when(message.create_date),
            'record': record.display_name if record is not None else '',
            'res_model': message.res_model or '',
            'res_id': message.res_id or 0,
            'about': (asked.template_id.name or asked.campaign_id.name
                      or asked.automation_id.name or '') if asked else '',
            'marketing': bool(message.campaign_id or message.automation_id),
        }

    @api.model
    def mark_handled(self, message_id):
        """Ticked off the tray, by hand."""
        self._check()
        message = self.env['epg.whatsapp.message'].browse(int(message_id)).exists()
        if message:
            message.action_mark_handled()
        return True

    def _card(self, message, best=None, records=None):
        record = records.get(message.id) if records is not None else message._record()
        now = fields.Datetime.now()
        # An attachment, or the report that will become one when the message opens.
        document = message.sudo().attachment_id.name or (
            message.template_id.report_id.name if message.template_id.report_id
            and message.res_model else '')
        waited = now - (message.ready_at or message.create_date or now)
        hours = int(waited.total_seconds() // 3600)
        return {
            'id': message.id,
            'state': message.state,
            'partner': message.sudo().partner_id.display_name or '',
            'partner_id': message.sudo().partner_id.id or False,
            'number': message.number,
            'template': (message.template_id.name or message.campaign_id.name
                         or message.automation_id.name or _('Message')),
            'body': message.body or '',
            'record': record.display_name if record is not None else '',
            'res_model': message.res_model or '',
            'res_id': message.res_id or 0,
            'document': document or '',
            'replies': message._reply_labels(),
            'user': message.user_id.name or '',
            'waited': (_("%s min", max(int(waited.total_seconds() // 60), 0)) if hours < 1
                       else _("%s h", hours) if hours < 48 else _("%s days", hours // 24)),
            'overdue': bool(message.account_id.expire_hours and
                            hours >= message.account_id.expire_hours * 0.75),
            'sent_at': fields.Datetime.context_timestamp(
                self, message.sent_at).strftime('%d %b %H:%M') if message.sent_at else '',
            'sent_by': message.sent_by_id.name or '',
            # `read`: they opened something of it (blue ticks); `seen`: the document.
            'read': bool(message.seen_at),
            'seen': bool(message.seen_at and message.attachment_id),
            'seen_count': message.seen_count,
            'reply': message.reply_summary or '',
            'best_time': ((best or {}).get(message.number) or {}).get('label', ''),
            'nudge': bool(message.nudge_of_id),
            'campaign': message.campaign_id.name or '',
            'automation': message.automation_id.name or '',
            'marketing': bool(message.campaign_id or message.automation_id),
            'scheduled': fields.Datetime.context_timestamp(
                self, message.scheduled_at).strftime('%a %d %b, %I:%M %p')
            if message.scheduled_at else '',
            'clicked': bool(message.clicked_at),
            'opted_out': bool(message.opted_out_at),
            'paid': bool(message.paid_claimed_at),
        }

    @api.model
    def set_open_with(self, value):
        if value not in dict(OPEN_WITH):
            return False
        self.env.user.sudo().whatsapp_open_with = value
        return True

    @api.model
    def update_body(self, message_id, body):
        """Edit a message on the Desk before it is sent."""
        self._check()
        message = self.env['epg.whatsapp.message'].browse(int(message_id)).exists()
        if not message or message.state not in ('ready', 'opened'):
            return False
        message.write({'body': body or '', 'state': 'ready', 'final_text': False,
                       'opened_at': False, 'opened_by_id': False})
        return True
