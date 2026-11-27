# Part of ebshel_hr_auto_attendance. See LICENSE file for full copyright and licensing details.
{
    "name": "Automatic Attendance from Working Hours",
    "version": "20.0.1.1.0",
    "category": "Human Resources/Attendances",
    "summary": "Attendance entries made from each employee's working hours, "
    "skipping public holidays, time off and days they recorded themselves.",
    "description": """
Automatic Attendance from Working Hours
=======================================
For staff who do not check in - an office, a workshop on fixed hours - tick
Automatic Attendance on the employee. Once a working day is over, an entry
is made for it from their working schedule (09:00-18:00, say), in their
own time zone. Days that are not working days, public holidays, approved
time off and pending time-off requests get no entry, and a day on which the
employee recorded any attendance of their own is left alone. An entry that
somebody deletes is not made again.
    """,
    "author": "Ebshel Technologies",
    "maintainer": "Ebshel Technologies",
    "website": "https://ebshel.com",
    "support": "ebinpg777@gmail.com",
    "license": "OPL-1",
    "depends": ["hr_attendance"],
    "data": [
        "data/ir_cron_data.xml",
        "views/hr_employee_views.xml",
        "views/hr_attendance_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "post_init_hook": "_post_init_hook",
    "installable": True,
    "application": False,
}
