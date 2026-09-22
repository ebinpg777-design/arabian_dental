# -*- coding: utf-8 -*-
"""Marketing on the lab's own WhatsApp, made easy. (client, 2026-09-17)

* **Ideas** - a gallery of ready-written campaigns: a festival greeting, a new
  appliance, "we miss you". Pick one, change three words, send.
* **A flyer page** - a campaign carries a picture. The message links to a page on
  the lab's own address that shows it, the text, the reply buttons and "Chat with
  us". A WhatsApp message with a photo, without the API.
* **Broadcast mode** - one text, pasted once into a WhatsApp broadcast list, and
  Odoo logs it to every doctor on the list. The links in it are the campaign's,
  not one doctor's: opens are counted, and a doctor who answers or asks to stop
  says which number they are.
* **Interested** - the quick reply that means "yes": tapping it becomes a to-do
  for the doctor's salesperson.
* **Follow-up** - a new campaign to the doctors who opened nothing.
"""
import re
import secrets

from markupsafe import Markup, escape
from psycopg2 import sql

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .link_channel import _button_row

IDEA_CATEGORIES = [
    ('greeting', 'Greetings & festivals'),
    ('news', 'News & launches'),
    ('offer', 'Offers'),
    ('reengage', 'Win back'),
    ('feedback', 'Feedback & referrals'),
    ('info', 'Information'),
]
MODES = [
    ('personal', 'One message per doctor, sent from the Desk'),
    ('broadcast', 'One broadcast, sent once from WhatsApp'),
]


def wa_html(text):
    """WhatsApp's light markup as HTML, escaped first: *bold*, _italic_, ~strike~."""
    lines = []
    for line in (text or '').splitlines():
        out = str(escape(line))
        out = re.sub(r'\*([^*\n]+)\*', r'<b>\1</b>', out)
        out = re.sub(r'_([^_\n]+)_', r'<i>\1</i>', out)
        out = re.sub(r'~([^~\n]+)~', r'<s>\1</s>', out)
        lines.append(Markup(out))
    return Markup('<br/>').join(lines)


class EpgWhatsappIdea(models.Model):
    """A campaign written in advance, waiting for the day it is needed."""
    _name = 'epg.whatsapp.idea'
    _description = 'WhatsApp Campaign Idea'
    _order = 'sequence, name'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    category = fields.Selection(IDEA_CATEGORIES, default='info', required=True)
    note = fields.Char('When to Use', help="One line: the moment this idea is for.")
    headline = fields.Char(help="The flyer's title, and the link's text in the message.")
    body = fields.Text('Message', required=True)
    quick_replies = fields.Text('Quick Replies')
    lead_reply = fields.Char('The "Interested" Reply')

    def action_use(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('New Campaign'),
            'res_model': 'epg.whatsapp.campaign', 'view_mode': 'form',
            'context': {'default_idea_id': self.id},
        }


class EpgWhatsappCampaign(models.Model):
    _inherit = 'epg.whatsapp.campaign'

    idea_id = fields.Many2one(
        'epg.whatsapp.idea', string='Start From an Idea', ondelete='set null',
        help="Fills the message, the replies and the headline; edit from there.")
    mode = fields.Selection(MODES, default='personal', required=True)
    image = fields.Image('Picture', max_width=1600, max_height=1600)
    headline = fields.Char(
        help="The flyer page's title, and the link's text in the message.")
    lead_reply = fields.Char(
        'The "Interested" Reply',
        help="One of the quick replies, word for word. A doctor who taps it becomes a "
             "to-do for their salesperson.")
    interested_count = fields.Integer(compute='_compute_interest')

    # --- broadcast: one text, one set of links, for everyone at once
    access_token = fields.Char(readonly=True, copy=False)
    broadcast_text = fields.Text(compute='_compute_broadcast_text')
    page_url = fields.Char(compute='_compute_broadcast_text')
    broadcast_opens = fields.Integer('Page Opened', readonly=True, copy=False)
    broadcast_link_clicks = fields.Integer('Link Tapped', readonly=True, copy=False)
    broadcast_sent_at = fields.Datetime(readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals.setdefault('access_token', secrets.token_urlsafe(12))
        return super().create(vals_list)

    @api.onchange('idea_id')
    def _onchange_idea(self):
        idea = self.idea_id
        if not idea:
            return
        self.body = idea.body
        self.quick_replies = idea.quick_replies
        self.headline = idea.headline
        self.lead_reply = idea.lead_reply
        if not self.name:
            self.name = idea.name

    def _compute_interest(self):
        Message = self.env['epg.whatsapp.message'].sudo()
        for campaign in self:
            campaign.interested_count = Message.search_count([
                ('campaign_id', '=', campaign.id), ('direction', '=', 'inbound'),
                ('body', '=', campaign.lead_reply)]) if campaign.lead_reply else 0

    def _reply_labels(self):
        self.ensure_one()
        return [line.strip() for line in (self.quick_replies or '').splitlines()
                if line.strip()]

    def _button_rows(self):
        self.ensure_one()
        rows = []
        for line in (self.buttons or '').splitlines():
            rows += _button_row(line)
        return rows

    # ------------------------------------------------------------------ broadcast
    @api.model
    def _from_token(self, token):
        if not token or len(token) < 12:
            return self.browse()
        return self.sudo().search([('access_token', '=', token)], limit=1)

    def _page_base(self):
        self.ensure_one()
        return '%s/wa/c/%s' % (self.account_id.sudo()._link_base(), self.access_token or '')

    def _page_text(self):
        """The message as everyone receives it: no doctor's name to fill in."""
        self.ensure_one()
        body = (self.body or '').replace('{{name}}', _('Doctor'))
        return self.env['epg.whatsapp.template']._render_text(
            self.env['res.partner'], body).strip()

    @api.depends('body', 'image', 'headline', 'link_url', 'link_label', 'quick_replies',
                 'buttons', 'optout', 'access_token', 'mode')
    def _compute_broadcast_text(self):
        for campaign in self:
            base = campaign._page_base()
            campaign.page_url = base if campaign.access_token else False
            parts = [campaign._page_text()]
            if campaign.image:
                parts.append('🖼 %s\n%s' % (campaign.headline or _('See more'), base))
            elif campaign.link_url:
                parts.append('🔗 %s\n%s/l' % (campaign.link_label or _('Open'), base))
            labels = campaign._reply_labels()
            if labels:
                parts.append('\n'.join([_("Tap to reply:")] + [
                    '%s → %s/r/%d' % (label, base, index)
                    for index, label in enumerate(labels, start=1)]))
            rows = campaign._button_rows()
            if rows:
                parts.append('\n'.join('🔗 %s\n%s/b/%d' % (label, base, index)
                                      for index, (label, _url) in enumerate(rows, start=1)))
            if campaign.optout:
                parts.append(_("_Don't want these messages? Tap: %s/o_", base))
            campaign.broadcast_text = '\n\n'.join(part for part in parts if part)

    def action_mark_broadcast_sent(self):
        """The text went out from WhatsApp as one broadcast: logged to every doctor
        on the list, as if sent one by one."""
        self.ensure_one()
        if self.mode != 'broadcast':
            raise UserError(_("Only a broadcast campaign is marked sent this way."))
        if self.audience_id and not self.partner_ids:
            self.partner_ids = [(6, 0, self.audience_id._partners().ids)]
        Message = self.env['epg.whatsapp.message']
        text = self.broadcast_text
        seen, vals_list = set(), []
        for partner in self.partner_ids:
            number = self._number_of(partner)
            if not number or number in seen:
                continue
            seen.add(number)
            vals_list.append({
                'account_id': self.account_id.id, 'campaign_id': self.id,
                'res_model': 'res.partner', 'res_id': partner.id, 'partner_id': partner.id,
                'number': number, 'body': text, 'quick_replies': self.quick_replies or False,
                'buttons': self.buttons or False, 'user_id': self.env.user.id,
            })
        if not vals_list:
            raise UserError(_("None of the recipients has a WhatsApp number."))
        now = fields.Datetime.now()
        messages = Message.create(vals_list)
        sent = Message
        for message in messages._cancel_blocked():
            conv = message._conversation()
            if conv.opt_out:
                message.write({'state': 'cancel',
                               'error': _('This number has opted out of messages.')})
                continue
            message.write({'state': 'sent', 'sent_at': now, 'ready_at': now,
                           'sent_by_id': self.env.user.id, 'final_text': text})
            conv._note_outbound(now)
            sent |= message
        sent._chatter_sent()
        self.write({'state': 'done', 'broadcast_sent_at': now})
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'type': 'success', 'title': _("Broadcast logged"),
                       'message': _("%(sent)s doctor(s) logged as sent, %(skipped)s left out.",
                                    sent=len(sent), skipped=len(messages) - len(sent))},
        }

    def _find_by_number(self, raw):
        """(partner, digits) for a number typed on a public page - one of the
        campaign's own recipients first."""
        self.ensure_one()
        Template = self.env['epg.whatsapp.template']
        digits = Template._normalise_number(raw)
        Partner = self.env['res.partner'].sudo()
        if len(digits) < 9:
            return Partner, ''
        tail = digits[-9:]
        # The recipients first, compared as digits: numbers are typed with spaces
        # and a country code or without, and the tail is what stays the same.
        for partner in self.partner_ids:
            if self._number_of(partner).endswith(tail):
                return partner.sudo(), digits
        column = 'whatsapp_number' if 'whatsapp_number' in Partner._fields else 'phone'
        self.env.cr.execute(sql.SQL(
            "SELECT id FROM res_partner WHERE active AND "
            "regexp_replace(COALESCE({column}, ''), '\\D', '', 'g') LIKE %s "
            "ORDER BY id LIMIT 1").format(column=sql.Identifier(column)), ('%' + tail,))
        row = self.env.cr.fetchone()
        return (Partner.browse(row[0]) if row else Partner), digits

    def _own_message_for(self, partner):
        self.ensure_one()
        if not partner:
            return self.env['epg.whatsapp.message'].sudo()
        return self.env['epg.whatsapp.message'].sudo().search(
            [('campaign_id', '=', self.id), ('direction', '=', 'outbound'),
             ('partner_id', '=', partner.id)], limit=1)

    def _broadcast_reply(self, index, raw_number):
        """A quick reply from the broadcast page, by the number the doctor typed."""
        self.ensure_one()
        labels = self._reply_labels()
        if not (1 <= index <= len(labels)):
            return ''
        label = labels[index - 1]
        partner, digits = self._find_by_number(raw_number)
        if not digits:
            return ''
        own = self._own_message_for(partner)
        if own:
            own._register_quick_reply(index, '')
            return label
        Message = self.env['epg.whatsapp.message'].sudo()
        conv = self.env['epg.whatsapp.conversation'].sudo()._get_or_create(
            self.account_id, digits, partner)
        reply = Message.create({
            'account_id': self.account_id.id, 'conversation_id': conv.id,
            'partner_id': partner.id or False, 'number': digits, 'body': label,
            'direction': 'inbound', 'state': 'received', 'campaign_id': self.id,
            'res_model': 'res.partner' if partner else False, 'res_id': partner.id or False,
            'sent_at': fields.Datetime.now(), 'company_id': self.company_id.id,
        })
        conv._note_inbound()
        if partner:
            reply._chatter_post(Markup('<p>💬 <b>%s</b> %s</p>') % (
                _("%s replied on WhatsApp:", partner.display_name), label), author=partner)
        reply._note_interest(label)
        return label

    def _broadcast_stop(self, raw_number, reason=None):
        """The doctor asked to stop, from the broadcast page."""
        self.ensure_one()
        partner, digits = self._find_by_number(raw_number)
        if not digits:
            return False
        own = self._own_message_for(partner)
        if not own:
            conv = self.env['epg.whatsapp.conversation'].sudo()._get_or_create(
                self.account_id, digits, partner)
            own = self.env['epg.whatsapp.message'].sudo().create({
                'account_id': self.account_id.id, 'conversation_id': conv.id,
                'partner_id': partner.id or False, 'number': digits,
                'body': _("Asked to stop"), 'direction': 'inbound', 'state': 'received',
                'campaign_id': self.id, 'res_model': 'res.partner' if partner else False,
                'res_id': partner.id or False, 'sent_at': fields.Datetime.now(),
                'company_id': self.company_id.id,
            })
        own._register_opt_out(reason)
        return True

    # ------------------------------------------------------------------ the flyer
    def _flyer(self, message=None):
        """What the flyer page shows - for one doctor's message, or the broadcast."""
        self.ensure_one()
        base = self.account_id.sudo()._link_base()
        personal = message is not None
        if personal:
            token = message.sudo().access_token
            replies = ['%s/wa/r/%s/%d' % (base, token, i) for i in range(1, 10)]
            link = '%s/wa/l/%s' % (base, token)
            stop = '%s/wa/o/%s' % (base, token)
            image = '%s/wa/f/%s/img' % (base, token)
            labels = message._reply_labels()
            rows = message._button_rows()
            buttons = [{'label': label, 'url': '%s/wa/b/%s/%d' % (base, token, index), 'kind': 'link'}
                       for index, (label, _url) in enumerate(rows, start=1)]
            text = message.body
        else:
            prefix = self._page_base()
            replies = ['%s/r/%d' % (prefix, i) for i in range(1, 10)]
            link = prefix + '/l'
            stop = prefix + '/o'
            image = prefix + '/img'
            labels = self._reply_labels()
            buttons = [{'label': label, 'url': '%s/b/%d' % (prefix, index), 'kind': 'link'}
                       for index, (label, _url) in enumerate(self._button_rows(), start=1)]
            text = self._page_text()
        number = re.sub(r'\D', '', self.account_id.whatsapp_number or '')
        return {
            'company_name': self.company_id.sudo().name or '',
            'headline': self.headline or self.name,
            'lines': wa_html(text),
            'facts': [],
            'summary': ' '.join((text or '').split())[:150],
            'image': image if self.image else '',
            'replies': [{'label': label, 'url': replies[index]}
                        for index, label in enumerate(labels[:9])],
            'link': {'label': self.link_label or _('Open'), 'url': link} if self.link_url else None,
            'buttons': buttons,
            'chat': 'https://wa.me/%s' % number if number else '',
            'stop': stop if self.optout else '',
            'personal': personal,
        }

    # ------------------------------------------------------------------ after
    def action_follow_up(self):
        """A new campaign to the doctors who opened nothing and answered nothing."""
        self.ensure_one()
        Message = self.env['epg.whatsapp.message'].sudo()
        sent = Message.search([('campaign_id', '=', self.id), ('direction', '=', 'outbound'),
                               ('state', 'in', ('sent', 'read', 'delivered'))])
        silent = sent.filtered(lambda m: not (m.seen_at or m.clicked_at or m.reply_ids
                                              or m.opted_out_at))
        partners = silent.mapped('partner_id')
        if not partners:
            raise UserError(_("Everyone who received this opened or answered it."))
        copy = self.copy({
            'name': _("Follow-up: %s", self.name), 'partner_ids': [(6, 0, partners.ids)],
            'audience_id': False, 'send_on': False, 'state': 'draft',
        })
        return {'type': 'ir.actions.act_window', 'res_model': 'epg.whatsapp.campaign',
                'res_id': copy.id, 'view_mode': 'form'}

    def action_duplicate(self):
        self.ensure_one()
        copy = self.copy({'name': _("%s (copy)", self.name), 'state': 'draft'})
        return {'type': 'ir.actions.act_window', 'res_model': 'epg.whatsapp.campaign',
                'res_id': copy.id, 'view_mode': 'form'}

    def action_view_interested(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _("Interested"),
            'res_model': 'epg.whatsapp.message', 'view_mode': 'list,form',
            'domain': [('campaign_id', '=', self.id), ('direction', '=', 'inbound'),
                       ('body', '=', self.lead_reply)],
        }


class EpgWhatsappMessage(models.Model):
    _inherit = 'epg.whatsapp.message'

    def _is_marketing(self):
        """A campaign's or an automation's message, as opposed to one about the
        contact's own case, invoice or payment."""
        self.ensure_one()
        return bool(self.campaign_id or self.automation_id)

    def _send_blocked_reason(self):
        reason = super()._send_blocked_reason()
        if reason:
            return reason
        partner = self.partner_id.sudo()
        if partner and self._is_marketing() and not partner.whatsapp_marketing_optin:
            return _("%s switched off WhatsApp marketing messages.", partner.display_name)
        return False

    automation_id = fields.Many2one(
        'epg.whatsapp.automation', index='btree_not_null', ondelete='set null', copy=False)
    optout_reason = fields.Char('Why They Stopped', readonly=True, copy=False)

    def _on_quick_reply(self, label, index):
        result = super()._on_quick_reply(label, index)
        self._note_interest(label)
        return result

    def _note_interest(self, label):
        """The "interested" answer becomes a to-do for the doctor's salesperson."""
        for message in self.sudo():
            source = message.campaign_id or message.automation_id
            lead = (source.lead_reply or '').strip() if source else ''
            partner = message.partner_id
            if not (lead and partner and (label or '').strip() == lead):
                continue
            owner = partner.user_id or source.create_uid
            if not owner or not hasattr(partner, 'activity_schedule'):
                continue
            partner.activity_schedule(
                'mail.mail_activity_data_todo',
                summary=_("WhatsApp: %s is interested", partner.display_name),
                note=_("%(who)s tapped “%(label)s” on “%(source)s”. Give them a call.",
                       who=partner.display_name, label=label, source=source.name),
                user_id=owner.id)
