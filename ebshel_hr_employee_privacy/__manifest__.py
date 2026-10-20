# Part of ebshel_hr_employee_privacy. See LICENSE file for full copyright and licensing details.
{
    "name": "Employee Privacy: See Only Yourself",
    "version": "20.0.1.0.0",
    "category": "Human Resources/Employees",
    "summary": "A user without Employees access sees only their own employee record: "
    "the Employees app, the directory and every employee picker show just them. "
    "Employees officers and administrators keep seeing everyone.",
    "description": """
Employee Privacy: See Only Yourself
===================================
Odoo lets every internal user browse the whole employee directory. With this
module, a user who has no Employees access (no "Officer" or "Administrator"
role) sees one employee: their own. HR users and system administrators are
not restricted.
    """,
    "author": "Ebshel Technologies",
    "maintainer": "Ebshel Technologies",
    "website": "https://ebshel.com",
    "support": "ebinpg777@gmail.com",
    "license": "OPL-1",
    "price": 9.0,
    "currency": "USD",
    "depends": ["hr"],
    "data": ["security/ir.access.csv"],
    "images": ["static/description/banner.png"],
    "installable": True,
    "application": False,
}
