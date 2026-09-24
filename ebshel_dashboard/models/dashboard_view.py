# -*- coding: utf-8 -*-
"""Dynamic Dashboards - a saved view: how one reader likes to look at a board.

A period, a focus and the "mine" switch, under a name, so "Belgium, this
quarter, my records" is one click next time instead of three.
"""
import json

from odoo import api, fields, models


class DashboardView(models.Model):
    _name = 'dashboard.view'
    _description = 'Saved Dashboard View'
    _order = 'name, id'

    name = fields.Char(required=True)
    user_id = fields.Many2one(
        'res.users', required=True, ondelete='cascade', index=True,
        default=lambda self: self.env.uid)
    board_id = fields.Many2one(
        'dashboard.board', required=True, ondelete='cascade', index=True)
    period = fields.Char()
    focus = fields.Text(help="The focus as the browser holds it, JSON.")
    mine = fields.Boolean()

    def _payload(self):
        self.ensure_one()
        focus = None
        if self.focus:
            try:
                focus = json.loads(self.focus)
            except ValueError:
                focus = None
        return {'id': self.id, 'name': self.name, 'period': self.period or False,
                'focus': focus, 'mine': self.mine}

    @api.model
    def list_views(self, board_id):
        return [view._payload() for view in self.search(
            [('user_id', '=', self.env.uid), ('board_id', '=', int(board_id))])]

    @api.model
    def save_view(self, board_id, name, period=None, focus=None, mine=False):
        name = (name or '').strip() or self.env._('My view')
        existing = self.search([('user_id', '=', self.env.uid), ('board_id', '=', int(board_id)),
                                ('name', '=', name)], limit=1)
        values = {
            'name': name, 'board_id': int(board_id), 'period': period or False,
            'focus': json.dumps(focus) if focus else False, 'mine': bool(mine),
        }
        if existing:
            existing.write(values)
            return existing._payload()
        return self.create(values)._payload()

    @api.model
    def remove_view(self, view_id):
        self.search([('id', '=', int(view_id)), ('user_id', '=', self.env.uid)]).unlink()
        return True
