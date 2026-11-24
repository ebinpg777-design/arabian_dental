# -*- coding: utf-8 -*-
"""The floor moves onto the rows of the lab's own production sheets.

Benches and routes only - twenty-nine benches and fifteen short routes, a few
seconds of work. The open orders are NOT moved here: there are thousands of
them, each one a rebuild of its steps, and an upgrade that holds the site down
for that is an upgrade nobody can run in the day. They are moved afterwards,
with the site up, by `lab.masters.loader.reroute_open_orders`.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['lab.masters.loader'].apply_floor_layout()
