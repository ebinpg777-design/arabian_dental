# Automatic Attendance from Working Hours (`ebshel_hr_auto_attendance`)

Odoo 20 module, depends on `hr_attendance` (works with Time Off when installed).

For staff who do not check in - an office, a workshop on fixed hours - tick
**Automatic Attendance** on the employee (Payroll tab, under Working Hours).
Once a working day is over where the employee works, an attendance entry is
made from their working schedule, e.g. 09:00-18:00 in their own time zone.

## Which days get an entry

* Only the days and hours of the employee's working schedule (a Monday to
  Saturday schedule gives Saturdays; Sunday gets nothing).
* Not public holidays (global time off on the schedule or the company).
* Not approved time off (taken out of the working hours, so a half day off
  leaves the other half), nor time off still waiting for approval.
* Not a day on which the employee recorded any attendance of their own.
* Each day is handled once: an entry somebody deletes is not made again.
  Ticking the box starts from today; the past is never filled in.

Entries are marked **Created Automatically** (filter and list column), with
in/out mode "Technical".

## Public holidays loaded by Odoo

Odoo 20 loads its public holidays (Time Off > Public Holidays > Load, and the
yearly job adding next year's) as the date at midnight UTC with no length, so
they take no working hour out - neither here nor in Odoo's time-off counts.
This module makes every such company-wide holiday its whole day in the
company's time zone, on install, on upgrade and whenever one is created.

## Settings (Attendances)

* **New Employees** - tick Automatic Attendance on new employees.
* **Never for Members of** - groups whose members record their own
  attendance (a field force): no automatic entries, even when ticked.

## Technical

Hourly cron `cron_auto_attendance` (`hr.employee._cron_auto_attendance`),
catching up at most 31 days after downtime. Tests:
`--test-tags /ebshel_hr_auto_attendance` (11).
