# -*- coding: utf-8 -*-
"""Doctor-call warning (R11) and the pending-information bucket (R14).

Both exist for the same reason: an order that cannot move forward must not sit in the
queue looking like one that can. A registration user opening the To Verify list should
see only orders they can actually act on; everything waiting on somebody else belongs in
its own list, ageing visibly.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

HOLD_REASONS = [
    ('missing_info', 'Missing Information'),
    ('awaiting_doctor_response', 'Awaiting Doctor Response'),
    ('pricing_approval', 'Pricing Approval'),
    ('patient_postponed', 'Patient Postponed'),
    ('other', 'Other'),
]

CALL_REASONS = [
    ('unclear_prescription', 'Unclear Prescription'),
    ('missing_measurement', 'Missing Measurement'),
    ('shade_confirmation', 'Shade Confirmation'),
    ('price_approval', 'Price Approval'),
    ('delivery_clarification', 'Delivery Clarification'),
    ('other', 'Other'),
]


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    # ------------------------------------------------------------------ doctor call
    call_doctor_required = fields.Boolean(
        'Doctor Call Needed', tracking=True, copy=False,
        help="Something on this prescription has to be confirmed with the doctor "
             "before the case can be verified.")
    call_doctor_reason = fields.Selection(CALL_REASONS, tracking=True, copy=False)
    call_doctor_note = fields.Text('What to ask', copy=False)
    call_doctor_resolved = fields.Boolean(readonly=True, copy=False, tracking=True)
    call_log_ids = fields.One2many('lab.doctor.call.log', 'order_id',
                                   string='Call Attempts')
    doctor_call_count = fields.Integer(compute='_compute_doctor_call_count')
    call_pending = fields.Boolean(
        compute='_compute_call_pending', store=True,
        help="A call is outstanding — used by the banner, the filters and the "
             "verification gate.")

    call_status = fields.Selection(
        [('none', 'No call needed'),
         ('pending', 'Call pending'),
         ('done', 'Call done')],
        compute='_compute_call_status', store=True, string='Doctor Call',
        help="One field a person can read at a glance, instead of two tick boxes they "
             "have to combine in their head.")

    @api.depends('call_doctor_required', 'call_doctor_resolved')
    def _compute_call_status(self):
        for order in self:
            if not order.call_doctor_required:
                order.call_status = 'none'
            elif order.call_doctor_resolved:
                order.call_status = 'done'
            else:
                order.call_status = 'pending'

    # ------------------------------------------------------------------ hold
    hold_reason = fields.Selection(HOLD_REASONS, tracking=True, copy=False)
    hold_note = fields.Text(copy=False)
    hold_date = fields.Datetime(readonly=True, copy=False)
    next_followup_date = fields.Date(tracking=True, copy=False)
    days_on_hold = fields.Integer(
        compute='_compute_days_on_hold', store=True,
        help="Recomputed daily by the follow-up cron so ageing is visible without "
             "opening the record.")
    days_waiting = fields.Integer(
        compute='_compute_days_waiting',
        help="How long this has been sitting in the verification queue.")

    def _compute_doctor_call_count(self):
        counts = dict(self.env['lab.doctor.call.log']._read_group(
            [('order_id', 'in', self.ids)], ['order_id'], ['__count']))
        for order in self:
            order.doctor_call_count = counts.get(order, 0)

    @api.depends('call_doctor_required', 'call_doctor_resolved')
    def _compute_call_pending(self):
        for order in self:
            order.call_pending = bool(
                order.call_doctor_required and not order.call_doctor_resolved)

    @api.depends('hold_date', 'verification_state')
    def _compute_days_on_hold(self):
        now = fields.Datetime.now()
        for order in self:
            if order.verification_state == 'on_hold' and order.hold_date:
                order.days_on_hold = (now - order.hold_date).days
            else:
                order.days_on_hold = 0

    def _compute_days_waiting(self):
        now = fields.Datetime.now()
        for order in self:
            stamp = order.verification_date or order.write_date or order.create_date
            order.days_waiting = (now - stamp).days if (
                order.verification_state == 'to_verify' and stamp) else 0

    # ------------------------------------------------------------------ config
    @api.model
    def _doctor_call_attempts_before_hold(self):
        try:
            return int(self.env['ir.config_parameter'].sudo().get_param(
                'lab_order_control.doctor_call_attempts_before_hold', 3))
        except (TypeError, ValueError):
            return 3

    @api.model
    def _hold_warning_days(self):
        try:
            return int(self.env['ir.config_parameter'].sudo().get_param(
                'lab_order_control.hold_warning_days', 5))
        except (TypeError, ValueError):
            return 5

    # ------------------------------------------------------------------ call actions
    def action_log_doctor_call(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Log a Doctor Call'),
            'res_model': 'lab.doctor.call.log', 'view_mode': 'form',
            'target': 'new',
            'context': {'default_order_id': self.id,
                        'default_phone': self.partner_id.phone or ''},
        }

    def action_view_doctor_calls(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Call Attempts'),
            'res_model': 'lab.doctor.call.log', 'view_mode': 'list,form',
            'domain': [('order_id', '=', self.id)],
            'context': {'default_order_id': self.id},
        }

    def action_resolve_doctor_call(self):
        """Close the query. Requires evidence — an answered call or a written outcome."""
        for order in self:
            answered = order.call_log_ids.filtered(lambda l: l.response == 'answered')
            if not answered and not order.call_doctor_note:
                raise UserError(_(
                    "Record how this was resolved: either log a call that was answered, "
                    "or write the outcome in 'What to ask' before marking it resolved."))
            order.write({'call_doctor_resolved': True})
            order.message_post(body=_("Doctor query resolved."))
        return True

    # ------------------------------------------------------------------ hold actions
    def action_put_on_hold(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Put On Hold'),
            'res_model': 'lab.hold.wizard', 'view_mode': 'form', 'target': 'new',
            'context': {'default_order_id': self.id},
        }

    def _put_on_hold(self, reason, note=False, followup=False, notify_manager=False):
        """Move to the pending-information bucket. Also the path the call cron uses."""
        self.ensure_one()
        self.write({
            'verification_state': 'on_hold',
            'hold_reason': reason,
            'hold_note': note or self.hold_note,
            'hold_date': fields.Datetime.now(),
            'next_followup_date': followup or fields.Date.add(
                fields.Date.context_today(self), days=1),
            # A new hold is a new stall: the once-per-hold warning starts over, or a
            # case held a second time never reaches a manager.
            'hold_warned': False,
        })
        self.message_post(body=_(
            "Put on hold — %(reason)s%(note)s",
            reason=dict(HOLD_REASONS).get(reason, reason),
            note=(': %s' % note) if note else ''))
        if notify_manager:
            self._notify_managers(_(
                "%(order)s is on hold: %(reason)s",
                order=self.name, reason=dict(HOLD_REASONS).get(reason, reason)))
        return True

    def action_resume_from_hold(self):
        """Back into the verification queue — and re-verified from scratch.

        Deliberately not straight to `verified`: the reason it was held was that
        something about the order was unknown, so it has to be looked at again.
        """
        for order in self:
            if order.verification_state != 'on_hold':
                raise UserError(_("%s is not on hold.", order.name))
            order.write({
                'verification_state': 'to_verify',
                'hold_reason': False, 'hold_date': False,
                'next_followup_date': False, 'hold_warned': False,
            })
            order.message_post(body=_("Resumed — back in the verification queue."))
        return True

    def _notify_managers(self, body):
        group = self.env.ref('lab_fieldwork.group_fieldwork_manager',
                             raise_if_not_found=False)
        if not group:
            return
        partners = group.sudo().all_user_ids.filtered('active').partner_id
        if partners:
            self.message_notify(partner_ids=partners.ids, body=body,
                                subject=_("Lab: order needs attention"))

    # ------------------------------------------------------------------ the gate
    def action_verify(self):
        """Extend the existing gate: an open doctor query blocks verification.

        Verifying an order whose prescription is still in question is exactly the
        rubber-stamp the maker-checker control exists to prevent.
        """
        for order in self:
            if order.call_pending:
                raise UserError(_(
                    "%(order)s has an unresolved doctor query (%(reason)s). Resolve the "
                    "call before verifying — that question is the reason this order is "
                    "not ready.",
                    order=order.name,
                    reason=dict(CALL_REASONS).get(order.call_doctor_reason, _('unspecified'))))
            if order.verification_state == 'on_hold':
                raise UserError(_(
                    "%s is on hold. Resume it first, so the hold is closed on purpose "
                    "rather than bypassed.", order.name))
        return super().action_verify()

    # ------------------------------------------------------------------ cron
    @api.model
    def _cron_pending_info_followup(self):
        """Daily: refresh ageing, raise today's follow-ups, warn once when it drags."""
        today = fields.Date.context_today(self)
        held = self.search([('verification_state', '=', 'on_hold')])
        held._compute_days_on_hold()          # stored — keeps the list honest
        due = held.filtered(
            lambda o: o.next_followup_date and o.next_followup_date <= today)
        for order in due:
            order.activity_schedule(
                'mail.mail_activity_data_todo', date_deadline=today,
                summary=_("Follow up: %s", order.name),
                note=order.hold_note or '',
                user_id=(order.verified_by_id or order.user_id or self.env.user).id)

        threshold = self._hold_warning_days()
        stale = held.filtered(
            lambda o: o.days_on_hold >= threshold and not o.hold_warned)
        for order in stale:
            order._notify_managers(_(
                "%(order)s has been on hold %(days)s days (%(reason)s).",
                order=order.name, days=order.days_on_hold,
                reason=dict(HOLD_REASONS).get(order.hold_reason, '')))
        stale.write({'hold_warned': True})
        return len(due) + len(stale)

    # One warning per hold, not one per day — a daily manager ping about the same
    # stuck order is how people learn to ignore the notification entirely.
    hold_warned = fields.Boolean(readonly=True, copy=False)
