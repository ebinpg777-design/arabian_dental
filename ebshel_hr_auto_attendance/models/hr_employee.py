# Part of ebshel_hr_auto_attendance. See LICENSE file for full copyright and licensing details.
import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# How far back a run catches up after the server was down, at most.
MAX_DAYS_BACK = 31


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    auto_attendance = fields.Boolean(
        string="Automatic Attendance", tracking=True, groups="hr.group_hr_user",
        default=lambda self: self.env.company.auto_attendance_default,
        help="Once each working day is over, an attendance entry is made from this "
             "employee's working hours. Public holidays, time off (approved or asked "
             "for) and days on which they recorded attendance themselves get none.")
    auto_attendance_done_until = fields.Date(
        string="Automatic Attendance Made Until", readonly=True, copy=False,
        groups="hr.group_hr_user",
        help="The last day already handled. Each day is handled once, so an entry "
             "somebody deletes is not made again.")

    def write(self, vals):
        if vals.get('auto_attendance'):
            # Ticked now: start from today. Days already gone are not invented, and
            # a box ticked again after months does not fill the months in.
            fresh = self.filtered(lambda e: not e.auto_attendance)
            res = super().write(vals)
            fresh.sudo().auto_attendance_done_until = False
            return res
        return super().write(vals)

    # ------------------------------------------------------------------ the cron
    @api.model
    def _cron_auto_attendance(self):
        now = fields.Datetime.now().replace(tzinfo=timezone.utc)
        made = 0
        for employee in self.sudo().search([('auto_attendance', '=', True)]):
            if employee._auto_attendance_excluded():
                continue
            try:
                with self.env.cr.savepoint():
                    made += employee._make_auto_attendance(now)
            except Exception:  # one employee's bad data must not stop everybody's
                _logger.exception("Automatic attendance failed for employee %s", employee.id)
        if made:
            _logger.info("Automatic attendance: %s entries made", made)
        return made

    def _auto_attendance_excluded(self):
        self.ensure_one()
        groups = self.company_id.auto_attendance_excluded_group_ids
        return bool(groups and self.user_id and (self.user_id.all_group_ids & groups))

    def _make_auto_attendance(self, now):
        """Make the entries of every finished working day since the last one handled.

        `now` is an aware UTC datetime. Returns the number of entries made.
        """
        self.ensure_one()
        calendar = self.resource_calendar_id
        resource = self.resource_id
        if not calendar or not resource:
            return 0
        tz = ZoneInfo(resource.tz or self.tz or 'UTC')
        today = now.astimezone(tz).date()
        day = self.auto_attendance_done_until + timedelta(days=1) \
            if self.auto_attendance_done_until else today
        day = max(day, today - timedelta(days=MAX_DAYS_BACK))
        made = 0
        while day <= today:
            start = datetime.combine(day, time.min, tzinfo=tz)
            end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
            work = calendar._work_intervals_batch(
                start, end, resources_per_tz=resource._get_resources_per_tz())[resource.id]
            intervals = [(s, e) for s, e, _records in work]
            if day == today and (not intervals or max(e for _s, e in intervals) > now):
                break  # today is not over yet: the next run comes back for it
            if intervals and not self._auto_attendance_skip_day(start, end):
                self.env['hr.attendance'].create([{
                    'employee_id': self.id,
                    'check_in': s.astimezone(timezone.utc).replace(tzinfo=None),
                    'check_out': e.astimezone(timezone.utc).replace(tzinfo=None),
                    'in_mode': 'technical',
                    'out_mode': 'technical',
                    'auto_created': True,
                } for s, e in intervals])
                made += len(intervals)
            self.auto_attendance_done_until = day
            day += timedelta(days=1)
        return made

    def _auto_attendance_skip_day(self, start, end):
        """A day already recorded, or with time off asked for: no entry is made."""
        self.ensure_one()
        start_utc = start.astimezone(timezone.utc).replace(tzinfo=None)
        end_utc = end.astimezone(timezone.utc).replace(tzinfo=None)
        if self.env['hr.attendance'].sudo().search_count([
            ('employee_id', '=', self.id), ('check_in', '<', end_utc),
            '|', ('check_out', '=', False), ('check_out', '>', start_utc),
        ], limit=1):
            return True
        # Approved time off is already taken out of the working hours; a request
        # still waiting for approval is not, and must not be answered with a full
        # day's attendance the moment it is approved.
        if 'hr.leave' in self.env and self.env['hr.leave'].sudo().search_count([
            ('employee_id', '=', self.id), ('state', 'in', ('confirm', 'validate1')),
            ('date_from', '<', end_utc), ('date_to', '>', start_utc),
        ], limit=1):
            return True
        return False
