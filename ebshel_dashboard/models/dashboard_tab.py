# -*- coding: utf-8 -*-
"""Dynamic Dashboards - a page of a board.

A board with forty cards is a scroll; a board with four tabs is a book. A tab
is a name and an order; a card joins it through ``tab_id``, and a card with no
tab sits on the first page.
"""
from odoo import api, fields, models

from .dashboard_icons import TAB_ICON, normalize_icon, normalize_icon_vals


class DashboardTab(models.Model):
    _name = 'dashboard.tab'
    _description = 'Dashboard Tab'
    _order = 'sequence, id'

    # copy=True: Odoo 20 suffixes a copied `name` with "(copy)" by default;
    # a copied board keeps its tabs' names (the cards re-find them by name).
    name = fields.Char(required=True, translate=True, copy=True)
    board_id = fields.Many2one(
        'dashboard.board', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    icon = fields.Char(default=TAB_ICON)
    item_ids = fields.One2many('dashboard.item', 'tab_id', string='Cards')
    item_count = fields.Integer(compute='_compute_item_count')

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([normalize_icon_vals(vals) for vals in vals_list])

    def write(self, vals):
        return super().write(normalize_icon_vals(vals))

    @api.depends('item_ids')
    def _compute_item_count(self):
        for tab in self:
            tab.item_count = len(tab.item_ids)

    def _payload(self):
        self.ensure_one()
        return {'id': self.id, 'name': self.name, 'icon': normalize_icon(self.icon) or TAB_ICON,
                'sequence': self.sequence}
