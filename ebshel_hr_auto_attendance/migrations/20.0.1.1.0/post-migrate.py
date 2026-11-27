# Part of ebshel_hr_auto_attendance. See LICENSE file for full copyright and licensing details.
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    # Public holidays loaded by Odoo with no length (00:00-00:00): whole days.
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['resource.calendar.leaves'].search([('resource_id', '=', False)])._whole_day_public_holidays()
