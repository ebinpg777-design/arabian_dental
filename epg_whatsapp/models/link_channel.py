# -*- coding: utf-8 -*-
"""WhatsApp without the API: Odoo writes, a person taps Send, Odoo keeps the record.

Meta bills every Cloud API message, and for a lab that notifies on every case,
dispatch, invoice and payment that adds up to real money. (client, 2026-09-17)
WhatsApp itself will open a chat with the text already typed - from a link - on
WhatsApp Web, the desktop app or a phone. So on this channel:

1. an event or a person creates the message exactly as before, and it waits on the
   WhatsApp Desk as *To Send*;
2. somebody opens it in WhatsApp with one tap, presses Send there, and confirms here;
3. the record's chatter gets what was sent, by whom and when.

What the API used to give for free is rebuilt from links. A PDF cannot be attached
through a chat link, so it travels as a private link instead, and the doctor opening
that link is a genuine read receipt. Quick replies are links too: the doctor taps
"✅ Received" and the answer lands on the chatter.
"""
import io
import logging
import re
import secrets
from datetime import timedelta

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.tools import human_size
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# The link previewers that fetch a URL when a message is typed or received. Their
# visit is not the doctor opening anything, and counting it would mark every
# message read the moment it was sent.
PREVIEW_AGENTS = re.compile(
    r'whatsapp|facebookexternalhit|facebot|bot\b|crawler|spider|preview|slurp|'
    r'telegram|skype|discord|slack', re.I)

TRACKED_LINK = re.compile(r'https?://\S+/wa/[drlopfc]/\S+')
BUNDLE_DIVIDER = '\n\n┈┈┈┈┈┈┈┈┈┈\n\n'


def _button_row(line):
    """[(label, url)] for one  Label | URL  line; nothing for a line that would
    make a dead button - no label, no link, or a link whose placeholder came out
    empty (tel: with nothing after it)."""
    label, sep, url = line.partition('|')
    label, url = label.strip(), url.strip()
    scheme, _colon, rest = url.partition(':')
    if not (sep and label and _colon and rest.strip('/') and
            scheme.lower() in ('http', 'https', 'tel', 'mailto')):
        return []
    return [(label, url)]


class EpgWhatsappMessage(models.Model):
    _inherit = 'epg.whatsapp.message'

    user_id = fields.Many2one(
        'res.users', string='To Be Sent By', index='btree_not_null',
        help="Who should send it. Empty: anybody with WhatsApp rights.")
    ready_at = fields.Datetime('Waiting Since', readonly=True, copy=False)
    opened_at = fields.Datetime('Opened in WhatsApp', readonly=True, copy=False)
    opened_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    sent_by_id = fields.Many2one('res.users', string='Sent By', readonly=True, copy=False)
    final_text = fields.Text(
        'Text Sent', readonly=True, copy=False,
        help="The exact text handed to WhatsApp, links included.")

    access_token = fields.Char(readonly=True, copy=False, index='btree_not_null',
                               groups='epg_whatsapp.group_whatsapp_user')
    link_expires_at = fields.Datetime('Links Valid Until', readonly=True, copy=False)
    seen_count = fields.Integer(
        'Times Opened', readonly=True, copy=False,
        help="How often the recipient opened the document link.")
    seen_at = fields.Datetime('First Opened', readonly=True, copy=False,
                              index='btree_not_null')
    quick_replies = fields.Text(readonly=True, copy=False)
    buttons = fields.Text(
        'Link Buttons', readonly=True, copy=False,
        help="Label | URL per line, the placeholders already filled in.")
    page_opened_at = fields.Datetime('Message Page Opened', readonly=True, copy=False)
    reply_to_id = fields.Many2one('epg.whatsapp.message', string='In Reply To',
                                  index='btree_not_null', ondelete='set null')
    reply_ids = fields.One2many('epg.whatsapp.message', 'reply_to_id', string='Replies')
    reply_summary = fields.Char(compute='_compute_reply_summary')

    @api.depends('reply_ids.body')
    def _compute_reply_summary(self):
        for message in self:
            message.reply_summary = ' · '.join(message.reply_ids.mapped('body'))[:200]

    # ------------------------------------------------------------------ helpers
    def _record(self):
        """The record this message is about, or None.

        Read as superuser: the person at the Desk sends what the lab already wrote,
        and the invoice it names is not theirs to open - the Desk says what it is,
        the placeholders fill in, and the drill to the form still asks their own
        rights. Read as the user, a front-desk login without Invoicing got an
        Access Error on the whole Desk and blank headers in the text.
        """
        self.ensure_one()
        return self._records_of(self).get(self.id)

    @api.model
    def _records_of(self, messages):
        """{message.id: record or None} for the messages that are about something -
        one query per model, not one per card, and their names read in one go."""
        wanted = {}
        for message in messages:
            if message.res_model and message.res_id and message.res_model in self.env:
                wanted.setdefault(message.res_model, set()).add(message.res_id)
        found = {}
        for model, ids in wanted.items():
            records = self.env[model].sudo().browse(list(ids)).exists()
            records.mapped('display_name')
            found[model] = {record.id: record for record in records}
        return {message.id: found.get(message.res_model, {}).get(message.res_id)
                for message in messages if message.res_model}

    @api.model
    def _base_url(self):
        """What every link in a message starts with: the sender's public address."""
        account = self[:1].account_id.sudo()
        if not account:
            account = self.env['epg.whatsapp.account'].sudo().search(
                [('channel', '=', 'link')], limit=1)
        if account:
            return account._link_base()
        return self.env['ir.config_parameter'].sudo().get_param('web.base.url', '').rstrip('/')

    def _reply_labels(self):
        self.ensure_one()
        return [line.strip() for line in (self.quick_replies or '').splitlines()
                if line.strip()]

    def _button_rows(self):
        """[(label, url)] from the Label | URL lines; a line without a real link is
        left out rather than sent as a dead button."""
        self.ensure_one()
        rows = []
        for line in (self.buttons or '').splitlines():
            rows += _button_row(line)
        return rows

    @api.model
    def _from_token(self, token):
        if not token or len(token) < 12:
            return self.browse()
        return self.sudo().search([('access_token', '=', token)], limit=1)

    # ------------------------------------------------------------------ prepare
    def _prepare_link(self):
        """Ready for a person to send: links minted, sender assigned, clock started."""
        now = fields.Datetime.now()
        for message in self:
            account = message.account_id
            if account.expire_hours and message.create_date and \
                    message.create_date < now - timedelta(hours=account.expire_hours):
                # Queued long ago and never prepared: already too late to be news.
                message.write({'state': 'cancel', 'next_try_at': False, 'error': _(
                    "Not sent within %s hours, so it was dropped.", account.expire_hours)})
                continue
            vals = {
                'state': 'ready',
                'ready_at': now,
                'error': False,
                'next_try_at': False,
                # A reminder carries the document link only, not the answers again.
                'quick_replies': message.quick_replies or (
                    False if message.nudge_of_id
                    else message._doctor_template().quick_replies),
                'link_expires_at': now + timedelta(days=account.link_valid_days or 90),
            }
            if not message.sudo().access_token:
                # Sixteen characters: a link that fits on one line of a phone, and
                # still nothing anyone could guess.
                vals['access_token'] = secrets.token_urlsafe(12)
            if not message.user_id and account.desk_user_id:
                vals['user_id'] = account.desk_user_id.id
            template = message.template_id
            if not message.buttons and template.buttons and not message.nudge_of_id:
                record = message._record()
                if record is not None:
                    vals['buttons'] = template._render_text(record, template.buttons)
            # Quiet hours: prepared now, on the Desk when the lab is open.
            opening = account._next_send_moment()
            if opening and (not message.scheduled_at or message.scheduled_at < opening):
                vals['scheduled_at'] = opening
            message.sudo().write(vals)
        return True

    @api.model
    def _expire_ready(self):
        """Cancel link messages nobody sent in time. Opened ones stay: somebody may
        have pressed Send in WhatsApp and only forgotten to say so here."""
        now = fields.Datetime.now()
        dropped = 0
        for account in self.env['epg.whatsapp.account'].sudo().search(
                [('channel', '=', 'link'), ('expire_hours', '>', 0)]):
            limit = now - timedelta(hours=account.expire_hours)
            # Counted from when it was due: a message snoozed until tomorrow is not
            # late today.
            stale = self.sudo().search([
                ('account_id', '=', account.id), ('state', '=', 'ready'),
                ('ready_at', '<', limit),
                '|', ('scheduled_at', '=', False), ('scheduled_at', '<', limit)])
            if stale:
                stale.write({'state': 'cancel', 'error': _(
                    "Not sent within %s hours, so it was dropped.", account.expire_hours)})
                dropped += len(stale)
        return dropped

    def _doctor_template(self):
        """The template as this message's doctor reads it."""
        self.ensure_one()
        return self.template_id._for(self._record(), partner=self.partner_id) \
            if self.template_id else self.template_id

    def _render_part(self, text):
        """A header or footer, with its placeholders filled like the body's."""
        self.ensure_one()
        record = self._record()
        if not text or record is None or not self.template_id:
            return text or ''
        return self.template_id._render_text(record, text)

    def _link_text(self, footer=True):
        """The full text handed to WhatsApp: header, body, document link, quick
        replies, footer - in the order a doctor reads a message on a phone."""
        self.ensure_one()
        # Through sudo throughout: the token is a guarded field, and an attachment
        # rendered by the queue belongs to OdooBot.
        sudo = self.sudo()
        base = self._base_url()
        token = sudo.access_token
        parts = []
        # A reminder is a short line of its own, not the notification over again.
        template = self._doctor_template()
        header = '' if self.nudge_of_id else self._render_part(template.header_text)
        if header:
            parts.append('*%s*' % header.strip())
        parts.append((self.body or '').strip())
        campaign = sudo.campaign_id
        if campaign.image and token:
            # A picture cannot ride on a chat link: the flyer page shows it, with
            # the text and the buttons, and the plain link becomes a button there.
            parts.append('🖼 %s\n%s/wa/f/%s' % (
                campaign.headline or _('See more'), base, token))
        elif campaign.link_url and token:
            parts.append('🔗 %s\n%s/wa/l/%s' % (
                campaign.link_label or _('Open'), base, token))
        # The tappable things: the document, the payment, the answers, the buttons.
        # Each its own link in the text - or, on a "page" template, one link to a
        # page where they are real buttons.
        tappable = []
        if sudo.attachment_id and token:
            tappable.append('📄 %s\n%s/wa/d/%s' % (
                sudo.attachment_id.name or _('Document'), base, token))
        pay = self._pay_text(base, token)
        if pay:
            tappable.append(pay)
        labels = self._reply_labels()
        if labels and token:
            lines = [_("Tap to reply:")]
            for index, label in enumerate(labels, start=1):
                lines.append('%s → %s/wa/r/%s/%s' % (label, base, token, index))
            tappable.append('\n'.join(lines))
        buttons = self._button_rows()
        if buttons and token:
            tappable.append('\n'.join('🔗 %s\n%s/wa/b/%s/%d' % (label, base, token, index)
                                      for index, (label, _url) in enumerate(buttons, start=1)))
        if tappable and self.template_id.button_style == 'page':
            # One link, and it says what it opens: WhatsApp shows it as a card, and
            # the buttons are real buttons on the other side.
            label = self._render_part(template.page_label)
            parts.append('%s\n%s/wa/m/%s' % (
                label.strip() if label else '👉 %s' % _("Tap here to open"), base, token))
        else:
            parts.extend(tappable)
        if (campaign.optout or sudo.automation_id.optout) and token:
            parts.append(_("_Don't want these messages? Tap: %(url)s_",
                           url='%s/wa/o/%s' % (base, token)))
        footer = footer and self._render_part(template.footer_text)
        if footer:
            parts.append('_%s_' % footer.strip())
        return '\n\n'.join(part for part in parts if part)

    def _bundle_text(self):
        """Several messages for one doctor, as the one WhatsApp they receive.

        Each keeps its own document link and quick replies; the footer is said once,
        at the end, where a signature belongs.
        """
        messages = list(self)
        if len(messages) == 1:
            return messages[0]._link_text()
        texts = [message._link_text(footer=(index == len(messages) - 1))
                 for index, message in enumerate(messages)]
        return BUNDLE_DIVIDER.join(texts)

    # ------------------------------------------------------------------ the person
    def _check_link_user(self):
        if not (self.env.su or self.env.user.has_group('epg_whatsapp.group_whatsapp_user')):
            raise UserError(_("Sending WhatsApp messages needs the 'WhatsApp: send' right."))

    def get_open_payload(self):
        """Everything the send dialog needs, in one call - for one message, or for
        several waiting for the same doctor, sent as one WhatsApp."""
        self._check_link_user()
        messages = self.exists().filtered(lambda m: m.direction == 'outbound')
        drafts = messages.filtered(lambda m: m.state == 'draft' and m.channel == 'link')
        if drafts:
            # Still in the queue: prepared now rather than by the next cron run.
            drafts.action_send()
        usable, blocked = self.browse(), []
        for message in messages:
            if message.state in ('cancel', 'error') and message.error:
                blocked.append(message.error)
                continue
            if message.state not in ('ready', 'opened', 'sent', 'read'):
                continue
            if message.state in ('ready', 'opened'):
                reason = message._send_blocked_reason()
                if reason:
                    # Returned, not raised: an exception would roll the cancellation
                    # back and the refused message would come straight back.
                    message.write({'state': 'cancel', 'error': reason[:500]})
                    blocked.append(reason)
                    continue
            usable |= message
        if not usable:
            return {'id': self[:1].id, 'ids': [],
                    'blocked': blocked[0] if blocked else
                    _("This message cannot be sent any more.")}
        if len(set(usable.mapped('number'))) > 1:
            raise UserError(_("Only messages to the same number can go as one."))
        # The PDF is rendered here, for the message about to go, and nowhere
        # earlier: the Desk lists two hundred messages without paying two seconds
        # of wkhtmltopdf for each.
        usable._attach_pending_reports()
        first = usable[0]
        text = usable._bundle_text()
        records = self._records_of(usable)
        return {
            'id': first.id,
            'ids': usable.ids,
            'state': 'opened' if all(m.state == 'opened' for m in usable) else first.state,
            'number': first.number,
            'text': text,
            'length': len(text),
            # The words alone, for editing in the dialog; a bundle is edited on the Desk.
            'body': first.body or '' if len(usable) == 1 else '',
            'partner': first.partner_id.display_name or '',
            'record': ', '.join(dict.fromkeys(
                r.display_name for r in records.values() if r is not None)),
            'template': ', '.join(dict.fromkeys(
                m.template_id.name or m.campaign_id.name or '' for m in usable if
                m.template_id or m.campaign_id)),
            'document': ', '.join(n for n in usable.sudo().mapped('attachment_id.name') if n),
            # The file itself, for a phone's share sheet or a download to drop into
            # WhatsApp Web: the link is what Odoo can track, the file is what some
            # doctors want in the chat.
            'attachments': [{
                'id': att.id, 'name': att.name or _('Document'),
                'mimetype': att.mimetype or 'application/pdf',
                'size': human_size(att.file_size or 0) if att.file_size else '',
                'url': '/web/content/%d?download=true' % att.id,
            } for att in usable.sudo().mapped('attachment_id')],
            'replies': [label for m in usable for label in m._reply_labels()],
            'sender_number': first.account_id.whatsapp_number or '',
            'open_with': self.env.user.whatsapp_open_with or 'auto',
            'blocked_others': blocked,
            'warnings': usable._send_warnings(),
            'history': first._history(exclude=usable),
            'best_time': self._best_times([first.number]).get(first.number) or False,
        }

    def get_qr(self):
        """The QR code that hands these messages to a phone - drawn only when a
        phone is chosen, not on every open."""
        self._check_link_user()
        usable = self.exists().filtered(
            lambda m: m.direction == 'outbound' and m.state in ('ready', 'opened'))
        if not usable:
            return ''
        return self._qr_svg('https://wa.me/%s?text=%s' % (
            usable[0].number, _quote(usable._bundle_text())))

    @api.model
    def _qr_svg(self, value):
        """A QR code as inline SVG, for handing a message to a phone."""
        try:
            import qrcode
            import qrcode.image.svg
        except ImportError:
            return ''
        try:
            qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L,
                               box_size=10, border=2)
            qr.add_data(value)
            qr.make(fit=True)
            image = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
            stream = io.BytesIO()
            image.save(stream)
            svg = stream.getvalue().decode()
            return svg[svg.index('<svg'):] if '<svg' in svg else svg
        except Exception as exc:                                   # noqa: BLE001
            _logger.info("WhatsApp QR not drawn: %s", exc)
            return ''

    def action_mark_opened(self):
        self._check_link_user()
        now = fields.Datetime.now()
        ready = self.filtered(lambda m: m.state == 'ready')
        ready._attach_pending_reports()
        for message in ready:
            message.write({'state': 'opened', 'opened_at': now,
                           'opened_by_id': self.env.user.id,
                           'final_text': message._link_text()})
        return True

    def action_mark_sent(self):
        """The person pressed Send in WhatsApp."""
        self._check_link_user()
        now = fields.Datetime.now()
        waiting = self.filtered(lambda m: m.state in ('ready', 'opened'))
        waiting._attach_pending_reports()
        for message in waiting:
            message.write({
                'state': 'sent', 'sent_at': now, 'sent_by_id': self.env.user.id,
                'final_text': message.final_text or message._link_text(),
            })
            message._conversation()._note_outbound(now)
            message._chatter_sent()
        return True

    def action_mark_not_sent(self):
        self._check_link_user()
        self.filtered(lambda m: m.state == 'opened').write(
            {'state': 'ready', 'opened_at': False, 'opened_by_id': False})
        return True

    def action_discard(self):
        """A message made for one click and never opened: gone, as if never written.

        Never a message that was opened in WhatsApp. Whoever opened it may well
        have pressed Send there and simply closed this window; deleting it would
        take its links with it, and the doctor's document, answers and payment
        link would all die in their chat - which is exactly what happened on
        production the day this button shipped. Such a message stays on the Desk
        as opened-but-unconfirmed, where it can still be confirmed or cancelled,
        and it marks itself sent the moment the doctor opens one of its links.
        (production, 2026-09-18)
        """
        self._check_link_user()
        untouched = self.filtered(
            lambda m: m.channel == 'link' and m.state == 'ready'
            and not m.opened_at and not m.sent_at and not m.seen_at)
        untouched.sudo().unlink()
        return True

    def action_cancel(self):
        self._check_link_user()
        self.filtered(lambda m: m.state in ('draft', 'ready', 'opened', 'error')).write(
            {'state': 'cancel', 'next_try_at': False,
             'error': _("Cancelled by %s.", self.env.user.name)})
        return True

    def action_open_in_whatsapp(self):
        """Button on the message form and list: the same send dialog as the Desk."""
        self.ensure_one()
        return {'type': 'ir.actions.client', 'tag': 'epg_whatsapp_send',
                'params': {'message_id': self.id}}

    def log_reply(self, text):
        """A reply typed into WhatsApp, pasted here so the record knows about it."""
        self.ensure_one()
        self._check_link_user()
        text = (text or '').strip()
        if not text:
            raise UserError(_("Paste or type the reply first."))
        reply = self._create_reply(text)
        self._chatter_post(self._chatter_reply_markup(text, self.env.user.name))
        return reply.id

    @api.model
    def _chatter_reply_markup(self, text, who):
        return Markup('<p>💬 <b>%s</b> %s</p><p>%s</p>') % (
            _("Reply on WhatsApp"), _("(logged by %s)", who), _lines(text))

    # ------------------------------------------------------------------ recipients
    def _create_reply(self, text):
        self.ensure_one()
        sudo = self.sudo()
        conv = sudo._conversation()
        reply = sudo.create({
            'account_id': self.account_id.id,
            'conversation_id': conv.id,
            'partner_id': self.partner_id.id,
            'number': self.number,
            'body': text,
            'direction': 'inbound',
            'state': 'received',
            'reply_to_id': self.id,
            'res_model': self.res_model,
            'res_id': self.res_id,
            'campaign_id': sudo.campaign_id.id,
            'automation_id': sudo.automation_id.id,
            'sent_at': fields.Datetime.now(),
            'company_id': self.company_id.id,
        })
        conv._note_inbound()
        return reply

    def _links_alive(self):
        self.ensure_one()
        return not self.link_expires_at or self.link_expires_at > fields.Datetime.now()

    def _was_sent(self):
        """Opened by the recipient means it was sent, confirmed here or not."""
        self.ensure_one()
        message = self.sudo()
        if message.state == 'opened':
            message.write({'state': 'sent', 'sent_at': fields.Datetime.now(),
                           'sent_by_id': message.opened_by_id.id})
            message._conversation()._note_outbound()
            message._chatter_sent()
        return message.state in ('sent', 'read', 'delivered')

    def _mark_seen(self, now=None, count=True):
        """The recipient read it - a document, a link, the payment page. The first
        time turns the message Read and stands any reminder down; every time is
        counted. Returns True the first time."""
        self.ensure_one()
        message = self.sudo()
        now = now or fields.Datetime.now()
        first = not message.seen_at
        vals = {'seen_count': message.seen_count + 1} if count else {}
        if first:
            vals.update({'seen_at': now, 'state': 'read', 'read_at': now})
        if vals:
            message.write(vals)
        if first:
            message._document_seen()
        return first

    def _register_seen(self, user_agent=''):
        """The document link was opened. Returns True when it counted."""
        self.ensure_one()
        message = self.sudo()
        if PREVIEW_AGENTS.search(user_agent or ''):
            return False
        if not message._was_sent():
            return False
        if message._mark_seen():
            message._chatter_post(Markup('<p>👁 <b>%s</b></p>') % _(
                "%(who)s opened %(doc)s from WhatsApp",
                who=message.partner_id.display_name or message.number,
                doc=message.attachment_id.name or _("the document")),
                author=message.partner_id)
        return True

    def _register_button(self, index, user_agent=''):
        """A link button was tapped. Returns the URL to go on to, or ''."""
        self.ensure_one()
        message = self.sudo()
        rows = message._button_rows()
        if not (1 <= index <= len(rows)):
            return ''
        label, url = rows[index - 1]
        if PREVIEW_AGENTS.search(user_agent or '') or not message._was_sent():
            return url
        now = fields.Datetime.now()
        message._mark_seen(now, count=False)
        if not message.clicked_at:
            message.write({'clicked_at': now})
        message._chatter_post(Markup('<p>🔗 <b>%s</b></p>') % _(
            "%(who)s tapped “%(label)s” on WhatsApp",
            who=message.partner_id.display_name or message.number, label=label),
            author=message.partner_id)
        return url

    def _register_page_open(self, user_agent=''):
        """The message page was opened: read, said once."""
        self.ensure_one()
        message = self.sudo()
        if PREVIEW_AGENTS.search(user_agent or '') or not message._was_sent():
            return False
        if message.page_opened_at:
            return True
        now = fields.Datetime.now()
        message._mark_seen(now, count=False)
        message.write({'page_opened_at': now})
        message._chatter_post(Markup('<p>👁 <b>%s</b></p>') % _(
            "%s opened the message page", message.partner_id.display_name or message.number),
            author=message.partner_id)
        return True

    def _page(self):
        """The message page: the text and every button of one message, for the
        flyer template."""
        self.ensure_one()
        from .marketing import wa_html
        sudo = self.sudo()
        base = self._base_url()
        token = sudo.access_token
        template = self._doctor_template()
        buttons = []
        if sudo.attachment_id:
            # The report's name on the button - "Tax Invoice" - not the file name
            # with the record number and the extension in it.
            buttons.append({'label': '📄 %s' % (
                template.report_id.name or sudo.attachment_id.name or _('Document')),
                'url': '%s/wa/d/%s' % (base, token), 'kind': 'link'})
        if self.pay_amount and self.account_id.upi_id:
            buttons.append({'label': '💳 %s' % _("Pay %s now", self.env['epg.whatsapp.template']._money(self.pay_amount, self.currency_id)),
                            'url': '%s/wa/p/%s' % (base, token), 'kind': 'pay'})
        buttons += [{'label': label, 'url': '%s/wa/b/%s/%d' % (base, token, index), 'kind': 'link'}
                    for index, (label, _url) in enumerate(self._button_rows(), start=1)]
        number = re.sub(r'\D', '', self.account_id.whatsapp_number or '')
        marketing = sudo.campaign_id or sudo.automation_id
        return {
            'company_name': self.company_id.sudo().name or '',
            'headline': self._preview_title(),
            # The heading is the body's own first line when the template has no
            # header: printing it again under itself reads as a stutter.
            'lines': wa_html(self._page_body()),
            'facts': self._page_facts(),
            'summary': self._preview_summary(),
            'image': '',
            'replies': [{'label': label, 'url': '%s/wa/r/%s/%d' % (base, token, index)}
                        for index, label in enumerate(self._reply_labels(), start=1)],
            'link': None,
            'buttons': buttons,
            'chat': 'https://wa.me/%s' % number if number else '',
            'stop': '%s/wa/o/%s' % (base, token) if marketing and marketing.optout else '',
            'personal': True,
        }

    def _plain_lines(self):
        """The message's lines without WhatsApp's marks, greetings dropped."""
        self.ensure_one()
        out = []
        for line in (self.body or '').splitlines():
            text = re.sub(r'[*_~]', '', line).strip()
            if text and not text.lower().startswith(('dear', 'hello', 'hi ')):
                out.append(text)
        return out

    def _page_facts(self):
        """Where the case stands, for the page the doctor opens: [(label, value)].

        Empty here - epg_whatsapp knows nothing about orders or invoices. A
        business module fills it in, and the doctor tapping "Open case" sees the
        stage their work is at rather than a message they have already read.
        (client, 2026-09-18)
        """
        self.ensure_one()
        return []

    def _page_body(self):
        """The message as the page shows it, without the line that became its heading.

        The heading is the first line that is not a greeting, so that is the line
        to drop - not simply the first one, or "Dear Dr Menon," would go and the
        heading would stay twice.
        """
        self.ensure_one()
        body = self.body or ''
        if self._render_part(self.template_id.header_text):
            return body
        title = self._preview_title()
        lines = body.splitlines()
        for index, line in enumerate(lines):
            if re.sub(r'[*_~]', '', line).strip() == title:
                kept = lines[:index] + lines[index + 1:]
                return '\n'.join(kept).strip('\n')
        return body

    def _preview_title(self):
        """The card's title: the message's own heading - "🧾 Invoice OC245822" -
        rather than the template's name, which is the lab's word, not the doctor's."""
        self.ensure_one()
        header = self._render_part(self.template_id.header_text)
        if header:
            return header.strip()
        lines = self._plain_lines()
        return lines[0][:80] if lines else (self.template_id.name or _('Message'))

    def _preview_summary(self):
        """The line under it: the facts, not the whole message."""
        self.ensure_one()
        lines = self._plain_lines()
        if not self._render_part(self.template_id.header_text):
            lines = lines[1:]
        return ' · '.join(lines)[:150] or (self.company_id.sudo().name or '')

    def _register_quick_reply(self, index, user_agent=''):
        """A quick-reply link was tapped. Returns the answer, or '' when ignored."""
        self.ensure_one()
        message = self.sudo()
        labels = message._reply_labels()
        if not (1 <= index <= len(labels)) or PREVIEW_AGENTS.search(user_agent or ''):
            return ''
        label = labels[index - 1]
        if not message._was_sent():
            return ''
        if label in message.reply_ids.mapped('body'):
            return label                    # tapped twice: said once
        message._create_reply(label)
        if message.state != 'read':
            now = fields.Datetime.now()
            message.write({'state': 'read', 'read_at': now})
        message._chatter_post(Markup('<p>💬 <b>%s</b> %s</p>') % (
            _("%s replied on WhatsApp:", message.partner_id.display_name or message.number),
            label), author=message.partner_id)
        message._on_quick_reply(label, index)
        return label

    def _on_quick_reply(self, label, index):
        """Hook: business modules act on an answer (a rating, a receipt)."""
        return True

    # ------------------------------------------------------------------ chatter
    def _chatter_target(self):
        self.ensure_one()
        record = self._record()
        if record is not None and hasattr(record, 'message_post'):
            return record
        if self.partner_id:
            return self.partner_id
        return None

    def _chatter_post(self, body, author=None):
        """Post on the record the message is about. Never raises: a chatter line
        must not undo the send it describes."""
        for message in self:
            target = message._chatter_target()
            if target is None:
                continue
            if not author:
                user = message.env.user
                author = message.env.ref('base.partner_root') if user._is_public() \
                    else user.partner_id
            try:
                target.sudo().message_post(
                    body=body, message_type='comment', subtype_xmlid='mail.mt_note',
                    author_id=author.id)
            except Exception as exc:                                  # noqa: BLE001
                _logger.warning("WhatsApp: chatter post on %s failed: %s", target, exc)

    def _chatter_sent(self):
        for message in self:
            how = _("via Meta Cloud API") if message.channel == 'cloud_api' \
                else _("from WhatsApp")
            who = message.sent_by_id.name or message.env.user.name
            # The private links stay out of the chatter: anyone who can read the
            # record would otherwise hold the doctor's reply buttons.
            text = TRACKED_LINK.sub('🔗', message.final_text or message.body or '')
            # Paragraphs, not a blockquote: the chatter folds a blockquote away as
            # quoted text, and the message is the whole point of the note.
            message._chatter_post(Markup(
                '<p><b>📱 %s</b> %s</p><p>%s</p>') % (
                _("WhatsApp to %(to)s (%(number)s)",
                  to=message.partner_id.display_name or _("contact"),
                  number=message.number),
                _("sent %(how)s by %(who)s", how=how, who=who),
                _lines(text)))


def _lines(text):
    return Markup('<br/>').join(escape(line) for line in (text or '').splitlines())


def _quote(text):
    from urllib.parse import quote
    return quote(text or '', safe='')
