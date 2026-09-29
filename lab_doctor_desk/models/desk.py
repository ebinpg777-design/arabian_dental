# -*- coding: utf-8 -*-
"""What the desk's screen is made of. One call paints the board; one more opens a
ticket. Read as superuser after the desk role is checked: the people at the desk are
shown the clinic, the patient and what was ordered without holding the sales role."""
import re
from datetime import datetime, time, timedelta
from urllib.parse import quote

import pytz

from odoo import _, api, fields, models

from odoo.addons.lab_order_control.models.doctor_call import RESPONSES
from odoo.addons.lab_order_control.models.sale_order_hold import CALL_REASONS

from .ticket import AGENT, CHANNELS, LEAD, OPEN, OUTCOMES, PRIORITIES, STATES


class DoctorDesk(models.AbstractModel):
    _name = 'lab.doctor.desk'
    _description = 'Doctor Call Desk'

    # ------------------------------------------------------------------ helpers
    @api.model
    def _today_start(self):
        tz = self.env['lab.doctor.ticket']._desk_tz()
        local = pytz.utc.localize(fields.Datetime.now()).astimezone(tz)
        start = tz.localize(datetime.combine(local.date(), time.min))
        return start.astimezone(pytz.utc).replace(tzinfo=None)

    @api.model
    def _local(self, moment):
        if not moment:
            return False
        tz = self.env['lab.doctor.ticket']._desk_tz()
        return pytz.utc.localize(moment).astimezone(tz)

    @api.model
    def _when(self, moment):
        """A moment the way somebody at a desk says it: 'today 4:30 pm', 'tomorrow 10:00 am'."""
        local = self._local(moment)
        if not local:
            return ''
        today = self._local(fields.Datetime.now()).date()
        clock = local.strftime('%I:%M %p').lstrip('0').lower()
        gap = (local.date() - today).days
        if gap == 0:
            return _("today %s", clock)
        if gap == 1:
            return _("tomorrow %s", clock)
        if gap == -1:
            return _("yesterday %s", clock)
        return '%s %s' % (local.strftime('%d %b'), clock)

    @api.model
    def _ago(self, moment, now=None):
        if not moment:
            return ''
        minutes = int(((now or fields.Datetime.now()) - moment).total_seconds() // 60)
        ahead = minutes < 0
        minutes = abs(minutes)
        if minutes < 60:
            text = _("%s min", max(minutes, 1))
        elif minutes < 48 * 60:
            text = _("%(h)s h %(m)s min", h=minutes // 60, m=minutes % 60) if minutes % 60 and minutes < 600 \
                else _("%s h", minutes // 60)
        else:
            text = _("%s days", minutes // 1440)
        return _("in %s", text) if ahead else text

    @api.model
    def _digits(self, number):
        return re.sub(r'\D', '', number or '')

    @api.model
    def _card(self, ticket, now, siblings):
        partner = ticket.partner_id
        reasons = dict(CALL_REASONS)
        minutes_left = int((ticket.deadline - now).total_seconds() // 60) if ticket.deadline else None
        calls = ticket.call_ids.sorted(lambda c: (c.call_datetime, c.id))
        return {
            'id': ticket.id, 'name': ticket.name, 'state': ticket.state,
            'state_label': dict(STATES)[ticket.state],
            'priority': ticket.priority, 'priority_label': dict(PRIORITIES)[ticket.priority],
            'reason': ticket.reason, 'reason_label': reasons.get(ticket.reason, ''),
            'question': ticket.question or '',
            'clinic': partner.display_name or '', 'practice': ticket.doctor_id.display_name or '',
            'doctor_id': ticket.doctor_id.id,
            'patient': ticket.patient or '',
            'phone': ticket.phone or '', 'whatsapp': ticket.whatsapp or '',
            'order': ticket.order_name or '', 'order_id': ticket.order_id.id,
            'raised_by': ticket.raised_by_id.name or '', 'raised_ago': self._ago(ticket.raised_on, now),
            'raised_on': self._when(ticket.raised_on),
            'agent_id': ticket.agent_id.id or False, 'agent': ticket.agent_id.name or '',
            'mine': ticket.agent_id == self.env.user,
            'deadline': self._when(ticket.deadline), 'minutes_left': minutes_left,
            'left': self._ago(now - timedelta(minutes=abs(minutes_left)), now) if minutes_left is not None else '',
            'due_state': ticket.due_state,
            'attempts': [c.response for c in calls],
            'callback': self._when(ticket.callback_at), 'callback_due': ticket.callback_due,
            'callback_in': self._ago(ticket.callback_at, now) if ticket.callback_at else '',
            'escalated': ticket.escalated, 'escalation_note': ticket.escalation_note or '',
            'field_user': ticket.field_user_id.name or '',
            'siblings': max(0, siblings.get(ticket.doctor_id.id, 0) - (1 if ticket.state in OPEN else 0)),
            'outcome': ticket.outcome or '', 'outcome_label': dict(OUTCOMES).get(ticket.outcome, ''),
            'answer': ticket.answer or '', 'closed_by': ticket.closed_by_id.name or '',
            'closed_on': self._when(ticket.closed_on), 'hours_to_close': round(ticket.hours_to_close or 0.0, 1),
        }

    # ------------------------------------------------------------------ the board
    @api.model
    def get_desk(self):
        Ticket = self.env['lab.doctor.ticket']
        Ticket._check_desk()
        companies = self.env.companies
        now = fields.Datetime.now()
        Sudo = Ticket.sudo()
        base = [('company_id', 'in', companies.ids)]
        live = Sudo.search(base + [('state', 'in', OPEN)], order='priority desc, deadline, id', limit=500)
        start = self._today_start()
        done = Sudo.search(base + [('state', '=', 'closed'), ('closed_on', '>=', start)],
                           order='closed_on desc', limit=40)
        siblings = {}
        for doctor, count in Sudo._read_group(base + [('state', 'in', OPEN)], ['doctor_id'], ['__count']):
            siblings[doctor.id] = count
        month = Sudo._read_group(
            base + [('state', '=', 'closed'), ('closed_on', '>=', now - timedelta(days=30)),
                    ('outcome', '!=', 'unreached')],
            [], ['hours_to_close:avg', '__count'])[0]
        first = Sudo.search_count(base + [('state', '=', 'closed'), ('closed_on', '>=', now - timedelta(days=30)),
                                          ('first_call_answer', '=', True)])
        cards = [self._card(t, now, siblings) for t in live]
        me = self.env.user
        load = {}
        for user, count in Sudo._read_group(base + [('state', 'in', OPEN), ('agent_id', '!=', False)],
                                            ['agent_id'], ['__count']):
            load[user.id] = count
        agents = self.env.ref(AGENT).sudo().all_user_ids.filtered(
            lambda u: u.active and not u.share and (u.company_ids & companies))
        return {
            'me': {'id': me.id, 'name': me.name, 'at_desk': me.at_doctor_desk, 'lead': Ticket._is_lead()},
            'on': [c.name for c in companies if c.sudo().doctor_tickets],
            'off': [c.name for c in companies if not c.sudo().doctor_tickets],
            'cards': cards,
            'done': [self._card(t, now, siblings) for t in done],
            'kpis': {
                'open': len(live),
                'pool': len([c for c in cards if c['state'] == 'new']),
                'mine': len([c for c in cards if c['mine']]),
                'due': len([c for c in cards if c['callback_due']]),
                'late': len([c for c in cards if c['due_state'] == 'late']),
                'escalated': len([c for c in cards if c['escalated']]),
                'closed_today': len(done),
                'avg_hours': round(month[0] or 0.0, 1), 'closed_month': month[1],
                'first_call': round(100.0 * first / month[1]) if month[1] else None,
            },
            'agents': [{'id': u.id, 'name': u.name, 'at_desk': u.at_doctor_desk, 'open': load.get(u.id, 0)}
                       for u in agents.sorted('name')],
            'reasons': [list(r) for r in CALL_REASONS],
            'responses': [list(r) for r in RESPONSES if r[0] != 'answered'],
            'outcomes': [list(o) for o in OUTCOMES],
            'channels': [list(c) for c in CHANNELS],
            'retry': max(companies[:1].sudo().doctor_ticket_retry or 60, 5),
            'now': self._when(now),
        }

    @api.model
    def get_ticket(self, ticket_id):
        """Everything somebody needs in front of them before they ring."""
        Ticket = self.env['lab.doctor.ticket']
        Ticket._check_desk()
        ticket = Ticket.sudo().browse(int(ticket_id)).exists()
        if not ticket or ticket.company_id not in self.env.companies:
            return False
        now = fields.Datetime.now()
        order = ticket.order_id
        responses = dict(RESPONSES)
        channels = dict(CHANNELS)
        others = Ticket.sudo().search([('doctor_id', '=', ticket.doctor_id.id), ('state', 'in', OPEN),
                                       ('id', '!=', ticket.id)], order='priority desc, deadline')
        card = self._card(ticket, now, {ticket.doctor_id.id: len(others) + (1 if ticket.state in OPEN else 0)})
        lines = []
        for line in order.order_line.filtered(lambda l: not l.display_type and l.product_id):
            bits = []
            if 'ul' in line._fields and line.ul:
                bits.append(dict(line._fields['ul']._description_selection(self.env)).get(line.ul, ''))
            if 'teeth' in line._fields and line.teeth:
                bits.append(line.teeth)
            if 'color_scheme' in line._fields and line.color_scheme:
                bits.append(line.color_scheme.display_name)
            lines.append({'name': line.product_id.display_name, 'qty': line.product_uom_qty,
                          'detail': ' · '.join(b for b in bits if b)})
        events = [{'at': ticket.raised_on, 'icon': 'fa-flag', 'tone': 'raised',
                   'text': _("Raised by %s", ticket.raised_by_id.name or '')}]
        if ticket.taken_on and ticket.agent_id:
            events.append({'at': ticket.taken_on, 'icon': 'fa-hand-paper-o', 'tone': 'taken',
                           'text': _("Taken by %s", ticket.agent_id.name)})
        for call in ticket.call_ids:
            events.append({
                'at': call.call_datetime, 'icon': 'fa-phone' if call.channel == 'phone' else
                'fa-whatsapp' if call.channel == 'whatsapp' else 'fa-user',
                'tone': 'answered' if call.response == 'answered' else 'missed',
                'text': '%s · %s' % (responses.get(call.response, ''), call.called_by_id.name or ''),
                'note': call.remarks or '', 'channel': channels.get(call.channel, '')})
        if ticket.escalated_on:
            events.append({'at': ticket.escalated_on, 'icon': 'fa-exclamation-triangle', 'tone': 'escalated',
                           'text': _("Escalated: %s", ticket.escalation_note or '')})
        if ticket.closed_on:
            events.append({'at': ticket.closed_on, 'icon': 'fa-check', 'tone': 'closed',
                           'text': _("Closed by %s", ticket.closed_by_id.name or '')})
        events.sort(key=lambda e: e['at'])
        for event in events:
            event['when'] = self._when(event.pop('at'))
        past = Ticket.sudo().search([('doctor_id', '=', ticket.doctor_id.id), ('state', '=', 'closed'),
                                     ('id', '!=', ticket.id), ('answer', '!=', False)],
                                    order='closed_on desc', limit=3)
        reasons = dict(CALL_REASONS)
        message = _("Hello Doctor, this is %(lab)s about %(patient)s (order %(order)s). %(question)s",
                    lab=ticket.company_id.name, patient=ticket.patient or _("your patient"),
                    order=ticket.order_name, question=ticket.question or reasons.get(ticket.reason, ''))
        wa = self._digits(ticket.whatsapp)
        field_user = ticket._field_user()
        card.update({
            'instruction': (order.instruction if 'instruction' in order._fields else '') or '',
            'order_date': self._when(order.date_order),
            'order_state': dict(order._fields['state']._description_selection(self.env)).get(order.state, ''),
            'order_due': fields.Date.to_string(order.date_due) if 'date_due' in order._fields and order.date_due else '',
            'verification': dict(order._fields['verification_state']._description_selection(self.env)).get(
                order.verification_state, ''),
            'lines': lines,
            'events': events,
            'others': [{'id': t.id, 'name': t.name, 'patient': t.patient or '', 'order': t.order_name or '',
                        'reason_label': reasons.get(t.reason, ''), 'question': t.question or '',
                        'priority': t.priority, 'state': t.state} for t in others],
            'habits': Ticket.doctor_habits(ticket.doctor_id.id),
            'past': [{'name': t.name, 'when': self._when(t.closed_on), 'reason_label': reasons.get(t.reason, ''),
                      'question': t.question or '', 'answer': t.answer or ''} for t in past],
            'tel': 'tel:%s' % re.sub(r'[^\d+]', '', ticket.phone) if ticket.phone else '',
            # only ever to the number kept for WhatsApp, never the phone: that is often a landline
            'wa': 'https://wa.me/%s?text=%s' % (wa, quote(message)) if wa else '',
            'message': message,
            'field_candidate': field_user.name or '',
            'can_open_order': self.env['sale.order'].has_access('read'),
        })
        return card

    @api.model
    def set_at_desk(self, at_desk):
        self.env['lab.doctor.ticket']._check_desk()
        self.env.user.sudo().at_doctor_desk = bool(at_desk)
        return bool(at_desk)
