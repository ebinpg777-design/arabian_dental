# -*- coding: utf-8 -*-
from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    workcenters = fields.Many2many(
        'mrp.workcenter', 'workcenter_users_rel', 'uid', 'workcenter_id',
        string="Workcenters")
    # Odoo 20 dropped SELF_READABLE_FIELDS / SELF_WRITEABLE_FIELDS: a user reads
    # their own record's fields anyway, and a field a user may change on their own
    # record (the preferences form) is marked user_writeable instead.
    invoice_signature = fields.Binary('Signature', copy=False, user_writeable=True)
