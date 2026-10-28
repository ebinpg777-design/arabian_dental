# -*- coding: utf-8 -*-
from odoo import fields, models


class LabMaterial(models.Model):
    """The material a case is made in - VITA, VM9, IVOCLAR, ... - v17 dental_sale's
    res.material, picked on the order (client, 2026-10-28: "In old server there is an
    option of entering Material in the sale order. Please do the same here"). The
    migration had only kept it as text in Special Instructions ("Material: VITA")."""
    _name = 'lab.material'
    _description = 'Material'
    _order = 'name'

    name = fields.Char(string='Material', required=True)
    active = fields.Boolean(default=True)

    _name_unique = models.Constraint('unique(name)', 'This material already exists.')
