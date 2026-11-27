# Part of ebshel_hr_auto_attendance. See LICENSE file for full copyright and licensing details.
from . import models


def _post_init_hook(env):
    # public holidays already loaded with no length: make them whole days
    env['resource.calendar.leaves'].sudo().search([('resource_id', '=', False)])._whole_day_public_holidays()
