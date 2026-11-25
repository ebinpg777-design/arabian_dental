# -*- coding: utf-8 -*-
"""Trail Carving and Wax Up after Teeth Setting (models/floor_layout.py).

An END script: it runs once every module is loaded. A bench has fields other modules
add - the production sheet's required `sheet_position`, the station's leads - and a
bench made in this module's own post script left them out (NOT NULL violation)."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['lab.masters.loader']._floor_add_after_teeth_setting()
