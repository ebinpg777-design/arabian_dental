# -*- coding: utf-8 -*-
"""Dynamic Dashboards - give a board a menu of its own.

A dashboard people use every day should not live two clicks inside another
app's menu. This wizard puts it where its readers will look for it: under
Dashboards, under any menu that already exists - Sales, Inventory, whatever
the board is about - or as an app of its own on the top bar.

The menu is a real `ir.ui.menu` pointing at a real client action, so it
behaves like every other menu in the database: it can be reordered, renamed,
restricted, and it survives an upgrade of this module untouched. Re-running
the wizard on a board that already has one *moves* that menu rather than
leaving a second copy behind.
"""
from odoo import api, fields, models
from odoo.exceptions import UserError

PLACEMENTS = [
    ('dashboards', 'Under Dashboards'),
    ('menu', 'Under another menu'),
    ('app', 'As an app of its own'),
]


class DashboardMenuWizard(models.TransientModel):
    _name = 'dashboard.menu.wizard'
    _description = 'Add a Dashboard to the Menu'

    board_id = fields.Many2one('dashboard.board', required=True, ondelete='cascade')
    name = fields.Char(string='Menu Name', required=True)
    placement = fields.Selection(PLACEMENTS, required=True, default='dashboards')
    parent_id = fields.Many2one(
        'ir.ui.menu', string='Inside',
        help="Any menu already in the database - the dashboard is added under it.")
    sequence = fields.Integer(
        string='Position', default=20,
        help="Lower numbers come first among the entries beside it.")
    restrict = fields.Boolean(
        string='Only Its Groups',
        help="Shows the menu only to the groups the dashboard itself is "
             "restricted to. Without it the entry is visible to everyone - "
             "though the board behind it still is not.")
    has_groups = fields.Boolean(compute='_compute_has_groups')
    existing_menu_id = fields.Many2one('ir.ui.menu', compute='_compute_existing')
    existing_path = fields.Char(compute='_compute_existing')

    @api.depends('board_id')
    def _compute_has_groups(self):
        for wizard in self:
            wizard.has_groups = bool(wizard.board_id.group_ids)

    @api.depends('board_id')
    def _compute_existing(self):
        for wizard in self:
            menu = wizard.board_id.sudo().menu_id
            wizard.existing_menu_id = menu
            wizard.existing_path = menu.complete_name if menu else ''

    @api.model
    def default_get(self, fields_list):
        """Open on the board's own name, and on where its menu already is."""
        values = super().default_get(fields_list)
        board = self.env['dashboard.board'].browse(values.get('board_id')
                                                   or self.env.context.get('default_board_id'))
        if not board.exists():
            return values
        menu = board.sudo().menu_id
        values.setdefault('name', menu.name or board.name)
        values.setdefault('restrict', bool(board.group_ids))
        if menu:
            values['sequence'] = menu.sequence
            if not menu.parent_id:
                values['placement'] = 'app'
            elif menu.parent_id == self.env.ref('ebshel_dashboard.menu_dashboard_root',
                                                raise_if_not_found=False):
                values['placement'] = 'dashboards'
            else:
                values['placement'] = 'menu'
                values['parent_id'] = menu.parent_id.id
        return values

    @api.onchange('placement')
    def _onchange_placement(self):
        if self.placement != 'menu':
            self.parent_id = False

    def action_apply(self):
        """Create the menu, or move the one this board already has."""
        self.ensure_one()
        board = self.board_id
        if not board._is_dashboard_manager():
            raise UserError(self.env._('Only a dashboard manager can add a menu.'))
        if board.owner_id:
            raise UserError(self.env._(
                'A personal dashboard cannot go in the menu: publish it first, '
                'so the people who see the menu can open what is behind it.'))
        if self.placement == 'menu' and not self.parent_id:
            raise UserError(self.env._('Pick the menu to put it under.'))
        if self.placement == 'menu':
            parent = self.parent_id
        elif self.placement == 'dashboards':
            parent = self.env.ref('ebshel_dashboard.menu_dashboard_root')
        else:
            parent = False   # an app of its own, on the top bar
        board.sudo()._install_menu(
            name=(self.name or board.name).strip(),
            parent=parent,
            sequence=self.sequence,
            restrict=self.restrict,
        )
        # The menus are read once per session: without a reload the entry is
        # in the database but not yet on the reader's screen.
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_remove(self):
        self.ensure_one()
        self.board_id.action_remove_menu()
        return {'type': 'ir.actions.client', 'tag': 'reload'}
