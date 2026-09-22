# -*- coding: utf-8 -*-
"""Which actions a menu actually points at.

Used to tell the handful of act_window records a user navigates to apart from
the many that only exist to be opened from a button or embedded in a form. The
lookup lives here rather than in a comprehension over ``ir.ui.menu`` because
``action`` is a Reference field: it is stored as ``'model,id'`` text, so one
query with the right strings beats reading every menu in the database.
"""
from odoo import models


class IrUiMenu(models.Model):
    _inherit = 'ir.ui.menu'

    def _menu_bound_actions(self, actions):
        """Ids of ``actions`` that at least one menu leads to."""
        if not actions:
            return set()
        references = [f'{action._name},{action.id}' for action in actions]
        menus = self.sudo().with_context(ir_ui_menu_full_list=True).search(
            [('action', 'in', references)])
        # `action` is a Reference field: searching takes the "model,id" string,
        # but *reading* it back gives a recordset.
        return {menu.action.id for menu in menus if menu.action}
