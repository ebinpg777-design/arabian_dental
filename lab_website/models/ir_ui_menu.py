# -*- coding: utf-8 -*-
import copy

from odoo import api, models
from odoo.http import request


class IrUiMenu(models.Model):
    """Keep the public site up when a root menu is not the visitor's to see.

    `website` overrides `load_menus_root` to fill in a root menu's action from
    the web menus, and indexes them directly::

        if (not menu['action']
                and web_menus[menu['id']]['actionModel']      # KeyError
                and web_menus[menu['id']]['actionID']):

    The two sources do not agree for a visitor who is not logged in.
    `load_menus_root` returns every root menu, while `load_web_menus` drops the
    ones the user has no group for - and this database has several, Calendar
    (menu 122, restricted to Role/User) among them. A public request that
    renders the website layout with `force_action` therefore raised
    `KeyError: 122` and Odoo answered 500.

    It took out `/`, `/contactus` AND `/web/login` on 2026-09-08 - the login
    page, so nobody could get in to look. It is intermittent only because
    `load_menus_root` is ormcached per user, language and force_action: a
    worker that had already cached the plain variant served fine until the
    force_action variant was asked for.

    So: read the web menus defensively, and do the lookup ourselves rather than
    let core's indexing run. `force_action` is dropped before calling super so
    the parent returns the plain tree without attempting its own lookup, and
    the result is copied before being touched - that dict is the ormcache's own
    object, and editing it in place would poison every later reader.
    (client, 2026-09-08)
    """
    _inherit = 'ir.ui.menu'

    @api.model
    def load_menus_root(self):
        if not self.env.context.get('force_action'):
            return super().load_menus_root()

        plain = super(IrUiMenu, self.with_context(force_action=False)).load_menus_root()
        root_menus = copy.deepcopy(plain)
        web_menus = self.load_web_menus(request.session.debug if request else False)
        for menu in root_menus.get('children', []):
            if menu.get('action'):
                continue
            entry = web_menus.get(menu['id'])
            if entry and entry.get('actionModel') and entry.get('actionID'):
                menu['action'] = '%s,%s' % (entry['actionModel'], entry['actionID'])
        return root_menus
