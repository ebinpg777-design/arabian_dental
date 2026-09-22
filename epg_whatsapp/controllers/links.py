# -*- coding: utf-8 -*-
"""The two public links a WhatsApp app / Web message carries.

`/wa/d/<token>` - the attached document. A link previewer is answered with a small
page carrying the document's title (so the chat shows a proper card) and is not
counted; the doctor gets the PDF, and the first open is the read receipt.

`/wa/r/<token>/<n>` - quick reply number n. The answer is recorded once, on the
record's chatter, and the doctor sees a short thank-you.

`/wa/l/<token>` - a campaign's tracked link: counted, then redirected.

`/wa/o/<token>` - stop these messages: asked on GET, done on POST.
"""
import base64

from markupsafe import Markup

from odoo import http
from odoo.http import content_disposition, request
from odoo.tools.mimetypes import guess_mimetype


class WhatsappLinks(http.Controller):

    STOP_REASONS = (
        ('too_many', 'Too many messages'),
        ('not_relevant', 'Not relevant to me'),
        ('wrong_number', 'Wrong number'),
        ('stop', 'Just stop'),
    )

    def _page(self, message, title, text, icon='✅', status=200, form_action=None,
              buttons=None, ask_number=False, company=None):
        company = company or (message.company_id if message else request.env.company)
        response = request.render('epg_whatsapp.link_page', {
            'title': title, 'text': text, 'icon': icon,
            'company_name': company.sudo().name or '',
            'form_action': form_action, 'buttons': buttons or [], 'ask_number': ask_number,
        })
        response.status_code = status
        return response

    def _stop_buttons(self):
        return [{'name': 'reason', 'value': value, 'label': label}
                for value, label in self.STOP_REASONS]

    @staticmethod
    def _agent():
        return request.httprequest.headers.get('User-Agent', '')

    @staticmethod
    def _image_response(campaign):
        data = base64.b64decode(campaign.image or b'')
        if not data:
            return request.not_found()
        return request.make_response(data, headers=[
            ('Content-Type', guess_mimetype(data, default='image/png')),
            ('Content-Length', str(len(data))),
            ('Cache-Control', 'private, max-age=3600'),
            ('X-Robots-Tag', 'noindex'),
        ])

    @http.route('/wa/d/<string:token>', type='http', auth='public', methods=['GET'],
                sitemap=False, readonly=False)
    def document(self, token, **kwargs):
        Message = request.env['epg.whatsapp.message'].sudo()
        message = Message._from_token(token)
        if not message:
            return self._page(None, 'Link not found',
                              'This link is not valid. Please ask us to send it again.',
                              icon='🔗', status=404)
        if not message.attachment_id:
            # The message is there, the file is not: say so rather than call the
            # doctor's own link invalid.
            return self._page(message, 'Document not available',
                              'We cannot find this document any more. Reply on '
                              'WhatsApp and we will send it again.',
                              icon='📄', status=404)
        if not message._links_alive():
            return self._page(message, 'Link expired',
                              'This link has expired. Reply on WhatsApp and we will '
                              'send the document again.', icon='⌛', status=410)
        agent = request.httprequest.headers.get('User-Agent', '')
        message._register_seen(agent)
        attachment = message.attachment_id.sudo()
        content = attachment.raw or b''
        return request.make_response(content, headers=[
            ('Content-Type', attachment.mimetype or 'application/pdf'),
            ('Content-Length', str(len(content))),
            ('Content-Disposition', content_disposition(attachment.name or 'document.pdf',
                                                        'inline')),
            ('Cache-Control', 'private, no-store'),
            ('X-Robots-Tag', 'noindex'),
        ])

    @http.route('/wa/r/<string:token>/<int:index>', type='http', auth='public',
                methods=['GET'], sitemap=False, readonly=False)
    def quick_reply(self, token, index, **kwargs):
        Message = request.env['epg.whatsapp.message'].sudo()
        message = Message._from_token(token)
        if not message:
            return self._page(None, 'Link not found',
                              'This link is not valid.', icon='🔗', status=404)
        labels = message._reply_labels()
        if not (1 <= index <= len(labels)):
            return self._page(message, 'Link not found',
                              'This answer is not available.', icon='🔗', status=404)
        if not message._links_alive():
            return self._page(message, 'Link expired',
                              'This link has expired. Please reply on WhatsApp instead.',
                              icon='⌛', status=410)
        agent = request.httprequest.headers.get('User-Agent', '')
        label = message._register_quick_reply(index, agent) or labels[index - 1]
        return self._page(message, 'Thank you',
                          'We have your answer: %s' % label, icon='💬')

    @http.route('/wa/l/<string:token>', type='http', auth='public', methods=['GET'],
                sitemap=False, readonly=False)
    def tracked_link(self, token, **kwargs):
        """A campaign's link: counted, then on to where it points."""
        message = request.env['epg.whatsapp.message'].sudo()._from_token(token)
        url = message.campaign_id.link_url if message else ''
        if not url or not url.lower().startswith(('http://', 'https://')):
            return self._page(None, 'Link not found', 'This link is not valid.',
                              icon='🔗', status=404)
        message._register_click(request.httprequest.headers.get('User-Agent', ''))
        return request.redirect(url, code=302, local=False)

    @http.route('/wa/o/<string:token>', type='http', auth='public', methods=['GET', 'POST'],
                sitemap=False, readonly=False, csrf=False)
    def opt_out(self, token, **kwargs):
        """Stop these messages. A GET only asks: link previewers fetch every link in
        a chat, and one of them must never opt a doctor out. The button POSTs."""
        message = request.env['epg.whatsapp.message'].sudo()._from_token(token)
        if not message:
            return self._page(None, 'Link not found', 'This link is not valid.',
                              icon='🔗', status=404)
        # Under an offer the stop is for offers; updates about the doctor's own
        # cases and invoices are not what they are tired of.
        marketing = message._is_marketing()
        done = ('You will not receive offers and news from us on WhatsApp any more. '
                'Updates about your own cases and invoices still come.'
                if marketing else 'You will not receive these WhatsApp messages any more.')
        already = message.opted_out_at or message.conversation_id.opt_out or (
            marketing and message.partner_id and not message.partner_id.whatsapp_marketing_optin)
        if already:
            return self._page(message, 'You are unsubscribed', done, icon='🔕')
        if request.httprequest.method != 'POST':
            return self._page(message, 'Stop these messages?',
                              'Tap a reason and we will stop sending you offers and news. '
                              'Updates about your own cases and invoices still come.'
                              if marketing else
                              'Tap a reason and we will stop sending you these messages.',
                              icon='🔔', form_action='/wa/o/%s' % token,
                              buttons=self._stop_buttons())
        message._register_opt_out(kwargs.get('reason'))
        return self._page(message, 'You are unsubscribed', done, icon='🔕')

    @http.route('/wa/p/<string:token>', type='http', auth='public', methods=['GET', 'POST'],
                sitemap=False, readonly=False, csrf=False)
    def pay(self, token, **kwargs):
        """Pay now: the amount, the doctor's UPI app opened with it filled in, a QR
        code, and an "I've paid" button that tells the lab."""
        message = request.env['epg.whatsapp.message'].sudo()._from_token(token)
        if not message or not message.pay_amount:
            return self._page(None, 'Link not found', 'This link is not valid.',
                              icon='🔗', status=404)
        if not message._links_alive():
            return self._page(message, 'Link expired',
                              'This link has expired. Reply on WhatsApp and we will '
                              'send it again.', icon='⌛', status=410)
        agent = request.httprequest.headers.get('User-Agent', '')
        if request.httprequest.method == 'POST':
            message._register_paid_claim()
            return self._page(message, 'Thank you',
                              'We have noted your payment of %s and will confirm it '
                              'shortly.' % message.currency_id.format(message.pay_amount),
                              icon='💚')
        message._register_pay_open(agent)
        page = message.get_pay_page()
        # The QR code is our own SVG: said so, or the template escapes it into a
        # thousand-character word that stretches the page off the phone.
        response = request.render('epg_whatsapp.pay_page', dict(
            page, qr=Markup(page['qr']), company_name=message.company_id.sudo().name or '',
            form_action='/wa/p/%s' % token))
        response.headers['X-Robots-Tag'] = 'noindex'
        return response

    # ------------------------------------------------------------------ the flyer
    @http.route('/wa/f/<string:token>', type='http', auth='public', methods=['GET'],
                sitemap=False, readonly=False)
    def flyer(self, token, **kwargs):
        """A campaign's picture, words and buttons, for one doctor."""
        message = request.env['epg.whatsapp.message'].sudo()._from_token(token)
        campaign = message.campaign_id if message else None
        if not campaign or not campaign.image:
            return self._page(None, 'Link not found', 'This link is not valid.',
                              icon='🔗', status=404)
        if not message._links_alive():
            return self._page(message, 'Link expired',
                              'This link has expired. Reply on WhatsApp and we will '
                              'send it again.', icon='⌛', status=410)
        message._register_click(self._agent())
        response = request.render('epg_whatsapp.flyer_page', campaign._flyer(message))
        response.headers['X-Robots-Tag'] = 'noindex'
        return response

    @http.route('/wa/f/<string:token>/img', type='http', auth='public', methods=['GET'],
                sitemap=False)
    def flyer_image(self, token, **kwargs):
        message = request.env['epg.whatsapp.message'].sudo()._from_token(token)
        if not message or not message.campaign_id.image:
            return request.not_found()
        return self._image_response(message.campaign_id)

    # ------------------------------------------------------------------ the broadcast
    def _campaign(self, ctoken):
        return request.env['epg.whatsapp.campaign']._from_token(ctoken)

    @http.route('/wa/c/<string:ctoken>', type='http', auth='public', methods=['GET'],
                sitemap=False, readonly=False)
    def broadcast_page(self, ctoken, **kwargs):
        """The flyer of a broadcast: counted once per browser, never for a previewer."""
        from ..models.link_channel import PREVIEW_AGENTS
        campaign = self._campaign(ctoken)
        if not campaign:
            return self._page(None, 'Link not found', 'This link is not valid.',
                              icon='🔗', status=404)
        cookie = 'wa_c_%s' % campaign.id
        counted = request.httprequest.cookies.get(cookie)
        if not counted and not PREVIEW_AGENTS.search(self._agent()):
            campaign.write({'broadcast_opens': campaign.broadcast_opens + 1})
        response = request.render('epg_whatsapp.flyer_page', campaign._flyer())
        response.headers['X-Robots-Tag'] = 'noindex'
        if not counted:
            response.set_cookie(cookie, '1', max_age=365 * 24 * 3600, samesite='Lax')
        return response

    @http.route('/wa/c/<string:ctoken>/img', type='http', auth='public', methods=['GET'],
                sitemap=False)
    def broadcast_image(self, ctoken, **kwargs):
        campaign = self._campaign(ctoken)
        if not campaign or not campaign.image:
            return request.not_found()
        return self._image_response(campaign)

    @http.route('/wa/c/<string:ctoken>/l', type='http', auth='public', methods=['GET'],
                sitemap=False, readonly=False)
    def broadcast_link(self, ctoken, **kwargs):
        from ..models.link_channel import PREVIEW_AGENTS
        campaign = self._campaign(ctoken)
        url = campaign.link_url if campaign else ''
        if not url or not url.lower().startswith(('http://', 'https://')):
            return self._page(None, 'Link not found', 'This link is not valid.',
                              icon='🔗', status=404)
        if not PREVIEW_AGENTS.search(self._agent()):
            campaign.write({'broadcast_link_clicks': campaign.broadcast_link_clicks + 1})
        return request.redirect(url, code=302, local=False)

    @http.route('/wa/c/<string:ctoken>/r/<int:index>', type='http', auth='public',
                methods=['GET', 'POST'], sitemap=False, readonly=False, csrf=False)
    def broadcast_reply(self, ctoken, index, **kwargs):
        """A quick reply from a broadcast: the doctor says which number they are."""
        campaign = self._campaign(ctoken)
        labels = campaign._reply_labels() if campaign else []
        if not (1 <= index <= len(labels)):
            return self._page(None, 'Link not found', 'This answer is not available.',
                              icon='🔗', status=404)
        label = labels[index - 1]
        company = campaign.company_id
        if request.httprequest.method == 'POST':
            if campaign._broadcast_reply(index, kwargs.get('number', '')):
                return self._page(None, 'Thank you', 'We have your answer: %s' % label,
                                  icon='💬', company=company)
            return self._page(None, 'One more thing', 'Please enter the WhatsApp number '
                              'this message reached you on.', icon='📱',
                              form_action='/wa/c/%s/r/%s' % (ctoken, index), ask_number=True,
                              buttons=[{'name': 'confirm', 'value': '1', 'label': label}],
                              company=company)
        return self._page(None, label, 'Enter the WhatsApp number this message reached '
                          'you on, so we know who is answering.', icon='💬',
                          form_action='/wa/c/%s/r/%s' % (ctoken, index), ask_number=True,
                          buttons=[{'name': 'confirm', 'value': '1', 'label': label}],
                          company=company)

    @http.route('/wa/c/<string:ctoken>/o', type='http', auth='public',
                methods=['GET', 'POST'], sitemap=False, readonly=False, csrf=False)
    def broadcast_stop(self, ctoken, **kwargs):
        campaign = self._campaign(ctoken)
        if not campaign:
            return self._page(None, 'Link not found', 'This link is not valid.',
                              icon='🔗', status=404)
        company = campaign.company_id
        if request.httprequest.method == 'POST' and campaign._broadcast_stop(
                kwargs.get('number', ''), kwargs.get('reason')):
            return self._page(None, 'You are unsubscribed',
                              'You will not receive these WhatsApp messages any more.',
                              icon='🔕', company=company)
        return self._page(None, 'Stop WhatsApp messages?',
                          'Enter the WhatsApp number this message reached you on, then '
                          'tap a reason.', icon='🔔', form_action='/wa/c/%s/o' % ctoken,
                          ask_number=True, buttons=self._stop_buttons(), company=company)

    # ------------------------------------------------------------------ buttons and the message page
    @http.route('/wa/b/<string:token>/<int:index>', type='http', auth='public', methods=['GET'],
                sitemap=False, readonly=False)
    def button(self, token, index, **kwargs):
        """A link button: counted, then on to where it points."""
        message = request.env['epg.whatsapp.message'].sudo()._from_token(token)
        url = message._register_button(index, self._agent()) if message else ''
        if not url:
            return self._page(None, 'Link not found', 'This link is not valid.',
                              icon='🔗', status=404)
        return request.redirect(url, code=302, local=False)

    @http.route('/wa/m/<string:token>', type='http', auth='public', methods=['GET'],
                sitemap=False, readonly=False)
    def message_page(self, token, **kwargs):
        """One message, with its document, payment, answers and buttons as buttons."""
        message = request.env['epg.whatsapp.message'].sudo()._from_token(token)
        if not message:
            return self._page(None, 'Link not found', 'This link is not valid.',
                              icon='🔗', status=404)
        if not message._links_alive():
            return self._page(message, 'Link expired',
                              'This link has expired. Reply on WhatsApp and we will '
                              'send it again.', icon='⌛', status=410)
        message._register_page_open(self._agent())
        response = request.render('epg_whatsapp.flyer_page', message._page())
        response.headers['X-Robots-Tag'] = 'noindex'
        return response

    @http.route('/wa/c/<string:ctoken>/b/<int:index>', type='http', auth='public',
                methods=['GET'], sitemap=False, readonly=False)
    def broadcast_button(self, ctoken, index, **kwargs):
        from ..models.link_channel import PREVIEW_AGENTS
        campaign = self._campaign(ctoken)
        rows = campaign._button_rows() if campaign else []
        if not (1 <= index <= len(rows)):
            return self._page(None, 'Link not found', 'This link is not valid.',
                              icon='🔗', status=404)
        if not PREVIEW_AGENTS.search(self._agent()):
            campaign.write({'broadcast_link_clicks': campaign.broadcast_link_clicks + 1})
        return request.redirect(rows[index - 1][1], code=302, local=False)
