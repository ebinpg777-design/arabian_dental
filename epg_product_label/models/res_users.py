# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResUsers(models.Model):
    _inherit = 'res.users'

    epg_default_label_template_id = fields.Many2one(
        'epg.label.template', string='Default Label Template',
        domain="[('active', '=', True)]", user_writeable=True,
        help="Template the print wizard opens on for this user.")
    epg_allowed_label_template_ids = fields.Many2many(
        'epg.label.template', 'epg_label_template_users_rel', 'user_id', 'template_id',
        string='Allowed Label Templates',
        help="Leave empty to let this user print with every template. "
             "Administrators are never restricted.")

    # Odoo 20 dropped SELF_READABLE_FIELDS / SELF_WRITEABLE_FIELDS: a user reads
    # their own record anyway, and the default template is user_writeable above.

    @api.constrains('epg_default_label_template_id', 'epg_allowed_label_template_ids')
    def _check_default_label_template(self):
        """A default the user is not allowed to use would fail at print time."""
        for user in self:
            allowed = user.epg_allowed_label_template_ids
            default = user.epg_default_label_template_id
            if default and allowed and default not in allowed:
                user.epg_allowed_label_template_ids = allowed | default
