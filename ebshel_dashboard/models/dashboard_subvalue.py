# -*- coding: utf-8 -*-
"""Dynamic Dashboards - the small numbers under a big one.

A number card answers one question: how many orders. The questions that come
straight after it are almost always the same shape - how many of those are
late, how many are mine, what are they worth - and putting each of them on a
card of its own turns a board into a wall.

A sub-value is one of those follow-up numbers, computed over the card's own
filter plus one more condition, and drawn small underneath the headline. It
carries the card through the same period, the same focus, the same "only
mine" and the same filter bar, so the small numbers always belong to the big
one above them.

Each can say what share of the headline it is - a bar the width of that
share - and each opens exactly the records it counted.
"""
from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .dashboard_item import AGGREGATES, CARD_COLORS


class DashboardSubvalue(models.Model):
    _name = 'dashboard.item.subvalue'
    _description = 'Dashboard Sub-value'
    _order = 'sequence, id'

    item_id = fields.Many2one(
        'dashboard.item', string='Card', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(
        string='Label', required=True,
        help="What this number is: Late, Mine, This week...")
    domain = fields.Char(
        string='Filter', default='[]', required=True,
        help="What makes this number different from the one above it - added "
             "to the card's own filter, never instead of it.")
    aggregate = fields.Selection(
        AGGREGATES, default='count', required=True,
        help="Left as Count, this is how many records match. Leave it the "
             "same as the card's and the shares add up.")
    measure_field_id = fields.Many2one(
        'ir.model.fields', string='Measure',
        domain="[('model_id', '=', parent.model_id), "
               "('ttype', 'in', ['integer', 'float', 'monetary']), ('store', '=', True)]",
        ondelete='cascade')
    measure_name = fields.Char(related='measure_field_id.name')
    color = fields.Selection(
        CARD_COLORS, string='Colour', default='slate',
        help="Colours the label and its share bar.")
    show_share = fields.Boolean(
        string='Share of the Big Number', default=True,
        help="Draws what fraction of the headline this is, as a percentage "
             "and a bar the width of it.")

    @api.constrains('aggregate', 'measure_field_id')
    def _check_measure(self):
        for sub in self:
            if sub.aggregate != 'count' and not sub.measure_field_id:
                raise ValidationError(self.env._(
                    'Sub-value "%(name)s" aggregates a field, so it needs one.',
                    name=sub.name))

    @api.constrains('domain')
    def _check_domain(self):
        for sub in self:
            try:
                sub.item_id._eval_domain(sub.domain)
            except Exception as err:  # noqa: BLE001 - whatever it was, it is not a domain
                raise ValidationError(self.env._(
                    'The filter of sub-value "%(name)s" cannot be read: %(error)s',
                    name=sub.name, error=err)) from err

    def _payload(self, headline, period=None, focus=None, mine=False, filters=None):
        """This number, ready to draw under the card.

        ``headline`` is the card's own value, so a share can be worked out
        without asking the database for it a second time.
        """
        self.ensure_one()
        item = self.item_id
        Model = self.env.get(item.model_name)
        if Model is None:
            return None
        domain = (item.item_domain(period, focus=focus, mine=mine, filters=filters)
                  + item._eval_domain(self.domain))
        value = self._aggregate(Model, domain)
        share = None
        if self.show_share and headline:
            share = round(value / headline * 100, 1)
        return {
            'id': self.id,
            'label': self.name,
            'value': value,
            'color': self.color or 'slate',
            'share': share,
        }

    def _aggregate(self, Model, domain):
        """The number itself, under this sub-value's own aggregate."""
        self.ensure_one()
        item = self.item_id
        if self.aggregate == 'count' or not self.measure_name:
            return float(item._count(Model, domain))
        spec = '%s:%s' % (self.measure_name,
                          self.aggregate if self.aggregate in ('sum', 'avg', 'max', 'min')
                          else 'sum')
        return item._memoised(
            ('agg', Model._name, spec, repr(domain)),
            lambda: (lambda rows: float(rows[0][0] or 0.0) if rows else 0.0)(
                Model._read_group(domain, [], [spec])))

    def _drill_domain(self, period=None, focus=None, mine=False, filters=None):
        """Exactly the records this small number counted."""
        self.ensure_one()
        item = self.item_id
        return (item.item_domain(period, focus=focus, mine=mine, filters=filters)
                + item._eval_domain(self.domain))
