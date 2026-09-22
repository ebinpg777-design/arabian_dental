# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ResUsers(models.Model):
    """Attendance for the field force, on Odoo's own `hr.attendance`.

    Deliberately not a parallel "field attendance" model. Payroll, leave, overtime and
    every HR report already read `hr.attendance`; a second record of when somebody
    started work would be a second answer to the same question, and the two would
    disagree the first time anyone corrected one of them.
    """
    _inherit = 'res.users'

    # Odoo 19 removed `mobile` from res.users (and res.partner - one number per
    # contact, held in `phone`). The field executives' phone app still asks for it:
    # every login from the app fires a res.users.search_read with `mobile` in the
    # field list, which raises "Invalid field 'mobile' on 'res.users'" - an error on
    # every phone login, from a client we cannot patch. Answer with the number we do
    # keep rather than refusing the question. Compute-only: the app reads the field,
    # it never filters on it.
    mobile = fields.Char(
        compute='_compute_mobile',
        string='Mobile',
        help="Compatibility for phone clients built against Odoo <= 18: "
             "reads the partner's phone, which is where Odoo 19 keeps "
             "the one number a contact has.")

    @api.depends('partner_id.phone')
    def _compute_mobile(self):
        for user in self:
            user.mobile = user.partner_id.phone or ''

    def _fw_employee(self):
        """The employee behind this user, in this company."""
        self.ensure_one()
        return self.employee_id or self.env['hr.employee'].sudo().search(
            [('user_id', '=', self.id), ('company_id', 'in', self.env.companies.ids)],
            limit=1)

    @api.model
    def _fw_attendance_required(self):
        return self.env['ir.config_parameter'].sudo().get_param(
            'lab_fieldwork.require_attendance', 'True') in ('True', 'true', '1')

    def _fw_check_on_duty(self, what):
        """Refuse field work before the day has been started.

        The message says what is missing and how to fix it. "Access denied" on a phone
        at a clinic door, with a doctor waiting, is how an executive learns to stop
        using the app rather than how they learn to mark attendance.
        """
        self.ensure_one()
        # NOT `self.env.su` here: `env.user` hands back the user record in SUPERUSER
        # mode, so a bypass tested on this recordset is always true and the gate never
        # fires. The caller checks its own environment, where the flag is real.
        if not self._fw_attendance_required():
            return True
        employee = self._fw_employee()
        if not employee:
            raise UserError(_(
                "You have no employee record, so attendance cannot be marked and "
                "%(what)s cannot be started. Ask HR to link your user to an employee.",
                what=what))
        if employee.attendance_state != 'checked_in':
            raise UserError(_(
                "Mark your attendance before %(what)s. Open My Day and press "
                "“Start my day”.", what=what))
        return True


class LabMyDay(models.AbstractModel):
    """The attendance half of the My Day screen."""
    _inherit = 'lab.my.day'

    @api.model
    def get_day(self, day=None):
        data = super().get_day(day)
        data['attendance'] = self._attendance_payload()
        return data

    @api.model
    def _attendance_payload(self):
        user = self.env.user
        employee = user._fw_employee()
        if not employee:
            return {
                'state': 'no_employee',
                'required': user._fw_attendance_required(),
                'since': '', 'hours': 0.0, 'checked_out_at': '',
            }
        # sudo: an executive reads their OWN attendance without being given the HR
        # application, which would show them everybody's.
        employee = employee.sudo()
        return {
            'state': employee.attendance_state,          # checked_in / checked_out
            'required': user._fw_attendance_required(),
            # Local clock time, no date. The card only ever shows TODAY, so repeating
            # the date says nothing, and a raw UTC stamp is an hour or five off whatever
            # the phone's own clock says — which reads as the app being wrong.
            'since': self._local_time(employee.last_check_in)
            if employee.attendance_state == 'checked_in' else '',
            'checked_out_at': self._local_time(employee.last_check_out)
            if employee.attendance_state == 'checked_out' else '',
            'hours': round(employee.hours_today or 0.0, 2),
            'employee': employee.name,
        }

    @api.model
    def _local_time(self, value):
        if not value:
            return ''
        return fields.Datetime.context_timestamp(self, value).strftime('%H:%M')

    @api.model
    def attendance_toggle(self, latitude=False, longitude=False):
        """Start or end the working day.

        One call for both directions, mirroring `hr.employee._attendance_action_change`
        and the card's single button: the state decides what happens, so there is never
        a wrong button to press.
        """
        employee = self.env.user._fw_employee()
        if not employee:
            raise UserError(_(
                "You have no employee record, so attendance cannot be marked. Ask HR to "
                "link your user to an employee."))
        geo = None
        if latitude or longitude:
            # The same fix the visit check-in uses, so a day started in the field is
            # located exactly as a clinic call is. hr.attendance prefixes these with
            # in_/out_ depending on the direction, which is why the keys are bare.
            geo = {'latitude': latitude or 0.0, 'longitude': longitude or 0.0,
                   'mode': 'systray'}
        # sudo: marking your own attendance is not the same privilege as the HR app.
        employee.sudo()._attendance_action_change(geo_information=geo)
        employee.invalidate_recordset()
        return self._attendance_payload()


class LabVisit(models.Model):
    _inherit = 'lab.visit'

    def do_check_in(self, latitude=False, longitude=False):
        # Before the geo-stamp and before the state moves: a visit that opened and then
        # failed would leave the executive at a clinic with a half-started record.
        if not self.env.su:
            self.env.user._fw_check_on_duty(_('visiting a clinic'))
        return super().do_check_in(latitude=latitude, longitude=longitude)


class LabTrip(models.Model):
    _inherit = 'lab.trip'

    def action_start(self):
        if not self.env.su:
            self.env.user._fw_check_on_duty(_('starting your travel'))
        return super().action_start()
