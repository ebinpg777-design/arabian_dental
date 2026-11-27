# Part of ebshel_hr_auto_attendance. See LICENSE file for full copyright and licensing details.
# res_company first: hr.employee's default reads the company's setting, and its
# column must exist before the employee column is filled on install.
from . import res_company
from . import hr_attendance
from . import hr_employee
from . import res_config_settings
