# -*- coding: utf-8 -*-
"""What a bench technician sees in the app switcher.

A technician on this database holds Manufacturing/User and the bench group, and
nothing else. That should mean "the Station Board" — but Odoo's menu groups are
ADDITIVE and most app roots are open to every internal user (`base.group_user`):
Discuss, Calendar, Contacts, Website, Employees and Track Order are all visible by
design, Inventory arrives because `mrp.group_mrp_user` implies `stock.group_stock_user`
in stock Odoo, and any menu whose groups were lost is visible to everybody at all.
There is no `groups="a,-b"` that hides a menu from a subset of people: that syntax
UNLINKS a group from the menu, it does not exclude anyone, and a record rule cannot
help because menu visibility is not a rule.

`_load_menus_blacklist` is the hook Odoo provides for exactly this, and core uses it
the same way (hr, hr_holidays_attendance). lab_fieldwork already uses it to keep a
supervisor's own round off their menu.

The rule here: for somebody in the bench group who is not an administrator, an app is
worth showing only if they were DIRECTLY granted one of the groups that gates it.
Directly, not by implication — Inventory is reached only through the Manufacturing
implication, and a technician bending wire has no business in it. Everything else,
including any app whose menu carries no groups at all, disappears from the switcher.

Nothing is taken away: the screens still work if reached by URL, and this changes no
group, no record rule and no ACL. It is the menu only. An administrator who wants a
technician to see an app grants them its group, which is what granting a group should
have meant all along. (client, 2026-08-29)
"""
from odoo import api, models

# Held by every internal user, so they say nothing about what somebody does here.
GENERIC_GROUPS = (
    'base.group_user',
    'base.group_no_one',      # Technical Features: implied by group_user in Odoo 19
    'base.group_multi_currency',
    'base.group_allow_export',
)

# The one app a bench technician is here for.
BENCH_APPS = (
    'mrp.menu_mrp_root',
)


class IrUiMenu(models.Model):
    _inherit = 'ir.ui.menu'

    @api.model
    def _load_menus_blacklist(self):
        blacklist = super()._load_menus_blacklist()
        user = self.env.user
        bound = self.env.ref('lab_workcenter_scan.group_workcenter_bound',
                             raise_if_not_found=False)
        # An administrator keeps the whole switcher: they are the person who has to
        # go and look at Settings when something is wrong.
        if not bound or not user.has_group('lab_workcenter_scan.group_workcenter_bound') \
                or user.has_group('base.group_system'):
            return blacklist
        # And only for somebody who can actually work a bench. 44 people on this
        # database carry the bench group without Manufacturing/User — they cannot open
        # the Station Board at all — and tidying their switcher would leave them
        # staring at nothing whatsoever, which is a worse screen than a noisy one.
        if not user.has_group('mrp.group_mrp_user'):
            return blacklist

        generic = set()
        for xml_id in GENERIC_GROUPS:
            group = self.env.ref(xml_id, raise_if_not_found=False)
            if group:
                generic.add(group.id)
        # `group_ids` is what was granted on the user, not what those grants imply.
        granted = set(user.group_ids.ids) - generic - {bound.id}

        keep = set()
        for xml_id in BENCH_APPS:
            menu = self.env.ref(xml_id, raise_if_not_found=False)
            if menu:
                keep.add(menu.id)

        roots = self.sudo().with_context(ir_ui_menu_full_list=True).search(
            [('parent_id', '=', False)])
        hide = [root.id for root in roots
                if root.id not in keep and not (set(root.group_ids.ids) & granted)]
        # Never hand back an empty switcher. If the Manufacturing menu itself has
        # gone (renamed, uninstalled, its groups changed), hiding everything else
        # leaves the person with no way into the system at all — better a noisy menu
        # than none, and better a visible symptom than a silent lockout.
        if len(hide) >= len(roots):
            return blacklist
        blacklist.extend(hide)
        return blacklist
