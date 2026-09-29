# -*- coding: utf-8 -*-
"""The desk is a company's choice. Off, this module does nothing at all."""
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ResCompany(models.Model):
    _inherit = 'res.company'

    doctor_tickets = fields.Boolean(
        'Doctor Call Desk',
        help="A case that needs the doctor raises a ticket for the desk, and its order "
             "cannot be confirmed until the ticket is closed.")
    doctor_ticket_assign = fields.Selection(
        [('pool', 'The desk takes them'), ('spread', 'Shared out by load')],
        string='New tickets', default='pool', required=True,
        help="Taken from the pool by whoever is free, or given straight to the person at "
             "the desk who has the fewest open.")
    doctor_ticket_hours = fields.Float(
        'Answer within (hours)', default=24.0,
        help="How long a normal case may wait for the doctor's answer before its ticket is late.")
    doctor_ticket_rush_hours = fields.Float(
        'Urgent and emergency within (hours)', default=4.0)
    doctor_ticket_retry = fields.Integer(
        'Try again after (minutes)', default=60,
        help="When the doctor did not answer and no time was agreed, the ticket comes back "
             "to the top of the desk after this long.")
    doctor_ticket_escalate = fields.Integer(
        'Escalate after attempts', default=3,
        help="This many unanswered attempts and the ticket is escalated to the desk lead. "
             "0 never escalates on attempts.")
    doctor_ticket_confirm = fields.Boolean(
        'Confirm the order when its last ticket closes',
        help="Only an order that needs no checking, or has already been verified, and whose "
             "answer was 'go ahead as written'. Anything else waits for a person.")


    def write(self, vals):
        # Here and not in the settings' own save: the settings write through to the
        # company the moment they are filled in, so by the time they are saved the
        # company already reads "on" and nothing can tell that it was switched.
        switching = self.filtered(lambda c: not c.doctor_tickets) if vals.get('doctor_tickets') else self.browse()
        res = super().write(vals)
        for company in switching:
            company._doctor_desk_must_be_manned()
            company._doctor_raise_waiting()
        return res

    def _doctor_desk_must_be_manned(self):
        self.ensure_one()
        holders = self.env.ref('lab_doctor_desk.group_doctor_desk_agent').sudo().all_user_ids.filtered(
            lambda u: u.active and not u.share and self in u.company_ids)
        if not holders:
            raise UserError(_(
                "Nobody holds the Doctor Call Desk role in %s. Give it to the people who will take "
                "the tickets first (Settings > Users), or every order that needs the doctor will "
                "wait for a desk that nobody sits at.", self.name))

    def _doctor_orders_without_ticket(self):
        self.ensure_one()
        return [('company_id', '=', self.id), ('state', 'in', ('draft', 'sent')),
                ('call_pending', '=', True), ('doctor_ticket_ids', '=', False)]

    def _doctor_raise_waiting(self):
        """Switched on: what was already waiting on the doctor gets its ticket, so no
        order is left behind the gate with nothing at the desk to open it."""
        for company in self:
            orders = self.env['sale.order'].sudo().search(company._doctor_orders_without_ticket())
            orders._raise_doctor_tickets()
        return True


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    doctor_tickets = fields.Boolean(related='company_id.doctor_tickets', readonly=False)
    doctor_ticket_assign = fields.Selection(related='company_id.doctor_ticket_assign', readonly=False, required=True)
    doctor_ticket_hours = fields.Float(related='company_id.doctor_ticket_hours', readonly=False)
    doctor_ticket_rush_hours = fields.Float(related='company_id.doctor_ticket_rush_hours', readonly=False)
    doctor_ticket_retry = fields.Integer(related='company_id.doctor_ticket_retry', readonly=False)
    doctor_ticket_escalate = fields.Integer(related='company_id.doctor_ticket_escalate', readonly=False)
    doctor_ticket_confirm = fields.Boolean(related='company_id.doctor_ticket_confirm', readonly=False)
    doctor_ticket_waiting = fields.Integer(compute='_compute_doctor_ticket_waiting')

    @api.depends('company_id')
    def _compute_doctor_ticket_waiting(self):
        """Orders already waiting on the doctor with no ticket: what switching on will raise."""
        for settings in self:
            settings.doctor_ticket_waiting = self.env['sale.order'].sudo().search_count(
                settings.company_id._doctor_orders_without_ticket())

