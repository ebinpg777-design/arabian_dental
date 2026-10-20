# Employee Privacy: See Only Yourself (`ebshel_hr_employee_privacy`)

Odoo 20 module: a user without Employees access sees only their own employee
record. Depends on `hr` only.

Odoo lets every internal user browse the whole employee directory
(`hr.employee.public`, what the Employees app shows to people without an HR
role). Some companies do not want that: a technician or a clerk should find
themselves - for their own attendance, time off or profile - and nobody else.

## What it does

* Two `ir.access` restrictions (no group, so they apply to everyone and are
  AND-ed with the permissions): on `hr.employee.public` and on `hr.employee`,
  the domain is `[('user_id', '=', user.id)]` for a user who has neither
  `hr.group_hr_user` (Employees: Officer, which Administrator implies) nor
  `base.group_system`, and empty (no restriction) for those who do.
* So: the Employees app, the directory and every employee picker show a
  plain user just themselves; a user with no employee record sees nobody; HR
  users and system administrators are not restricted.

## Tests

`tests/test_privacy.py` (4): plain user sees only themselves, officer sees
everyone, system administrator not restricted, user without an employee sees
nobody.
