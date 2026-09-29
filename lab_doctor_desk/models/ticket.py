# -*- coding: utf-8 -*-
"""The doctor call ticket.

One question for one doctor about one order, from the moment it is raised to the
answer that closes it. The order it belongs to cannot be confirmed while it is open:
that is the whole point - a case that starts on a guess is a case made twice.

Who does what:

* anybody who registers a case raises one (by marking the case "call the doctor");
* the **desk** takes it, calls, and records what the doctor said;
* a **lead** shares tickets out, takes an escalation, and may close one the doctor
  could not be reached for - a decision, so it is theirs;
* the **field executive** is handed the ones a phone cannot settle, and answers from
  the clinic.
"""
from collections import Counter
from datetime import timedelta

import pytz
from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.lab_order_control.models.doctor_call import RESPONSES
from odoo.addons.lab_order_control.models.sale_order_hold import CALL_REASONS

STATES = [
    ('new', 'New'),
    ('taken', 'In hand'),
    ('waiting', 'Waiting for the doctor'),
    ('field', 'With the field executive'),
    ('closed', 'Closed'),
    ('cancelled', 'Withdrawn'),
]
OPEN = ('new', 'taken', 'waiting', 'field')
OUTCOMES = [
    ('as_is', 'Go ahead as written'),
    ('change', 'Change the order'),
    ('cancel', 'The doctor cancelled the case'),
    ('unreached', 'Could not be reached'),
]
CHANNELS = [('phone', 'Phone'), ('whatsapp', 'WhatsApp'), ('person', 'In person')]
PRIORITIES = [('0', 'Normal'), ('1', 'Urgent'), ('2', 'Emergency')]
ORDER_PRIORITY = {'emergency': '2', 'urgent': '1'}
AGENT = 'lab_doctor_desk.group_doctor_desk_agent'
LEAD = 'lab_doctor_desk.group_doctor_desk_lead'


class DoctorTicket(models.Model):
    _name = 'lab.doctor.ticket'
    _description = 'Doctor Call Ticket'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'priority desc, deadline, id'

    name = fields.Char('Ticket', default='New', readonly=True, copy=False, index=True)
    company_id = fields.Many2one('res.company', required=True, index=True,
                                 default=lambda self: self.env.company)
    order_id = fields.Many2one('sale.order', string='Order', required=True, ondelete='cascade',
                               index=True, readonly=True)
    order_name = fields.Char(related='order_id.name', store=True, string='Order Number')
    order_state = fields.Selection(related='order_id.state', string='Order Status')
    partner_id = fields.Many2one('res.partner', string='Doctor / Clinic', index=True, readonly=True)
    doctor_id = fields.Many2one(
        'res.partner', string='Practice', index=True, readonly=True,
        help="The practice the doctor belongs to: every open question for it can be "
             "settled in one call.")
    salesperson_id = fields.Many2one('res.users', string='Salesperson', readonly=True, index=True)
    patient = fields.Char(readonly=True)
    phone = fields.Char(help="The number to ring. Corrected here, it is corrected for this ticket only.")
    whatsapp = fields.Char(readonly=True)

    reason = fields.Selection(CALL_REASONS, required=True, default='other', tracking=True)
    question = fields.Text('What to ask')
    priority = fields.Selection(PRIORITIES, default='0', required=True, tracking=True, index=True)
    state = fields.Selection(STATES, default='new', required=True, tracking=True, index=True, copy=False)

    raised_by_id = fields.Many2one('res.users', string='Raised by', readonly=True, index=True,
                                   default=lambda self: self.env.user)
    raised_on = fields.Datetime(default=fields.Datetime.now, readonly=True, index=True)
    agent_id = fields.Many2one('res.users', string='Taken by', tracking=True, index=True, copy=False)
    taken_on = fields.Datetime(readonly=True, copy=False)
    deadline = fields.Datetime('Answer needed by', tracking=True, copy=False)
    callback_at = fields.Datetime('Call again at', tracking=True, copy=False)
    callback_told = fields.Boolean(copy=False)
    first_call_on = fields.Datetime(readonly=True, copy=False)

    call_ids = fields.One2many('lab.doctor.call.log', 'ticket_id', string='Attempts')
    attempts = fields.Integer(compute='_compute_attempts', store=True)
    unanswered = fields.Integer(compute='_compute_attempts', store=True)
    last_response = fields.Selection(RESPONSES, compute='_compute_attempts', store=True)

    escalated = fields.Boolean(tracking=True, copy=False, index=True)
    escalated_on = fields.Datetime(readonly=True, copy=False)
    escalation_note = fields.Char(copy=False)
    field_user_id = fields.Many2one('res.users', string='Asked in person by', tracking=True, copy=False, index=True)
    field_note = fields.Char('For the executive', copy=False)

    outcome = fields.Selection(OUTCOMES, tracking=True, copy=False)
    answer = fields.Text("What the doctor said", copy=False)
    channel = fields.Selection(CHANNELS, string='Answered by', copy=False)
    tell_production = fields.Boolean(
        'Put the answer on the job card', default=True,
        help="The answer is added to the order's instructions, which the job card prints.")
    closed_on = fields.Datetime(readonly=True, copy=False, index=True)
    closed_by_id = fields.Many2one('res.users', string='Closed by', readonly=True, copy=False)
    hours_open = fields.Float(compute='_compute_clock', string='Hours open')
    hours_to_close = fields.Float('Hours to an answer', readonly=True, copy=False, aggregator='avg')
    first_call_answer = fields.Boolean('Answered at the first call', readonly=True, copy=False)
    overdue = fields.Boolean(compute='_compute_clock', search='_search_overdue')
    due_state = fields.Selection([('ok', 'In time'), ('soon', 'Soon'), ('late', 'Late')], compute='_compute_clock')
    callback_due = fields.Boolean(compute='_compute_clock')
    sibling_count = fields.Integer(compute='_compute_sibling_count', string='Other open questions')
    colour = fields.Integer(compute='_compute_clock')

    # ------------------------------------------------------------------ computes
    @api.depends('call_ids.response')
    def _compute_attempts(self):
        for ticket in self:
            calls = ticket.call_ids.sorted(lambda c: (c.call_datetime, c.id))
            ticket.attempts = len(calls)
            ticket.unanswered = len(calls.filtered(lambda c: c.response != 'answered'))
            ticket.last_response = calls[-1:].response or False

    @api.depends('state', 'deadline', 'raised_on', 'closed_on', 'callback_at')
    def _compute_clock(self):
        now = fields.Datetime.now()
        for ticket in self:
            end = ticket.closed_on or now
            ticket.hours_open = max(0.0, (end - ticket.raised_on).total_seconds() / 3600.0) if ticket.raised_on else 0.0
            is_open = ticket.state in OPEN
            late = bool(is_open and ticket.deadline and ticket.deadline < now)
            ticket.overdue = late
            soon = False
            if is_open and ticket.deadline and not late and ticket.raised_on:
                window = (ticket.deadline - ticket.raised_on).total_seconds()
                soon = (ticket.deadline - now).total_seconds() < max(3600.0, window * 0.25)
            ticket.due_state = 'late' if late else 'soon' if soon else 'ok'
            ticket.callback_due = bool(ticket.state == 'waiting' and ticket.callback_at and ticket.callback_at <= now)
            ticket.colour = 1 if late else 2 if soon else 10 if ticket.state == 'closed' else 0

    def _search_overdue(self, operator, value):
        if operator not in ('=', '!=') or not isinstance(value, bool):
            raise UserError(_("Late is a yes or a no."))
        late = [('state', 'in', OPEN), ('deadline', '<', fields.Datetime.now())]
        if (operator == '=') == value:
            return late
        return ['|', '|', ('state', 'not in', OPEN), ('deadline', '=', False), ('deadline', '>=', fields.Datetime.now())]

    def _compute_sibling_count(self):
        counts = Counter()
        doctors = self.mapped('doctor_id')
        if doctors:
            for doctor, count in self.sudo()._read_group(
                    [('doctor_id', 'in', doctors.ids), ('state', 'in', OPEN)], ['doctor_id'], ['__count']):
                counts[doctor.id] = count
        for ticket in self:
            mine = 1 if ticket.state in OPEN else 0
            ticket.sibling_count = max(0, counts.get(ticket.doctor_id.id, 0) - mine)

    @api.depends('name', 'partner_id', 'patient')
    def _compute_display_name(self):
        for ticket in self:
            parts = [ticket.name, ticket.sudo().partner_id.display_name, ticket.patient]
            ticket.display_name = ' · '.join(p for p in parts if p)

    # ------------------------------------------------------------------ who
    @api.model
    def _is_agent(self):
        return self.env.su or self.env.user.has_group(AGENT)

    @api.model
    def _is_lead(self):
        return self.env.su or self.env.user.has_group(LEAD)

    def _check_desk(self):
        if not self._is_agent():
            raise AccessError(_("Doctor call tickets are worked at the Doctor Call Desk. "
                                "Ask for the desk role if this is your job."))

    def _check_lead(self):
        if not self._is_lead():
            raise AccessError(_("Only a desk lead can do this."))

    def _check_open(self):
        for ticket in self:
            if ticket.state not in OPEN:
                raise UserError(_("%(ticket)s is %(state)s: there is nothing left to do on it.",
                                  ticket=ticket.name, state=dict(STATES)[ticket.state].lower()))

    # ------------------------------------------------------------------ raising
    @api.model
    def _hours_for(self, company, priority):
        hours = company.doctor_ticket_rush_hours if priority in ('1', '2') else company.doctor_ticket_hours
        if priority == '2':
            hours = (hours or 0.0) / 2.0
        return max(hours or 0.0, 0.25)

    @api.model
    def _values_from_order(self, order):
        """What a ticket takes from its order. Read as superuser: the desk does not need
        the sales role to be shown which clinic to ring."""
        order = order.sudo()
        partner = order.partner_id
        priority = ORDER_PRIORITY.get(order.priority, '0')
        now = fields.Datetime.now()
        return {
            'order_id': order.id,
            'company_id': order.company_id.id,
            'partner_id': partner.id,
            'doctor_id': partner.commercial_partner_id.id,
            'salesperson_id': order.user_id.id,
            'patient': order.patient if 'patient' in order._fields else False,
            'phone': partner.phone or partner.commercial_partner_id.phone or False,
            'whatsapp': (partner.whatsapp_number or partner.commercial_partner_id.whatsapp_number or False)
            if 'whatsapp_number' in partner._fields else False,
            'reason': order.call_doctor_reason or 'other',
            'question': order.call_doctor_note or False,
            'priority': priority,
            'raised_on': now,
            'deadline': now + timedelta(hours=self._hours_for(order.company_id, priority)),
        }

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('order_id'):
                base = self._values_from_order(self.env['sale.order'].browse(vals['order_id']))
                for key, value in base.items():
                    vals.setdefault(key, value)
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].sudo().with_company(vals.get('company_id')).next_by_code(
                    'lab.doctor.ticket') or 'New'
        tickets = super(DoctorTicket, self.with_context(mail_create_nosubscribe=True)).create(vals_list)
        for ticket in tickets:
            ticket.order_id.sudo().message_post(body=_(
                "Doctor ticket %(ticket)s raised: %(reason)s.",
                ticket=ticket.name, reason=dict(CALL_REASONS).get(ticket.reason, '')))
            if ticket.company_id.doctor_ticket_assign == 'spread' and not ticket.agent_id:
                ticket._share_out()
        return tickets

    def _desk_users(self, at_desk_only=True):
        self.ensure_one()
        users = self.env.ref(AGENT).sudo().all_user_ids.filtered(
            lambda u: u.active and not u.share and self.company_id in u.company_ids)
        return users.filtered('at_doctor_desk') if at_desk_only else users

    def _share_out(self):
        """To whoever at the desk has the fewest open. Nobody at the desk: it stays in the pool."""
        self.ensure_one()
        users = self._desk_users()
        if not users:
            return False
        load = Counter()
        for user, count in self.sudo()._read_group(
                [('agent_id', 'in', users.ids), ('state', 'in', OPEN)], ['agent_id'], ['__count']):
            load[user.id] = count
        chosen = min(users, key=lambda u: (load.get(u.id, 0), u.id))
        self.sudo().write({'agent_id': chosen.id, 'state': 'taken', 'taken_on': fields.Datetime.now()})
        self.sudo().activity_schedule(
            'mail.mail_activity_data_call', user_id=chosen.id, date_deadline=fields.Date.to_date(self.deadline),
            summary=_("Call %s", self.sudo().partner_id.display_name), note=escape(self.question or ''))
        return chosen

    # ------------------------------------------------------------------ at the desk
    def action_take(self):
        self._check_desk()
        self._check_open()
        for ticket in self:
            if ticket.agent_id and ticket.agent_id != self.env.user and not self._is_lead():
                raise UserError(_("%(ticket)s is already with %(who)s.",
                                  ticket=ticket.name, who=ticket.agent_id.name))
            ticket.sudo().write({
                'agent_id': self.env.uid,
                'state': 'taken' if ticket.state == 'new' else ticket.state,
                'taken_on': ticket.taken_on or fields.Datetime.now(),
            })
        return True

    def action_release(self):
        """Back to the pool: somebody else may take it."""
        self._check_desk()
        self._check_open()
        for ticket in self:
            if ticket.agent_id != self.env.user and not self._is_lead():
                raise UserError(_("%s is not yours to release.", ticket.name))
            ticket.sudo().write({'agent_id': False, 'state': 'new' if ticket.state == 'taken' else ticket.state})
            ticket.sudo().activity_unlink(['mail.mail_activity_data_call'])
        return True

    def action_assign(self, user_id):
        self._check_lead()
        self._check_open()
        user = self.env['res.users'].browse(int(user_id)).exists()
        if not user or not user.has_group(AGENT):
            raise UserError(_("Tickets are given to people who hold the desk role."))
        self.sudo().write({'agent_id': user.id, 'taken_on': fields.Datetime.now()})
        self.filtered(lambda t: t.state == 'new').sudo().write({'state': 'taken'})
        for ticket in self:
            ticket.sudo().message_notify(
                partner_ids=user.partner_id.ids, subject=_("Doctor ticket %s", ticket.name),
                body=_("%(ticket)s is yours: call %(who)s.", ticket=ticket.name,
                       who=ticket.sudo().partner_id.display_name))
        return True

    def log_call(self, response, remarks='', callback_at=False, also=None):
        """One attempt, on this ticket and on any other open question for the same
        doctor that the same call was about."""
        self.ensure_one()
        self._check_desk()
        if response not in dict(RESPONSES):
            raise UserError(_("That is not an outcome of a call."))
        if response == 'answered':
            raise UserError(_("An answered call closes the ticket: record what the doctor said."))
        tickets = self | self.browse([int(i) for i in (also or [])]).exists().filtered(
            lambda t: t.state in OPEN and t.doctor_id == self.doctor_id)
        tickets._check_open()
        callback = fields.Datetime.to_datetime(callback_at) if callback_at else False
        if response == 'callback_requested' and not callback:
            raise UserError(_("When did the doctor ask to be called back?"))
        now = fields.Datetime.now()
        for ticket in tickets:
            ticket._attempt(response, remarks)
            retry = ticket.company_id.doctor_ticket_retry or 60
            ticket.sudo().write({
                'state': 'waiting',
                'agent_id': ticket.agent_id.id or self.env.uid,
                'taken_on': ticket.taken_on or now,
                'callback_at': callback or now + timedelta(minutes=retry),
                'callback_told': False,
            })
            limit = ticket.company_id.doctor_ticket_escalate
            if limit and ticket.unanswered >= limit and not ticket.escalated:
                ticket._escalate(_("%s attempts without an answer", ticket.unanswered))
        return True

    def _attempt(self, response, remarks='', channel='phone'):
        self.ensure_one()
        now = fields.Datetime.now()
        # as superuser: the log writes on the order's chatter, and the desk does not hold
        # the sales role - but it is the caller who is named on it
        self.env['lab.doctor.call.log'].sudo().create({
            'order_id': self.order_id.id, 'ticket_id': self.id, 'response': response,
            'remarks': remarks or False, 'called_by_id': self.env.uid, 'phone': self.phone or False,
            'channel': channel, 'call_datetime': now,
        })
        if not self.first_call_on:
            self.sudo().first_call_on = now

    def action_answer(self, outcome, answer, channel='phone', tell_production=True, also=None):
        """The doctor answered: the ticket closes on what was said."""
        self.ensure_one()
        in_person = self.state == 'field' and self.field_user_id == self.env.user
        if not in_person:
            self._check_desk()
        outcomes = dict(OUTCOMES)
        if outcome not in outcomes:
            raise UserError(_("Say what the answer means for the order."))
        answer = (answer or '').strip()
        if outcome == 'unreached':
            self._check_lead()
            if len(answer) < 5:
                raise UserError(_("Going ahead without the doctor's answer is a decision: write why."))
        elif not answer:
            raise UserError(_("Write what the doctor said."))      # "A2" is an answer
        if in_person or channel not in dict(CHANNELS):
            # asked at the clinic is asked in person, whatever the form said
            channel = 'person' if in_person else 'phone'
        tickets = self | self.browse([int(i) for i in (also or [])]).exists().filtered(
            lambda t: t.state in OPEN and t.doctor_id == self.doctor_id)
        tickets._check_open()
        now = fields.Datetime.now()
        for ticket in tickets:
            if outcome != 'unreached':
                ticket._attempt('answered', answer, channel=channel)
            ticket.sudo().write({
                'state': 'closed', 'outcome': outcome, 'answer': answer, 'channel': channel,
                'tell_production': bool(tell_production), 'closed_on': now, 'closed_by_id': self.env.uid,
                'agent_id': ticket.agent_id.id or (False if in_person else self.env.uid),
                'callback_at': False,
                'hours_to_close': (now - ticket.raised_on).total_seconds() / 3600.0 if ticket.raised_on else 0.0,
                'first_call_answer': outcome != 'unreached' and ticket.attempts == 1,
            })
            ticket.sudo().activity_unlink(['mail.mail_activity_data_call', 'mail.mail_activity_data_todo'])
            ticket._settle_order()
            ticket._tell_who_raised_it()
        return True

    def action_hand_to_field(self, user_id=False, note=''):
        """A phone will not settle it: whoever visits the clinic asks in person."""
        self._check_desk()
        self._check_open()
        for ticket in self:
            user = self.env['res.users'].browse(int(user_id)).exists() if user_id else ticket._field_user()
            if not user:
                raise UserError(_("Nobody visits %s: choose who is to ask.",
                                  ticket.sudo().partner_id.display_name))
            ticket.sudo().write({'state': 'field', 'field_user_id': user.id, 'field_note': note or False,
                                 'callback_at': False})
            ticket.sudo().activity_schedule(
                'mail.mail_activity_data_todo', user_id=user.id, date_deadline=fields.Date.context_today(ticket),
                summary=_("Ask %s in person", ticket.sudo().partner_id.display_name),
                note=escape(ticket.question or '') + (Markup('<br/>') + escape(note) if note else ''))
        return True

    def _field_user(self):
        """Who carries this clinic: the executive of the visit the order came from, else
        whoever raised it, else its salesperson."""
        self.ensure_one()
        order = self.order_id.sudo()
        visit_user = order.visit_id.user_id if 'visit_id' in order._fields and order.visit_id else self.env['res.users']
        for user in (visit_user, self.raised_by_id, self.salesperson_id):
            if user and user.active and not user.share:
                return user
        return self.env['res.users']

    def action_back_to_desk(self):
        """Taken back from the field, or out of waiting: it is in hand again."""
        self._check_desk()
        self._check_open()
        self.sudo().write({'state': 'taken', 'agent_id': self.env.uid, 'callback_at': False})
        self.sudo().activity_unlink(['mail.mail_activity_data_todo'])
        return True

    def action_escalate(self, note=''):
        self._check_desk()
        self._check_open()
        for ticket in self:
            ticket._escalate(note or _("Escalated by %s", self.env.user.name))
        return True

    def _escalate(self, note):
        self.ensure_one()
        self.sudo().write({'escalated': True, 'escalated_on': fields.Datetime.now(), 'escalation_note': note})
        leads = self.env.ref(LEAD).sudo().all_user_ids.filtered(
            lambda u: u.active and self.company_id in u.company_ids)
        if leads:
            self.sudo().message_notify(
                partner_ids=leads.partner_id.ids, subject=_("Doctor ticket %s escalated", self.name),
                body=_("%(ticket)s (%(who)s, order %(order)s): %(note)s.", ticket=self.name,
                       who=self.sudo().partner_id.display_name, order=self.order_name, note=note))

    def action_settle_escalation(self):
        self._check_lead()
        self.sudo().write({'escalated': False})
        return True

    def action_set_callback(self, callback_at):
        self._check_desk()
        self._check_open()
        when = fields.Datetime.to_datetime(callback_at)
        if not when:
            raise UserError(_("Choose when to call again."))
        self.sudo().write({'state': 'waiting', 'callback_at': when, 'callback_told': False,
                           'agent_id': self.env.uid})
        return True

    def action_withdraw(self, reason=''):
        """The question is no longer a question. A lead's decision: it opens the gate."""
        self._check_lead()
        self._check_open()
        for ticket in self:
            ticket._withdraw(reason or _("withdrawn by %s", self.env.user.name))
            ticket._settle_order(withdrawn=True)
        return True

    def _withdraw(self, reason):
        self.ensure_one()
        self.sudo().write({'state': 'cancelled', 'closed_on': fields.Datetime.now(),
                           'closed_by_id': self.env.uid, 'callback_at': False, 'escalated': False})
        self.sudo().activity_unlink(['mail.mail_activity_data_call', 'mail.mail_activity_data_todo'])
        self.sudo().message_post(body=_("Withdrawn: %s.", reason))

    def action_reopen(self):
        self._check_lead()
        for ticket in self:
            if ticket.state in OPEN:
                continue
            if ticket.order_id.sudo().state not in ('draft', 'sent'):
                raise UserError(_("%(order)s is already %(state)s: a new question needs a new ticket.",
                                  order=ticket.order_name, state=ticket.order_id.sudo().state))
            now = fields.Datetime.now()
            ticket.sudo().write({
                'state': 'taken', 'agent_id': self.env.uid, 'outcome': False, 'closed_on': False,
                'closed_by_id': False, 'hours_to_close': 0.0, 'first_call_answer': False,
                'deadline': now + timedelta(hours=self._hours_for(ticket.company_id, ticket.priority)),
            })
            vals = {'call_doctor_required': True, 'call_doctor_resolved': False}
            if 'is_pending_work' in ticket.order_id._fields:
                vals['is_pending_work'] = True
            ticket.order_id.sudo().with_context(doctor_ticket_sync=True).write(vals)
            ticket.order_id.sudo().message_post(body=_("Doctor ticket %s reopened.", ticket.name))
        return True

    # ------------------------------------------------------------------ what closing does
    def _settle_order(self, withdrawn=False):
        """The answer goes where the work is: on the order, on the job card, and the
        order moves on once nothing is left to ask."""
        self.ensure_one()
        order = self.order_id.sudo()
        if not withdrawn:
            order.message_post(body=Markup("%s<br/><b>%s</b> %s") % (
                _("Doctor ticket %(ticket)s closed: %(outcome)s.", ticket=self.name,
                  outcome=dict(OUTCOMES).get(self.outcome, '')),
                _("The doctor said:") if self.outcome != 'unreached' else _("Decided:"), self.answer or ''))
            if self.tell_production and self.answer and self.outcome in ('as_is', 'change') \
                    and 'instruction' in order._fields and order.state != 'cancel':
                said = _("Doctor (%(day)s): %(answer)s", day=fields.Date.context_today(self).strftime('%d/%m'),
                         answer=self.answer)
                order.with_context(doctor_ticket_sync=True).write(
                    {'instruction': ', '.join(p for p in (order.instruction, said) if p)})
        if order.doctor_ticket_ids.filtered(lambda t: t.state in OPEN):
            return                                   # another question is still open
        if order.state != 'cancel':
            vals = {'call_doctor_resolved': True}
            if 'is_pending_work' in order._fields:
                vals['is_pending_work'] = False
            order.with_context(doctor_ticket_sync=True).write(vals)
            if order.verification_state == 'on_hold' and order.hold_reason == 'awaiting_doctor_response':
                order.action_resume_from_hold()
        if withdrawn or order.state not in ('draft', 'sent'):
            return
        if self.outcome == 'change':
            order.activity_schedule(
                'mail.mail_activity_data_todo', date_deadline=fields.Date.context_today(self),
                user_id=(order.entered_by_id or order.user_id or self.env.user).id,
                summary=_("Change %s: the doctor's answer", order.name), note=escape(self.answer or ''))
        elif self.outcome == 'cancel':
            order.activity_schedule(
                'mail.mail_activity_data_todo', date_deadline=fields.Date.context_today(self),
                user_id=(order.verified_by_id or order.entered_by_id or order.user_id or self.env.user).id,
                summary=_("Cancel %s: the doctor cancelled the case", order.name), note=escape(self.answer or ''))
        elif self.outcome == 'as_is' and self.company_id.doctor_ticket_confirm:
            self._confirm_order(order)

    def _confirm_order(self, order):
        """Only what needs nobody's eye: no checking owed, or already checked."""
        if order._verification_required() and order.verification_state != 'verified':
            return False
        try:
            with self.env.cr.savepoint():
                order.action_confirm()
            order.message_post(body=_("Confirmed by itself: its last doctor ticket closed."))
            return True
        except Exception as error:                                   # a credit block, a missing line
            order.message_post(body=_("Not confirmed by itself after the doctor's answer: %s", error))
            return False

    def _tell_who_raised_it(self):
        self.ensure_one()
        who = (self.raised_by_id | self.salesperson_id).filtered(
            lambda u: u.active and not u.share and u != self.env.user)
        if not who:
            return
        self.sudo().message_notify(
            partner_ids=who.partner_id.ids,
            subject=_("%(who)s: the doctor's answer", who=self.sudo().partner_id.display_name),
            body=Markup("%s<br/><b>%s</b><br/>%s") % (
                _("%(order)s, %(patient)s", order=self.order_name, patient=self.patient or ''),
                dict(OUTCOMES).get(self.outcome, ''), self.answer or ''))

    # ------------------------------------------------------------------ what is known about the doctor
    @api.model
    def _desk_tz(self):
        name = (self.env.context.get('tz') or self.env.user.tz
                or self.env.company.partner_id.tz or 'Asia/Kolkata')
        try:
            return pytz.timezone(name)
        except pytz.UnknownTimeZoneError:
            return pytz.timezone('Asia/Kolkata')

    @api.model
    def doctor_habits(self, doctor_id):
        """When this practice picks up, from every call ever logged to it: the two hours of
        the day that were answered most, and how many attempts in ten reach somebody."""
        Log = self.env['lab.doctor.call.log'].sudo()
        calls = Log.search_read(
            [('order_id.partner_id.commercial_partner_id', '=', int(doctor_id))],
            ['call_datetime', 'response'], order='call_datetime desc', limit=400)
        if not calls:
            return {'calls': 0, 'answered': 0, 'rate': None, 'best': '', 'hours': []}
        tz = self._desk_tz()
        by_hour, answered = Counter(), 0
        tried = Counter()
        for call in calls:
            hour = pytz.utc.localize(call['call_datetime']).astimezone(tz).hour
            tried[hour] += 1
            if call['response'] == 'answered':
                answered += 1
                by_hour[hour] += 1
        best = ''
        if answered >= 3:
            start = max(range(24), key=lambda h: (by_hour[h] + by_hour[(h + 1) % 24], -h))
            inside = by_hour[start] + by_hour[(start + 1) % 24]
            if inside >= 2:
                best = _("Usually answers between %(a)s and %(b)s (%(n)s of %(all)s answered calls)",
                         a=self._clock(start), b=self._clock((start + 2) % 24), n=inside, all=answered)
        return {
            'calls': len(calls), 'answered': answered, 'rate': round(100.0 * answered / len(calls)),
            'best': best,
            'hours': [{'hour': h, 'label': self._clock(h), 'tried': tried[h], 'answered': by_hour[h]}
                      for h in range(7, 22)],
        }

    @api.model
    def _clock(self, hour):
        return '%d %s' % (hour % 12 or 12, 'am' if hour < 12 else 'pm')

    # ------------------------------------------------------------------ the watch
    @api.model
    def _cron_watch(self):
        """Every few minutes: what is late is escalated once, and a callback whose time
        has come is put in front of whoever holds it."""
        now = fields.Datetime.now()
        late = self.sudo().search([('state', 'in', OPEN), ('deadline', '<', now), ('escalated', '=', False)])
        for ticket in late:
            ticket._escalate(_("no answer within the time allowed"))
        due = self.sudo().search([('state', '=', 'waiting'), ('callback_at', '<=', now),
                                  ('callback_told', '=', False), ('agent_id', '!=', False)])
        for ticket in due:
            ticket.message_notify(
                partner_ids=ticket.agent_id.partner_id.ids, subject=_("Call %s now", ticket.partner_id.display_name),
                body=_("%(ticket)s: it is time to call %(who)s again.", ticket=ticket.name,
                       who=ticket.partner_id.display_name))
        due.write({'callback_told': True})
        return len(late) + len(due)

    # ------------------------------------------------------------------ opening things
    def action_open_order(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'sale.order', 'res_id': self.order_id.id,
                'view_mode': 'form', 'views': [(False, 'form')]}

    def action_open_desk(self):
        return {'type': 'ir.actions.client', 'tag': 'lab_doctor_desk', 'params': {'ticket_id': self[:1].id}}
