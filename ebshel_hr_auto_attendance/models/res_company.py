# Part of ebshel_hr_auto_attendance. See LICENSE file for full copyright and licensing details.
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    auto_attendance_default = fields.Boolean(
        string="Automatic Attendance for New Employees",
        help="New employees start with Automatic Attendance ticked.")
    auto_attendance_excluded_group_ids = fields.Many2many(
        'res.groups', 'res_company_auto_attendance_group_rel', 'company_id', 'group_id',
        string="Never for Members of",
        help="Employees whose user belongs to one of these groups never get automatic "
             "entries, even when ticked: they record their own attendance.")
