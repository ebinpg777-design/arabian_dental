# -*- coding: utf-8 -*-
"""Build a dashboard from a model, in one step.

The work is :meth:`dashboard.board.build_from_model`; this wizard is the door
to it: pick a model, give the board a name if the suggested one will not do,
and open the result.
"""
from odoo import api, fields, models


class DashboardBuild(models.TransientModel):
    _name = 'dashboard.build'
    _description = 'Build a Dashboard from a Model'

    model_id = fields.Many2one(
        'ir.model', string='Model', required=True, ondelete='cascade',
        domain=[('transient', '=', False), ('abstract', '=', False)],
        help="The records the dashboard is about: Sales Orders, Tasks, Contacts...")
    name = fields.Char(string='Dashboard Name')
    hint = fields.Char(compute='_compute_hint')

    @api.depends('model_id')
    def _compute_hint(self):
        for wizard in self:
            wizard.hint = ''
            if wizard.model_id and wizard.model_id.model in self.env:
                Model = self.env[wizard.model_id.model]
                date, split, _second, user, measure = self.env['dashboard.board']._pick_fields(Model)
                found = []
                for name, what in ((date, self.env._('a business date')),
                                   (split, self.env._('a status to split by')),
                                   (user, self.env._('a responsible user')),
                                   (measure, self.env._('an amount to sum'))):
                    if name:
                        label = Model._fields[name].string
                        found.append(f'{what} ({label})')
                wizard.hint = (self.env._('Found: %(what)s.', what=', '.join(found))
                               if found else self.env._('Only a count can be built for this model.'))

    def action_build(self):
        self.ensure_one()
        board = self.env['dashboard.board'].build_from_model(
            self.model_id.model, name=self.name or None)
        return board.action_open_board()
