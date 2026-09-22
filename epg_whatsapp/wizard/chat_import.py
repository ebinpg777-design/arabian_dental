# -*- coding: utf-8 -*-
"""Bring a WhatsApp chat into Odoo: the doctor's own words, on their record.

Without the API, what a doctor types in WhatsApp stays on the lab's phone. WhatsApp
exports any chat as a text file (chat ▸ ⋮ ▸ More ▸ Export chat); this reads it,
keeps the doctor's messages that Odoo does not already have, and files the whole
export on the contact. (client, 2026-09-17)

Two line shapes, one per platform:

    12/09/26, 8:05 pm - Dr Anjali: Received, thank you        (Android)
    [12/09/26, 8:05:14 PM] Dr Anjali: Received, thank you      (iPhone)

A line that starts with neither continues the message before it.
"""
import base64
import re
from datetime import datetime, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError

LINE = re.compile(
    r'^\[?(?P<date>\d{1,2}/\d{1,2}/\d{2,4}),?\s+(?P<time>\d{1,2}:\d{2}(?::\d{2})?\s*'
    r'(?:[AaPp]\.?[Mm]\.?)?)\]?\s*(?:-\s*)?(?P<who>[^:]{1,80}?):\s(?P<text>.*)$')
DATE_TOKEN = re.compile(r'^\[?(\d{1,2})/(\d{1,2})/\d{2,4},?\s', re.M)
DAY_FIRST = ('%d/%m/%y', '%d/%m/%Y')
MONTH_FIRST = ('%m/%d/%y', '%m/%d/%Y')
TIME_FORMATS = ('%I:%M %p', '%I:%M:%S %p', '%H:%M', '%H:%M:%S')
SYSTEM_LINES = ('Messages and calls are end-to-end encrypted', '<Media omitted>',
                'This message was deleted', 'You deleted this message')


class EpgWhatsappChatImport(models.TransientModel):
    _name = 'epg.whatsapp.chat.import'
    _description = 'Import a WhatsApp Chat'

    partner_id = fields.Many2one('res.partner', string='Contact', required=True)
    file = fields.Binary('Exported Chat (.txt)', required=True)
    filename = fields.Char()
    doctor_name = fields.Char(
        'Their Name in the Chat', compute='_compute_doctor_name', store=True,
        readonly=False,
        help="How the contact appears in the export. Filled in from the file: the "
             "name that is not ours.")
    senders = fields.Char(compute='_compute_parsed')
    line_count = fields.Integer(compute='_compute_parsed')
    include_ours = fields.Boolean(
        'Also Our Messages', default=False,
        help="Log the lab's own lines that Odoo does not know as sent messages.")
    result = fields.Char(readonly=True)

    # ------------------------------------------------------------------ parsing
    @api.model
    def _date_order(self, content):
        """Day first or month first: the file says which, wherever a day passes 12.

        A phone set to an American locale writes 09/12/26 for the 12th of
        September; read day-first that is the 9th of December, and every reply in
        the chat lands three months late. Undecidable files are read day-first,
        the way this lab's phones write dates.
        """
        first_over = second_over = False
        for match in DATE_TOKEN.finditer(content):
            first, second = int(match.group(1)), int(match.group(2))
            first_over = first_over or first > 12
            second_over = second_over or second > 12
        return MONTH_FIRST if second_over and not first_over else DAY_FIRST

    @api.model
    def _parse(self, content):
        """[(when_naive_local, who, text)] from an export's text."""
        rows = []
        date_formats = self._date_order(content)
        for raw in content.splitlines():
            line = raw.strip('‎‪‬ \r\n')
            match = LINE.match(line)
            if not match:
                if rows and line:
                    rows[-1] = (rows[-1][0], rows[-1][1], rows[-1][2] + '\n' + line)
                continue
            when = self._stamp(match.group('date'), match.group('time'), date_formats)
            if not when:
                continue
            text = match.group('text').strip()
            if any(text.startswith(s) for s in SYSTEM_LINES):
                continue
            rows.append((when, match.group('who').strip(), text))
        return rows

    @api.model
    def _stamp(self, date, time, date_formats=DAY_FIRST):
        time = re.sub(r'\s+', ' ', time.replace('.', '')).strip().upper()
        for dfmt in date_formats:
            for tfmt in TIME_FORMATS:
                try:
                    return datetime.strptime('%s %s' % (date, time), '%s %s' % (dfmt, tfmt))
                except ValueError:
                    continue
        return None

    def _content(self):
        self.ensure_one()
        try:
            return base64.b64decode(self.file or b'').decode('utf-8', errors='replace')
        except Exception as exc:                                       # noqa: BLE001
            raise UserError(_("Could not read the file: %s", exc)) from exc

    @api.depends('file', 'partner_id')
    def _compute_parsed(self):
        for wizard in self:
            if not wizard.file:
                wizard.senders = wizard.line_count = False
                continue
            rows = wizard._parse(wizard._content())
            counts = {}
            for _when, who, _text in rows:
                counts[who] = counts.get(who, 0) + 1
            wizard.line_count = len(rows)
            wizard.senders = ', '.join('%s (%s)' % (who, n) for who, n in
                                       sorted(counts.items(), key=lambda kv: -kv[1]))

    @api.depends('file')
    def _compute_doctor_name(self):
        for wizard in self:
            if wizard.doctor_name or not wizard.file:
                continue
            counts = {}
            for _when, who, _text in wizard._parse(wizard._content()):
                counts[who] = counts.get(who, 0) + 1
            ours = wizard._our_names()
            wizard.doctor_name = next(
                (who for who, _n in sorted(counts.items(), key=lambda kv: -kv[1])
                 if who.lower() not in ours), False)

    def _our_names(self):
        names = {self.env.company.name, self.env.user.name}
        for account in self.env['epg.whatsapp.account'].sudo().search([]):
            names.add(account.name)
            names.add(account.upi_payee_name or '')
        return {n.lower() for n in names if n}

    # ------------------------------------------------------------------ import
    def action_import(self):
        self.ensure_one()
        rows = self._parse(self._content())
        if not rows:
            raise UserError(_("No messages were found in this file. Export the chat "
                              "from WhatsApp as a text file (without media)."))
        if not self.doctor_name:
            raise UserError(_("Say which name in the chat is the contact's."))
        Message = self.env['epg.whatsapp.message'].sudo()
        Desk = self.env['epg.whatsapp.desk']
        conv = Desk._conversation_for_partner(self.partner_id.id)
        if not conv:
            raise UserError(_("%s has no WhatsApp number and no conversation yet.",
                              self.partner_id.display_name))
        tz = pytz.timezone(self.env.user.tz or self.env.company.partner_id.tz or 'Asia/Kolkata')
        known = Message.search([('conversation_id', '=', conv.id)])
        seen = {(m.direction, (m.body or '').strip(),
                 (m.sent_at or m.create_date).replace(second=0, microsecond=0))
                for m in known}
        doctor = self.doctor_name.strip().lower()
        added_in = added_out = skipped = 0
        latest_inbound = None
        vals_list = []
        for when_local, who, text in rows:
            when = tz.localize(when_local).astimezone(pytz.utc).replace(tzinfo=None)
            inbound = who.strip().lower() == doctor
            if not inbound and not self.include_ours:
                continue
            key = ('inbound' if inbound else 'outbound', text.strip(), when.replace(second=0))
            # The same words within the same minute: already here (a quick reply, a
            # logged reply, a message sent from the Desk).
            near = {(key[0], key[1], key[2] + timedelta(minutes=d)) for d in (-1, 0, 1)}
            if near & seen:
                skipped += 1
                continue
            seen.add(key)
            # Ours carry no sender: whoever pressed Send on the phone is not in the
            # file, and the importer was not it.
            vals_list.append({
                'account_id': conv.account_id.id, 'conversation_id': conv.id,
                'partner_id': self.partner_id.id, 'number': conv.number, 'body': text,
                'direction': 'inbound' if inbound else 'outbound',
                'state': 'received' if inbound else 'sent',
                'sent_at': when,
                'res_model': 'res.partner', 'res_id': self.partner_id.id,
                'company_id': conv.company_id.id,
            })
            if inbound:
                added_in += 1
                latest_inbound = max(latest_inbound or when, when)
            else:
                added_out += 1
        if vals_list:
            Message.create(vals_list)
        if latest_inbound and (not conv.last_inbound_at or latest_inbound > conv.last_inbound_at):
            conv.sudo().write({'last_inbound_at': latest_inbound})
        self.env['ir.attachment'].sudo().create({
            'name': self.filename or _('WhatsApp chat %s.txt', self.partner_id.display_name),
            'datas': self.file, 'mimetype': 'text/plain',
            'res_model': 'res.partner', 'res_id': self.partner_id.id,
        })
        self.partner_id.sudo().message_post(
            body=_("WhatsApp chat imported by %(who)s: %(inbound)s message(s) from "
                   "%(name)s%(ours)s, %(skipped)s already known.",
                   who=self.env.user.name, inbound=added_in, name=self.doctor_name,
                   ours=(_(" and %s of ours", added_out) if added_out else ''),
                   skipped=skipped),
            message_type='comment', subtype_xmlid='mail.mt_note')
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'type': 'success', 'title': _("Chat imported"),
                       'message': _("%(inbound)s from %(name)s added, %(skipped)s already "
                                    "known.", inbound=added_in, name=self.doctor_name,
                                    skipped=skipped),
                       'next': {'type': 'ir.actions.act_window_close'}},
        }
