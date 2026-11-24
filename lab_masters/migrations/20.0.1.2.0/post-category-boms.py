# -*- coding: utf-8 -*-
"""Every product the lab makes gets its category's bill of materials.

A few hundred products, a few seconds: each becomes a good manufactured to order
and gets the steps of its category's route (models/category_boms.py). Orders
already confirmed keep what they have; the next order of these products raises
its jobs.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['lab.masters.loader'].make_category_boms()
