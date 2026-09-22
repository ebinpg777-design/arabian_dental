# -*- coding: utf-8 -*-
"""Automations: a message that sends itself to whoever matches, when they match.

"We miss you" to a doctor with no case in 45 days; "welcome" to a doctor whose
first case came this month. An audience says who, the automation says what and
how often, and the messages appear on the Desk every morning on their own - at
most once per doctor every N days, and never more than a day's worth at once.
(client, 2026-09-17)
"""
from datetime import datetime, time, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class EpgWhatsappAutomation(models.Model):
    _name = 'epg.whatsapp.automation'
    _description = 'WhatsApp Automation'
    _order = 'sequence, name'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company)
    audience_id = fields.Many2one(
        'epg.whatsapp.audience', string='Audience', required=True, ondelete='restrict')
    audience_count = fields.Integer(related='audience_id.count')
    account_id = fields.Many2one(
        'epg.whatsapp.account', string='Send From', required=True,
        domain="[('channel', '=', 'link')]",
        default=lambda self: self.env['epg.whatsapp.account'].search(
            [('channel', '=', 'link')], limit=1))
    user_id = fields.Many2one(
        'res.users', string='Sent By', default=lambda self: self.env.user,
        help="Whose Desk the messages land on. Empty: everyone's.")
    body = fields.Text('Message', required=True,
                       help="Use {{name}} or any contact field in double braces.")
    quick_replies = fields.Text('Quick Replies')
    buttons = fields.Text('Link Buttons', help="One per line, as  Label | https://…")
    lead_reply = fields.Char('The "Interested" Reply')
    optout = fields.Boolean('Add a Stop Link', default=True)
    repeat_days = fields.Integer(
        'Not Again Within (days)', default=90,
        help="A doctor who received this waits this long before it can come again. "
             "0: once, ever.")
    daily_limit = fields.Integer('At Most per Day', default=30)
    hour = fields.Integer('Send At (hour)', default=10)
    at_best_time = fields.Boolean("At Each Doctor's Usual Time")

    last_run_at = fields.Datetime(readonly=True, copy=False)
    run_count = fields.Integer(readonly=True, copy=False)
    message_ids = fields.One2many('epg.whatsapp.message', 'automation_id')
    due_count = fields.Integer('Due Now', compute='_compute_due')
    sent_count = fields.Integer(compute='_compute_stats')
    waiting_count = fields.Integer(compute='_compute_stats')
    replied_count = fields.Integer(compute='_compute_stats')

    # ------------------------------------------------------------------ who
    def _candidates(self):
        """Everyone in the audience who has not had this lately."""
        self.ensure_one()
        partners = self.audience_id._partners()
        if not partners:
            return partners
        domain = [('automation_id', '=', self.id), ('direction', '=', 'outbound'),
                  ('state', '!=', 'cancel'), ('partner_id', 'in', partners.ids)]
        if self.repeat_days:
            domain.append(('create_date', '>=',
                           fields.Datetime.now() - timedelta(days=self.repeat_days)))
        had = {p.id for [p] in self.env['epg.whatsapp.message'].sudo()._read_group(
            domain, ['partner_id'])}
        return partners.filtered(lambda p: p.id not in had)

    def _compute_due(self):
        for automation in self:
            try:
                automation.due_count = len(automation._candidates())
            except UserError:
                automation.due_count = 0

    def _compute_stats(self):
        Message = self.env['epg.whatsapp.message'].sudo()
        base = [('automation_id', 'in', self.ids)]
        by_state = {}
        for automation, state, n in Message._read_group(
                base + [('direction', '=', 'outbound')], ['automation_id', 'state'], ['__count']):
            by_state.setdefault(automation.id, {})[state] = n
        replied = {a.id: n for a, n in Message._read_group(
            base + [('direction', '=', 'inbound')], ['automation_id'], ['__count'])}
        for automation in self:
            states = by_state.get(automation.id, {})
            automation.sent_count = sum(states.get(s, 0) for s in ('sent', 'read', 'delivered'))
            automation.waiting_count = sum(states.get(s, 0) for s in ('draft', 'ready', 'opened'))
            automation.replied_count = replied.get(automation.id, 0)

    # ------------------------------------------------------------------ run
    def _run(self):
        """One day's messages for each automation, on the Desk."""
        Message = self.env['epg.whatsapp.message']
        Template = self.env['epg.whatsapp.template']
        Campaign = self.env['epg.whatsapp.campaign']
        created = Message
        for automation in self.filtered('active'):
            if automation.account_id.channel != 'link':
                continue
            partners = automation._candidates()[:max(automation.daily_limit, 1)]
            seen, vals_list = set(), []
            for partner in partners:
                number = Campaign._number_of(partner)
                if not number or number in seen:
                    continue
                seen.add(number)
                vals_list.append({
                    'account_id': automation.account_id.id,
                    'automation_id': automation.id,
                    'res_model': 'res.partner', 'res_id': partner.id,
                    'partner_id': partner.id, 'number': number,
                    'body': Template._render_text(partner, automation.body),
                    'quick_replies': automation.quick_replies or False,
                    'buttons': Template._render_text(partner, automation.buttons)
                    if automation.buttons else False,
                    'user_id': automation.user_id.id or False,
                })
            messages = Message.create(vals_list)
            messages.action_send()
            ready = messages.filtered(lambda m: m.state == 'ready')
            automation._schedule(ready)
            automation.write({'last_run_at': fields.Datetime.now(),
                              'run_count': automation.run_count + 1})
            created |= messages
        return created

    def _schedule(self, messages):
        """Today at the automation's hour, or at the doctor's own usual hour."""
        self.ensure_one()
        Message = self.env['epg.whatsapp.message']
        tz = Message._lab_tz()
        now = Message._local_now()
        default = tz.localize(datetime.combine(now.date(), time(max(0, min(self.hour, 23)))))
        best = Message._best_times(messages.mapped('number')) if self.at_best_time else {}
        for message in messages:
            moment = default
            hour = (best.get(message.number) or {}).get('hour')
            if hour is not None:
                moment = tz.localize(datetime.combine(now.date(), time(hour)))
                if moment < now:
                    moment += timedelta(days=1)
            if moment > now:
                message.scheduled_at = Message._utc(moment)

    @api.model
    def _cron_run(self):
        self.search([('active', '=', True)])._run()

    def action_run_now(self):
        created = self._run()
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'type': 'success' if created else 'info',
                       'title': _("Automation run"),
                       'message': _("%s message(s) put on the Desk.", len(created))},
        }

    def action_view_messages(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': self.name,
            'res_model': 'epg.whatsapp.message', 'view_mode': 'list,form',
            'domain': [('automation_id', '=', self.id)],
        }
