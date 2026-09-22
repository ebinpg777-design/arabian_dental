# -*- coding: utf-8 -*-
"""How a contact stands on WhatsApp: engaged, quiet, never messaged, or stopped.

Read off the messages, never stored: a doctor who opened an invoice last night is
engaged this morning without a cron. Filterable, so a campaign can leave out the
quiet and the stopped. (client, 2026-09-17)
"""
from datetime import timedelta

from odoo import _, api, fields, models

ENGAGED_DAYS = 90
ENGAGEMENT = [
    ('engaged', 'Engaged'),
    ('quiet', 'Quiet'),
    ('never', 'Never messaged'),
    ('stopped', 'Stopped'),
]


class ResPartner(models.Model):
    _inherit = 'res.partner'

    whatsapp_marketing_optin = fields.Boolean(
        'WhatsApp Marketing', default=True,
        help="Off: no campaign or automation reaches this contact - updates about their "
             "own cases and invoices still do. The Stop link under a marketing message "
             "switches it off; a person switches it back on here.")
    whatsapp_engagement = fields.Selection(
        ENGAGEMENT, string='WhatsApp', compute='_compute_whatsapp_engagement',
        search='_search_whatsapp_engagement',
        help="Engaged: opened a document, tapped a link or answered in the last 90 "
             "days. Quiet: messaged, but nothing back in that time. Stopped: asked "
             "not to be messaged.")
    whatsapp_engagement_label = fields.Char(compute='_compute_whatsapp_engagement')
    whatsapp_last_seen = fields.Datetime('Last Seen on WhatsApp',
                                         compute='_compute_whatsapp_engagement')
    whatsapp_sent_count = fields.Integer(compute='_compute_whatsapp_engagement')

    @api.model
    def _engagement_sets(self, partner_ids=None):
        """(engaged, stopped, sent) partner-id sets, over these partners or all."""
        Message = self.env['epg.whatsapp.message'].sudo()
        Conversation = self.env['epg.whatsapp.conversation'].sudo()
        since = fields.Datetime.now() - timedelta(days=ENGAGED_DAYS)
        scope = [('partner_id', 'in', list(partner_ids))] if partner_ids is not None else \
            [('partner_id', '!=', False)]
        engaged = set()
        for domain in ([('seen_at', '>=', since)], [('clicked_at', '>=', since)],
                       [('paid_claimed_at', '>=', since)],
                       [('direction', '=', 'inbound'), ('create_date', '>=', since)]):
            engaged |= {p.id for [p] in Message._read_group(scope + domain, ['partner_id'])}
        stopped = {p.id for [p] in Conversation._read_group(
            scope + [('opt_out', '=', True)], ['partner_id'])}
        sent = {p.id for [p] in Message._read_group(
            scope + [('direction', '=', 'outbound'),
                     ('state', 'in', ('sent', 'read', 'delivered', 'simulated'))],
            ['partner_id'])}
        return engaged, stopped, sent

    def _compute_whatsapp_engagement(self):
        engaged, stopped, sent = self._engagement_sets(self.ids)
        Message = self.env['epg.whatsapp.message'].sudo()
        last_seen = {}
        for partner, seen, clicked in Message._read_group(
                [('partner_id', 'in', self.ids)], ['partner_id'],
                ['seen_at:max', 'clicked_at:max']):
            stamps = [s for s in (seen, clicked) if s]
            last_seen[partner.id] = max(stamps) if stamps else False
        for partner, latest in Message._read_group(
                [('partner_id', 'in', self.ids), ('direction', '=', 'inbound')],
                ['partner_id'], ['create_date:max']):
            if latest and (not last_seen.get(partner.id) or latest > last_seen[partner.id]):
                last_seen[partner.id] = latest
        counts = {p.id: n for p, n in Message._read_group(
            [('partner_id', 'in', self.ids), ('direction', '=', 'outbound'),
             ('state', 'in', ('sent', 'read', 'delivered', 'simulated'))],
            ['partner_id'], ['__count'])}
        labels = dict(ENGAGEMENT)
        for partner in self:
            if partner.id in stopped:
                state = 'stopped'
            elif partner.id in engaged:
                state = 'engaged'
            elif partner.id in sent:
                state = 'quiet'
            else:
                state = 'never'
            partner.whatsapp_engagement = state
            partner.whatsapp_engagement_label = labels[state]
            partner.whatsapp_last_seen = last_seen.get(partner.id, False)
            partner.whatsapp_sent_count = counts.get(partner.id, 0)

    def _search_whatsapp_engagement(self, operator, value):
        if operator not in ('=', '!=', 'in', 'not in'):
            return [('id', 'in', [])]
        # Odoo 19 hands `in` an OrderedSet, `=` a string: both become a plain set.
        wanted = {value} if isinstance(value, str) else set(value or [])
        if operator in ('!=', 'not in'):
            wanted = set(dict(ENGAGEMENT)) - wanted
        engaged, stopped, sent = self._engagement_sets()
        ids = set()
        if 'stopped' in wanted:
            ids |= stopped
        if 'engaged' in wanted:
            ids |= engaged - stopped
        if 'quiet' in wanted:
            ids |= sent - engaged - stopped
        if 'never' in wanted:
            # Everyone else: said as an exclusion, so it needs no list of all contacts.
            return ['|', ('id', 'in', list(ids)), ('id', 'not in', list(sent | stopped))]
        return [('id', 'in', list(ids))]

    def action_whatsapp_chat(self):
        """The conversation with this contact, in the Chats screen."""
        self.ensure_one()
        return {'type': 'ir.actions.client', 'tag': 'epg_whatsapp_chats',
                'name': _('WhatsApp'), 'params': {'partner_id': self.id}}
