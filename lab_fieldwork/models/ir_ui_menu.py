# -*- coding: utf-8 -*-
"""Menus a role holds but does not need.

A field work manager or administrator often also carries the Executive role — to cover a
round, or simply because it was granted when the app was set up — and Odoo's menu groups
are additive: holding Executive puts "My Day" on their menu with no way to say "except
for these people". `groups="a,-b"` on a menuitem UNLINKS group b from the menu, it does
not hide the menu from b, and a record rule cannot help either because the permissive
manager rule unions everything straight back.

`_load_menus_blacklist` is the hook Odoo provides for exactly this, and core uses it the
same way (hr, hr_holidays_attendance). The menu still exists and the screen still works
if reached directly — this only stops it cluttering the menu of somebody who runs the
team rather than walks it. (client, 2026-08-27)
"""
from odoo import api, models

# What a supervisor does not need on their own menu: the executive's personal round.
SUPERVISOR_HIDES = (
    'lab_fieldwork.menu_fw_my_day',
)

SUPERVISOR_GROUPS = (
    'lab_fieldwork.group_fieldwork_manager',
    'lab_fieldwork.group_fieldwork_admin',
)


class IrUiMenu(models.Model):
    _inherit = 'ir.ui.menu'

    @api.model
    def _load_menus_blacklist(self):
        blacklist = super()._load_menus_blacklist()
        user = self.env.user
        supervisor = any(
            user.has_group(group) for group in SUPERVISOR_GROUPS
            if self.env.ref(group, raise_if_not_found=False))
        if not supervisor:
            return blacklist
        for xml_id in SUPERVISOR_HIDES:
            menu = self.env.ref(xml_id, raise_if_not_found=False)
            if menu:
                blacklist.append(menu.id)
        return blacklist
