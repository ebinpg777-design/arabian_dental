# -*- coding: utf-8 -*-
"""Dynamic Dashboards - the board itself.

A ``dashboard.board`` is a named page of cards. It owns what every card on it
has in common: the period they are read over, how often the page refreshes,
how dense the grid is, and who may see it.

The browser asks for one board at a time through :meth:`DashboardBoard.read_board`,
which returns the whole page - definition and numbers - in a single call.
"""
import base64
import json
import logging
import re

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .dashboard_item import (
    BOARD_COLUMNS, CARD_COLORS, DATE_FIELD_CANDIDATES, DATE_TYPES, NUMERIC_TYPES, PERIODS,
    USER_FIELD_CANDIDATES,
)

_logger = logging.getLogger(__name__)

# Kept under its old name: the periods are the card's, the board only picks one.
BOARD_PERIODS = PERIODS

# Auto-refresh choices, in seconds. 0 is off - the default, because a board
# nobody asked to refresh should not keep a database busy.
REFRESH_CHOICES = [
    ('0', 'Off'),
    ('15', 'Every 15 seconds'),
    ('30', 'Every 30 seconds'),
    ('45', 'Every 45 seconds'),
    ('60', 'Every minute'),
    ('120', 'Every 2 minutes'),
    ('300', 'Every 5 minutes'),
    ('600', 'Every 10 minutes'),
    ('900', 'Every 15 minutes'),
]

# Colour ramps a board's charts can take. The hex values live in
# static/src/core/board_colors.js; these are the names the form offers.
PALETTES = [
    ('default', 'Default'),
    ('cool', 'Cool'),
    ('warm', 'Warm'),
    ('neon', 'Neon'),
    ('sunset', 'Sunset'),
    ('mono', 'One colour, shaded'),
]

# How many columns the grid has. Twelve is the default and what card widths
# are expressed in; six and eight are for wall displays and simple boards.
GRID_CHOICES = [('6', '6 columns'), ('8', '8 columns'), ('12', '12 columns')]
DENSITIES = [('comfortable', 'Comfortable'), ('compact', 'Compact')]

# Version stamp of an exported board file. Bumped when the shape changes.
EXPORT_VERSION = 2

# Fields a generated board prefers to split by, best first.
SPLIT_HINTS = {
    'stage_id': 0, 'state_id': 1, 'team_id': 2, 'category_id': 3, 'categ_id': 3,
    'type_id': 3, 'country_id': 4, 'partner_id': 5, 'product_id': 5, 'company_id': 6,
}
# Numeric fields worth summing on a generated board, best first.
MEASURE_HINTS = (
    'amount_total', 'amount_untaxed', 'price_total', 'price_subtotal', 'expected_revenue',
    'quantity', 'product_qty', 'product_uom_qty', 'planned_hours', 'allocated_hours',
    'duration', 'worked_hours',
)
# Numeric fields that are never a measure anybody wants.
MEASURE_NOISE = ('id', 'sequence', 'color', 'priority', 'message_bounce', 'rating_count')


class DashboardBoard(models.Model):
    _name = 'dashboard.board'
    _description = 'Dashboard'
    _order = 'sequence, name, id'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10, index=True)
    active = fields.Boolean(default=True)
    icon = fields.Char(default='fa-tachometer', help="FontAwesome icon of the board.")
    color = fields.Selection(CARD_COLORS, default='indigo', required=True)
    description = fields.Text(translate=True, help="Shown under the board title.")

    item_ids = fields.One2many('dashboard.item', 'board_id', string='Cards', copy=True)
    item_count = fields.Integer(compute='_compute_item_count', string='Card Count')
    tab_ids = fields.One2many('dashboard.tab', 'board_id', string='Tabs', copy=True)
    filter_ids = fields.One2many('dashboard.filter', 'board_id', string='Filters', copy=True)
    allow_range = fields.Boolean(
        string='Custom Date Range', default=True,
        help="Lets the reader pick any two dates as the period.")

    period = fields.Selection(
        BOARD_PERIODS, default='this_month', required=True, string='Default Period',
        help="Period the board opens on. Cards without a period field ignore it.")
    allow_period = fields.Boolean(
        string='Period Selector', default=True,
        help="Lets the reader change the period from the board itself.")
    auto_refresh = fields.Selection(
        REFRESH_CHOICES, default='0', required=True, string='Auto Refresh')
    columns = fields.Selection(
        GRID_CHOICES, default='12', required=True, string='Grid',
        help="How many columns the board is laid out on. Card widths are always "
             "given out of twelve and scaled to the grid.")
    density = fields.Selection(
        DENSITIES, default='comfortable', required=True,
        help="Compact tightens spacing and type for boards with many cards.")
    allow_mine = fields.Boolean(
        string='"Only Mine" Switch', default=True,
        help="Offers the reader a switch that narrows every card with a user "
             "field to their own records.")
    palette = fields.Selection(
        PALETTES, default='default', required=True,
        help="The colour ramp the charts of this board take.")
    slide_seconds = fields.Integer(
        string='Slideshow', default=0,
        help="In presentation mode, move to the next dashboard after this many "
             "seconds. 0 stays on this one.")
    menu_path = fields.Char(string='In the Menu', compute='_compute_menu_path')
    menu_id = fields.Many2one(
        'ir.ui.menu', string='Menu Item', copy=False, ondelete='set null', readonly=True,
        help="A menu entry of its own, created with the button beside.")
    menu_action_id = fields.Many2one(
        'ir.actions.client', copy=False, ondelete='set null', readonly=True)

    owner_id = fields.Many2one(
        'res.users', string='Owner', ondelete='cascade', index=True,
        help="Empty means the board is shared with everybody. Set means it is "
             "personal: only its owner sees it.")
    group_ids = fields.Many2many(
        'res.groups', string='Restricted To',
        help="Shared board: only these groups see it. Empty means everybody.")
    company_ids = fields.Many2many(
        'res.company', 'dashboard_board_company_rel', 'board_id', 'company_id',
        string='Companies',
        help="Shown only while one of these companies is active in the "
             "switcher. Empty means every company.")
    # The single company of 1.1: read once by the 1.2 migration, then left
    # alone. Kept so an upgrade never drops a value it has not copied yet.
    company_id = fields.Many2one('res.company', string='Company (1.1)', readonly=True)

    @api.depends('menu_id')
    def _compute_menu_path(self):
        for board in self:
            menu = board.sudo().menu_id
            board.menu_path = menu.complete_name if menu else ''

    def _compute_item_count(self):
        counts = {}
        if self.ids:
            grouped = self.env['dashboard.item']._read_group(
                [('board_id', 'in', self.ids)], ['board_id'], ['__count'])
            counts = {board.id: count for board, count in grouped}
        for board in self:
            board.item_count = counts.get(board.id, 0)

    def _is_dashboard_manager(self):
        return self.env.user.has_group('ebshel_dashboard.group_dashboard_manager')

    @api.model_create_multi
    def create(self, vals_list):
        """Only the role builds dashboards.

        The gate is here rather than in the access rights alone so that every
        path says the same thing - the wizard, the copy button, an import, a
        bare RPC. Without the role a reader cannot make a board at all, and so
        never owns one they could then edit either.
        """
        if not self._is_dashboard_manager():
            raise AccessError(self.env._(
                'Building dashboards needs the Dashboards / Manager role. '
                'Ask an administrator for it.'))
        return super().create(vals_list)

    def write(self, vals):
        """A dashboard is changed by whoever may build on it."""
        for board in self:
            if not board._can_edit():
                raise AccessError(self.env._(
                    'You cannot change "%(name)s".', name=board.name))
        return super().write(vals)

    @api.constrains('group_ids', 'owner_id')
    def _check_groups(self):
        for board in self:
            if board.owner_id and board.group_ids:
                raise ValidationError(self.env._(
                    'A personal board is already restricted to its owner: '
                    'remove the groups, or publish it first.'))

    def copy_data(self, default=None):
        """Name a copy for what it is.

        `super()` already puts the original name in the values, so this
        overwrites rather than defaults - unless the caller named the copy
        itself, which wins.
        """
        vals_list = super().copy_data(default=default)
        if default and 'name' in default:
            return vals_list
        for board, vals in zip(self, vals_list):
            vals['name'] = self.env._('%(name)s (copy)', name=board.name)
        return vals_list

    def copy(self, default=None):
        """Copy the board, then re-tie the copied cards to the copied tabs:
        the one2many copies come out with the *original* tab ids."""
        new = super().copy(default=default)
        for board, copy in zip(self, new):
            by_name = {tab.name: tab for tab in copy.tab_ids}
            for item in copy.item_ids.filtered('tab_id'):
                if item.tab_id.board_id != copy:
                    item.tab_id = by_name.get(item.tab_id.name, False)
        return new

    def rename(self, name):
        """Give this dashboard another name, from the page showing it."""
        self.ensure_one()
        if not self._can_edit():
            raise UserError(self.env._('You cannot change a dashboard you do not own.'))
        name = (name or '').strip()
        if not name:
            raise UserError(self.env._('A dashboard needs a name.'))
        self.write({'name': name})
        # The menu entry, if it has one, is the same board by another name.
        if self.menu_id:
            self.sudo().menu_id.name = name
            self.sudo().menu_action_id.name = name
            self.env.registry.clear_cache()
        return {'name': self.name, 'menu': bool(self.menu_id)}

    def set_icon(self, icon):
        """Give this dashboard another icon, from the page showing it."""
        self.ensure_one()
        if not self._can_edit():
            raise UserError(self.env._('You cannot change a dashboard you do not own.'))
        icon = (icon or '').strip()
        # Any FontAwesome 4 class is allowed - the picker offers a curated set,
        # the field takes the rest - but only a class: this ends up in markup.
        if not re.fullmatch(r'fa-[a-z0-9-]{1,40}', icon):
            raise UserError(self.env._(
                'An icon is a FontAwesome class, like "fa-line-chart".'))
        self.write({'icon': icon})
        return {'icon': self.icon}

    def add_tab(self, name):
        """A new page on this board, from the board itself."""
        self.ensure_one()
        if not self._can_edit():
            raise UserError(self.env._('You cannot change a dashboard you do not own.'))
        tab = self.env['dashboard.tab'].create({
            'board_id': self.id, 'name': (name or '').strip() or self.env._('New tab'),
            'sequence': (max(self.tab_ids.mapped('sequence')) + 10) if self.tab_ids else 10,
        })
        return tab._payload()

    def move_to_tab(self, item_id, tab_id):
        """Put a card on a tab (or back on the first page with a false tab)."""
        self.ensure_one()
        if not self._can_edit():
            raise UserError(self.env._('You cannot change a dashboard you do not own.'))
        item = self.item_ids.filtered(lambda i: i.id == int(item_id))
        tab = self.tab_ids.filtered(lambda t: t.id == int(tab_id)) if tab_id else False
        if item:
            item.tab_id = tab.id if tab else False
        return True

    # ------------------------------------------------------------------
    # What the browser reads
    # ------------------------------------------------------------------
    def _is_visible(self, user_groups):
        """True if this board should appear in the reader's board list.

        Two gates: the groups the board is restricted to, and the companies.
        A board with companies is shown only while one of them is active in
        the company switcher; with none it is shown everywhere.
        """
        self.ensure_one()
        if self.group_ids and not (self.group_ids & user_groups):
            return False
        if self.company_ids and not (self.company_ids & self.env.companies):
            return False
        return True

    def _board_payload(self, full=True, default_id=None):
        """The board as the page needs it.

        ``full=False`` is the sidebar's version: the same facts minus the
        filter bar's dropdown values, which cost a group-by query per filter
        and only matter for the board actually on screen. ``default_id`` is
        the reader's favourite, looked up once by the caller rather than once
        per board here.
        """
        if default_id is None:
            default_id = self.env['dashboard.preference']._default_board_id()
        self.ensure_one()
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description or '',
            'icon': self.icon or 'fa-tachometer',
            'color': self.color,
            'period': self.period,
            'allow_period': self.allow_period,
            'allow_mine': self.allow_mine,
            'auto_refresh': int(self.auto_refresh or '0'),
            'columns': int(self.columns or BOARD_COLUMNS),
            'density': self.density or 'comfortable',
            'palette': self.palette or 'default',
            'slide_seconds': self.slide_seconds or 0,
            'has_menu': bool(self.menu_id),
            'allow_range': self.allow_range,
            'tabs': [tab._payload() for tab in self.tab_ids.sorted(lambda t: (t.sequence, t.id))],
            'filters': [flt._payload() for flt in self.filter_ids.sorted(lambda f: (f.sequence, f.id))]
                       if full else [],
            'personal': bool(self.owner_id),
            'companies': self.company_ids.mapped('name'),
            'mine': self.owner_id.id == self.env.uid,
            'can_edit': self._can_edit(),
            'is_default': self.id == default_id,
            'item_count': len(self.item_ids),
        }

    def unlink(self):
        """Delete a dashboard, and take with it what only it was holding.

        Its cards, tabs and filters go by themselves - they cascade. Its menu
        and the client action behind that menu do not: left alone they stay in
        everybody's menu bar, pointing at a board that is not there any more.
        """
        for board in self:
            if not board._can_edit():
                raise AccessError(self.env._(
                    'You cannot delete "%(name)s": it is not yours to change.',
                    name=board.name))
        menus = self.sudo().menu_id
        actions = self.sudo().menu_action_id
        # Favourites and watchlists clear themselves: those columns are
        # `ondelete='set null'`.
        result = super().unlink()
        menus.unlink()
        actions.unlink()
        if menus:
            self.env.registry.clear_cache()
        return result

    @api.model
    def delete_board(self, board_id):
        """Remove a dashboard from the page that is showing it.

        Answers with the board to open next - the reader is standing on the
        one that just went, and an empty page is not an answer.
        """
        board = self.browse(int(board_id)).exists()
        if not board:
            return {'next': False}
        name = board.name
        board.unlink()
        following = self._default_board()
        return {'next': following.id if following else False, 'name': name}

    def _can_edit(self):
        """Whether this reader may build on this board at all.

        Building a dashboard is a job, not a right everybody has: it needs
        the *Dashboards / Manager* role. Beyond that, a personal board is its
        owner's alone - even another manager only reads it - while a shared
        board is open to every manager.

        Everyone else reads. They can open a board, narrow it, click through
        to the records and take a PDF of it; they cannot move a card, change
        one, or make one.
        """
        self.ensure_one()
        if not self._is_dashboard_manager():
            return False
        if self.owner_id:
            return self.owner_id.id == self.env.uid
        return True

    def _is_default(self):
        self.ensure_one()
        return self.env['dashboard.preference']._default_board_id() == self.id

    @api.model
    def get_boards(self):
        """Every board this user may open, in display order.

        Group restrictions are applied here rather than by a record rule: a
        board restricted to a group is not *secret*, it is simply not offered
        to people it was not built for.
        """
        try:
            boards = self.search([])
        except AccessError:
            return []
        user_groups = self._user_groups()
        default_id = self.env['dashboard.preference']._default_board_id()
        return [board._board_payload(full=False, default_id=default_id) for board in boards
                if board._is_visible(user_groups)]

    def _user_groups(self):
        return self.env.user.all_group_ids

    @api.model
    def read_board(self, board_id=None, period=None, focus=None, mine=False, with_values=True,
                   filters=None):
        """One whole board - definition, cards and their numbers - in one call.

        With ``with_values=False`` the cards come back as definitions only,
        each flagged ``pending``: the page can draw its whole layout at once
        and ask for the numbers afterwards, in chunks, through
        :meth:`board_values`. On a table of two hundred thousand rows that is
        the difference between a blank page for a second and a dashboard that
        is there immediately, filling in.

        Everything is read with the caller's own rights. A card the caller
        cannot compute comes back flagged, so the rest of the board still
        renders around the hole. ``focus`` is the board-wide focus (see
        :meth:`dashboard.item._focus_leaves`); ``mine`` the reader's own
        "only mine" switch.
        """
        board = self.browse(int(board_id)).exists() if board_id else self._default_board()
        if not board:
            return {'board': None, 'items': [], 'boards': self.get_boards(),
                    'watchlist': [], 'note': '', 'user_name': self.env.user.name}
        board.check_access('read')
        if not board._is_visible(board._user_groups()):
            raise AccessError(self.env._('This dashboard was not shared with you.'))
        if isinstance(period, dict) and not board.allow_range:
            period = board.period
        period = period or board.period
        mine = bool(mine and board.allow_mine)
        # One memo for the whole page: cards that ask the same question of the
        # same model get one query between them (see `_memoised`).
        board = board.with_context(dashboard_memo={})
        items = []
        for item in board.item_ids.sorted(lambda i: (i.sequence, i.id)):
            if not item._visible_to_reader():
                continue
            if with_values:
                items.append(item.compute_values(period, focus=focus, mine=mine, filters=filters))
            else:
                items.append(dict(item._payload(), pending=True, value=0.0,
                                  period=period if isinstance(period, dict) else (period or 'all')))
        return {
            'board': dict(board._board_payload(), period=period),
            'items': items,
            'boards': self.get_boards(),
            'periods': [{'key': key, 'label': str(label)} for key, label in BOARD_PERIODS],
            'focus': focus if isinstance(focus, dict) else None,
            'mine': mine,
            'filters': [f for f in (filters or []) if isinstance(f, dict)],
            'can_build': self._is_dashboard_manager(),
            'watchlist': self.env['dashboard.preference'].watchlist_payload(),
            'note': self.env['dashboard.note']._mine(board.id).body or '',
            'views': self.env['dashboard.view'].list_views(board.id),
            'user_name': self.env.user.name,
        }

    @api.model
    def board_values(self, board_id, item_ids, period=None, focus=None, mine=False, filters=None):
        """The numbers for some of a board's cards.

        The second half of a progressive load: the page has the layout, this
        fills in a handful of cards at a time. Same rights, same memo - a
        chunk shares one set of queries.
        """
        board = self.browse(int(board_id)).exists()
        if not board:
            return []
        board.check_access('read')
        if not board._is_visible(board._user_groups()):
            raise AccessError(self.env._('This dashboard was not shared with you.'))
        if isinstance(period, dict) and not board.allow_range:
            period = board.period
        period = period or board.period
        mine = bool(mine and board.allow_mine)
        wanted = {int(one) for one in (item_ids or [])}
        board = board.with_context(dashboard_memo={})
        values = []
        for item in board.item_ids.sorted(lambda i: (i.sequence, i.id)):
            if item.id not in wanted or not item._visible_to_reader():
                continue
            values.append(item.compute_values(period, focus=focus, mine=mine, filters=filters))
        return values

    def _default_board(self):
        """The board opened when none was asked for.

        The reader's favourite first, then the first visible one.
        """
        user_groups = self._user_groups()
        preferred = self.browse(self.env['dashboard.preference']._default_board_id()).exists()
        if preferred and preferred._is_visible(user_groups):
            try:
                preferred.check_access('read')
                return preferred
            except AccessError:
                pass
        for board in self.search([]):
            if board._is_visible(user_groups):
                return board
        return self.browse()

    def set_default(self, on=True):
        """Make this board the reader's favourite - the one that opens first."""
        self.ensure_one()
        self.env['dashboard.preference']._set_default_board(self.id if on else False)
        return True

    # ------------------------------------------------------------------
    # Editing from the board itself
    # ------------------------------------------------------------------
    def arrange_items(self, layout):
        """Persist a drag-and-drop rearrangement.

        ``layout`` is the list of card ids in their new order, as read back
        from the DOM after the drop. Ids that do not belong to this board are
        ignored rather than trusted.
        """
        self.ensure_one()
        if not self._can_edit():
            raise UserError(self.env._('You cannot rearrange a dashboard you do not own.'))
        mine = set(self.item_ids.ids)
        sequence = 10
        for raw_id in layout or []:
            item_id = int(raw_id)
            if item_id not in mine:
                continue
            self.env['dashboard.item'].browse(item_id).sequence = sequence
            sequence += 10
        return True

    def restore_layout(self, layout):
        """Put the cards back where they were.

        Arranging a board writes as you drag - that is what makes it feel
        immediate - so "discard" cannot mean "do not save". It means: here is
        the arrangement from before you started, put it back. Each entry is
        `{id, sequence, width, height, tab_id}`; ids from another board are
        ignored rather than trusted.
        """
        self.ensure_one()
        if not self._can_edit():
            raise UserError(self.env._('You cannot change a dashboard you do not own.'))
        by_id = {item.id: item for item in self.item_ids}
        for entry in layout or []:
            item = by_id.get(int(entry.get('id') or 0))
            if not item:
                continue
            tab_id = int(entry['tab_id']) if entry.get('tab_id') else False
            if tab_id and tab_id not in self.tab_ids.ids:
                tab_id = False   # not a tab of this board: back to the overview
            item.write({
                'sequence': int(entry.get('sequence') or item.sequence),
                'width': int(entry.get('width') or item.width),
                'height': int(entry.get('height') or item.height),
                'tab_id': tab_id,
            })
        return True

    def resize_item(self, item_id, width, height=None):
        """Persist a card resize made by dragging its edge."""
        self.ensure_one()
        if not self._can_edit():
            raise UserError(self.env._('You cannot resize a dashboard you do not own.'))
        item = self.item_ids.filtered(lambda i: i.id == int(item_id))
        if not item:
            return False
        values = {'width': max(1, min(int(width), BOARD_COLUMNS))}
        if height is not None:
            values['height'] = int(height)
        item.write(values)
        return True

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_open_board(self):
        """Open this board as its client action."""
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'ebshel_dashboard.board',
            'name': self.name,
            'params': {'board_id': self.id},
            'context': {'dashboard_board_id': self.id},
        }

    @api.model
    def action_open_dashboards(self):
        """Menu entry: open the reader's favourite, or the first board they can see."""
        board = self._default_board()
        if not board:
            return {
                'type': 'ir.actions.client',
                'tag': 'ebshel_dashboard.board',
                'name': self.env._('Dashboards'),
                'params': {},
            }
        return board.action_open_board()

    def action_create_menu(self):
        """Ask where the menu should go, and what it should be called."""
        self.ensure_one()
        if not self._is_dashboard_manager():
            raise UserError(self.env._('Only a dashboard manager can add a menu.'))
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Add to the Menu'),
            'res_model': 'dashboard.menu.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_board_id': self.id},
        }

    def _install_menu(self, name=None, parent=None, sequence=20, restrict=False):
        """Put this board in the menu, or move the entry it already has.

        ``parent`` reads three ways on purpose: left out, the entry goes under
        Dashboards where it belongs by default; a menu record puts it under
        that menu; and an explicit ``False`` makes it an app of its own on the
        top bar.

        One board, one menu: running this again renames and moves what is
        there rather than leaving a second entry behind. The client action
        belongs to the board too, so removing the menu takes it with it.
        """
        self.ensure_one()
        if parent is None:
            parent = self.env.ref('ebshel_dashboard.menu_dashboard_root')
        if self.owner_id:
            raise UserError(self.env._(
                'A personal dashboard cannot go in the menu: publish it first.'))
        name = (name or self.name).strip()
        Menu = self.env['ir.ui.menu'].sudo()
        action = self.menu_action_id.sudo()
        if action:
            action.write({'name': name, 'params': {'board_id': self.id}})
        else:
            action = self.env['ir.actions.client'].sudo().create({
                'name': name,
                'tag': 'ebshel_dashboard.board',
                'params': {'board_id': self.id},
            })
        values = {
            'name': name,
            'parent_id': parent.id if parent else False,
            'action': 'ir.actions.client,%s' % action.id,
            'sequence': sequence or 20,
        }
        # A top menu shows an icon on the app switcher; a child never does.
        if not parent:
            values['web_icon'] = 'ebshel_dashboard,static/description/icon.png'
        # 19.0 calls the menu's groups `group_ids`; 18.0 `groups_id`.
        groups_field = 'group_ids' if 'group_ids' in Menu._fields else 'groups_id'
        values[groups_field] = [(6, 0, self.group_ids.ids if (restrict and self.group_ids) else [])]
        menu = self.menu_id.sudo()
        if menu:
            menu.write(values)
        else:
            menu = Menu.create(values)
        self.sudo().write({'menu_id': menu.id, 'menu_action_id': action.id})
        # Menus are cached per session; without this the entry exists but is
        # not served until the next restart.
        self.env.registry.clear_cache()
        return menu

    def action_remove_menu(self):
        self.ensure_one()
        if not self._is_dashboard_manager():
            raise UserError(self.env._('Only a dashboard manager can remove a menu.'))
        self.sudo().menu_id.unlink()
        self.sudo().menu_action_id.unlink()
        self.env.registry.clear_cache()
        return True

    def action_view_items(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Cards of %(name)s', name=self.name),
            'res_model': 'dashboard.item',
            'domain': [('board_id', '=', self.id)],
            'context': {'default_board_id': self.id},
            'views': [(False, 'list'), (False, 'form')],
        }

    def action_publish(self):
        """Turn a personal board into a shared one."""
        if not self._is_dashboard_manager():
            raise UserError(self.env._('Only a dashboard manager can publish a board.'))
        self.write({'owner_id': False})
        return True

    def action_copy_to_me(self):
        """Take a personal copy of a shared board."""
        self.ensure_one()
        copy = self.copy({'owner_id': self.env.uid, 'group_ids': [(5, 0, 0)]})
        return copy.action_open_board()

    # ------------------------------------------------------------------
    # Building a board from a model
    # ------------------------------------------------------------------
    @api.model
    def _pick_fields(self, Model):
        """The fields a generated board is built on.

        Returns ``(date, split, second_split, user, measure)`` as field names,
        any of which may be ``None``. Guesses are ranked: a ``state`` selection
        beats any other split, a monetary field beats any other measure, the
        model's business date beats ``create_date``.
        """
        all_fields = Model._fields

        def stored(name, types=None):
            field = all_fields.get(name)
            if field is None or not field.store:
                return None
            if types and field.type not in types:
                return None
            return field

        date = next((name for name in DATE_FIELD_CANDIDATES if stored(name, DATE_TYPES)), None)
        if not date:
            date = next((name for name, field in all_fields.items()
                         if field.store and field.type in DATE_TYPES and name != 'write_date'),
                        None)

        splits = []
        for name, field in all_fields.items():
            if not field.store:
                continue
            if field.type == 'selection':
                try:
                    selection = field._description_selection(self.env)
                except Exception:  # noqa: BLE001 - a broken selection is not a candidate
                    continue
                if 2 <= len(selection) <= 12:
                    splits.append((0 if name == 'state' else 1, len(selection), name))
            elif field.type == 'many2one' and name in SPLIT_HINTS:
                splits.append((SPLIT_HINTS[name], 0, name))
        splits.sort()
        split = splits[0][2] if splits else None
        second = splits[1][2] if len(splits) > 1 else None

        user = next((name for name in USER_FIELD_CANDIDATES
                     if stored(name, ('many2one',))
                     and all_fields[name].comodel_name == 'res.users'), None)

        measure = next((name for name, field in all_fields.items()
                        if field.store and field.type == 'monetary'), None)
        if not measure:
            measure = next((name for name in MEASURE_HINTS if stored(name, NUMERIC_TYPES)), None)
        if not measure:
            measure = next((name for name, field in all_fields.items()
                            if field.store and field.type == 'float'
                            and name not in MEASURE_NOISE and not name.startswith('x_')), None)
        return date, split, second, user, measure

    @api.model
    def build_from_model(self, model_name, name=None):
        """A ready-made board for one model, built from what its fields suggest.

        Not a template: the cards are chosen from the model itself - a count,
        the measure it carries, its status field, its business date, the user
        it is assigned to - so a board on Sales Orders and one on Tasks come
        out different, and both make sense on first opening.
        """
        Model = self.env.get(model_name)
        if Model is None or Model._transient or Model._abstract:
            raise UserError(self.env._('There is no such model to build a dashboard on.'))
        Model.check_access('read')
        model = self.env['ir.model'].sudo().search([('model', '=', model_name)], limit=1)
        Field = self.env['ir.model.fields'].sudo()

        def field_id(field_name):
            if not field_name:
                return False
            return Field.search([('model', '=', model_name), ('name', '=', field_name)],
                                limit=1).id

        def label(field_name):
            field = Model._fields.get(field_name)
            return str(field.string) if field is not None else field_name

        date, split, second, user, measure = self._pick_fields(Model)
        records = model.name or model_name
        board = self.create({
            'name': name or self.env._('%(model)s Overview', model=records),
            'description': self.env._('Built from the %(model)s model.', model=records),
            'icon': 'fa-tachometer',
            'period': 'this_month' if date else 'all',
        })
        cards = []
        sequence = [10]

        def add(**values):
            values.update(board_id=board.id, model_id=model.id, sequence=sequence[0])
            sequence[0] += 10
            cards.append(values)

        add(name=self.env._('All %(model)s', model=records), kind='kpi', aggregate='count',
            icon='fa-database', color='indigo', width=3)
        if date:
            add(name=self.env._('New %(model)s', model=records), kind='kpi', aggregate='count',
                date_field_id=field_id(date), compare=True, icon='fa-plus-circle',
                color='sky', width=3)
        if measure:
            add(name=self.env._('Total %(field)s', field=label(measure)), kind='kpi',
                aggregate='sum', measure_field_id=field_id(measure),
                date_field_id=field_id(date), compare=bool(date), show_count=True,
                icon='fa-money', color='emerald', width=3, digits=2)
        if user:
            add(name=self.env._('My %(model)s', model=records), kind='kpi', aggregate='count',
                only_mine=True, user_field_id=field_id(user), date_field_id=field_id(date),
                icon='fa-user', color='violet', width=3)
        if date:
            add(name=self.env._('Over Time'), kind='area', aggregate='count',
                date_field_id=field_id(date), group_by_interval='month',
                icon='fa-area-chart', color='sky', width=8 if split else 12)
        if split:
            add(name=self.env._('By %(field)s', field=label(split)), kind='donut',
                aggregate='count', group_by_field_id=field_id(split), limit=6,
                icon='fa-pie-chart', color='amber', width=4)
        if user:
            add(name=self.env._('By %(field)s', field=label(user)), kind='hbar',
                aggregate='count', group_by_field_id=field_id(user), limit=8,
                date_field_id=field_id(date), icon='fa-bar-chart', color='orange', width=6)
        elif second:
            add(name=self.env._('By %(field)s', field=label(second)), kind='hbar',
                aggregate='count', group_by_field_id=field_id(second), limit=8,
                icon='fa-bar-chart', color='orange', width=6)
        add(name=self.env._('Top %(model)s', model=records), kind='list',
            aggregate='sum' if measure else 'count',
            measure_field_id=field_id(measure) if measure else False,
            date_field_id=field_id(date), limit=8, icon='fa-list', color='slate',
            width=6 if (user or second) else 12)
        self.env['dashboard.item'].create(cards)
        return board

    # ------------------------------------------------------------------
    # Export / import
    # ------------------------------------------------------------------
    def export_boards(self):
        """The selected boards as a portable JSON string."""
        boards = self or self.search([])
        payload = {
            'version': EXPORT_VERSION,
            'boards': [{
                'name': board.name,
                'description': board.description or '',
                'icon': board.icon,
                'color': board.color,
                'period': board.period,
                'allow_period': board.allow_period,
                'allow_mine': board.allow_mine,
                'auto_refresh': board.auto_refresh,
                'columns': board.columns,
                'density': board.density,
                'palette': board.palette,
                'slide_seconds': board.slide_seconds,
                'tabs': [{'name': tab.name, 'icon': tab.icon, 'sequence': tab.sequence}
                         for tab in board.tab_ids],
                'items': [item._export_dict() for item in board.item_ids],
            } for board in boards],
        }
        return json.dumps(payload, indent=2, default=str)

    def action_export_boards(self):
        """Download the selected boards as a JSON file.

        The file is handed over as an attachment rather than as a string in a
        notification: a board is meant to travel to another database, and a
        file is what travels.
        """
        boards = self or self.search([])
        if not boards:
            raise UserError(self.env._('There is no dashboard to export.'))
        stem = re.sub(r'[^A-Za-z0-9_-]+', '_', boards.name).strip('_') \
            if len(boards) == 1 else 'dashboards'
        attachment = self.env['ir.attachment'].create({
            'name': f"{stem or 'dashboard'}.json",
            'type': 'binary',
            'datas': base64.b64encode(boards.export_boards().encode('utf-8')),
            'mimetype': 'application/json',
            'res_model': 'dashboard.board',
            'res_id': boards[:1].id,
        })
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }

    @api.model
    def import_boards(self, payload, replace=False):
        """Create boards from an exported file.

        Cards whose model or fields do not exist here are skipped, and named in
        the result: importing a sales board into a database without Sales must
        say what it could not bring, not fail as a whole.
        """
        if isinstance(payload, str):
            payload = json.loads(payload)
        boards = payload.get('boards') if isinstance(payload, dict) else payload
        if not isinstance(boards, list):
            raise UserError(self.env._('This file does not contain a dashboard.'))
        Item = self.env['dashboard.item']
        Model = self.env['ir.model']
        Field = self.env['ir.model.fields'].sudo()
        created, skipped = [], []
        for entry in boards:
            model_names = {item.get('model') for item in entry.get('items') or []}
            missing = {name for name in model_names if name and name not in self.env}
            values = {key: entry.get(key) for key in
                      ('name', 'description', 'icon', 'color', 'period', 'allow_period',
                       'allow_mine', 'auto_refresh', 'columns', 'density', 'palette',
                       'slide_seconds')
                      if entry.get(key) is not None}
            values.setdefault('name', self.env._('Imported Dashboard'))
            if replace:
                self.search([('name', '=', values['name'])]).unlink()
            board = self.create(values)
            tabs_by_name = {}
            for raw_tab in entry.get('tabs') or []:
                tab = self.env['dashboard.tab'].create({
                    'board_id': board.id, 'name': raw_tab.get('name') or self.env._('Tab'),
                    'icon': raw_tab.get('icon') or 'fa-folder-o',
                    'sequence': raw_tab.get('sequence') or 10})
                tabs_by_name[tab.name] = tab.id
            for raw in entry.get('items') or []:
                model_name = raw.get('model')
                free = raw.get('kind') == 'text'
                if not free and (not model_name or model_name in missing):
                    skipped.append(f"{raw.get('name')} ({model_name})")
                    continue
                model = Model.sudo().search([('model', '=', model_name)], limit=1) \
                    if model_name else Model
                item_values = {key: raw[key] for key in Item._export_field_names()
                               if key in raw and key != 'tab_name'}
                item_values.update(board_id=board.id, model_id=model.id or False)
                if raw.get('tab_name') and raw['tab_name'] in tabs_by_name:
                    item_values['tab_id'] = tabs_by_name[raw['tab_name']]
                for source, target in (('measure', 'measure_field_id'),
                                       ('measure2', 'measure2_field_id'),
                                       ('group_by', 'group_by_field_id'),
                                       ('stack_by', 'stack_field_id'),
                                       ('date_field', 'date_field_id'),
                                       ('user_field', 'user_field_id')):
                    name = raw.get(source)
                    if not name:
                        continue
                    field = Field.search(
                        [('model', '=', model_name), ('name', '=', name)], limit=1)
                    if field:
                        item_values[target] = field.id
                variables = []
                for raw_variable in raw.get('variables') or []:
                    var_values = {key: raw_variable.get(key) for key in
                                  ('name', 'label', 'domain', 'aggregate')
                                  if raw_variable.get(key) is not None}
                    if raw_variable.get('measure'):
                        field = Field.search([('model', '=', model_name),
                                              ('name', '=', raw_variable['measure'])], limit=1)
                        if field:
                            var_values['measure_field_id'] = field.id
                    variables.append((0, 0, var_values))
                if variables:
                    item_values['variable_ids'] = variables
                subvalues = []
                for raw_sub in raw.get('subvalues') or []:
                    measure = Field.search([('model_id', '=', model.id),
                                            ('name', '=', raw_sub.get('measure') or '')], limit=1)
                    subvalues.append((0, 0, {
                        'name': raw_sub.get('name') or self.env._('Sub-value'),
                        'domain': raw_sub.get('domain') or '[]',
                        'aggregate': raw_sub.get('aggregate') or 'count',
                        'measure_field_id': measure.id if measure else False,
                        'color': raw_sub.get('color') or 'slate',
                        'show_share': bool(raw_sub.get('show_share', True)),
                        'sequence': raw_sub.get('sequence') or 10,
                    }))
                if subvalues:
                    item_values['subvalue_ids'] = subvalues
                try:
                    Item.create(item_values)
                except (ValidationError, UserError) as err:
                    _logger.info('Skipped imported card %s: %s', raw.get('name'), err)
                    skipped.append(f"{raw.get('name')} ({model_name})")
            created.append(board.id)
        return {'boards': created, 'skipped': skipped}
