# Employee Privacy: See Only Yourself (`ebshel_hr_employee_privacy`)

Odoo 20 module: a user without any employee-reading role finds only their own
employee record in the Employees app, the directory and every employee picker.
Depends on `hr` only.

Odoo lets every internal user browse the whole employee directory
(`hr.employee.public`, what the Employees app shows to people without an HR
role). Some companies do not want that: a technician or a clerk should find
themselves - for their own attendance, time off or profile - and nobody else.

## What it does

`hr.employee.public._search` adds `user_id = the current user` to every search
made by a user who cannot read `hr.employee` at all (no Employees officer or
administrator role, no grant from another app) and is not the superuser. Lists,
kanbans, name searches and pickers therefore show that user themselves; a user
with no employee record linked to their login sees nobody.

## What it deliberately does not do

* It does not add an `ir.access` restriction. A restriction is AND-ed with every
  permission, so it would also narrow users another module lets see employees
  (attendance officers, a shop-floor or field-work role), and it would make
  reading a colleague's record raise - a work order naming another technician,
  a desk listing other executives, an avatar card would break for them.
* It does not block reads. A record that names a colleague still displays; only
  what comes back from a search is narrowed. Code running as superuser (sudo)
  sees everything, as it should.

## Tests

`tests/test_privacy.py` (5): plain user lists only themselves, a colleague they
are shown still reads, officer sees everyone, system administrator not
restricted, user without an employee sees nobody.
