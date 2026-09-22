# -*- coding: utf-8 -*-
"""The pickup's view of a case slip: when its impression physically reached the lab.

Lives in lab_delivery, not lab_fieldwork: the stamp only means something once a
pickup record exists to write it, and fieldwork must stay installable without the
delivery machinery.
"""
from odoo import fields, models


class LabCase(models.Model):
    _inherit = 'lab.case'

    impression_received_at = fields.Datetime(
        string='Impression at Lab', readonly=True, copy=False,
        help="When the physical impression reached the lab, stamped by the pickup "
             "that carried it. Empty means it is still travelling - or was scanned "
             "and never travelled at all.")
    pickup_ids = fields.Many2many(
        'lab.delivery', 'lab_delivery_case_rel', 'case_id', 'delivery_id',
        string='Pickups', copy=False,
        help="The lab-bound trips this slip's impression travelled on.")
