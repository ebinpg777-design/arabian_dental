# Part of ebshel_hr_auto_attendance. See LICENSE file for full copyright and licensing details.
from odoo import fields, models


class HrAttendance(models.Model):
    _inherit = 'hr.attendance'

    auto_created = fields.Boolean(
        string="Created Automatically", readonly=True, copy=False, index='btree_not_null',
        help="Made from the employee's working hours, not recorded by anybody.")
