# Part of ebshel_hr_employee_privacy. See LICENSE file for full copyright and licensing details.
{
    "name": "Employee Privacy: See Only Yourself",
    "version": "20.0.1.1.0",
    "category": "Human Resources/Employees",
    "summary": "A user without any employee-reading role finds only themselves in the "
    "Employees app, the directory and every employee picker; whoever may see "
    "employees - HR, administrators, roles other apps grant - still sees everyone.",
    "description": """
Employee Privacy: See Only Yourself
===================================
Odoo lets every internal user browse the whole employee directory. With this
module, a user who has no employee-reading role at all (no Employees
"Officer" or "Administrator", nothing granted by another app) finds one
employee in lists and pickers: their own. A colleague named on a record they
may see is still shown. HR users and administrators are not restricted.
    """,
    "author": "Ebshel Technologies",
    "maintainer": "Ebshel Technologies",
    "website": "https://ebshel.com",
    "support": "ebinpg777@gmail.com",
    "license": "OPL-1",
    "price": 9.0,
    "currency": "USD",
    "depends": ["hr"],
    "images": ["static/description/banner.png"],
    "installable": True,
    "application": False,
}
