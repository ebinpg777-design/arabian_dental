# -*- coding: utf-8 -*-
"""Apps on the home screen follow the access rights on the user form.

Odoo opens many app roots to EVERY internal user - Discuss, Calendar, Contacts,
Website, Employees and Track Order are gated by `base.group_user` /
`base.group_user_regular`, which everybody holds - and an app whose menus carry no
groups at all is on everybody's screen. So "Contact: No" on a user form still left
Contacts on the home screen (client, 2026-10-20: "Unwanted menus are visible").

The rule, for anybody who is not an administrator: an app is shown only when the user
holds one of the groups that gate it - on the app's root menu or on any of its
submenus - other than the groups every internal user holds anyway. Odoo's spreadsheet
Dashboards stays on everybody's screen (client's choice). The bench technicians'
stricter rule (lab_workcenter_scan) still applies on top of this one.

Nothing is taken away: screens still open by URL; no group, ACL or record rule changes.
It is the home screen only. To give somebody an app, grant them its access right.
`lab_access_control.apps_follow_rights = False` switches the rule off.
"""
from collections import defaultdict

from odoo import api, models

ALWAYS_SHOWN = (
    'spreadsheet_dashboard.spreadsheet_dashboard_menu_root',
)
PARAM = 'lab_access_control.apps_follow_rights'


class IrUiMenu(models.Model):
    _inherit = 'ir.ui.menu'

    @api.model
    def _load_menus_blacklist(self):
        blacklist = super()._load_menus_blacklist()
        user = self.env.user
        if self.env.su or user.share or user.has_group('base.group_system'):
            return blacklist
        if self.env['ir.config_parameter'].sudo().get_str(PARAM, 'True') == 'False':
            return blacklist
        return list(set(blacklist) | set(self._lab_apps_without_rights(user)))

    @api.model
    def _lab_generic_group_ids(self):
        """Groups every internal user holds: they say nothing about what somebody does."""
        ids = set()
        for xmlid in ('base.group_user', 'base.group_user_regular', 'base.group_no_one'):
            group = self.env.ref(xmlid, raise_if_not_found=False)
            if group:
                ids.add(group.id)
                ids.update(group.all_implied_ids.ids)
        return ids

    @api.model
    def _lab_apps_without_rights(self, user):
        """Root menus of the apps `user` holds no specific right for."""
        generic = self._lab_generic_group_ids()
        always = {menu.id for menu in (self.env.ref(x, raise_if_not_found=False) for x in ALWAYS_SHOWN) if menu}
        mine = set(user.all_group_ids.ids) - generic
        gates = defaultdict(set)
        menus = self.sudo().with_context(ir_ui_menu_full_list=True, active_test=False).search([])
        for menu in menus:
            gates[int(menu.parent_path.split('/')[0])].update(menu.group_ids.ids)
        return [root for root, groups in gates.items()
                if root not in always and not ((groups - generic) & mine)]
