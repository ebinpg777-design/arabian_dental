# -*- coding: utf-8 -*-
"""Turn on "Same Person on Several Sales Routes" once.

The client asked for it (2026-11-27: "able to add same members in different
sales routes"). Only when nobody has set the parameter yet: a later 'off' in
Settings stays off through every upgrade after this one.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Param = env['ir.config_parameter'].sudo()
    if not Param.search_count([('key', '=', 'sales_team.membership_multi')]):
        Param.set_bool('sales_team.membership_multi', True)
