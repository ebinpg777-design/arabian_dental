# -*- coding: utf-8 -*-
"""What the executive who raised the question is shown: where it stands, and - when
the desk hands it to them - the question itself, to be asked at the clinic."""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .ticket import CHANNELS, OPEN, OUTCOMES, STATES


class LabCase(models.Model):
    _inherit = 'lab.case'

    doctor_ticket_id = fields.Many2one('lab.doctor.ticket', compute='_compute_doctor_ticket', string='Doctor Ticket')
    doctor_ticket_state = fields.Selection(STATES, compute='_compute_doctor_ticket', string='Doctor Call')
    doctor_ticket_line = fields.Char(compute='_compute_doctor_ticket')
    doctor_ticket_answer = fields.Text(compute='_compute_doctor_ticket', string="The doctor's answer")
    doctor_ticket_mine = fields.Boolean(compute='_compute_doctor_ticket')
    doctor_ticket_ask = fields.Text(compute='_compute_doctor_ticket', string='To ask the doctor')

    @api.depends('sale_order_id')
    def _compute_doctor_ticket(self):
        labels = dict(STATES)
        for case in self:
            order = case.sudo().sale_order_id
            tickets = order.doctor_ticket_ids if order else self.env['lab.doctor.ticket'].sudo()
            ticket = tickets.filtered(lambda t: t.state in OPEN)[:1] or tickets.sorted('id')[-1:]
            case.doctor_ticket_id = ticket.id or False
            case.doctor_ticket_state = ticket.state or False
            case.doctor_ticket_answer = ticket.answer if ticket.state == 'closed' else False
            case.doctor_ticket_mine = bool(ticket and ticket.state == 'field' and ticket.field_user_id == self.env.user)
            # the question as the desk has it, which may be put better than it was on the slip
            case.doctor_ticket_ask = '\n'.join(p for p in (ticket.question, ticket.field_note) if p) or False
            line = False
            if ticket:
                bits = [ticket.name, labels[ticket.state]]
                if ticket.state in OPEN and ticket.agent_id:
                    bits.append(_("with %s", ticket.agent_id.name))
                if ticket.state in OPEN and ticket.attempts:
                    bits.append(_("%s attempt(s)", ticket.attempts))
                if ticket.state == 'closed' and ticket.outcome:
                    bits.append(dict(OUTCOMES)[ticket.outcome])
                line = ' · '.join(bits)
            case.doctor_ticket_line = line

    def action_answer_doctor_ticket(self):
        self.ensure_one()
        if not self.doctor_ticket_mine:
            raise UserError(_("This question is not with you."))
        return {
            'type': 'ir.actions.act_window', 'name': _("What the doctor said"),
            'res_model': 'lab.doctor.ticket.answer', 'view_mode': 'form', 'target': 'new',
            'context': {'default_ticket_id': self.doctor_ticket_id.id, 'default_channel': 'person'},
        }


class DoctorTicketAnswer(models.TransientModel):
    """The doctor's answer, written down. One form for the desk and for the executive
    who asked in person."""
    _name = 'lab.doctor.ticket.answer'
    _description = "Doctor's Answer"

    ticket_id = fields.Many2one('lab.doctor.ticket', required=True, ondelete='cascade')
    question = fields.Text(related='ticket_id.question')
    # going ahead without an answer is a lead's decision, made at the desk - not offered here
    outcome = fields.Selection([o for o in OUTCOMES if o[0] != 'unreached'], required=True, default='as_is',
                               string='The order')
    in_person = fields.Boolean(compute='_compute_in_person')
    answer = fields.Text("What the doctor said", required=True)
    channel = fields.Selection(CHANNELS, default='phone', required=True, string='Answered by')
    tell_production = fields.Boolean('Put the answer on the job card', default=True)

    @api.depends('ticket_id')
    def _compute_in_person(self):
        for wizard in self:
            ticket = wizard.ticket_id.sudo()
            wizard.in_person = ticket.state == 'field' and ticket.field_user_id == self.env.user

    def action_save(self):
        self.ensure_one()
        self.ticket_id.action_answer(self.outcome, self.answer, channel=self.channel,
                                     tell_production=self.tell_production)
        return {'type': 'ir.actions.act_window_close'}


class DoctorTicketAttempt(models.TransientModel):
    """A call that was not answered."""
    _name = 'lab.doctor.ticket.attempt'
    _description = 'Doctor Call Attempt'

    ticket_id = fields.Many2one('lab.doctor.ticket', required=True, ondelete='cascade')
    response = fields.Selection(
        [('no_answer', 'No Answer'), ('busy', 'Busy'), ('switched_off', 'Switched Off'),
         ('callback_requested', 'Callback Requested'), ('wrong_number', 'Wrong Number')],
        required=True, default='no_answer', string='What happened')
    callback_at = fields.Datetime('Call again at')
    remarks = fields.Char()

    def action_save(self):
        self.ensure_one()
        self.ticket_id.log_call(self.response, remarks=self.remarks or '', callback_at=self.callback_at)
        return {'type': 'ir.actions.act_window_close'}
