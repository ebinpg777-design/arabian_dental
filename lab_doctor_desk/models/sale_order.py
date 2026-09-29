# -*- coding: utf-8 -*-
"""The order's side of the desk: a case that needs the doctor raises its ticket, and
the order waits for it.

Two flags say "call the doctor" on an order - `call_doctor_required`, which the
verification gate reads, and `is_pending_work`, the lab's older one that the job card
and the benches read. Either raises the ticket, and closing the ticket settles both,
so there is one fact again instead of two that drift.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .ticket import OPEN, STATES, LEAD

FLAGS = ('call_doctor_required', 'call_doctor_resolved', 'is_pending_work')


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    doctor_ticket_ids = fields.One2many('lab.doctor.ticket', 'order_id', string='Doctor Tickets')
    doctor_ticket_count = fields.Integer(compute='_compute_doctor_tickets')
    doctor_ticket_open = fields.Integer(compute='_compute_doctor_tickets', string='Open Doctor Tickets')
    doctor_ticket_note = fields.Char(compute='_compute_doctor_tickets', string='Doctor Ticket')
    doctor_tickets_on = fields.Boolean(related='company_id.doctor_tickets', string='Doctor Call Desk In Use')

    @api.depends('doctor_ticket_ids.state', 'doctor_ticket_ids.attempts', 'doctor_ticket_ids.callback_at')
    def _compute_doctor_tickets(self):
        labels = dict(STATES)
        for order in self:
            tickets = order.sudo().doctor_ticket_ids
            live = tickets.filtered(lambda t: t.state in OPEN)
            order.doctor_ticket_count = len(tickets)
            order.doctor_ticket_open = len(live)
            shown = live[:1] or tickets.sorted('id')[-1:]
            order.doctor_ticket_note = shown and _(
                "%(ticket)s: %(state)s%(tries)s", ticket=shown.name, state=labels[shown.state].lower(),
                tries=_(", %s attempt(s)", shown.attempts) if shown.attempts else '') or False

    # ------------------------------------------------------------------ raising
    def _raise_doctor_tickets(self):
        """A ticket for every order here that needs the doctor and has none open."""
        Ticket = self.env['lab.doctor.ticket'].sudo()
        raised = Ticket.browse()
        person = self.env.user if not self.env.user._is_superuser() else self.env['res.users']
        for order in self.sudo():
            if not order.company_id.doctor_tickets or order.state == 'cancel':
                continue
            older_flag = 'is_pending_work' in order._fields and order.is_pending_work
            if older_flag and not order.call_doctor_required:
                order.with_context(doctor_ticket_sync=True).write({
                    'call_doctor_required': True, 'call_doctor_resolved': False,
                    'call_doctor_reason': order.call_doctor_reason or 'other'})
            if not order.call_pending:
                continue
            if order.doctor_ticket_ids.filtered(lambda t: t.state in OPEN):
                continue
            raised |= Ticket.create({
                'order_id': order.id,
                'raised_by_id': (person or order.entered_by_id or order.user_id or self.env.user).id,
            })
        return raised

    def _withdraw_doctor_tickets(self, reason):
        for ticket in self.sudo().doctor_ticket_ids.filtered(lambda t: t.state in OPEN):
            ticket._withdraw(reason)

    def _guard_doctor_flags(self, vals):
        """With a ticket open, the flags are the desk's. Taking one off by hand would
        open the gate the ticket is there to hold - so it is a lead's act, and it
        withdraws the ticket in the open rather than leaving it to wait for nothing."""
        lifting = (vals.get('call_doctor_required') is False or vals.get('call_doctor_resolved') is True
                   or vals.get('is_pending_work') is False)
        if not lifting:
            return
        for order in self.sudo():
            if not order.company_id.doctor_tickets:
                continue
            live = order.doctor_ticket_ids.filtered(lambda t: t.state in OPEN)
            if not live:
                continue
            if not self.env.user.has_group(LEAD):
                raise UserError(_(
                    "%(order)s has an open doctor ticket (%(ticket)s). The call is settled at the "
                    "Doctor Call Desk: the ticket closes on the doctor's answer, and the order "
                    "moves on by itself.", order=order.name, ticket=', '.join(live.mapped('name'))))
            order._withdraw_doctor_tickets(_("the call was taken off the order by %s", self.env.user.name))

    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)
        orders._raise_doctor_tickets()
        return orders

    def write(self, vals):
        mine = self.env.context.get('doctor_ticket_sync')
        watched = not mine and any(flag in vals for flag in FLAGS)
        if watched:
            self._guard_doctor_flags(vals)
        res = super().write(vals)
        if watched:
            self._raise_doctor_tickets()
        if vals.get('state') == 'cancel':
            self._withdraw_doctor_tickets(_("the order was cancelled"))
        if not mine and ('call_doctor_note' in vals or 'call_doctor_reason' in vals):
            # a question put better before anybody rang is the question the desk should see
            for order in self.sudo():
                fresh = order.doctor_ticket_ids.filtered(lambda t: t.state in OPEN and not t.attempts)
                if fresh:
                    fresh.write({'question': order.call_doctor_note or False,
                                 'reason': order.call_doctor_reason or fresh[0].reason})
        return res

    # ------------------------------------------------------------------ the gate
    def _doctor_tickets_in_the_way(self):
        self.ensure_one()
        if not self.company_id.sudo().doctor_tickets:
            return self.env['lab.doctor.ticket']
        return self.sudo().doctor_ticket_ids.filtered(lambda t: t.state in OPEN)

    def action_confirm(self):
        labels = dict(STATES)
        for order in self:
            live = order._doctor_tickets_in_the_way()
            if live:
                raise UserError(_(
                    "%(order)s is waiting for the doctor: %(tickets)s. It can be confirmed once "
                    "the ticket is closed at the Doctor Call Desk.",
                    order=order.name,
                    tickets=', '.join('%s (%s)' % (t.name, labels[t.state].lower()) for t in live)))
        return super().action_confirm()

    def action_resolve_doctor_call(self):
        for order in self:
            live = order._doctor_tickets_in_the_way()
            if live:
                raise UserError(_(
                    "The doctor's answer is recorded on the ticket (%s) at the Doctor Call Desk; "
                    "closing it resolves the call here.", ', '.join(live.mapped('name'))))
        return super().action_resolve_doctor_call()

    def action_log_doctor_call(self):
        self.ensure_one()
        live = self._doctor_tickets_in_the_way()
        if live:
            return self.action_view_doctor_tickets()
        return super().action_log_doctor_call()

    def action_view_doctor_tickets(self):
        self.ensure_one()
        tickets = self.sudo().doctor_ticket_ids
        action = {'type': 'ir.actions.act_window', 'name': _('Doctor Tickets'),
                  'res_model': 'lab.doctor.ticket', 'domain': [('order_id', '=', self.id)],
                  'context': {'create': False}}
        if len(tickets) == 1:
            action.update(view_mode='form', views=[(False, 'form')], res_id=tickets.id)
        else:
            action.update(view_mode='list,form', views=[(False, 'list'), (False, 'form')])
        return action

    def _action_cancel(self):
        res = super()._action_cancel()
        self._withdraw_doctor_tickets(_("the order was cancelled"))
        return res
