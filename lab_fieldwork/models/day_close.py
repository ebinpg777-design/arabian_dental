# -*- coding: utf-8 -*-
import logging

from odoo.exceptions import UserError, ValidationError

from odoo import _, api, fields, models

from .local_day import local_now

_logger = logging.getLogger(__name__)

DEFAULT_HOUR = 21


class LabDayCloser(models.AbstractModel):
    """Close what an executive left open when the day ends.

    People forget. Somebody finishes a round at six, drives home, and the app still says
    they are on duty at midnight — the attendance runs for fourteen hours, the Control
    Tower shows them at a clinic, and the next morning's board is wrong before anyone
    has done anything.

    The rule this follows is what makes it safe: **close only what can be closed without
    inventing data.**

    * Attendance — closed. The check-out TIME is the only fact involved, and it is
      stamped `auto_check_out` so payroll can see it was not a person pressing a button.
    * A visit where the outcome IS recorded — closed. Nothing is invented; they told us
      what happened and forgot the last tap.
    * A visit with no outcome, or a trip with no closing odometer — LEFT OPEN and
      flagged. Closing them would mean writing an outcome nobody gave or a distance
      nobody drove, and a fabricated number in a travel claim is worse than an open one:
      the open one gets chased, the invented one gets paid.

    Odoo's own `hr.attendance._cron_auto_check_out` is deliberately not relied on here.
    It fires only when somebody EXCEEDS their scheduled hours plus a tolerance, and it
    skips employees on flexible hours — which is most of a field force. It answers a
    payroll question; this answers "they forgot".
    """
    _name = 'lab.day.closer'
    _description = 'Field Work — End of Day'

    # ------------------------------------------------------------------ policy
    @api.model
    def _enabled(self):
        return self.env['ir.config_parameter'].sudo().get_param(
            'lab_fieldwork.auto_close_day', 'True') in ('True', 'true', '1')

    @api.model
    def _cutoff_hour(self):
        try:
            return min(23, max(0, int(float(self.env['ir.config_parameter'].sudo()
                                            .get_param('lab_fieldwork.auto_close_hour',
                                                       DEFAULT_HOUR)))))
        except (TypeError, ValueError):
            return DEFAULT_HOUR

    @api.model
    def _day_is_over_for(self, user):
        """Has the cutoff passed where THIS person is?

        Evaluated per user rather than by scheduling the cron for one hour: a lab with
        staff in two timezones would otherwise close one group's day in the middle of
        their afternoon.
        """
        # Never UTC: most of the field force has no tz, and a UTC clock said
        # the day was over at half past two in the morning here.
        return local_now(self.env, user).hour >= self._cutoff_hour()

    # ------------------------------------------------------------------ the cron
    @api.model
    def _cron_close_day(self):
        if not self._enabled():
            return
        closed = self._close_visits()
        closed += self._close_trips()
        closed += self._close_attendance()
        if closed:
            _logger.info("Field work: closed %s item(s) left open at end of day", closed)
        return closed

    @api.model
    def _close_visits(self):
        """Visits where the executive said what happened and forgot to close."""
        visits = self.env['lab.visit'].sudo().search([('state', '=', 'open')])
        done = 0
        for visit in visits:
            if not self._day_is_over_for(visit.user_id):
                continue
            if not visit.outcome:
                # Nothing to close it WITH. Say so on the record so the morning board
                # shows a question rather than a silence.
                if not visit.auto_close_failed:
                    visit.auto_close_failed = True
                    visit.message_post(body=_(
                        "Still open at the end of the day with nothing recorded about "
                        "what happened. It has been left open on purpose — an outcome "
                        "nobody gave is not one this system will invent."))
                continue
            # do_check_out(), NOT a direct write. It is the only path that calls
            # _register_cases(), which is the only automated path that turns a
            # SUBMITTED case slip into a sale order. Writing state='done' here left
            # those slips submitted for ever - and the visit form hides the
            # "Submitted N" banner once the visit is done, so the stuck work was
            # concealed rather than flagged, on a record only a manager can reopen.
            # It also skipped the check-out gates (cash with no pay mode, GPS far
            # with no reason) and lab_delivery's override, so the job closed
            # visits the button itself would have refused. (client, 2026-08-26)
            #
            # A savepoint per visit: one slip raising mid-loop must not leave an
            # earlier slip's order committed against a visit that stays open. The
            # button path gets this free from the request rollback; a cron does not.
            try:
                with self.env.cr.savepoint():
                    visit.with_context(fw_auto_close=True).sudo().do_check_out()
            except (UserError, ValidationError) as exc:
                if not visit.auto_close_failed:
                    visit.auto_close_failed = True
                    visit.message_post(body=_(
                        "Could not be closed automatically at the end of the day: %s",
                        exc.args[0] if exc.args else exc))
                continue
            visit.message_post(body=_(
                "Closed automatically at the end of the day. The outcome was already "
                "recorded; only the check-out was missing."))
            done += 1
        return done

    @api.model
    def _close_trips(self):
        """Trips where the closing reading is in and only the button was missed."""
        trips = self.env['lab.trip'].sudo().search([('state', '=', 'open')])
        done = 0
        for trip in trips:
            if not self._day_is_over_for(trip.user_id):
                continue
            if not trip.odo_end:
                if not trip.auto_close_failed:
                    trip.auto_close_failed = True
                    trip.message_post(body=_(
                        "Still on the road at the end of the day with no closing "
                        "odometer reading. Left open deliberately: a distance nobody "
                        "drove would go straight into a travel claim."))
                continue
            trip.write({'state': 'closed', 'auto_closed': True})
            trip.message_post(body=_(
                "Submitted automatically at the end of the day — the closing reading "
                "was already entered."))
            done += 1
        return done

    @api.model
    def _close_attendance(self):
        """Sign out anyone from the field force still on duty."""
        group = self.env.ref('lab_fieldwork.group_fieldwork_executive',
                             raise_if_not_found=False)
        if not group:
            return 0
        # `mapped()` takes FIELD names; `_fw_employee` is a method, so it is called
        # per user rather than mapped over the recordset.
        employees = self.env['hr.employee']
        for user in group.sudo().all_user_ids.filtered('active'):
            employees |= user._fw_employee()
        done = 0
        for employee in employees:
            if employee.attendance_state != 'checked_in':
                continue
            if not self._day_is_over_for(employee.user_id):
                continue
            attendance = self.env['hr.attendance'].sudo().search(
                [('employee_id', '=', employee.id), ('check_out', '=', False)], limit=1)
            if not attendance:
                continue
            attendance.write({
                'check_out': fields.Datetime.now(),
                # Odoo's own marker, so every HR report already knows how to read it.
                'out_mode': 'auto_check_out',
            })
            attendance.message_post(body=_(
                "Checked out automatically: the working day ended and no check-out was "
                "recorded."))
            done += 1
        return done
