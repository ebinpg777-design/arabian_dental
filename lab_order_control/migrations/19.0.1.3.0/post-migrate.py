# -*- coding: utf-8 -*-
"""Generate the Sales Route tiles (row 3 of the order ribbon) for the existing teams."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['crm.team']._lab_sync_all_route_tiles()
