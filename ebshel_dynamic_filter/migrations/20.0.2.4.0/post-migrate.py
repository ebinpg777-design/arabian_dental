# -*- coding: utf-8 -*-
"""20.0.2.4.0: tile icons move from FontAwesome to Odoo 20's icon fonts.

Odoo 20 ships no FontAwesome: a tile still carrying ``fa-truck`` would draw
nothing at all. Every stored ``fa-*`` icon is translated to the closest
Material Symbols / odoo_ui_icons name (``local_shipping``), and an unknown one
to the generic filter icon, so every existing tile keeps a face.
"""
import logging

from odoo.addons.ebshel_dynamic_filter.models.tile_icons import normalize_icon

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        SELECT id, icon
          FROM filter_tile
         WHERE icon LIKE 'fa-%%' OR icon LIKE 'fa %%'
    """)
    rows = cr.fetchall()
    for tile_id, icon in rows:
        cr.execute("UPDATE filter_tile SET icon = %s WHERE id = %s",
                   (normalize_icon(icon), tile_id))
    if rows:
        _logger.info("Filter tiles: %s FontAwesome icons translated for Odoo 20.", len(rows))
