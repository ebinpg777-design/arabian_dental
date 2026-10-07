# -*- coding: utf-8 -*-
from odoo import fields, models


class ResUsers(models.Model):
    _inherit = 'res.users'

    # Odoo 20 dropped SELF_READABLE_FIELDS / SELF_WRITEABLE_FIELDS: a user reads
    # their own record anyway, and a field a user may change on their own record
    # is marked user_writeable instead.
    at_doctor_desk = fields.Boolean(
        'At the doctor call desk', default=True, user_writeable=True,
        help="Switched off by somebody who is away from the desk: tickets shared out by "
             "load pass them by until they are back.")
