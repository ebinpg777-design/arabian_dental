# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    # Odoo 20 dropped SELF_READABLE_FIELDS / SELF_WRITEABLE_FIELDS: a field a user
    # may change on their own record (the preferences form) is user_writeable.
    color_scheme = fields.Selection(related="res_users_settings_id.color_scheme", readonly=False, user_writeable=True)
