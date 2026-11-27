# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    # Odoo 20 dropped SELF_READABLE_FIELDS / SELF_WRITEABLE_FIELDS: a field a user
    # may change on their own record (the preferences form) is user_writeable.
    color_scheme = fields.Selection(related="res_users_settings_id.color_scheme", readonly=False, user_writeable=True)

    @api.model
    def ebshel_home_notices(self):
        """Notices shown above the apps on the home screen, for the user asking.

        Empty here: other modules add their own by overriding this and appending
        to what `super()` returns. Each notice is a dict:

            key     unique, stable name
            title   one short line
            text    one sentence
            icon    a Font Awesome class, e.g. 'fa-cube'
            tone    'info' | 'warning' | 'danger' | 'success'
            count   optional number shown as a badge
            action  optional client action (an act_window dict) opened on tap

        Keep each one to a count query or two: this runs every time the home
        screen opens.
        """
        return []

