# -*- coding: utf-8 -*-
"""20.0: board, card and tab icons move from FontAwesome to Odoo 20's icon fonts.

Odoo 20 ships no FontAwesome: a card still carrying ``fa-truck`` would draw
nothing at all. Every stored ``fa-*`` icon is translated to the closest
Material Symbols / odoo_ui_icons name (``local_shipping``), and an unknown one
to a generic icon, so every existing board, card and tab keeps a face.
"""
import logging

from odoo.addons.ebshel_dashboard.models.dashboard_icons import normalize_icon

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    for table in ('dashboard_board', 'dashboard_item', 'dashboard_tab'):
        cr.execute(f"""
            SELECT id, icon
              FROM {table}
             WHERE icon LIKE 'fa-%%' OR icon LIKE 'fa %%'
        """)
        rows = cr.fetchall()
        for record_id, icon in rows:
            cr.execute(f"UPDATE {table} SET icon = %s WHERE id = %s",
                       (normalize_icon(icon), record_id))
        if rows:
            _logger.info("Dashboards: %s FontAwesome icons of %s translated for Odoo 20.",
                         len(rows), table)
