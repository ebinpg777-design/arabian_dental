# -*- coding: utf-8 -*-
"""Technical Features for administrators only (client, 2026-10-20).

The Odoo 17 book gave every user Technical Features (base.group_no_one): "Role / User"
implied it and all 35 users also held it directly, and the migration carried both
over - so every user got debug-only menus (Link Tracker) and the Labels app, whose
menu had been regated on it. Standard Odoo keeps it for administrators.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    no_one = env.ref('base.group_no_one', raise_if_not_found=False)
    if not no_one:
        return
    # 1. no longer implied by the groups every internal user holds
    for xmlid in ('base.group_user', 'base.group_user_regular'):
        group = env.ref(xmlid, raise_if_not_found=False)
        if group and no_one in group.implied_ids:
            group.write({'implied_ids': [(3, no_one.id)]})
            _logger.info("lab_access_control: %s no longer implies Technical Features", xmlid)
    # 2. off every user who is not an administrator
    admin = env.ref('base.group_system')
    users = env['res.users'].with_context(active_test=False).search([('group_ids', 'in', no_one.id)])
    users = users.filtered(lambda u: admin not in u.all_group_ids)
    if users:
        users.write({'group_ids': [(3, no_one.id)]})
    _logger.info("lab_access_control: Technical Features removed from %s non-administrators", len(users))
    # 3. the Labels app is for whoever prints labels, as epg_product_label declares it
    menu = env.ref('epg_product_label.menu_label_root', raise_if_not_found=False)
    label_user = env.ref('epg_product_label.group_label_user', raise_if_not_found=False)
    if menu and label_user and no_one in menu.group_ids:
        menu.write({'group_ids': [(6, 0, label_user.ids)]})
        _logger.info("lab_access_control: Labels menu gated on Print Labels again")
