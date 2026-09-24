# -*- coding: utf-8 -*-
"""Dynamic Dashboards - a reader's own note on a board.

A board is shared; what a reader wants to remember about it is not. One note
per reader per board, kept in a side drawer of the board, private by record
rule.
"""
from odoo import api, fields, models


class DashboardNote(models.Model):
    _name = 'dashboard.note'
    _description = 'Personal Dashboard Note'
    _rec_name = 'board_id'

    user_id = fields.Many2one(
        'res.users', required=True, ondelete='cascade', index=True,
        default=lambda self: self.env.uid)
    board_id = fields.Many2one(
        'dashboard.board', required=True, ondelete='cascade', index=True)
    body = fields.Html(sanitize=True)

    @api.model
    def _mine(self, board_id):
        return self.search(
            [('user_id', '=', self.env.uid), ('board_id', '=', int(board_id))], limit=1)

    @api.model
    def save_note(self, board_id, body):
        """Write the reader's note on a board, creating it on first use."""
        note = self._mine(board_id)
        if note:
            note.body = body or False
        else:
            note = self.create({'user_id': self.env.uid, 'board_id': int(board_id),
                                'body': body or False})
        return {'id': note.id, 'body': note.body or ''}
