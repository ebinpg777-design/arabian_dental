# -*- coding: utf-8 -*-
"""Dynamic Dashboards - one variable of a formula card.

A formula card is a number made of other numbers: ``a / b * 100`` is a win
rate when ``a`` counts the won orders and ``b`` counts them all. Each variable
is an aggregate of the card's model over its own filter, read under the same
period, focus and "mine" switch as the card, so the ratio moves with the page.
"""
from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .dashboard_item import AGGREGATES, VARIABLE_NAMES


class DashboardVariable(models.Model):
    _name = 'dashboard.item.variable'
    _description = 'Dashboard Formula Variable'
    _order = 'sequence, id'

    item_id = fields.Many2one(
        'dashboard.item', string='Card', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(
        string='Letter', required=True, default='a', size=1,
        help="The letter the formula uses for this number: a, b, c...")
    label = fields.Char(string='Meaning', help="What the number is, for the card's hint.")
    domain = fields.Char(
        string='Filter', default='[]', required=True,
        help="Records this number is about - in addition to the card's own filter.")
    aggregate = fields.Selection(AGGREGATES, default='count', required=True)
    measure_field_id = fields.Many2one(
        'ir.model.fields', string='Measure',
        domain="[('model_id', '=', parent.model_id), "
               "('ttype', 'in', ['integer', 'float', 'monetary']), ('store', '=', True)]",
        ondelete='cascade')
    measure_name = fields.Char(related='measure_field_id.name')

    @api.constrains('name', 'item_id')
    def _check_name(self):
        for variable in self:
            name = (variable.name or '').strip().lower()
            if len(name) != 1 or name not in VARIABLE_NAMES:
                raise ValidationError(self.env._(
                    'A variable is one letter, a to z: "%(name)s" is not.', name=variable.name))
            twins = variable.item_id.variable_ids.filtered(
                lambda other: other != variable and (other.name or '').strip().lower() == name)
            if twins:
                raise ValidationError(self.env._(
                    'Two variables of "%(card)s" are both called "%(name)s".',
                    card=variable.item_id.name, name=name))

    @api.constrains('aggregate', 'measure_field_id')
    def _check_measure(self):
        for variable in self:
            if variable.aggregate != 'count' and not variable.measure_field_id:
                raise ValidationError(self.env._(
                    'Variable "%(name)s" aggregates a field, so it needs one.',
                    name=variable.name))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name'):
                vals['name'] = vals['name'].strip().lower()
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('name'):
            vals['name'] = vals['name'].strip().lower()
        return super().write(vals)

    def _value(self, Model, period, focus, mine):
        """This variable's number, under the card's own restrictions."""
        self.ensure_one()
        item = self.item_id
        domain = item.item_domain(period, focus=focus, mine=mine) + item._eval_domain(self.domain)
        if self.aggregate == 'count' or not self.measure_name:
            return float(Model.search_count(domain))
        spec = f'{self.measure_name}:{self.aggregate}'
        rows = Model._read_group(domain, [], [spec])
        return float(rows[0][0] or 0.0) if rows else 0.0
