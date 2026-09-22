# -*- coding: utf-8 -*-
"""Recorded history of a tile's value.

A tile's sparkline is normally a group-by on a date field of the model - which
answers "when were these records dated", not "how did this number move". For a
tile like *Open Tickets* the two are very different questions, and plenty of
models have no usable date field at all.

Setting a tile's *Trend From* to **Recorded history** makes the daily cron store
one point per day here instead, so the sparkline shows the number the way the
business actually lived it.
"""
from odoo import fields, models


class FilterTileSnapshot(models.Model):
    _name = 'filter.tile.snapshot'
    _description = 'Filter Tile History Point'
    _order = 'captured_on desc, id desc'
    _rec_name = 'captured_on'

    tile_id = fields.Many2one(
        'filter.tile', required=True, ondelete='cascade', index=True)
    captured_on = fields.Date(
        required=True, index=True, default=fields.Date.context_today)
    value = fields.Float(
        help="The tile's number on that day: its measure, or its record count.")
    record_count = fields.Integer(string='Records')

    # One point per tile per day - the cron re-writes today's point instead of
    # piling up, so running it twice (or by hand) never doubles the history.
    _tile_day_uniq = models.Constraint(
        'unique (tile_id, captured_on)',
        'A tile can only have one history point per day.',
    )
