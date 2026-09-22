# -*- coding: utf-8 -*-
"""Sending smarter through the lab's own WhatsApp. (client, 2026-09-17)

* **Best time** - every document a doctor opens and every answer they tap says when
  they read WhatsApp. The hour they do it most is shown beside their messages, and a
  message can be snoozed until then.
* **Snooze** - later today, this evening, tomorrow morning, or the doctor's best time.
* **Nudges** - an invoice nobody opened gets one gentle reminder after a set time,
  and the reminder stands down if the doctor opens the invoice first.
* **Warnings** - the same notice already sent for the same record, or this number
  messaged minutes ago, is said before it goes again.
* **History** - the last few messages with this doctor, in the send dialog.
"""
from collections import Counter
from datetime import datetime, time, timedelta

import pytz
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

BEST_TIME_DAYS = 180
BEST_TIME_MIN_EVENTS = 3
HISTORY_ROWS = 6
DUPLICATE_DAYS = 7
RECENT_MINUTES = 30
NUDGE_BATCH = 100


class EpgWhatsappTemplate(models.Model):
    _inherit = 'epg.whatsapp.template'

    nudge_after_hours = fields.Integer(
        'Remind If Not Opened After (hours)', default=0,
        help="WhatsApp app / Web only. If the doctor has not opened the attached "
             "document this many hours after the message was sent, a short reminder "
             "is put on the Desk - once. 0 sends no reminder.")
    nudge_text = fields.Text(
        'Reminder Text',
        help="What the reminder says; placeholders work as in the message. The "
             "document link is added under it.")

    @api.model
    def _render_text(self, record, text, drop_empty=False):
        """The text with the record's values in it.

        ``drop_empty`` leaves out a line whose placeholders ALL came out with
        nothing - "🔖 *Consignment*" alone, when the courier gave no number, is
        furniture the doctor has to read past. A line the author wrote as words
        is never touched, and the gap left behind closes. (client, 2026-09-18)
        """
        import re as _re
        from .epg_whatsapp_template import PLACEHOLDER
        lines = []
        for line in (text or '').splitlines():
            state = {'had': False, 'filled': False}

            def _one(match, state=state):
                state['had'] = True
                value = self._resolve(record, match.group(1), match.group(2))
                if str(value).strip():
                    state['filled'] = True
                return value

            rendered = PLACEHOLDER.sub(_one, line)
            if drop_empty and state['had'] and not state['filled']:
                continue
            lines.append(rendered)
        out = '\n'.join(lines)
        return _re.sub(r'\n{3,}', '\n\n', out).strip('\n') if drop_empty else out


class EpgWhatsappMessage(models.Model):
    _inherit = 'epg.whatsapp.message'

    scheduled_at = fields.Datetime(
        'Send After', copy=False, index='btree_not_null',
        help="Snoozed: stays off the Desk until this time.")
    nudge_of_id = fields.Many2one(
        'epg.whatsapp.message', string='Reminder Of', index='btree_not_null',
        ondelete='cascade', copy=False)
    nudge_created = fields.Boolean(readonly=True, copy=False)
    campaign_id = fields.Many2one('epg.whatsapp.campaign', index='btree_not_null',
                                  ondelete='set null', copy=False)
    clicked_at = fields.Datetime('Link Tapped', readonly=True, copy=False)
    opted_out_at = fields.Datetime('Opted Out', readonly=True, copy=False)

    # ------------------------------------------------------------------ clock
    @api.model
    def _lab_tz(self):
        name = (self.env.context.get('tz') or self.env.user.tz
                or self.env.company.partner_id.tz or 'Asia/Kolkata')
        try:
            return pytz.timezone(name)
        except pytz.UnknownTimeZoneError:
            return pytz.timezone('Asia/Kolkata')

    @api.model
    def _local_now(self):
        return pytz.utc.localize(fields.Datetime.now()).astimezone(self._lab_tz())

    @api.model
    def _utc(self, local_dt):
        return local_dt.astimezone(pytz.utc).replace(tzinfo=None)

    @api.model
    def _hour_label(self, hour):
        return datetime.combine(fields.Date.today(), time(hour)).strftime('%I %p').lstrip('0')

    # ------------------------------------------------------------------ best time
    @api.model
    def _best_times(self, numbers):
        """{number: {'hour', 'label', 'events', 'share'}} for the numbers that have
        read enough to tell - opened documents, tapped links and replies."""
        numbers = [n for n in set(numbers or []) if n]
        if not numbers:
            return {}
        since = fields.Datetime.now() - timedelta(days=BEST_TIME_DAYS)
        Message = self.sudo()
        hours = {}
        tz = self._lab_tz()
        for row in Message.search_read(
                [('number', 'in', numbers), ('seen_at', '>=', since)], ['number', 'seen_at']):
            hours.setdefault(row['number'], []).append(row['seen_at'])
        for row in Message.search_read(
                [('number', 'in', numbers), ('direction', '=', 'inbound'),
                 ('create_date', '>=', since)], ['number', 'create_date']):
            hours.setdefault(row['number'], []).append(row['create_date'])
        result = {}
        for number, stamps in hours.items():
            if len(stamps) < BEST_TIME_MIN_EVENTS:
                continue
            counts = Counter(pytz.utc.localize(s).astimezone(tz).hour for s in stamps)
            hour, hits = counts.most_common(1)[0]
            result[number] = {
                'hour': hour,
                'label': _("usually reads around %s", self._hour_label(hour)),
                'events': len(stamps),
                'share': round(hits * 100 / len(stamps)),
            }
        return result

    # ------------------------------------------------------------------ snooze
    def _snooze_moment(self, preset):
        """The local moment a preset means, as naive UTC."""
        now = self._local_now()
        today = now.date()
        tz = self._lab_tz()

        def at(day, hour):
            return tz.localize(datetime.combine(day, time(hour)))

        if preset == 'hour':
            return self._utc(now + timedelta(hours=1))
        if preset == 'evening':
            moment = at(today, 18)
            if moment <= now + timedelta(minutes=30):
                moment = at(today + timedelta(days=1), 18)
            return self._utc(moment)
        if preset == 'tomorrow':
            return self._utc(at(today + timedelta(days=1), 9))
        if preset == 'best':
            best = self._best_times(self.mapped('number')).get(self[:1].number)
            if not best:
                raise UserError(_("Not enough history yet to know when this doctor "
                                  "reads WhatsApp."))
            moment = at(today, best['hour'])
            if moment <= now + timedelta(minutes=15):
                moment = at(today + timedelta(days=1), best['hour'])
            return self._utc(moment)
        raise UserError(_("Unknown snooze: %s", preset))

    def action_snooze(self, preset):
        """Off the Desk until later. Returns when, in the reader's clock."""
        self._check_link_user()
        waiting = self.filtered(lambda m: m.state in ('ready', 'opened'))
        if not waiting:
            return False
        when = waiting._snooze_moment(preset)
        waiting.write({'scheduled_at': when, 'state': 'ready',
                       'opened_at': False, 'opened_by_id': False})
        return pytz.utc.localize(when).astimezone(self._lab_tz()).strftime('%a %d %b, %I:%M %p')

    def action_unsnooze(self):
        self._check_link_user()
        self.filtered(lambda m: m.state == 'ready').write({'scheduled_at': False})
        return True

    # ------------------------------------------------------------------ guard rails
    def _send_warnings(self):
        """What the person should know before sending these again."""
        warnings = []
        since = fields.Datetime.now() - timedelta(days=DUPLICATE_DAYS)
        Message = self.sudo()
        done_states = ('sent', 'read', 'delivered', 'simulated')
        for message in self:
            if not (message.template_id and message.res_model and message.res_id):
                continue
            earlier = Message.search([
                ('id', 'not in', self.ids), ('template_id', '=', message.template_id.id),
                ('res_model', '=', message.res_model), ('res_id', '=', message.res_id),
                ('number', '=', message.number), ('state', 'in', done_states),
                ('sent_at', '>=', since)], order='sent_at desc', limit=1)
            if earlier:
                when = fields.Datetime.context_timestamp(self, earlier.sent_at)
                record = message._record()
                warnings.append(_(
                    "“%(template)s” was already sent for %(record)s on %(when)s%(who)s.",
                    template=message.template_id.name,
                    record=record.display_name if record is not None else _('this record'),
                    when=when.strftime('%d %b %H:%M'),
                    who=(_(" by %s", earlier.sent_by_id.name) if earlier.sent_by_id else '')))
        # Links a doctor cannot open are the one thing worth saying every time.
        problem = self[:1].account_id._link_problem() if self else ''
        if problem:
            warnings.append(problem)
        recent = Message.search([
            ('id', 'not in', self.ids), ('number', 'in', self.mapped('number')),
            ('direction', '=', 'outbound'), ('state', 'in', done_states),
            ('sent_at', '>=', fields.Datetime.now() - timedelta(minutes=RECENT_MINUTES))],
            order='sent_at desc', limit=1)
        if recent:
            minutes = int((fields.Datetime.now() - recent.sent_at).total_seconds() // 60)
            warnings.append(_("This number was messaged %s minutes ago.", max(minutes, 1)))
        return warnings

    def _history(self, exclude=None, limit=HISTORY_ROWS):
        """The last few messages with this number, newest first."""
        self.ensure_one()
        domain = [('number', '=', self.number),
                  ('state', 'in', ('sent', 'read', 'delivered', 'simulated', 'received'))]
        if exclude:
            domain.append(('id', 'not in', exclude.ids))
        rows = []
        for message in self.sudo().search(domain, order='create_date desc', limit=limit):
            stamp = message.sent_at or message.create_date
            rows.append({
                'id': message.id,
                'inbound': message.direction == 'inbound',
                'text': (message.body or '')[:160],
                'template': message.template_id.name or message.campaign_id.name or '',
                'when': fields.Datetime.context_timestamp(self, stamp).strftime('%d %b %H:%M')
                if stamp else '',
                'seen': bool(message.seen_at),
            })
        return rows

    # ------------------------------------------------------------------ nudges
    def _document_seen(self):
        """The doctor opened the document: the original counts as read too, and any
        reminder still waiting has nothing left to remind about."""
        for message in self.sudo():
            original = message.nudge_of_id
            if original and not original.seen_at:
                now = fields.Datetime.now()
                original.write({'seen_at': now, 'read_at': now, 'state': 'read'})
            family = (original or message)
            pending = self.sudo().search([
                ('nudge_of_id', '=', family.id), ('state', 'in', ('draft', 'ready')),
                ('id', '!=', message.id)])
            pending.write({'state': 'cancel', 'scheduled_at': False,
                           'error': _("Opened meanwhile - the reminder is not needed.")})

    @api.model
    def _cron_nudge_unopened(self):
        """Put one reminder on the Desk for each document nobody opened in time."""
        now = fields.Datetime.now()
        created = self.browse()
        templates = self.env['epg.whatsapp.template'].sudo().search(
            [('nudge_after_hours', '>', 0)])
        for template in templates:
            due = self.sudo().search([
                ('template_id', '=', template.id), ('channel', '=', 'link'),
                ('direction', '=', 'outbound'), ('state', '=', 'sent'),
                ('attachment_id', '!=', False), ('seen_at', '=', False),
                ('nudge_created', '=', False), ('nudge_of_id', '=', False),
                ('sent_at', '<=', now - timedelta(hours=template.nudge_after_hours)),
                '|', ('link_expires_at', '=', False), ('link_expires_at', '>', now),
            ], limit=NUDGE_BATCH)
            for original in due:
                record = original._record()
                text = template._render_text(record, template.nudge_text) \
                    if record is not None and template.nudge_text else ''
                nudge = self.sudo().create({
                    'account_id': original.account_id.id,
                    'template_id': template.id,
                    'res_model': original.res_model,
                    'res_id': original.res_id,
                    'partner_id': original.partner_id.id,
                    'number': original.number,
                    'body': text or _("A gentle reminder: %s is waiting for you.",
                                      original.attachment_id.name or _("your document")),
                    'attachment_id': original.attachment_id.id,
                    'user_id': (original.sent_by_id or original.user_id).id,
                    'nudge_of_id': original.id,
                    'quick_replies': False,
                })
                original.nudge_created = True
                nudge.action_send()
                created |= nudge
        return created

    # ------------------------------------------------------------------ campaign links
    def _register_click(self, user_agent=''):
        """The campaign's tracked link was tapped. Returns True when it counted."""
        self.ensure_one()
        from .link_channel import PREVIEW_AGENTS
        message = self.sudo()
        if PREVIEW_AGENTS.search(user_agent or '') or not message._was_sent():
            return False
        now = fields.Datetime.now()
        first = not message.clicked_at
        message._mark_seen(now)
        if first:
            message.write({'clicked_at': now})
            message._chatter_post(Markup('<p>🔗 <b>%s</b></p>') % _(
                "%(who)s opened the link in “%(campaign)s”",
                who=message.partner_id.display_name or message.number,
                campaign=message.campaign_id.name or _('a WhatsApp message')),
                author=message.partner_id)
        return True

    def _register_opt_out(self, reason=None):
        """The doctor asked to stop.

        Under a campaign or an automation that stops marketing only: the contact's
        switch goes off, which every campaign, automation and audience checks, and
        their invoices and case updates keep coming - the stop link said "these
        messages", and a doctor who is tired of offers has not asked to stop hearing
        that their case shipped. Anywhere else (the STOP word on the paid channel) it
        stops the whole number: recorded on the conversation, which every send
        checks, and handed to business modules through `_on_opt_out`."""
        self.ensure_one()
        message = self.sudo()
        if message.opted_out_at:
            return False
        now = fields.Datetime.now()
        if message._is_marketing():
            message.write({'opted_out_at': now, 'optout_reason': (reason or '')[:60] or False})
            partner = message.partner_id
            if partner:
                partner.whatsapp_marketing_optin = False
            self.sudo().search([
                ('number', '=', message.number), ('direction', '=', 'outbound'),
                ('state', 'in', ('draft', 'ready', 'opened')),
                '|', ('campaign_id', '!=', False), ('automation_id', '!=', False)]).write({
                    'state': 'cancel', 'scheduled_at': False,
                    'error': _("The recipient asked to stop marketing messages.")})
            message._chatter_post(Markup('<p>🔕 <b>%s</b></p>') % _(
                "%s asked to stop WhatsApp marketing messages",
                partner.display_name or message.number), author=partner)
            return True
        conv = message._conversation()
        conv.write({'opt_out': True, 'opt_out_at': now})
        message.write({'opted_out_at': now, 'optout_reason': (reason or '')[:60] or False})
        # Nothing already queued for this number may go out after a STOP.
        self.sudo().search([
            ('number', '=', message.number), ('direction', '=', 'outbound'),
            ('state', 'in', ('draft', 'ready', 'opened'))]).write({
                'state': 'cancel', 'scheduled_at': False,
                'error': _("The recipient asked to stop WhatsApp messages.")})
        message._on_opt_out()
        message._chatter_post(Markup('<p>🚫 <b>%s</b></p>') % _(
            "%s asked to stop WhatsApp messages", message.partner_id.display_name
            or message.number), author=message.partner_id)
        return True

    def _on_opt_out(self):
        """Hook: business modules record the opt-out where their own sends look."""
        return True
