# -*- coding: utf-8 -*-
"""Retire the unused Daily Sales menu.

Deleting the <menuitem> records from the data file is NOT enough on its own: Odoo's
stale-record collection did not remove them here, so the menu survived the upgrade and
the app tile stayed in the switcher — the data file said one thing and the running
system did another.

Only the MENUS go. The `daily.sales` model, its records, its views and its actions are
left alone: removing them would destroy history and break the Daily Sales Summary report
that still points at them. This closes the way in, nothing else, and putting the menu
back is three lines of XML.
"""
from odoo import SUPERUSER_ID, api

OBSOLETE = (
    'sale_custom.menu_daily_sales_report',
    'sale_custom.menu_detail_daily_sales',
    'sale_custom.menu_daily_sales_root',      # the parent last, once it is empty
)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    for xml_id in OBSOLETE:
        menu = env.ref(xml_id, raise_if_not_found=False)
        if menu:
            menu.unlink()
