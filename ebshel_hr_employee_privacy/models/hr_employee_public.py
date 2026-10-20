# Part of ebshel_hr_employee_privacy. See LICENSE file for full copyright and licensing details.
"""The employee directory lists only the user's own record when the user has no
employee-reading role.

`hr.employee.public` is what Odoo shows to every internal user: the Employees app,
the employee pickers and the avatar cards read it. Narrowing what it LISTS is done
here, in `_search`, rather than with an access restriction, on purpose:

* an `ir.access` restriction is AND-ed with every permission, so it would also cut
  down users another module deliberately lets see employees (attendance officers,
  a shop-floor or field-work role), and it makes reading a colleague's record raise
  - a work order naming another technician, a desk listing other executives, an
  avatar card would all break for them;
* a `_search` override only changes what comes back from a search: lists, kanbans,
  pickers and name searches show the user themselves, while a record that names
  a colleague still reads and displays.

Who is narrowed: a user who cannot read `hr.employee` at all - no Employees
officer/administrator role, no other module's grant - and is not the superuser.
"""
from odoo import api, models
from odoo.fields import Domain


class HrEmployeePublic(models.Model):
    _inherit = "hr.employee.public"

    def _only_own_employee(self):
        """True when the current user should see nobody but themselves."""
        return not self.env.su and not self.env["hr.employee"].has_access("read")

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, *, active_test=True, bypass_access=False):
        if not bypass_access and self._only_own_employee():
            domain = Domain.AND([Domain(domain), Domain("user_id", "=", self.env.uid)])
        return super()._search(domain, offset, limit, order, active_test=active_test, bypass_access=bypass_access)
