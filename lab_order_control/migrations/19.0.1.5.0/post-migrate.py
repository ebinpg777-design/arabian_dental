# -*- coding: utf-8 -*-
"""Drop the per-route filter tiles: Sales Routes are a search panel now.

One tile per route filled the whole ribbon once the lab passed twenty routes, hiding the
queue tiles behind it. The routes moved to the search panel on the left of every order,
invoice and contact list (sale_custom/views/search_panel_views.xml), so the generated
tiles and their now empty "Sales Routes" rows are removed here.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    removed = env['crm.team']._lab_remove_route_tiles()
    _logger.info("route filter tiles removed: %s", removed)
