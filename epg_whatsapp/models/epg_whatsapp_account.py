# -*- coding: utf-8 -*-
import base64
import hashlib
import hmac
import ipaddress
import logging
import uuid
from datetime import datetime, time, timedelta
from urllib.parse import urlparse

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

TIMEOUT = 10
# Prefix on the detail of a failure that is the CONNECTION, not the message: the queue
# stops the run on these instead of waiting out one timeout per message.
TRANSPORT_ERROR = '[transport] '


class EpgWhatsappAccount(models.Model):
    """A WhatsApp sender: the lab's own WhatsApp, or a Meta Cloud API number.

    Two channels (client, 2026-09-17):

    * **WhatsApp app / Web** - free. Odoo writes the message, a person sends it from
      the lab's own WhatsApp with one tap (WhatsApp Web, the desktop app, or a phone
      by QR code), and confirms. Documents travel as a private link, and the link is
      what tells Odoo the doctor opened it; quick-reply links bring answers back.
    * **Meta Cloud API** - automatic and unattended, and paid per message.

    For the API, the token is the credential for a paid channel, so it is never shown
    once saved and never written to the log.
    """
    _name = 'epg.whatsapp.account'
    _description = 'WhatsApp Sender'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    channel = fields.Selection(
        [('link', 'WhatsApp app / Web (free)'),
         ('cloud_api', 'Meta Cloud API (paid, automatic)')],
        string='Send Through', default='link', required=True,
        help="WhatsApp app / Web: Odoo prepares every message and a person sends it "
             "from the lab's own WhatsApp with one tap - no Meta account, no cost per "
             "message.\nMeta Cloud API: messages leave on their own, and Meta bills "
             "each one.")
    whatsapp_number = fields.Char(
        'Our WhatsApp Number',
        help="The number the lab sends from, shown to the person sending so they "
             "check the right WhatsApp is logged in.")
    desk_user_id = fields.Many2one(
        'res.users', string='Sent By',
        help="Who sends the automatic messages (invoice ready, dispatched...). Left "
             "empty, everyone with WhatsApp rights sees them on the Desk.")
    expire_hours = fields.Integer(
        'Drop Unsent After (hours)', default=72,
        help="A message nobody sent within this many hours is cancelled: a dispatch "
             "notice three days late does more harm than none. 0 keeps them.")
    link_valid_days = fields.Integer(
        'Document Links Valid (days)', default=90,
        help="How long the private link to an attached document keeps working.")
    # Where the links in a message point. web.base.url is whatever address the
    # admin last logged in from - on the lab's network that is 192.168.x.x, and a
    # doctor's phone cannot open it. (client, 2026-09-18)
    link_base_url = fields.Char(
        'Public Address for Links',
        help="The address doctors reach from outside, e.g. https://arabiandentallab.com. "
             "Every document, reply and payment link in a message starts with it. "
             "Empty: the system's own address (web.base.url).")
    link_problem = fields.Char(compute='_compute_link_problem')
    send_from_hour = fields.Integer(
        'Send From (hour)', default=0,
        help="Quiet hours. A message prepared before this hour waits for it - no "
             "invoice at 11 PM. 0 and 0: any time.")
    send_until_hour = fields.Integer('Send Until (hour)', default=0)
    api_cost_per_message = fields.Monetary(
        'Meta Price per Message', currency_field='currency_id',
        help="What one message would cost through the Cloud API. Used only to show "
             "what sending through WhatsApp app / Web has saved.")
    currency_id = fields.Many2one(related='company_id.currency_id')
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company)

    phone_number_id = fields.Char(
        'Phone Number ID', help="From Meta's WhatsApp Manager, not the phone number.")
    business_account_id = fields.Char('Business Account ID')
    access_token = fields.Char('Access Token', groups='base.group_system')
    api_version = fields.Char(default='v21.0', required=True)
    template_language = fields.Char(
        'Template Language', default='en',
        help="Language code of your approved templates, as registered with Meta "
             "(e.g. en, en_US, ml).")

    simulation_mode = fields.Boolean(
        'Simulation mode', default=True,
        help="Record every message exactly as it would be sent, and send nothing. "
             "The right default: a half-configured account that silently messages real "
             "doctors is far worse than one that silently messages nobody.")

    # --- webhook: how Meta tells us what happened to a message, and what came back
    webhook_token = fields.Char(
        'Verify Token', copy=False, groups='base.group_system',
        default=lambda self: uuid.uuid4().hex,
        help="Paste this into Meta's webhook setup as the Verify Token.")
    app_secret = fields.Char(
        'App Secret', groups='base.group_system',
        help="From the Meta app's Basic Settings. Used to verify that a callback "
             "really came from Meta.")
    webhook_url = fields.Char(compute='_compute_webhook_url')
    webhook_reachable = fields.Boolean(compute='_compute_webhook_url')
    webhook_warning = fields.Char(compute='_compute_webhook_url')

    message_count = fields.Integer(compute='_compute_message_count')
    conversation_count = fields.Integer(compute='_compute_message_count')

    def _compute_message_count(self):
        counts = dict(self.env['epg.whatsapp.message']._read_group(
            [('account_id', 'in', self.ids)], ['account_id'], ['__count']))
        convs = dict(self.env['epg.whatsapp.conversation']._read_group(
            [('account_id', 'in', self.ids)], ['account_id'], ['__count']))
        for account in self:
            account.message_count = counts.get(account, 0)
            account.conversation_count = convs.get(account, 0)

    def _compute_webhook_url(self):
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        reachable, detail = self._webhook_reachable()
        for account in self:
            account.webhook_url = '%s/whatsapp/webhook' % base.rstrip('/')
            account.webhook_reachable = reachable
            account.webhook_warning = detail

    @api.model
    def _webhook_reachable(self):
        """Can Meta's callback actually land on this server?

        Meta calls the webhook with no session and no cookie, so Odoo has nothing to
        resolve a database from except the dbfilter. If more than one database matches,
        every unauthenticated request is answered with a 404 — the webhook silently
        never works, and the only symptom is that delivery receipts and replies never
        arrive, which looks exactly like "WhatsApp is being slow".

        Worth surfacing rather than debugging later: it is a server configuration
        problem that no amount of correct module setup can overcome.
        """
        try:
            from odoo.http import db_filter
            from odoo.service.db import list_dbs
            matching = db_filter(list_dbs(force=True))
        except Exception:
            # list_db disabled, or no permission to enumerate — cannot judge, so say
            # nothing rather than raise a false alarm.
            return True, ''
        if len(matching) > 1:
            return False, _(
                "This server serves %(count)s databases (%(names)s), so Odoo cannot tell "
                "which one an unauthenticated request belongs to and answers Meta's "
                "callback with 404. Set a dbfilter that resolves to a single database "
                "for the hostname Meta will call.",
                count=len(matching), names=', '.join(sorted(matching)[:4]))
        return True, ''

    def action_view_conversations(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Conversations'),
            'res_model': 'epg.whatsapp.conversation', 'view_mode': 'list,form',
            'domain': [('account_id', '=', self.id)],
        }

    def action_view_messages(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Messages'),
            'res_model': 'epg.whatsapp.message', 'view_mode': 'list,form',
            'domain': [('account_id', '=', self.id)],
        }

    # ------------------------------------------------------------------ webhook
    @api.model
    def _for_business_account_id(self, waba_id):
        """Route a WhatsApp-Business-Account-level callback (template approvals)."""
        if not waba_id:
            return self.browse()
        return self.sudo().search([('business_account_id', '=', str(waba_id))], limit=1)

    def _for_phone_number_id(self, phone_number_id):
        """Route an incoming callback to the sender it belongs to."""
        if not phone_number_id:
            return self.browse()
        return self.sudo().search(
            [('phone_number_id', '=', phone_number_id)], limit=1)

    def _valid_signature(self, raw_body, header):
        """Is this callback really from Meta?

        The endpoint is public and takes anyone's POST, so without this check any
        stranger could mark messages as read or forge an inbound message — and forging
        an inbound message is not cosmetic, it opens the 24-hour window and makes the
        system willing to send free-form messages to that number.

        An account with no app secret configured cannot be verified, so it does not
        accept callbacks at all. Failing closed is the only safe direction here.
        """
        self.ensure_one()
        secret = self.sudo().app_secret
        if not secret:
            return False
        if not header or not header.startswith('sha256='):
            return False
        expected = hmac.new(secret.encode(), raw_body,
                            hashlib.sha256).hexdigest()
        # compare_digest, not ==: a plain comparison leaks the correct prefix length
        # through timing, one byte at a time.
        return hmac.compare_digest(expected, header.split('=', 1)[1].strip())

    def _link_base(self):
        """The address links start with: the sender's public address, or the system's."""
        self.ensure_one()
        base = (self.link_base_url or '').strip() or \
            self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        return base.rstrip('/')

    @api.model
    def _base_problem(self, base):
        """Why a doctor could not open links at this address, in plain words - or ''."""
        host = (urlparse(base).hostname or '') if base else ''
        if not host:
            return _("No address for links is set: set the Public Address on the sender.")
        if host == 'localhost' or host.endswith('.local') or '.' not in host:
            return _("Links point to %s, which only this computer can open. Set the "
                     "public address on the sender.", base)
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return ''
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            return _("Links point to %s, a private network address a doctor's phone "
                     "cannot open. Set the public address on the sender.", base)
        return ''

    def _link_problem(self):
        self.ensure_one()
        return self._base_problem(self._link_base())

    @api.depends('link_base_url')
    def _compute_link_problem(self):
        for account in self:
            account.link_problem = account._link_problem() if account.channel == 'link' else ''

    def _next_send_moment(self):
        """Now, or the next opening of the sending hours, as naive UTC - or False
        when the sender has no quiet hours."""
        self.ensure_one()
        start, end = self.send_from_hour or 0, self.send_until_hour or 0
        if not (0 <= start < end <= 24) or (start == 0 and end == 24):
            return False
        Message = self.env['epg.whatsapp.message']
        now = Message._local_now()
        if start <= now.hour < end:
            return False
        day = now.date() if now.hour < start else now.date() + timedelta(days=1)
        opening = Message._lab_tz().localize(datetime.combine(day, time(start)))
        return Message._utc(opening)

    # ------------------------------------------------------------------ sending
    @api.model
    def _default_account(self):
        return self.search([('company_id', 'in', (False, self.env.company.id))], limit=1)

    def _endpoint(self, path):
        return 'https://graph.facebook.com/%s/%s/%s' % (
            self.api_version, self.phone_number_id, path)

    def _headers(self):
        return {'Authorization': 'Bearer %s' % self.sudo().access_token}

    def _waba_endpoint(self, path):
        """Templates live under the business account, not the phone number."""
        return 'https://graph.facebook.com/%s/%s/%s' % (
            self.api_version, self.business_account_id, path)

    def sync_templates(self):
        """Pull every template Meta knows for this business account and stamp the
        matching ones here with their approval state.

        Returns {'fetched', 'matched', 'unknown'}: unknown are names Meta has that no
        template here claims - usually a template written straight in WhatsApp
        Manager, which is fine, but worth seeing. (client, 2026-08-28)
        """
        self.ensure_one()
        if self.simulation_mode:
            raise UserError(_("%s is in simulation mode: there is nothing to check "
                              "with Meta until it is live.", self.name))
        if not (self.business_account_id and self.sudo().access_token):
            raise UserError(_("%s needs its Business Account ID and access token "
                              "before templates can be checked.", self.name))
        Template = self.env['epg.whatsapp.template']
        url = self._waba_endpoint('message_templates')
        params = {'fields': 'name,status,language,category,rejected_reason',
                  'limit': 200}
        fetched, matched, unknown = 0, 0, []
        claimed = set(Template.sudo().search([
            ('meta_template_name', '!=', False)]).mapped('meta_template_name'))
        for _page in range(20):                    # a runaway 'next' must end
            try:
                response = requests.get(url, params=params, timeout=TIMEOUT,
                                        headers=self._headers())
            except requests.RequestException as exc:
                raise UserError(_("Could not reach Meta: %s", exc)) from exc
            if response.status_code >= 300:
                raise UserError(_("Meta refused the template list (%(code)s): %(text)s",
                                  code=response.status_code, text=response.text[:300]))
            data = response.json()
            for row in data.get('data') or []:
                fetched += 1
                name = row.get('name')
                if name in claimed:
                    matched += len(Template._apply_meta_status(
                        name, row.get('language'), row.get('status'),
                        category=row.get('category') or '',
                        reason=row.get('rejected_reason') or ''))
                elif name and name not in unknown:
                    unknown.append(name)
            url = ((data.get('paging') or {}).get('next'))
            params = None                          # 'next' carries its own query
            if not url:
                break
        return {'fetched': fetched, 'matched': matched, 'unknown': unknown}

    def _upload_media(self, attachment):
        """Put a file on Meta's servers and return its media id.

        Cloud API will not take bytes on the message call — a document is uploaded
        first and then sent by id. Skipping this step is why an attached PDF can sit on
        the log looking sent without ever having left.
        """
        self.ensure_one()
        try:
            response = requests.post(
                self._endpoint('media'), timeout=TIMEOUT, headers=self._headers(),
                data={'messaging_product': 'whatsapp',
                      'type': attachment.mimetype or 'application/pdf'},
                files={'file': (attachment.name,
                                base64.b64decode(attachment.datas or b''),
                                attachment.mimetype or 'application/pdf')})
        except requests.RequestException as exc:
            return None, str(exc)[:500]
        if response.status_code >= 300:
            return None, response.text[:500]
        return response.json().get('id'), None

    def _payload(self, number, body, attachment=None, template=None, variables=None):
        """What actually goes on the wire.

        Three shapes, and which one is used is not a preference — Meta decides:
        a template outside the 24-hour window, a document when there is a file, plain
        text otherwise.
        """
        base = {'messaging_product': 'whatsapp', 'to': number}
        if template:
            components = []
            if variables:
                components.append({
                    'type': 'body',
                    'parameters': [{'type': 'text', 'text': str(v)} for v in variables],
                })
            payload = dict(base, type='template', template={
                'name': template,
                'language': {'code': self.template_language or 'en'},
            })
            if components:
                payload['template']['components'] = components
            return payload
        if attachment:
            return dict(base, type='document', document={
                'id': attachment, 'caption': (body or '')[:1024],
                'filename': 'document.pdf',
            })
        return dict(base, type='text', text={'preview_url': False, 'body': body})

    def _send(self, number, body, attachment=None, template=None, variables=None):
        """Hand one message to Meta. Returns (ok, detail).

        Never raises: a notification that fails must not roll back the invoice that
        triggered it. The failure is recorded on the message, where somebody can see it
        and resend, rather than thrown at whoever happened to press Confirm.
        """
        self.ensure_one()
        if self.simulation_mode:
            return True, _('Simulated — not sent')
        if not (self.phone_number_id and self.sudo().access_token):
            return False, TRANSPORT_ERROR + _('This sender has no phone number ID or access token.')

        media_id = None
        if attachment and not template:
            media_id, err = self._upload_media(attachment)
            if err:
                # Losing the file is not a reason to lose the message: send the text and
                # say the attachment failed, rather than delivering nothing at all.
                _logger.warning("WhatsApp media upload failed: %s", err)

        payload = self._payload(number, body, attachment=media_id,
                                template=template, variables=variables)
        try:
            response = requests.post(self._endpoint('messages'), json=payload,
                                     timeout=TIMEOUT, headers=self._headers())
            if response.status_code < 300:
                return True, (response.json().get('messages') or [{}])[0].get('id', '')
            # Meta's own message, not a generic one: it names the actual problem
            # (unverified number, template not approved, window expired).
            detail = response.text[:500]
            _logger.warning("WhatsApp send failed (%s): %s", response.status_code, detail)
            return False, detail
        except requests.RequestException as exc:
            # Unreachable, DNS failure, timeout: nothing about the next message will be
            # different, so tell the caller this was the connection.
            _logger.warning("WhatsApp send failed: %s", exc)
            return False, TRANSPORT_ERROR + str(exc)[:480]
