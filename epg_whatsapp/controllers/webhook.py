# -*- coding: utf-8 -*-
"""Meta's callback into Odoo.

Two jobs, both of which the module was blind to before:

* **Receipts** — whether a message was delivered and whether it was read. Without them
  "sent" is the last thing anyone knows, and "sent" only means Meta accepted it.
* **Inbound messages** — what the doctor wrote back. These also open the 24-hour window
  in which free-form replies are allowed, so they are what makes the send path legal.

The endpoint is public because Meta calls it unauthenticated, which makes the signature
check the only thing standing between this and a stranger. It fails closed:
unverifiable means rejected, because a forged inbound message would open the messaging
window for a number of the attacker's choosing.
"""
import json
import logging

from markupsafe import Markup

from odoo import _, fields, http
from odoo.http import request

_logger = logging.getLogger(__name__)


class EpgWhatsappWebhook(http.Controller):

    @http.route('/whatsapp/webhook', type='http', auth='public', methods=['GET'],
                csrf=False, save_session=False)
    def verify(self, **params):
        """Meta's one-time subscription handshake."""
        mode = params.get('hub.mode')
        token = params.get('hub.verify_token')
        challenge = params.get('hub.challenge', '')
        if mode != 'subscribe' or not token:
            return request.make_response('', status=400)
        account = request.env['epg.whatsapp.account'].sudo().search(
            [('webhook_token', '=', token)], limit=1)
        if not account:
            _logger.warning("WhatsApp webhook: verification with an unknown token")
            return request.make_response('', status=403)
        return request.make_response(challenge, headers=[('Content-Type', 'text/plain')])

    @http.route('/whatsapp/webhook', type='http', auth='public', methods=['POST'],
                csrf=False, save_session=False)
    def notify(self, **_params):
        raw = request.httprequest.get_data()
        signature = request.httprequest.headers.get('X-Hub-Signature-256')
        try:
            payload = json.loads(raw or b'{}')
        except ValueError:
            return request.make_response('', status=400)

        Account = request.env['epg.whatsapp.account'].sudo()
        handled = 0
        for entry in payload.get('entry') or []:
            for change in entry.get('changes') or []:
                value = change.get('value') or {}
                # Template approvals arrive at the business-account level, with no
                # phone number in them: routed by the WABA id on the entry instead.
                if change.get('field') == 'message_template_status_update':
                    account = Account._for_business_account_id(entry.get('id'))
                    if not account:
                        _logger.warning("WhatsApp webhook: no sender for business "
                                        "account %s", entry.get('id'))
                        continue
                    if not account._valid_signature(raw, signature):
                        return request.make_response('', status=403)
                    handled += self._template_status(value)
                    continue
                phone_number_id = (value.get('metadata') or {}).get('phone_number_id')
                account = Account._for_phone_number_id(phone_number_id)
                if not account:
                    _logger.warning("WhatsApp webhook: no sender for phone_number_id %s",
                                    phone_number_id)
                    continue
                if not account._valid_signature(raw, signature):
                    # One bad signature condemns the whole request: a payload we cannot
                    # attribute is one we must not act on.
                    _logger.warning("WhatsApp webhook: bad signature for sender %s",
                                    account.display_name)
                    return request.make_response('', status=403)
                handled += self._statuses(account, value)
                handled += self._messages(account, value)

        # Always 200 once past the signature check. Meta retries anything else, and a
        # message we could not parse will not parse any better the fifth time.
        _logger.info("WhatsApp webhook: handled %s event(s)", handled)
        return request.make_response('', status=200)

    # ------------------------------------------------------------------ templates
    def _template_status(self, value):
        """Meta approved, rejected or paused an approved template."""
        templates = request.env['epg.whatsapp.template'].sudo()._apply_meta_status(
            value.get('message_template_name'),
            value.get('message_template_language'),
            value.get('event'),
            reason=value.get('reason') or '')
        return len(templates)

    # ------------------------------------------------------------------ receipts
    def _statuses(self, account, value):
        Message = request.env['epg.whatsapp.message'].sudo()
        count = 0
        for status in value.get('statuses') or []:
            errors = status.get('errors') or []
            first = errors[0] if errors else {}
            if Message._apply_status(
                    status.get('id'),
                    status.get('status'),
                    timestamp=self._stamp(status.get('timestamp')),
                    error=first.get('title') or first.get('message'),
                    error_code=str(first.get('code') or '') or None):
                count += 1
        return count

    # ------------------------------------------------------------------ inbound
    def _messages(self, account, value):
        env = request.env
        Conversation = env['epg.whatsapp.conversation'].sudo()
        Message = env['epg.whatsapp.message'].sudo()
        count = 0
        for msg in value.get('messages') or []:
            number = msg.get('from')
            if not number:
                continue
            body = self._body_of(msg)
            partner = self._partner_for(number)
            conv = Conversation._get_or_create(account, number, partner)
            when = self._stamp(msg.get('timestamp')) or fields.Datetime.now()

            # This is the moment the 24-hour window opens.
            conv._note_inbound(when)

            if self._is_opt_out(body):
                conv.write({'opt_out': True, 'opt_out_at': when})
            elif self._is_opt_in(body) and conv.opt_out:
                conv.write({'opt_out': False, 'opt_out_at': False})

            inbound = Message.create({
                'account_id': account.id,
                'conversation_id': conv.id,
                'partner_id': partner.id if partner else False,
                'number': number,
                'body': body or '',
                'direction': 'inbound',
                'state': 'received',
                'external_id': msg.get('id'),
                'sent_at': when,
                'company_id': account.company_id.id or env.company.id,
            })
            if partner:
                inbound._chatter_post(Markup('<p>💬 <b>%s</b></p><p>%s</p>') % (
                    _("Received on WhatsApp"), body or ''), author=partner)
            count += 1
        return count

    @staticmethod
    def _body_of(msg):
        """Whatever this message is, in words we can store."""
        kind = msg.get('type')
        if kind == 'text':
            return (msg.get('text') or {}).get('body') or ''
        if kind in ('image', 'document', 'audio', 'video', 'sticker'):
            payload = msg.get(kind) or {}
            return payload.get('caption') or '[%s]' % kind
        if kind == 'button':
            return (msg.get('button') or {}).get('text') or '[button]'
        if kind == 'interactive':
            inter = msg.get('interactive') or {}
            for key in ('button_reply', 'list_reply'):
                if inter.get(key):
                    return inter[key].get('title') or '[reply]'
        if kind == 'location':
            loc = msg.get('location') or {}
            return '[location %s,%s]' % (loc.get('latitude'), loc.get('longitude'))
        return '[%s]' % (kind or 'message')

    @staticmethod
    def _is_opt_out(body):
        return (body or '').strip().lower() in {
            'stop', 'unsubscribe', 'opt out', 'optout', 'cancel'}

    @staticmethod
    def _is_opt_in(body):
        return (body or '').strip().lower() in {'start', 'subscribe', 'resume'}

    @staticmethod
    def _stamp(raw):
        """Meta sends unix seconds as a string."""
        try:
            return fields.Datetime.to_datetime(int(raw)) if raw else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _partner_for(number):
        """Best-effort match to a contact, on the last 9 digits.

        Country codes and punctuation vary between what a clinic typed into Odoo and
        what WhatsApp reports; the tail of the number does not.
        """
        digits = ''.join(c for c in (number or '') if c.isdigit())
        if len(digits) < 9:
            return None
        tail = digits[-9:]
        Partner = request.env['res.partner'].sudo()
        # The WhatsApp Number first: it is the number WhatsApp actually uses.
        if 'whatsapp_number' in Partner._fields:
            partner = Partner.search([('whatsapp_number', 'like', tail)], limit=1)
            if partner:
                return partner
        return Partner.search(['|', ('phone', 'like', tail), ('mobile', 'like', tail)],
                              limit=1) if 'mobile' in Partner._fields else \
            Partner.search([('phone', 'like', tail)], limit=1)
