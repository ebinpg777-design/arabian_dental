# -*- coding: utf-8 -*-
from odoo import fields, models


class ResUsers(models.Model):
    _inherit = 'res.users'

    at_doctor_desk = fields.Boolean(
        'At the doctor call desk', default=True,
        help="Switched off by somebody who is away from the desk: tickets shared out by "
             "load pass them by until they are back.")

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + ['at_doctor_desk']

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + ['at_doctor_desk']
