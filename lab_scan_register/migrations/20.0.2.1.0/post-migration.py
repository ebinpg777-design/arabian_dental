# -*- coding: utf-8 -*-
"""Already Printed (Ortho 304d97d): the To Print tile leaves out the registers marked
so. The tiles are noupdate (the lab may recolour or retire them), so the shipped
domain is moved on only where it is still the one shipped - a tile the lab edited is
left as the lab made it. A database coming from 20.0.1.2.0 gets the tile new, with
the new domain, and this finds nothing to do."""
from odoo import SUPERUSER_ID, api

OLD = "[('state', '=', 'confirmed')]"
NEW = "[('state', '=', 'confirmed'), ('already_printed', '=', False)]"
OLD_TOOLTIP = 'Confirmed, waiting to go for 3D printing'
NEW_TOOLTIP = 'Confirmed, waiting to go for 3D printing (not those printed already)'


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    tile = env.ref('lab_scan_register.tile_scan_to_print', raise_if_not_found=False)
    if tile and (tile.domain or '').strip() == OLD:
        tile.domain = NEW
        if tile.tooltip == OLD_TOOLTIP:
            tile.tooltip = NEW_TOOLTIP
