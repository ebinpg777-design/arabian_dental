# -*- coding: utf-8 -*-
"""Dynamic Dashboards - the filter bar at the top of a board.

A focus is one value somebody clicked; a filter is a question the board asks
every time it opens - "which company?", "whose?", "which stage?". It sits in
the header as a dropdown, and every card whose model has that field follows
it. Cards on another model are left whole rather than emptied: a board that
mixes invoices and contacts narrows what it can.

The values offered are read from the data itself, so a filter never lists a
company nobody has an invoice for.
"""
from odoo import api, fields, models

# Field types a reader can pick a single value of. A date is the period's
# job, and a number is nobody's idea of a dropdown.
FILTER_TYPES = ('many2one', 'selection', 'boolean', 'char')
# Values offered per filter. Past this a dropdown stops being a shortcut.
MAX_OPTIONS = 50


class DashboardFilter(models.Model):
    _name = 'dashboard.filter'
    _description = 'Dashboard Filter'
    _order = 'sequence, id'

    board_id = fields.Many2one(
        'dashboard.board', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    model_id = fields.Many2one(
        'ir.model', string='On Model', required=True, ondelete='cascade',
        help="Cards on this model follow the filter; the others are left whole.")
    model_name = fields.Char(related='model_id.model', store=True)
    field_id = fields.Many2one(
        'ir.model.fields', string='Field', required=True, ondelete='cascade',
        domain="[('model_id', '=', model_id), ('ttype', 'in', "
               "['many2one', 'selection', 'boolean', 'char']), ('store', '=', True)]")
    field_name = fields.Char(related='field_id.name', store=True)
    name = fields.Char(
        string='Label', help="What the reader sees. Empty means the field's own label.")
    label = fields.Char(compute='_compute_label')

    @api.depends('name', 'field_id')
    def _compute_label(self):
        for record in self:
            record.label = record.name or record.field_id.field_description or record.field_name

    @api.onchange('model_id')
    def _onchange_model(self):
        if self.field_id.model_id != self.model_id:
            self.field_id = False

    def _payload(self):
        """The filter and the values it offers, for the header."""
        self.ensure_one()
        return {
            'id': self.id,
            'label': self.label,
            'model': self.model_name,
            'field': self.field_name,
            'options': self._options(),
        }

    def _options(self):
        """The values this field actually takes, read with the reader's rights.

        Grouping the table beats reading every record: one query, already
        ordered by how common each value is, and nothing the reader may not
        see can appear in it.
        """
        self.ensure_one()
        Model = self.env.get(self.model_name)
        field = Model._fields.get(self.field_name) if Model is not None else None
        if field is None or not field.store or field.type not in FILTER_TYPES:
            return []
        if field.type == 'boolean':
            return [{'key': 'true', 'label': self.env._('Yes')},
                    {'key': 'false', 'label': self.env._('No')}]
        try:
            Model.check_access('read')
            rows = Model._read_group([], [self.field_name], ['__count'],
                                     order='__count DESC', limit=MAX_OPTIONS)
        except Exception:  # noqa: BLE001 - no access is an empty dropdown
            return []
        options = []
        for raw, _count in rows:
            if isinstance(raw, models.Model):
                if not raw:
                    continue
                options.append({'key': raw.id, 'label': raw.display_name})
            elif raw in (False, None, ''):
                continue
            elif field.type == 'selection':
                selection = dict(field._description_selection(self.env) or [])
                options.append({'key': raw, 'label': selection.get(raw, raw)})
            else:
                options.append({'key': raw, 'label': str(raw)})
        return options
