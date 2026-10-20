# -*- coding: utf-8 -*-
from odoo import fields, models


class LabDistrict(models.Model):
    """The district a clinic is in - v17 dental_sale's res.district (21 districts:
    MALAPPURAM, PALAKKAD, NILGIRI, ...). The migration had only kept it as a partner
    tag (client, 2026-10-20: the Odoo 17 contact form's fields, back on the clinic)."""
    _name = 'lab.district'
    _description = 'District'
    _order = 'name'

    name = fields.Char(string='District', required=True)
    active = fields.Boolean(default=True)

    _name_unique = models.Constraint('unique(name)', 'This district already exists.')
