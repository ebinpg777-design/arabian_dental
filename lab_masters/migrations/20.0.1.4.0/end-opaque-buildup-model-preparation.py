# -*- coding: utf-8 -*-
"""Opaque and Buildup as two benches and two steps; Model Preparation on a printed
model (models/floor_layout.py).

An END script: it runs once every module is loaded. The Buildup bench is new, and a
bench has fields other modules add - the production sheet's required
`sheet_position`, the station's leads - which a bench made in this module's own
post script would be left without (NOT NULL violation).

The bills of materials only. The jobs already on the floor are put right online,
after the upgrade: `split_open_opaque_steps` and `add_open_model_preparation`."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    loader = env['lab.masters.loader']
    loader._floor_split_opaque_buildup()
    loader._floor_add_model_preparation()
