# -*- coding: utf-8 -*-
"""Dynamic Dashboards - what one reader prefers.

Kept apart from ``res.users`` on purpose: a preference is the reader's own
business, written by them, and a dedicated row per user avoids granting write
access on the user record for the sake of a favourite or a watchlist.
"""
from odoo import api, fields, models

from .dashboard_item import NUMBER_KINDS


class DashboardPreference(models.Model):
    _name = 'dashboard.preference'
    _description = 'Dashboard Preference'
    _rec_name = 'user_id'

    user_id = fields.Many2one(
        'res.users', required=True, ondelete='cascade', index=True,
        default=lambda self: self.env.uid)
    board_id = fields.Many2one(
        'dashboard.board', string='Favourite Dashboard', ondelete='set null',
        help="The board that opens first.")
    watch_item_ids = fields.Many2many(
        'dashboard.item', 'dashboard_watch_rel', 'preference_id', 'item_id',
        string='Watched Cards',
        help="Number cards pinned to the reader's watchlist, shown above every board.")

    @api.model
    def _mine(self):
        return self.search([('user_id', '=', self.env.uid)], limit=1)

    @api.model
    def _ensure(self):
        """The reader's row, created on first use."""
        return self._mine() or self.create({'user_id': self.env.uid})

    @api.model
    def _default_board_id(self):
        preference = self._mine()
        return preference.board_id.id if preference else False

    @api.model
    def _set_default_board(self, board_id):
        preference = self._mine()
        if preference:
            preference.board_id = board_id or False
        elif board_id:
            self.create({'user_id': self.env.uid, 'board_id': board_id})
        return True

    # ------------------------------------------------------------------
    # Watchlist
    # ------------------------------------------------------------------
    @api.model
    def toggle_watch(self, item_id):
        """Pin a number card to the watchlist, or unpin it. Returns the new state."""
        item = self.env['dashboard.item'].browse(int(item_id)).exists()
        if not item or item.kind not in NUMBER_KINDS:
            return False
        item.check_access('read')
        preference = self._ensure()
        if item in preference.watch_item_ids:
            preference.write({'watch_item_ids': [(3, item.id)]})
            return False
        preference.write({'watch_item_ids': [(4, item.id)]})
        return True

    @api.model
    def _watched_items(self):
        """The reader's pinned cards, re-searched so record rules apply: a card
        pinned yesterday from a board unshared today simply drops out."""
        preference = self._mine()
        if not preference or not preference.watch_item_ids:
            return self.env['dashboard.item']
        return self.env['dashboard.item'].search(
            [('id', 'in', preference.watch_item_ids.ids), ('board_id.active', '=', True)],
            order='board_id, sequence, id')

    @api.model
    def watchlist_payload(self):
        """The watchlist as the browser draws it: each card computed over its
        own board's period, with the board's name for the label."""
        payload = []
        for item in self._watched_items():
            entry = item.compute_values(item.board_id.period)
            entry['board_name'] = item.board_id.name
            entry['board_id'] = item.board_id.id
            payload.append(entry)
        return payload
