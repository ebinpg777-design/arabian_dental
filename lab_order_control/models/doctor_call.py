# -*- coding: utf-8 -*-
"""Every attempt to reach the doctor about an order (R11).

A prescription that needs a clarification — a shade, a measurement, a price the doctor
has to agree — stalls the whole case. Today that stall lives in somebody's head: they
rang, nobody answered, and the order sits looking exactly like one that is fine.

So each attempt is a record. It is not bureaucracy: the count of unanswered attempts is
what moves the order to On Hold automatically, and the log is what stops two people
ringing the same doctor an hour apart about the same case.
"""
from odoo import _, api, fields, models

RESPONSES = [
    ('answered', 'Answered'),
    ('no_answer', 'No Answer'),
    ('busy', 'Busy'),
    ('switched_off', 'Switched Off'),
    ('callback_requested', 'Callback Requested'),
    ('wrong_number', 'Wrong Number'),
]


class LabDoctorCallLog(models.Model):
    _name = 'lab.doctor.call.log'
    _description = 'Doctor Call Log'
    _order = 'call_datetime desc, id desc'
    _rec_name = 'display_name'

    order_id = fields.Many2one('sale.order', string='Order', required=True,
                               ondelete='cascade', index=True)
    partner_id = fields.Many2one(related='order_id.partner_id', store=True,
                                 string='Doctor / Clinic')
    # Deliberately NO m2o to `lab.case`: that model lives in `lab_fieldwork`, which
    # this module does not depend on — the same decoupling that keeps the maker-role
    # list a setting rather than a hard group reference. The slip is reachable the other
    # way round (`lab.case.sale_order_id`), so nothing is lost.

    call_datetime = fields.Datetime(default=fields.Datetime.now, required=True)
    called_by_id = fields.Many2one('res.users', default=lambda s: s.env.user,
                                   required=True, string='Called By')
    phone = fields.Char()
    response = fields.Selection(RESPONSES, required=True)
    remarks = fields.Text()
    next_call_datetime = fields.Datetime(
        string='Try Again At',
        help="Schedules a reminder for whoever is chasing this.")

    company_id = fields.Many2one(related='order_id.company_id', store=True)

    @api.depends('partner_id', 'response', 'call_datetime')
    def _compute_display_name(self):
        labels = dict(RESPONSES)
        for log in self:
            log.display_name = '%s — %s' % (
                log.partner_id.display_name or _('Call'),
                labels.get(log.response, ''))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # Default the number from the order rather than making the caller find it.
            if not vals.get('phone') and vals.get('order_id'):
                order = self.env['sale.order'].browse(vals['order_id'])
                vals['phone'] = order.partner_id.phone or ''
        logs = super().create(vals_list)
        for log in logs:
            log._after_logged()
        return logs

    def _after_logged(self):
        """React to the attempt: schedule the next one, or give up and hold."""
        self.ensure_one()
        order = self.order_id
        order.message_post(body=_(
            "Doctor call — %(response)s%(remark)s",
            response=dict(RESPONSES).get(self.response),
            remark=(': %s' % self.remarks) if self.remarks else ''))

        if self.next_call_datetime:
            order.activity_schedule(
                'mail.mail_activity_data_call',
                date_deadline=fields.Date.to_date(self.next_call_datetime),
                summary=_("Call %s again", order.partner_id.display_name),
                user_id=self.called_by_id.id)

        if self.response == 'answered':
            return

        # Enough unanswered attempts and the order stops pretending to be in progress.
        limit = order._doctor_call_attempts_before_hold()
        if not limit:
            return
        unanswered = self.search_count([
            ('order_id', '=', order.id), ('response', '!=', 'answered')])
        if unanswered >= limit and order.verification_state != 'on_hold':
            order.sudo()._put_on_hold(
                reason='awaiting_doctor_response',
                note=_("%(n)s attempts to reach the doctor went unanswered.",
                       n=unanswered),
                notify_manager=True)
