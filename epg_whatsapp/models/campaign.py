# -*- coding: utf-8 -*-
"""A message to many contacts - sent free, paced, measured, and easy to stop.

Holiday closures, a new appliance, a price list: the lab's news to its doctors.
(client, 2026-09-17)

* **Paced** - WhatsApp watches for a number suddenly messaging many people. A campaign
  puts at most `daily_limit` messages on the Desk per day and schedules the rest for
  the following days, so the lab's own number is never put at risk.
* **Measured** - who it went to, who tapped the link, who answered, who asked to stop.
* **Easy to stop** - each message ends with a private stop link. Tapping it shows a
  confirmation page (link previewers only ever GET, so they cannot opt anyone out),
  and a stopped number is refused by every later send.
"""
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class EpgWhatsappCampaign(models.Model):
    _name = 'epg.whatsapp.campaign'
    _description = 'WhatsApp Campaign'
    _order = 'create_date desc, id desc'

    name = fields.Char(required=True)
    state = fields.Selection(
        [('draft', 'Draft'), ('running', 'Sending'), ('done', 'Done'),
         ('cancel', 'Stopped')], default='draft', required=True, copy=False)
    account_id = fields.Many2one(
        'epg.whatsapp.account', string='Send From', required=True,
        domain="[('channel', '=', 'link')]",
        default=lambda self: self.env['epg.whatsapp.account'].search(
            [('channel', '=', 'link')], limit=1))
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company)

    audience_id = fields.Many2one(
        'epg.whatsapp.audience', string='Audience', ondelete='set null',
        help="A saved rule for who this goes to. Load it into the recipients, then add "
             "or remove by hand; an empty list is filled from it at launch.")
    audience_count = fields.Integer(related='audience_id.count')
    partner_ids = fields.Many2many('res.partner', string='Recipients')
    send_on = fields.Datetime(
        'Send On', help="When the first day's batch goes on the Desk. Empty: now.")
    at_best_time = fields.Boolean(
        "At Each Doctor's Usual Time",
        help="Where a doctor's reading hour is known, their message waits for it.")
    body = fields.Text(
        'Message', required=True,
        help="Use {{name}} or any contact field in double braces.")
    link_url = fields.Char(
        'Link', help="Optional. Sent as a tracked link: you see who opened it.")
    link_label = fields.Char('Link Text', default=lambda self: _('See details'))
    quick_replies = fields.Text(
        'Quick Replies', help="One answer per line. Each becomes a link the contact taps.")
    buttons = fields.Text(
        'Link Buttons',
        help="One per line, as  Label | https://…  - a link the doctor taps, tracked; "
             "a real button on the flyer page.")
    optout = fields.Boolean(
        'Add a Stop Link', default=True,
        help="Ends each message with a private link to stop these messages.")
    daily_limit = fields.Integer(
        'Messages per Day', default=40,
        help="At most this many go on the Desk per day; the rest are scheduled for the "
             "following days at 10 AM. Keeps the lab's own number safe from being "
             "flagged for bulk messaging.")

    message_ids = fields.One2many('epg.whatsapp.message', 'campaign_id')
    preview = fields.Text(compute='_compute_preview')
    recipient_count = fields.Integer(compute='_compute_counts')
    reachable_count = fields.Integer(compute='_compute_counts')
    day_count = fields.Integer(compute='_compute_counts')
    queued_count = fields.Integer(compute='_compute_stats')
    sent_count = fields.Integer(compute='_compute_stats')
    clicked_count = fields.Integer(compute='_compute_stats')
    replied_count = fields.Integer(compute='_compute_stats')
    optout_count = fields.Integer(compute='_compute_stats')
    cancel_count = fields.Integer(compute='_compute_stats')
    click_rate = fields.Float(compute='_compute_stats')
    engaged_count = fields.Integer(compute='_compute_engagement')
    quiet_count = fields.Integer(compute='_compute_engagement')
    stopped_count = fields.Integer(compute='_compute_engagement')

    @api.depends('partner_ids')
    def _compute_engagement(self):
        for campaign in self:
            states = campaign.partner_ids.mapped('whatsapp_engagement')
            campaign.engaged_count = states.count('engaged')
            campaign.quiet_count = states.count('quiet')
            campaign.stopped_count = states.count('stopped')

    def action_drop_unengaged(self):
        """Keep the doctors who read: the quiet and the stopped are left out."""
        self.ensure_one()
        keep = self.partner_ids.filtered(
            lambda p: p.whatsapp_engagement in ('engaged', 'never'))
        dropped = len(self.partner_ids) - len(keep)
        self.partner_ids = [(6, 0, keep.ids)]
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'type': 'info', 'title': _("Recipients trimmed"),
                       'message': _("%s quiet or stopped contact(s) left out.", dropped)},
        }

    # ------------------------------------------------------------------ numbers
    @api.model
    def _number_of(self, partner):
        """The contact's WhatsApp number where the field exists, digits with a
        country code. Never the phone when a WhatsApp number field is there."""
        Template = self.env['epg.whatsapp.template']
        raw = partner.whatsapp_number if 'whatsapp_number' in partner._fields \
            else partner.phone
        return Template._normalise_number(raw, partner)

    @api.depends('partner_ids', 'daily_limit')
    def _compute_counts(self):
        for campaign in self:
            reachable = sum(1 for p in campaign.partner_ids if campaign._number_of(p))
            campaign.recipient_count = len(campaign.partner_ids)
            campaign.reachable_count = reachable
            limit = max(campaign.daily_limit, 1)
            campaign.day_count = -(-reachable // limit) if reachable else 0

    @api.depends('body', 'partner_ids', 'link_url', 'link_label', 'quick_replies', 'optout')
    def _compute_preview(self):
        Template = self.env['epg.whatsapp.template']
        for campaign in self:
            sample = campaign.partner_ids[:1]
            text = Template._render_text(sample, campaign.body) if sample else (campaign.body or '')
            parts = [text.strip()]
            if campaign.link_url:
                parts.append('🔗 %s\n%s' % (campaign.link_label or _('Open'), '…/wa/l/…'))
            labels = [line.strip() for line in (campaign.quick_replies or '').splitlines()
                      if line.strip()]
            if labels:
                parts.append('\n'.join([_("Tap to reply:")] + ['%s → …' % l for l in labels]))
            if campaign.optout:
                parts.append(_("_Don't want these messages? Tap: …/wa/o/…_"))
            campaign.preview = '\n\n'.join(p for p in parts if p)

    def _compute_stats(self):
        """Counted in the database, whatever the campaign's size: a campaign to
        four hundred doctors is four hundred rows nobody needs to load."""
        Message = self.env['epg.whatsapp.message'].sudo()
        base = [('campaign_id', 'in', self.ids), ('direction', '=', 'outbound')]

        def counts(extra):
            return {campaign.id: n for campaign, n in Message._read_group(
                base + extra, ['campaign_id'], ['__count'])}

        by_state = {}
        for campaign, state, n in Message._read_group(base, ['campaign_id', 'state'],
                                                      ['__count']):
            by_state.setdefault(campaign.id, {})[state] = n
        clicked = counts([('clicked_at', '!=', False)])
        replied = counts([('reply_ids', '!=', False)])
        stopped = counts([('opted_out_at', '!=', False)])
        for campaign in self:
            states = by_state.get(campaign.id, {})
            sent = sum(states.get(s, 0) for s in ('sent', 'read', 'delivered'))
            campaign.queued_count = sum(states.get(s, 0) for s in ('draft', 'ready', 'opened'))
            campaign.sent_count = sent
            campaign.clicked_count = clicked.get(campaign.id, 0)
            campaign.replied_count = replied.get(campaign.id, 0)
            campaign.optout_count = stopped.get(campaign.id, 0)
            campaign.cancel_count = states.get('cancel', 0)
            campaign.click_rate = round(campaign.clicked_count * 100 / sent, 1) if sent else 0.0

    # ------------------------------------------------------------------ actions
    def action_load_audience(self):
        """The audience's contacts, into the recipients - to add to or prune."""
        self.ensure_one()
        if not self.audience_id:
            raise UserError(_("Pick an audience first."))
        partners = self.audience_id._partners()
        self.partner_ids = [(6, 0, partners.ids)]
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'type': 'info', 'title': self.audience_id.name,
                       'message': _("%s contact(s) loaded as recipients.", len(partners))},
        }

    def action_launch(self):
        """One message per reachable contact, paced over the days it needs."""
        self.ensure_one()
        if self.state != 'draft':
            raise UserError(_("This campaign has already been launched."))
        if self.mode != 'personal':
            raise UserError(_("A broadcast is not put on the Desk: copy its text into a "
                              "WhatsApp broadcast list, then press Mark as sent."))
        if self.audience_id and not self.partner_ids:
            self.partner_ids = [(6, 0, self.audience_id._partners().ids)]
        if self.account_id.channel != 'link':
            raise UserError(_("Campaigns are sent through a WhatsApp app / Web sender."))
        if self.link_url and not self.link_url.lower().startswith(('http://', 'https://')):
            raise UserError(_("The link must start with http:// or https://."))
        Message = self.env['epg.whatsapp.message']
        Template = self.env['epg.whatsapp.template']
        seen_numbers = set()
        vals_list = []
        for partner in self.partner_ids:
            number = self._number_of(partner)
            # Twice to one number (two contacts sharing a phone) is once too many.
            if not number or number in seen_numbers:
                continue
            seen_numbers.add(number)
            vals_list.append({
                'account_id': self.account_id.id,
                'campaign_id': self.id,
                'res_model': 'res.partner',
                'res_id': partner.id,
                'partner_id': partner.id,
                'number': number,
                'body': Template._render_text(partner, self.body),
                'quick_replies': self.quick_replies or False,
                'buttons': Template._render_text(partner, self.buttons) if self.buttons else False,
                'user_id': self.env.user.id,
            })
        # Created and prepared as one batch, then paced: a refused message (opted
        # out) takes no day's slot.
        messages = Message.create(vals_list)
        messages.action_send()
        ready = messages.filtered(lambda m: m.state == 'ready')
        if not ready:
            refused = messages.filtered(lambda m: m.state == 'cancel')
            if refused:
                raise UserError(_("No message could be sent: %s", refused[0].error))
            raise UserError(_("None of the recipients has a WhatsApp number."))
        self._schedule(ready)
        self.state = 'running'
        return self.action_open_desk()

    def _schedule(self, messages):
        """Pace the day's batches: the first now (or at Send On), the rest on the
        following days at 10 AM - or at each doctor's usual hour where it is known."""
        self.ensure_one()
        Message = self.env['epg.whatsapp.message']
        tz = Message._lab_tz()
        now = Message._local_now()
        start = pytz.utc.localize(self.send_on).astimezone(tz) if self.send_on else now
        limit = max(self.daily_limit, 1)
        best = Message._best_times(messages.mapped('number')) if self.at_best_time else {}
        for index, message in enumerate(messages):
            day = index // limit
            day_date = start.date() + timedelta(days=day)
            moment = start if not day else tz.localize(datetime.combine(day_date, time(10)))
            hour = (best.get(message.number) or {}).get('hour')
            if hour is not None:
                theirs = tz.localize(datetime.combine(day_date, time(hour)))
                # Their hour on that day - or the next, when it has already passed.
                moment = theirs if theirs >= moment else theirs + timedelta(days=1)
            if moment > now:
                message.scheduled_at = Message._utc(moment)

    def action_open_desk(self):
        return {'type': 'ir.actions.client', 'tag': 'epg_whatsapp_desk',
                'name': _('WhatsApp Desk')}

    def action_stop(self):
        """Cancel everything not sent yet."""
        for campaign in self:
            campaign.message_ids.filtered(
                lambda m: m.state in ('draft', 'ready', 'opened')).write(
                {'state': 'cancel', 'scheduled_at': False,
                 'error': _("Campaign stopped by %s.", self.env.user.name)})
            campaign.state = 'cancel'
        return True

    def action_mark_done(self):
        self.write({'state': 'done'})
        return True

    def _open_messages(self, domain, name):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': name,
            'res_model': 'epg.whatsapp.message', 'view_mode': 'list,form',
            'domain': [('campaign_id', '=', self.id)] + domain,
        }

    def action_view_sent(self):
        return self._open_messages([('state', 'in', ('sent', 'read', 'delivered'))], _("Sent"))

    def action_view_clicked(self):
        return self._open_messages([('clicked_at', '!=', False)], _("Opened the link"))

    def action_view_replied(self):
        return self._open_messages([('reply_ids', '!=', False)], _("Replied"))

    def action_view_optout(self):
        return self._open_messages([('opted_out_at', '!=', False)], _("Asked to stop"))

    def action_view_queued(self):
        return self._open_messages([('state', 'in', ('draft', 'ready', 'opened'))], _("Waiting"))
