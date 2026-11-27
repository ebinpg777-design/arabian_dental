# Part of ebshel_hr_auto_attendance. See LICENSE file for full copyright and licensing details.
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    auto_attendance_default = fields.Boolean(
        related='company_id.auto_attendance_default', readonly=False)
    auto_attendance_excluded_group_ids = fields.Many2many(
        related='company_id.auto_attendance_excluded_group_ids', readonly=False)
