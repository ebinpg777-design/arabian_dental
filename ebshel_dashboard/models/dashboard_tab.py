# -*- coding: utf-8 -*-
"""Dynamic Dashboards - a page of a board.

A board with forty cards is a scroll; a board with four tabs is a book. A tab
is a name and an order; a card joins it through ``tab_id``, and a card with no
tab sits on the first page.
"""
from odoo import api, fields, models


class DashboardTab(models.Model):
    _name = 'dashboard.tab'
    _description = 'Dashboard Tab'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    board_id = fields.Many2one(
        'dashboard.board', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    icon = fields.Char(default='fa-folder-o')
    item_ids = fields.One2many('dashboard.item', 'tab_id', string='Cards')
    item_count = fields.Integer(compute='_compute_item_count')

    @api.depends('item_ids')
    def _compute_item_count(self):
        for tab in self:
            tab.item_count = len(tab.item_ids)

    def _payload(self):
        self.ensure_one()
        return {'id': self.id, 'name': self.name, 'icon': self.icon or 'fa-folder-o',
                'sequence': self.sequence}
