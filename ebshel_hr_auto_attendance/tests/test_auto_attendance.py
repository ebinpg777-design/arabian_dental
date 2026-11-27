# Part of ebshel_hr_auto_attendance. See LICENSE file for full copyright and licensing details.
from datetime import date, datetime, timedelta, timezone

from odoo.tests import TransactionCase, new_test_user, tagged


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


@tagged('post_install', '-at_install')
class TestAutoAttendance(TransactionCase):
    """Monday to Saturday, 09:00-18:00 India time (03:30-12:30 UTC).

    The week of 16 Nov 2026: Monday 16 ... Saturday 21, Sunday 22.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.calendar = cls.env['resource.calendar'].create({
            'name': 'T Mon-Sat 9-18', 'hours_per_day': 9, 'company_id': cls.env.company.id,
            'attendance_ids': [(5, 0, 0)] + [
                (0, 0, {'dayofweek': str(d), 'hour_from': 9, 'hour_to': 18}) for d in range(6)],
        })
        cls.employee = cls.env['hr.employee'].create({
            'name': 'Auto Office', 'resource_calendar_id': cls.calendar.id, 'tz': 'Asia/Kolkata',
        })
        cls.employee.auto_attendance = True
        cls.Att = cls.env['hr.attendance']

    def run_until(self, now, done_until=None):
        if done_until:
            self.employee.auto_attendance_done_until = done_until
        return self.employee._make_auto_attendance(now)

    def entries(self, day):
        start = datetime.combine(day, datetime.min.time()) - timedelta(hours=5, minutes=30)
        return self.Att.search([('employee_id', '=', self.employee.id),
                                ('check_in', '>=', start), ('check_in', '<', start + timedelta(days=1))])

    def test_saturday_works_sunday_does_not(self):
        made = self.run_until(utc(2026, 11, 23, 6, 0), done_until=date(2026, 11, 20))
        self.assertEqual(made, 1)
        sat = self.entries(date(2026, 11, 21))
        self.assertEqual((sat.check_in, sat.check_out),
                         (datetime(2026, 11, 21, 3, 30), datetime(2026, 11, 21, 12, 30)))
        self.assertTrue(sat.auto_created)
        self.assertEqual(sat.in_mode, 'technical')
        self.assertFalse(self.entries(date(2026, 11, 22)), "Sunday is not a working day")
        self.assertFalse(self.entries(date(2026, 11, 23)), "Monday is not over at 11:30")
        self.assertEqual(self.employee.auto_attendance_done_until, date(2026, 11, 22))

    def test_a_day_is_made_once_it_is_over(self):
        self.run_until(utc(2026, 11, 16, 12, 0), done_until=date(2026, 11, 15))
        self.assertFalse(self.entries(date(2026, 11, 16)), "17:30 India time: not yet")
        self.run_until(utc(2026, 11, 16, 12, 31))
        self.assertEqual(len(self.entries(date(2026, 11, 16))), 1)

    def test_public_holiday_gets_no_entry(self):
        self.env['resource.calendar.leaves'].create({
            'name': 'T Holiday', 'company_id': self.env.company.id,
            'date_from': datetime(2026, 11, 16, 18, 30), 'date_to': datetime(2026, 11, 17, 18, 29),
        })
        self.run_until(utc(2026, 11, 18, 13, 0), done_until=date(2026, 11, 15))
        self.assertTrue(self.entries(date(2026, 11, 16)))
        self.assertFalse(self.entries(date(2026, 11, 17)), "public holiday")
        self.assertTrue(self.entries(date(2026, 11, 18)))

    def test_time_off_gets_no_entry(self):
        # what an approved time off leaves on the employee's resource
        self.env['resource.calendar.leaves'].create({
            'name': 'T Leave', 'resource_id': self.employee.resource_id.id,
            'calendar_id': self.calendar.id, 'company_id': self.env.company.id,
            'date_from': datetime(2026, 11, 17, 3, 30), 'date_to': datetime(2026, 11, 17, 12, 30),
        })
        self.run_until(utc(2026, 11, 17, 13, 0), done_until=date(2026, 11, 16))
        self.assertFalse(self.entries(date(2026, 11, 17)))

    def test_time_off_waiting_for_approval_gets_no_entry(self):
        if 'hr.leave' not in self.env:
            self.skipTest("Time Off is not installed")
        kind = self.env['hr.work.entry.type'].create({
            'name': 'T Casual', 'code': 'T Casual', 'requires_allocation': False,
            'request_unit': 'day', 'unit_of_measure': 'day',
        })
        day = date.today() + timedelta(days=14)
        while day.weekday() > 4:
            day += timedelta(days=1)
        leave = self.env['hr.leave'].create({
            'name': 'T asked', 'employee_id': self.employee.id, 'work_entry_type_id': kind.id,
            'request_date_from': day, 'request_date_to': day,
        })
        leave.sudo().write({'state': 'confirm'})
        self.run_until(utc(day.year, day.month, day.day, 13, 0), done_until=day - timedelta(days=1))
        self.assertFalse(self.entries(day))
        self.assertEqual(self.employee.auto_attendance_done_until, day)

    def test_a_day_recorded_by_hand_is_left_alone(self):
        self.Att.create({'employee_id': self.employee.id,
                         'check_in': datetime(2026, 11, 18, 4, 0), 'check_out': datetime(2026, 11, 18, 8, 0)})
        self.run_until(utc(2026, 11, 18, 13, 0), done_until=date(2026, 11, 17))
        own = self.entries(date(2026, 11, 18))
        self.assertEqual(len(own), 1)
        self.assertFalse(own.auto_created)

    def test_a_deleted_entry_is_not_made_again(self):
        self.run_until(utc(2026, 11, 18, 13, 0), done_until=date(2026, 11, 17))
        self.entries(date(2026, 11, 18)).unlink()
        self.run_until(utc(2026, 11, 18, 15, 0))
        self.assertFalse(self.entries(date(2026, 11, 18)))

    def test_members_of_excluded_groups_get_nothing(self):
        group = self.env['res.groups'].create({'name': 'T Field Force'})
        user = new_test_user(self.env, login='auto_field_t', groups='base.group_user')
        user.group_ids = [(4, group.id)]
        self.employee.user_id = user
        self.env.company.auto_attendance_excluded_group_ids = [(6, 0, group.ids)]
        self.assertTrue(self.employee._auto_attendance_excluded())
        self.employee.auto_attendance_done_until = date(2026, 11, 17)
        self.employee._cron_auto_attendance()
        self.assertFalse(self.entries(date(2026, 11, 18)))

    def test_ticking_starts_today(self):
        self.employee.auto_attendance_done_until = date(2026, 1, 1)
        self.employee.auto_attendance = False
        self.employee.auto_attendance = True
        self.assertFalse(self.employee.auto_attendance_done_until, "no months filled in")

    def test_new_employees_follow_the_company_setting(self):
        self.env.company.auto_attendance_default = True
        self.assertTrue(self.env['hr.employee'].create({'name': 'T New'}).auto_attendance)
        self.env.company.auto_attendance_default = False
        self.assertFalse(self.env['hr.employee'].create({'name': 'T New 2'}).auto_attendance)

    def test_a_public_holiday_loaded_without_length_is_its_whole_day(self):
        """Odoo 20 loads public holidays as date = date at midnight UTC (no length)."""
        self.env.company.tz = 'Asia/Kolkata'
        hol = self.env['resource.calendar.leaves'].create({
            'name': 'T Loaded Holiday', 'company_id': self.env.company.id,
            'date_from': datetime(2026, 11, 19), 'date_to': datetime(2026, 11, 19),
        })
        self.assertEqual((hol.date_from, hol.date_to),
                         (datetime(2026, 11, 18, 18, 30), datetime(2026, 11, 19, 18, 29, 59)))
        self.run_until(utc(2026, 11, 19, 13, 0), done_until=date(2026, 11, 18))
        self.assertFalse(self.entries(date(2026, 11, 19)), "holiday")
