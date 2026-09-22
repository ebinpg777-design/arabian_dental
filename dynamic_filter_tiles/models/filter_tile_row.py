# -*- coding: utf-8 -*-
"""The name a manager gave to one row of a model's ribbon.

Rows themselves need no record: a tile carries its row number, and the bar
stacks whatever numbers it finds. A *name* is the exception - "Pipeline",
"Operations", "Watch list" - because there is nowhere else to hang it once a
row is empty.

Naming rows is optional. A ribbon without a single named row shows no captions
at all, which is the shape most of them keep.
"""
from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .filter_tile import MAX_TILE_ROWS


class FilterTileRow(models.Model):
    _name = 'filter.tile.row'
    _description = 'Filter Tile Row'
    _order = 'model_name, number'

    name = fields.Char(string='Row Name', translate=True)
    model_id = fields.Many2one(
        'ir.model', required=True, ondelete='cascade',
        domain=[('transient', '=', False)])
    model_name = fields.Char(
        related='model_id.model', string='Model Name', store=True, index=True)
    number = fields.Integer(required=True, default=1)

    _model_row_uniq = models.Constraint(
        'unique (model_id, number)',
        'A row can only be named once per model.',
    )

    @api.constrains('number')
    def _check_number(self):
        for row in self:
            if not 1 <= row.number <= MAX_TILE_ROWS:
                raise ValidationError(self.env._(
                    'A ribbon has %(max)s rows at most, numbered from 1.', max=MAX_TILE_ROWS))

    @api.model
    def get_row_names(self, model_name=None):
        """``{model: {row number: name}}`` for every named row the user sees."""
        domain = [('model_name', '=', model_name)] if model_name else []
        names = {}
        for row in self.search(domain):
            if row.name and row.model_name:
                names.setdefault(row.model_name, {})[str(row.number)] = row.name
        return names

    @api.model
    def _shift_names(self, model_name, removed):
        """Follow a deleted row: drop its name, pull the ones below up by one.

        Ascending order matters - the slot each name moves into was vacated by
        the step before it, so the (model, number) uniqueness never trips.
        """
        rows = self.sudo().search(
            [('model_name', '=', model_name), ('number', '>=', removed)], order='number')
        for row in rows:
            if row.number == removed:
                row.unlink()
            else:
                row.number -= 1

    @api.model
    def set_row_name(self, model_name, number, name):
        """Name a row - or clear its name, which drops the record with it.

        Called straight from the ribbon, so it is the access rules on this model
        (managers only) that decide whether a rename is allowed.
        """
        model = self.env['ir.model']._get(model_name)
        if not model:
            return False
        number = max(1, min(MAX_TILE_ROWS, int(number or 1)))
        name = (name or '').strip()
        row = self.search([('model_id', '=', model.id), ('number', '=', number)], limit=1)
        if not name:
            row.unlink()
            return ''
        if row:
            row.name = name
        else:
            self.create({'model_id': model.id, 'number': number, 'name': name})
        return name
