# -*- coding: utf-8 -*-
from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    workcenters = fields.Many2many(
        'mrp.workcenter', 'workcenter_users_rel', 'uid', 'workcenter_id',
        string="Workcenters")
    invoice_signature = fields.Binary('Signature', copy=False)

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + ['workcenters', 'invoice_signature']

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + ['invoice_signature']
