# -*- coding: utf-8 -*-
"""Wax Up's Model Preparation as Cutting, Scooping and Blocking; Metal/Sintering and
Metal/Printing around Metal/Casting (client, 2026-11-27; models/floor_layout.py).

An END script, as the Opaque/Buildup split: the new benches have fields other modules
add (the production sheet's required `sheet_position`, the station's leads).

The bills of materials only. The jobs already on the floor are put right online,
after the upgrade: `split_open_model_preparation` and `add_open_metal_steps`."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    loader = env['lab.masters.loader']
    loader._floor_split_model_preparation()
    loader._floor_add_metal_steps()
    # On the printed Production Report, Scooping and Blocking stand with Cutting on the
    # model preparation lines under the heading - one line each, with its technician -
    # not as a second Wax Up table at the top of the sheet
    for xmlid in ('wc_wax_scooping', 'wc_wax_blocking'):
        bench = env.ref('lab_masters.%s' % xmlid, raise_if_not_found=False)
        if bench and 'sheet_position' in bench._fields:
            bench.sheet_position = 'model'
