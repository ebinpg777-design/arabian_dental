# -*- coding: utf-8 -*-
from odoo import fields, models


class LabReworkReason(models.Model):
    _name = 'lab.rework.reason'
    _description = 'Rework Reason'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    code = fields.Char(index=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    responsibility_default = fields.Selection(
        [('lab_error', 'Lab Error'), ('fit_issue', 'Fit Issue'),
         ('transit_damage', 'Transit Damage'), ('doctor_change', 'Doctor Change'),
         ('patient_issue', 'Patient Issue'), ('unknown', 'Unknown')],
        help="Pre-fills the Responsibility on the rework order - still editable per case.")
