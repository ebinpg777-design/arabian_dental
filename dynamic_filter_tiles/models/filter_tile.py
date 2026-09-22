# -*- coding: utf-8 -*-
"""Dynamic Filter Tiles - a ribbon of clickable filters above any view.

One ``filter.tile`` record = one clickable tile shown above a list / kanban view
of ``model_id``. Everything the tile shows (its value, its trend, its share of
the view) is computed on demand by :meth:`FilterTile.compute_tiles`, against the
domain the user is *currently* looking at - so tiles describe the records in
front of the user rather than a frozen snapshot.
"""
import ast
import json
import logging

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.fields import Domain
from odoo.tools import SQL
from odoo.tools.safe_eval import safe_eval, datetime as safe_datetime, time as safe_time

_logger = logging.getLogger(__name__)

# Palette keys. The matching hex values live in
# static/src/core/tile_colors.js - the client is the single source of truth for
# the actual colours so light/dark theming stays a pure CSS concern.
TILE_COLORS = [
    ('indigo', 'Indigo'),
    ('sky', 'Sky'),
    ('cyan', 'Cyan'),
    ('teal', 'Teal'),
    ('emerald', 'Emerald'),
    ('lime', 'Lime'),
    ('amber', 'Amber'),
    ('orange', 'Orange'),
    ('rose', 'Rose'),
    ('violet', 'Violet'),
    ('slate', 'Slate'),
]

# Rotated when tiles are generated automatically, so a fresh set looks composed.
AUTO_COLOR_CYCLE = [
    'sky', 'amber', 'emerald', 'violet', 'rose', 'cyan', 'orange', 'teal', 'indigo', 'lime',
]

# Keyword -> FontAwesome icon, used to give auto-generated tiles a sensible face.
AUTO_ICON_HINTS = [
    (('draft', 'new', 'quotation'), 'fa-pencil'),
    (('sent', 'send', 'mail', 'email'), 'fa-paper-plane'),
    (('confirm', 'approved', 'validate', 'posted'), 'fa-check-circle'),
    (('done', 'finish', 'complete', 'closed'), 'fa-flag-checkered'),
    (('cancel', 'refus', 'reject'), 'fa-times-circle'),
    (('wait', 'pending', 'progress', 'to approve', 'to_approve'), 'fa-hourglass-half'),
    (('late', 'overdue', 'expire'), 'fa-exclamation-triangle'),
    (('paid', 'payment', 'invoice'), 'fa-money'),
    (('deliver', 'ship', 'transfer', 'receipt'), 'fa-truck'),
    (('sale', 'order', 'purchase'), 'fa-shopping-cart'),
    (('low', 'normal', 'high', 'urgent', 'priority'), 'fa-star'),
]

# User fields tried, in order, when "Only My Records" is ticked but no field named.
USER_FIELD_CANDIDATES = (
    'user_id', 'invoice_user_id', 'salesperson_id', 'user_ids',
)

# Date fields tried, in order, when a tile wants a sparkline but names no field.
TREND_FIELD_CANDIDATES = (
    'date_order', 'date_invoice', 'invoice_date', 'date_deadline', 'date_begin',
    'scheduled_date', 'date', 'date_start', 'create_date',
)

NUMERIC_TYPES = ('integer', 'float', 'monetary')
DATE_TYPES = ('date', 'datetime')

# Field types a tile can be broken down by in its hover popover.
BREAKDOWN_TYPES = ('many2one', 'selection', 'boolean', 'char', 'date', 'datetime')

# Fields carried by an exported tile set. Deliberately not the technical ones
# (ids, alert state, sequence gaps): a tile set must land cleanly in a database
# that knows nothing about the one it came from.
EXPORT_FIELDS = (
    'name', 'domain', 'aggregate', 'symbol', 'color', 'custom_color', 'icon',
    'tooltip', 'width', 'row', 'show_trend', 'trend_source', 'target_value', 'view_types',
    'alert_operator', 'alert_value', 'alert_message', 'alert_notify',
)

# How many daily history points a "recorded history" sparkline draws.
HISTORY_POINTS = 12

# Tile width, in pixels. 0 means "the standard width", which the stylesheet owns
# (and which shrinks on its own in compact mode). The bounds are what stays
# readable: below MIN the value is clipped, above MAX one tile eats the ribbon.
MIN_TILE_WIDTH = 120
MAX_TILE_WIDTH = 520

# A ribbon can be stacked on several rows. Six is a ceiling, not a target: past
# that the ribbon is taller than the records it is supposed to describe.
MAX_TILE_ROWS = 6


class FilterTile(models.Model):
    _name = 'filter.tile'
    _description = 'Dynamic Filter Tile'
    _order = 'row, sequence, id'

    name = fields.Char(string='Label', required=True, translate=True)
    sequence = fields.Integer(default=10, index=True)
    row = fields.Integer(
        default=1, required=True, index=True,
        help="Which row of the ribbon this tile sits on. Rows are stacked in order, and each "
             "one scrolls on its own. Drag a tile from one row to another to move it.")
    active = fields.Boolean(default=True)

    model_id = fields.Many2one(
        'ir.model', required=True, ondelete='cascade',
        domain=[('transient', '=', False)],
        help="The tile appears above every list / kanban view of this model.")
    model_name = fields.Char(
        related='model_id.model', string='Model Name', store=True, index=True)
    action_id = fields.Many2one(
        'ir.actions.act_window', string='Only On', ondelete='cascade', index=True,
        domain="[('res_model', '=', model_name)]",
        help="Leave empty and the tile appears on every list / kanban view of the model.\n"
             "Set it and the tile only appears under that menu - which is what you want when "
             "one model is reached through several menus (customer invoices, vendor bills and "
             "journal entries are all the same model).")
    action_candidate_ids = fields.Many2many(
        'ir.actions.act_window', string='Reachable Menus',
        compute='_compute_action_candidate_ids',
        help="Technical: what the 'Only On' dropdown is allowed to offer.")

    domain = fields.Char(
        default='[]', required=True,
        help="Records this tile counts, and the filter applied when it is clicked.\n"
             "The domain understands uid (the id of whoever is looking), user, today, "
             "now, context_today() and relativedelta - the same names a record rule "
             "may use.")
    # "My records" as a first-class switch. The domain above HAS understood `uid`
    # all along, but the tree editor gives an ordinary user no way to type an
    # expression - a many2one condition offers a list of actual users and nothing
    # else. This pair is that missing option: tick the box, and the tile counts
    # each viewer's own records. (client, 2026-08-28)
    # A headline counter, as opposed to a slice of what is on screen. Tiles
    # normally count inside the filters the reader has applied, which is what
    # makes a ribbon a breakdown of the list under it. A "Finished" tile on a
    # screen whose default filter is "to do" then reads 0 for ever - a number
    # that looks like an answer and is not one. (client, 2026-09-10)
    ignore_filters = fields.Boolean(
        string='Count Regardless of Filters',
        help="Count every record this tile's own filter matches, whatever else is "
             "filtered on screen - for a headline figure such as \"Finished\" on a "
             "screen whose default filter hides finished work. Clicking the tile "
             "then clears the other filters, so the list matches the number.")
    only_mine = fields.Boolean(
        string='Only My Records',
        help="Count only the records of whoever is looking at the ribbon: the same "
             "tile reads 12 for one salesperson and 3 for another. The filter it "
             "applies when clicked narrows to that person too.")
    user_field_id = fields.Many2one(
        'ir.model.fields', string='My Records Means', ondelete='set null',
        domain="[('model_id', '=', model_id), ('ttype', '=', 'many2one'),"
               " ('relation', '=', 'res.users'), ('store', '=', True)]",
        help="Which field ties a record to its person - usually the salesperson or "
             "assignee field. Picked automatically when the model has an obvious one.")
    user_field_name = fields.Char(
        related='user_field_id.name', string='My Records Field Name')

    # --- value -----------------------------------------------------------
    measure_field_id = fields.Many2one(
        'ir.model.fields', string='Measure', ondelete='set null',
        domain="[('model_id', '=', model_id), ('ttype', 'in', ['integer', 'float', 'monetary']),"
               " ('store', '=', True)]",
        help="Leave empty to show a record count. Otherwise the tile aggregates this field.")
    measure_name = fields.Char(related='measure_field_id.name', string='Measure Field Name')
    aggregate = fields.Selection(
        [('sum', 'Sum'), ('avg', 'Average'), ('max', 'Maximum'), ('min', 'Minimum')],
        default='sum', required=True)
    symbol = fields.Char(
        string='Unit', help="Prefix shown before the value, e.g. a currency symbol. "
                            "Left empty, monetary measures use the company currency.")

    # --- look ------------------------------------------------------------
    color = fields.Selection(TILE_COLORS, default='indigo', required=True)
    custom_color = fields.Char(
        string='Custom Colour', help="Any CSS colour (#1f8fff, teal, rgb(...)). Overrides the palette.")
    icon = fields.Char(default='fa-bolt')
    tooltip = fields.Char(string='Hint', translate=True, help="Shown when hovering the tile.")
    width = fields.Integer(
        help="Width of the tile in pixels. Leave at 0 for the standard width - which also means "
             "the tile follows the compact / comfortable density. Drag the right edge of a tile "
             "in the ribbon to set it without opening this form.")

    # --- extras ----------------------------------------------------------
    show_trend = fields.Boolean(
        string='Sparkline', default=True,
        help="Draw the last 6 months of this tile under the value, with the period-over-period change.")
    trend_source = fields.Selection(
        [('field', 'Date field of the records'), ('history', 'Recorded history')],
        string='Trend From', default='field', required=True,
        help="Date field: groups the records on one of their dates - answers "
             "'when were these records dated'.\n"
             "Recorded history: the server stores this tile's number once a day - answers "
             "'how did this number move', and works on models with no date field at all.")
    trend_field_id = fields.Many2one(
        'ir.model.fields', string='Trend Date',
        domain="[('model_id', '=', model_id), ('ttype', 'in', ['date', 'datetime']), ('store', '=', True)]",
        ondelete='set null',
        help="Date field the sparkline is grouped on. Auto-detected when empty.")
    trend_field_name = fields.Char(related='trend_field_id.name', string='Trend Field Name')
    target_value = fields.Float(
        string='Target', help="When set, the tile shows a progress ring towards this number "
                              "instead of its share of the view.")
    breakdown_field_id = fields.Many2one(
        'ir.model.fields', string='Break Down By', ondelete='set null',
        domain="[('model_id', '=', model_id), ('ttype', 'in',"
               " ['many2one', 'selection', 'boolean', 'char', 'date', 'datetime']),"
               " ('store', '=', True)]",
        help="Field the tile splits itself on when you open its breakdown - each row of the "
             "popover filters the view further. Empty: the tile picks a sensible field itself.")
    breakdown_field_name = fields.Char(
        related='breakdown_field_id.name', string='Breakdown Field Name')

    # --- history ---------------------------------------------------------
    snapshot_ids = fields.One2many(
        'filter.tile.snapshot', 'tile_id', string='History')
    snapshot_count = fields.Integer(compute='_compute_snapshot_count', string='History Points')

    # --- alerts ----------------------------------------------------------
    alert_operator = fields.Selection(
        [('none', 'Never'), ('gt', 'Value rises above'), ('lt', 'Value drops below')],
        string='Warn Me When', default='none', required=True,
        help="Gives the tile a threshold: it turns into a warning as soon as the number "
             "crosses it, wherever it is shown.")
    alert_value = fields.Float(string='Threshold')
    alert_message = fields.Char(
        string='Warning Text', translate=True,
        help="Shown on the alerting tile and in the notification. Defaults to the tile's name.")
    alert_notify = fields.Boolean(
        string='Push a Notification',
        help="Also send a notification when the threshold is crossed - the hourly check runs "
             "server-side, so it reaches people who never opened the view.")
    alert_triggered = fields.Boolean(
        string='Currently Alerting', readonly=True, copy=False,
        help="Set by the alert check. Notifications are only sent when this flips on, so a "
             "tile that stays over its threshold does not notify every hour.")
    alert_triggered_on = fields.Datetime(string='Alerting Since', readonly=True, copy=False)

    # --- visibility ------------------------------------------------------
    view_types = fields.Selection(
        [('both', 'List & Kanban'), ('list', 'List only'), ('kanban', 'Kanban only')],
        string='Shown On', default='both', required=True)
    group_ids = fields.Many2many(
        'res.groups', string='Restricted To',
        help="Leave empty to show the tile to everybody who can read the model.")
    company_id = fields.Many2one(
        'res.company',
        help="Leave empty to show the tile in every company.")
    owner_id = fields.Many2one(
        'res.users', string='Private To', ondelete='cascade', index=True, copy=False,
        help="Set: the tile is personal - only this user sees it, and only this user can "
             "change it. Empty: the tile is shared with everybody, which is what a Filter "
             "Tiles manager publishes.")

    # Anchor for the live-preview widget on the form view; never stored.
    preview = fields.Char(compute='_compute_preview')

    def _compute_preview(self):
        self.preview = False

    @api.depends('model_name', 'action_id')
    def _compute_action_candidate_ids(self):
        """Actions worth scoping a tile to - one entry per name.

        A model like ``account.move`` carries a dozen act_window records, and
        several of them share a name: three "Invoices", two "Bills", two
        "Journal Entries". Most are never navigated to - they are opened from a
        button or embedded in another form - and offering them all makes the
        dropdown a list of identical labels nobody can choose between.

        So each name is represented once, by the action a menu actually points
        at when there is one. The tile's current action is always kept, however
        it was set, so an existing scope never becomes unselectable.
        """
        Action = self.env['ir.actions.act_window']
        for tile in self:
            if not tile.model_name:
                tile.action_candidate_ids = tile.action_id
                continue
            actions = Action.sudo().search([('res_model', '=', tile.model_name)])
            menu_bound = self.env['ir.ui.menu'].sudo()._menu_bound_actions(actions)
            best_by_name = {}
            for action in actions:
                name = (action.name or '').strip().lower()
                current = best_by_name.get(name)
                if current is None:
                    best_by_name[name] = action
                elif action.id in menu_bound and current.id not in menu_bound:
                    best_by_name[name] = action
            candidates = Action.browse(sorted(a.id for a in best_by_name.values()))
            tile.action_candidate_ids = candidates | tile.action_id

    @api.depends('snapshot_ids')
    def _compute_snapshot_count(self):
        counts = dict(self.env['filter.tile.snapshot']._read_group(
            [('tile_id', 'in', self.ids)], ['tile_id'], ['__count']))
        for tile in self:
            tile.snapshot_count = counts.get(tile, 0)

    # ------------------------------------------------------------------
    # Ownership
    # ------------------------------------------------------------------
    def _is_tile_manager(self):
        return self.env.user.has_group('dynamic_filter_tiles.group_tile_manager')

    @api.model_create_multi
    def create(self, vals_list):
        """Anybody may build tiles - but only a manager publishes them.

        A regular user's tile is silently stamped as personal, which is also
        what the record rules enforce: without an owner they could not write it
        back anyway, and failing at save time would be a poor way to find out.
        """
        if not self._is_tile_manager():
            for vals in vals_list:
                vals['owner_id'] = self.env.uid
        return super().create(vals_list)

    def write(self, vals):
        """Only a manager shares, hands over or restricts a tile.

        Record rules check a tile *before* it is written, so without this a
        regular user could publish their own tile to everybody by writing
        ``owner_id = False`` over RPC - skipping :meth:`action_publish` - or
        pass it to somebody else; and the crons compute shared tiles as
        superuser.
        """
        if not self.env.su and not self._is_tile_manager():
            if 'owner_id' in vals and vals['owner_id'] != self.env.uid:
                raise AccessError(self.env._(
                    'Only a Filter Tiles manager can share a tile or give it to somebody else.'))
            if 'group_ids' in vals:
                raise AccessError(self.env._(
                    'Only a Filter Tiles manager can restrict a tile to user groups.'))
        return super().write(vals)

    @api.onchange('model_id')
    def _onchange_model_id(self):
        """A domain / measure from another model is always wrong - drop them."""
        self.domain = '[]'
        self.measure_field_id = False
        self.trend_field_id = False
        self.user_field_id = False
        if self.only_mine:
            self.user_field_id = self._guess_user_field()

    @api.onchange('only_mine')
    def _onchange_only_mine(self):
        if self.only_mine and not self.user_field_id:
            self.user_field_id = self._guess_user_field()

    def _guess_user_field(self):
        """The obvious person field of the model, if it has one."""
        if not self.model_name or self.model_name not in self.env:
            return False
        Fields = self.env['ir.model.fields'].sudo()
        model_fields = self.env[self.model_name]._fields
        names = [name for name in USER_FIELD_CANDIDATES
                 if (f := model_fields.get(name)) is not None
                 and f.type == 'many2one' and f.comodel_name == 'res.users' and f.store]
        if not names:
            names = [name for name, f in model_fields.items()
                     if f.type == 'many2one' and f.comodel_name == 'res.users'
                     and f.store and not name.startswith(('create_', 'write_'))]
        if not names:
            return False
        return Fields.search([('model', '=', self.model_name),
                              ('name', '=', names[0])], limit=1).id or False

    @api.constrains('row')
    def _check_row(self):
        for tile in self:
            if not 1 <= tile.row <= MAX_TILE_ROWS:
                raise ValidationError(self.env._(
                    'A ribbon has %(max)s rows at most, numbered from 1.', max=MAX_TILE_ROWS))

    @api.constrains('width')
    def _check_width(self):
        for tile in self:
            if tile.width and not MIN_TILE_WIDTH <= tile.width <= MAX_TILE_WIDTH:
                raise ValidationError(self.env._(
                    'A tile is %(min)s to %(max)s pixels wide (0 for the standard width).',
                    min=MIN_TILE_WIDTH, max=MAX_TILE_WIDTH))

    @api.constrains('only_mine', 'user_field_id', 'model_id')
    def _check_only_mine(self):
        for tile in self:
            if not tile.only_mine:
                continue
            field = tile.sudo().user_field_id
            if not field:
                raise ValidationError(self.env._(
                    'Tile "%(name)s" counts only my records, but does not say which '
                    'field "my" means. Pick the person field.', name=tile.name))
            if field.model != tile.model_name or field.relation != 'res.users':
                raise ValidationError(self.env._(
                    'The person field of tile "%(name)s" must be a user field of '
                    '%(model)s.', name=tile.name, model=tile.model_name))

    @api.constrains('domain')
    def _check_domain(self):
        for tile in self:
            try:
                parsed = tile._eval_domain(tile.domain)
            except Exception as err:  # noqa: BLE001 - surfaced to the user as-is
                raise ValidationError(
                    self.env._('The domain of tile "%(name)s" cannot be evaluated:\n%(error)s',
                      name=tile.name, error=err)) from err
            if not isinstance(parsed, (list, tuple)):
                raise ValidationError(
                    self.env._('The domain of tile "%(name)s" must be a list.', name=tile.name))
            problem = self._browser_domain_problem(tile.domain)
            if problem:
                raise ValidationError(self.env._(
                    'The domain of tile "%(name)s" uses %(what)s, which the browser '
                    'cannot work out.\n\n%(hint)s',
                    name=tile.name, what=problem[0], hint=problem[1]))

    # ------------------------------------------------------------------
    # What the BROWSER can evaluate
    # ------------------------------------------------------------------
    # A tile's domain is evaluated twice: here in Python, to count it, and in
    # the browser, to filter the view when it is clicked and to draw its facet.
    # The second evaluator is a python-LIKE interpreter with a far smaller
    # vocabulary, and it does not fail politely: an expression it cannot work
    # out throws inside the rendering and takes the whole screen down with it,
    # every time that view is opened, for everybody. Python's safe_eval accepts
    # all of it, so the server used to wave such a tile through. It is checked
    # against the browser's own vocabulary here instead. (client, 2026-09-10)
    BROWSER_NAMES = frozenset({
        # py_builtin.js, plus what the web client puts in the evaluation
        # context: the session's user and companies.
        'bool', 'set', 'max', 'min', 'context_today', 'current_date', 'today',
        'now', 'datetime', 'time', 'relativedelta', 'uid', 'allowed_company_ids',
        'context', 'lang', 'tz', 'True', 'False', 'None',
    })
    BROWSER_CALLS = frozenset({
        'bool', 'set', 'max', 'min', 'context_today', 'relativedelta',
        'datetime.date', 'datetime.datetime', 'datetime.time', 'datetime.timedelta',
        'time.strftime',
        # methods py_date.js / py_interpreter.js implement
        'strftime', 'lower', 'upper', 'get', 'intersection', 'difference', 'union',
        'today', 'now', 'combine', 'to_utc', 'total_seconds', 'toordinal',
    })
    # The ones people reach for, and what to write instead.
    BROWSER_HINTS = {
        'replace': "For the first of the month write context_today().strftime('%Y-%m-01').",
        'weekday': "For the Monday of this week write "
                   "(context_today() + relativedelta(weekday=0, days=-6)).strftime('%Y-%m-%d').",
        'len': 'Counting is the tile itself, not part of its domain.',
        'isocalendar': 'Use relativedelta and strftime instead.',
    }

    @api.model
    def _browser_domain_problem(self, domain):
        """(what, hint) if the browser cannot evaluate this domain, else None."""
        if not domain or not isinstance(domain, str):
            return None
        try:
            tree = ast.parse(domain.strip(), mode='eval')
        except SyntaxError:
            return None       # the Python check above already rejected it
        supported = self.env._(
            'A tile domain may use: uid, today, now, context_today(), '
            'relativedelta(), datetime and time, and .strftime() on a date.')
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = self._dotted(node.func)
                tail = (name or '').split('.')[-1]
                if name in self.BROWSER_CALLS or tail in self.BROWSER_CALLS:
                    continue
                what = self.env._('%s()', name) if name else self.env._('a function')
                return (what, self.BROWSER_HINTS.get(tail, supported))
            if isinstance(node, ast.Name) and node.id not in self.BROWSER_NAMES:
                # `user` is a record-rule helper and exists only on the server;
                # in the browser it is simply not defined.
                return (self.env._('the name %s', node.id), supported)
        return None

    @api.model
    def _dotted(self, node):
        """'context_today' / 'datetime.date' / 'x.replace' for a call target."""
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
        elif isinstance(node, ast.Call):
            parts.append(self._dotted(node.func) or '')
        return '.'.join(reversed([p for p in parts if p]))

    def copy_data(self, default=None):
        vals_list = super().copy_data(default=default)
        for tile, vals in zip(self, vals_list):
            vals.setdefault('name', self.env._('%s (copy)', tile.name))
        return vals_list

    # ------------------------------------------------------------------
    # Domain helpers
    # ------------------------------------------------------------------
    def _domain_eval_context(self):
        """Same helpers the web client offers in a domain, so stored tile
        domains can use ``context_today()``, ``uid``, ``relativedelta`` ..."""
        return {
            'uid': self.env.uid,
            # An *empty* context, not the current one - the same thing
            # `ir.rule._eval_context` does, so a stored domain evaluates the
            # same way here as it would inside a record rule.
            # pylint: disable=context-overridden
            'user': self.env.user.with_context({}),
            'time': safe_time,
            'datetime': safe_datetime,
            'relativedelta': relativedelta,
            'context_today': lambda: fields.Date.context_today(self),
            'today': fields.Date.context_today(self),
            'now': fields.Datetime.now(),
            'current_date': fields.Date.context_today(self),
            'allowed_company_ids': self.env.companies.ids,
            'company_id': self.env.company.id,
        }

    @api.model
    def _mine_leaf(self, Model, tile):
        """The "and it is mine" condition of a tile definition dict, validated.

        The field name arrives from the browser with the rest of the definition,
        so it is checked against the model rather than trusted: it must be a
        stored many2one to res.users or it contributes nothing. The id it
        compares against is ``self.env.uid`` - whoever is asking - which is the
        entire point of the feature.
        """
        name = (tile or {}).get('mine_field') or ''
        if not name:
            return []
        field = Model._fields.get(name)
        if field is None or field.type != 'many2one' \
                or field.comodel_name != 'res.users' or not field.store:
            return []
        # A model may have its own idea of whose record this is. A dental lab's
        # work order carries two people - the technician who did it and the one
        # who finished it - and "mine" means either. A model says so by
        # implementing `_dft_mine_leaf`; everything else keeps the plain leaf.
        # (client, 2026-09-10)
        if hasattr(Model, '_dft_mine_leaf'):
            leaf = Model._dft_mine_leaf(name, self.env.uid)
            if isinstance(leaf, (list, tuple)) and leaf:
                return list(leaf)
        return [(name, '=', self.env.uid)]

    def _is_viewer_dependent(self):
        """True of a tile whose number depends on who is looking at it."""
        self.ensure_one()
        return bool(self.only_mine) or 'uid' in (self.domain or '')

    def _eval_domain(self, domain):
        """Accept both an already-parsed domain and a stored domain string."""
        if not domain:
            return []
        if isinstance(domain, (list, tuple)):
            return list(domain)
        return safe_eval(domain, self._domain_eval_context()) or []

    # ------------------------------------------------------------------
    # Registry - what the client renders
    # ------------------------------------------------------------------
    def _tile_payload(self):
        """Serialise one tile for the browser (definition only, no values).

        The ``ir.model.fields`` records a tile points at are read as superuser:
        regular users have no read access to that table (base grants them 0,0,0,0),
        and all we take from it is the technical name of a field the tile already
        stores a link to. Nothing about the user's *data* rights is bypassed - the
        values are still counted with their own rights in :meth:`compute_tiles`.
        """
        self.ensure_one()
        fields_sudo = self.sudo()
        measure = fields_sudo.measure_field_id.name or ''
        measure_type = ''
        if measure:
            field = self.env[self.model_name]._fields.get(measure) if self.model_name in self.env else None
            measure_type = field.type if field else ''
        symbol = self.symbol or ''
        if not symbol and measure_type == 'monetary':
            symbol = self.env.company.currency_id.symbol or ''
        mine_field = fields_sudo.user_field_id.name if self.only_mine else ''
        return {
            'id': self.id,
            'key': str(self.id),
            'name': self.name,
            'model': self.model_name,
            'domain': self.domain or '[]',
            # A tile saved before this check existed can still carry an
            # expression the browser cannot evaluate. Said here so the ribbon
            # greys it out instead of letting it break the view.
            'broken': bool(self._browser_domain_problem(self.domain)),
            'measure': measure,
            'aggregate': self.aggregate,
            'symbol': symbol,
            'color': self.color,
            'custom_color': self.custom_color or '',
            'icon': self.icon or '',
            'tooltip': self.tooltip or '',
            'width': self.width or 0,
            'show_trend': self.show_trend,
            'trend_source': self.trend_source,
            'trend_field': fields_sudo.trend_field_id.name or '',
            'target': self.target_value or 0.0,
            'view_types': self.view_types,
            'sequence': self.sequence,
            'row': max(self.row or 1, 1),
            # 0 = every view of the model; otherwise only that action's views.
            'action_id': self.action_id.id or 0,
            'breakdown_field': fields_sudo.breakdown_field_id.name or '',
            'alert_operator': self.alert_operator,
            'alert_value': self.alert_value or 0.0,
            'alert_message': self.alert_message or '',
            'mine_field': mine_field,
            # Counts the whole screen, not the reader's slice of it.
            'ignore_filters': self.ignore_filters,
            # The whole "and it is mine" condition, resolved here rather than
            # rebuilt in the browser: a model whose "mine" is two fields (a work
            # order's technician OR its finisher) must filter the same way it
            # counted. (client, 2026-09-10)
            'mine_domain': self._mine_leaf(
                self.env[self.model_name], {'mine_field': mine_field}
            ) if mine_field and self.model_name in self.env else [],
            'personal': bool(self.owner_id),
            'editable': bool(self.owner_id.id == self.env.uid or self._is_tile_manager()),
        }

    @api.model
    def get_tile_registry(self):
        """Every tile the current user may see, grouped by model name.

        Fetched once per session by the client and cached there, so opening a
        list view costs no extra round-trip when the model has no tiles.
        """
        empty = {'tiles': {}, 'rows': {}, 'can_manage': False, 'can_personalize': False}
        try:
            # Record rules already hide other people's personal tiles. Tiles are
            # configuration records - tens of them, not millions - so the whole
            # set is what the client caches for the session.
            tiles = self.search([])  # pylint: disable=no-search-all
        except AccessError:
            return empty
        user_groups = self.env.user.all_group_ids
        companies = set(self.env.companies.ids)
        registry = {}
        for tile in tiles:
            if tile.group_ids and not (tile.group_ids & user_groups):
                continue
            if tile.company_id and tile.company_id.id not in companies:
                continue
            if not tile.model_name or tile.model_name not in self.env:
                continue
            registry.setdefault(tile.model_name, []).append(tile._tile_payload())
        return {
            'tiles': registry,
            'rows': self.env['filter.tile.row'].get_row_names(),
            'can_manage': self._is_tile_manager(),
            'can_personalize': self.env.user._is_internal(),
        }

    # ------------------------------------------------------------------
    # Values - what the client displays
    # ------------------------------------------------------------------
    @api.model
    def compute_tiles(self, model_name, tiles, base_domain=None, periods=6,
                      unfiltered_domain=None):
        """Resolve a batch of tiles against ``base_domain`` in one round-trip.

        ``tiles`` is a list of definition dicts (as produced by
        :meth:`_tile_payload`, or hand-built by the live preview widget for an
        unsaved tile). Everything runs with the caller's own rights: a tile can
        never surface a record its owner is not allowed to read.

        Every tile on a ribbon counts the *same rows* through a different
        filter, so the numbers are gathered with conditional aggregation - one
        scan of the table for the whole ribbon instead of one per tile. See
        :meth:`_gather_counts`; :meth:`_gather_one_by_one` is the fallback and
        the reference implementation.
        """
        result = {'total': 0, 'tiles': {}}
        if not model_name or model_name not in self.env:
            return result
        Model = self.env[model_name]
        try:
            base = self._eval_domain(base_domain)
        except (AccessError, UserError, ValueError, SyntaxError, KeyError):
            return result
        # What a headline tile counts against: the screen's own domain without
        # the reader's filters. Absent (an older client, the live preview), it
        # is the same base as everything else. (client, 2026-09-10)
        try:
            unfiltered = self._eval_domain(unfiltered_domain) \
                if unfiltered_domain is not None else base
        except (AccessError, UserError, ValueError, SyntaxError, KeyError):
            unfiltered = base

        # Parse every tile's domain up front: a tile whose domain is broken is
        # reported as such and must not cost the rest of the ribbon its batch.
        prepared = []
        for tile in tiles or []:
            key = str(tile.get('key') or tile.get('id') or '')
            result['tiles'][key] = {
                'value': 0.0, 'count': 0, 'trend': [], 'delta': None,
                'alert': False, 'error': False,
            }
            try:
                prepared.append((key, tile, self._eval_domain(tile.get('domain'))
                                 + self._mine_leaf(Model, tile)))
            except (ValueError, SyntaxError, KeyError, TypeError) as err:
                _logger.info('Filter tile %s on %s has an unusable domain: %s',
                             key, model_name, err)
                result['tiles'][key]['error'] = True

        # Headline tiles are asked separately, against the screen without the
        # reader's filters: they cannot share the ribbon's single scan, because
        # that scan is bounded by exactly the filters they are meant to ignore.
        headline = [row for row in prepared if row[1].get('ignore_filters')]
        prepared = [row for row in prepared if not row[1].get('ignore_filters')]
        batched, manual = self._split_batchable(Model, prepared)
        try:
            if batched and self._gather_counts(Model, base, batched, result):
                if manual:
                    self._gather_one_by_one(Model, base, manual, result, with_total=False)
            else:
                batched, manual = [], prepared
                self._gather_one_by_one(Model, base, prepared, result, with_total=True)
            if headline:
                self._gather_one_by_one(Model, unfiltered, headline, result,
                                        with_total=not prepared)
                self._gather_trends(Model, unfiltered, [], headline, result, periods)
            self._gather_trends(Model, base, batched, manual, result, periods)
        except (AccessError, UserError) as err:
            _logger.info('Filter tiles on %s could not be computed: %s', model_name, err)
            return {'total': 0, 'tiles': {key: dict(entry, error=True)
                                          for key, entry in result['tiles'].items()}}

        for key, tile, _domain in prepared + headline:
            entry = result['tiles'][key]
            entry['alert'] = self._evaluate_alert(tile, entry['value'])
        return result

    def _split_batchable(self, Model, prepared):
        """Split the ribbon into tiles that may share a statement, and the rest.

        Two kinds of tile have to keep their own query - each one alone, not
        the whole ribbon with it:

        * A domain that mentions the model's ``active`` field. ``search_count``
          decides whether to add ``active IS TRUE`` by looking at *the whole
          domain it is given*, so an "Archived" tile asked on its own is
          answered over archived records - while the batch asks the base domain
          once, where that tile's condition is not visible, and would filter
          archived rows out before the tile ever sees them.
        * A domain that compiles to a **join**. A join added for one tile
          changes the rows every other tile in the same statement would see (an
          inner join drops rows, a to-many join duplicates them), and a tile
          must never be able to alter its neighbour's number. Found by probing
          each condition against a scratch query and watching its FROM clause;
          most relational leaves compile to a self-contained subquery instead,
          and those batch fine.
        """
        active_name = Model._active_name
        batched, manual = [], []
        probe = Model._search([])
        shape = (len(probe._tables), len(probe._joins))
        for item in prepared:
            _key, _tile, domain = item
            try:
                parsed = Domain(domain)
                if active_name and any(leaf.field_expr == active_name
                                       for leaf in parsed.iter_conditions()):
                    manual.append(item)
                    continue
                optimized = parsed.optimize_full(Model)
                if not (optimized.is_true() or optimized.is_false()):
                    optimized._to_sql(Model, probe.table, probe)
            except (ValueError, SyntaxError, KeyError, TypeError, NotImplementedError):
                manual.append(item)
                continue
            if (len(probe._tables), len(probe._joins)) != shape:
                manual.append(item)
                # The probe now carries this tile's join; start a clean one.
                probe = Model._search([])
                shape = (len(probe._tables), len(probe._joins))
            else:
                batched.append(item)
        return batched, manual

    def _measure_spec(self, Model, tile):
        """``'field:agg'`` for a tile that aggregates a column, else ``None``."""
        measure = tile.get('measure') or ''
        field = Model._fields.get(measure) if measure else None
        if field is None or field.type not in NUMERIC_TYPES or not field.store:
            return None
        aggregate = tile.get('aggregate') or 'sum'
        if aggregate not in ('sum', 'avg', 'max', 'min'):
            aggregate = 'sum'
        return f'{measure}:{aggregate}'

    def _tile_conditions(self, Model, query, prepared):
        """One SQL condition per tile, all against ``query``'s single table.

        Returns ``None`` - meaning "fall back to the slow path" - if a tile
        needs a join after all. :meth:`_split_batchable` already probed for
        that, so this is a safety net, not the selection mechanism: if the real
        query disagrees with the probe, correctness wins over speed.
        """
        shape = (len(query._tables), len(query._joins))
        conditions = {}
        for key, _tile, domain in prepared:
            try:
                parsed = Domain(domain).optimize_full(Model)
                if parsed.is_false():
                    conditions[key] = SQL("FALSE")
                elif parsed.is_true():
                    conditions[key] = SQL("TRUE")
                else:
                    conditions[key] = parsed._to_sql(Model, query.table, query)
            except (ValueError, SyntaxError, KeyError, TypeError, NotImplementedError):
                return None
            if (len(query._tables), len(query._joins)) != shape:
                return None
        return conditions

    def _gather_counts(self, Model, base, prepared, result):
        """Every tile's count and value, plus the view total, in one statement.

        ``SELECT COUNT(*), COUNT(*) FILTER (WHERE <tile 1>), ... FROM t WHERE <base>``
        - the base restriction is applied once, and each tile only contributes a
        ``FILTER`` clause. Returns False if the ribbon cannot be batched.
        """
        query = Model._search(base)
        conditions = self._tile_conditions(Model, query, prepared)
        if conditions is None:
            return False

        shape = (len(query._tables), len(query._joins))
        selects = [SQL("COUNT(*)")]
        columns = []
        for key, tile, _domain in prepared:
            condition = conditions[key]
            selects.append(SQL("COUNT(*) FILTER (WHERE %s)", condition))
            columns.append((key, 'count'))
            spec = self._measure_spec(Model, tile)
            if spec:
                try:
                    aggregate = Model._read_group_select(spec, query)
                except (ValueError, KeyError) as err:
                    _logger.info('Filter tile %s cannot aggregate %s: %s', key, spec, err)
                    result['tiles'][key]['error'] = True
                    continue
                selects.append(SQL("%s FILTER (WHERE %s)", aggregate, condition))
                columns.append((key, 'value'))
        # A measure may itself have needed a join (a related or monetary field).
        if (len(query._tables), len(query._joins)) != shape:
            return False

        rows = list(self.env.execute_query(query.select(*selects)))
        if not rows:
            return True
        row = rows[0]
        result['total'] = int(row[0] or 0)
        for index, (key, kind) in enumerate(columns, start=1):
            entry = result['tiles'][key]
            if kind == 'count':
                entry['count'] = int(row[index] or 0)
                entry['value'] = float(entry['count'])
            else:
                entry['value'] = float(row[index] or 0.0)
        return True

    def _gather_one_by_one(self, Model, base, prepared, result, with_total=True):
        """The unbatched path: one pair of queries per tile.

        Used when a tile's domain needs a join, which conditional aggregation
        cannot express safely. Slower, but it is the definition of the right
        answer - :meth:`_gather_counts` is only ever allowed to agree with it.
        """
        try:
            if with_total:
                result['total'] = Model.search_count(base)
        except (AccessError, UserError, ValueError, SyntaxError, KeyError):
            return
        for key, tile, tile_domain in prepared:
            entry = result['tiles'][key]
            if entry['error']:
                continue
            try:
                domain = base + tile_domain
                entry['count'] = Model.search_count(domain)
                spec = self._measure_spec(Model, tile)
                if spec:
                    groups = Model._read_group(domain, [], [spec])
                    entry['value'] = float(groups[0][0] or 0.0) if groups else 0.0
                else:
                    entry['value'] = float(entry['count'])
            except (AccessError, UserError, ValueError, SyntaxError, KeyError, TypeError) as err:
                _logger.info('Filter tile %s on %s could not be computed: %s',
                             key, Model._name, err)
                entry['error'] = True

    def _gather_trends(self, Model, base, batched, manual, result, periods):
        """Sparklines for every tile that wants one.

        Tiles that share a trend field share a statement: the buckets are the
        same, so only the ``FILTER`` differs. In practice a ribbon uses one or
        two date fields, which turns a query per tile into a query per field.
        ``manual`` tiles keep their own query, for the reason given in
        :meth:`_split_batchable`.
        """
        def wants_trend(item):
            key, tile, _domain = item
            return tile.get('show_trend') and not result['tiles'][key]['error']

        wanted = [item for item in list(batched) + list(manual) if wants_trend(item)]
        if not wanted:
            return

        for key, tile, _domain in wanted:
            if tile.get('trend_source') == 'history':
                entry = result['tiles'][key]
                entry['trend'] = self._history_trend(tile)
                entry['delta'] = self._compute_delta(entry['trend'])

        if periods < 2:
            return
        live = {item[0] for item in wanted if item[1].get('trend_source') != 'history'}

        first_month = fields.Date.context_today(self).replace(day=1)
        window_start = first_month - relativedelta(months=periods - 1)
        months = [window_start + relativedelta(months=index) for index in range(periods)]

        def one_at_a_time(group):
            for key, tile, domain in group:
                entry = result['tiles'][key]
                entry['trend'] = self._compute_trend(Model, base + domain, tile, periods)
                entry['delta'] = self._compute_delta(entry['trend'])

        one_at_a_time([item for item in manual if item[0] in live])

        by_field = {}
        for key, tile, domain in batched:
            if key not in live:
                continue
            field_name = self._trend_field_name(Model, tile)
            if field_name:
                by_field.setdefault(field_name, []).append((key, tile, domain))

        for field_name, group in by_field.items():
            series = self._trend_buckets(Model, base, field_name, group, window_start)
            if series is None:
                one_at_a_time(group)
                continue
            for key, _tile, _domain in group:
                buckets = series.get(key, {})
                entry = result['tiles'][key]
                entry['trend'] = [
                    round(buckets.get((month.year, month.month), 0.0), 2)
                    for month in months
                ]
                entry['delta'] = self._compute_delta(entry['trend'])

    def _trend_buckets(self, Model, base, field_name, group, window_start):
        """``{tile key: {(year, month): value}}`` for one shared trend field.

        Returns None when the group cannot be batched, so the caller falls back
        to :meth:`_compute_trend` per tile.
        """
        query = Model._search(base + [(field_name, '>=', window_start)])
        conditions = self._tile_conditions(Model, query, group)
        if conditions is None:
            return None
        shape = (len(query._tables), len(query._joins))
        try:
            bucket = Model._read_group_groupby(query.table, f'{field_name}:month', query)
        except (ValueError, KeyError):
            return None

        selects = [bucket]
        keys = []
        for key, tile, _domain in group:
            spec = self._measure_spec(Model, tile) or '__count'
            try:
                aggregate = Model._read_group_select(spec, query)
            except (ValueError, KeyError):
                return None
            selects.append(SQL("%s FILTER (WHERE %s)", aggregate, conditions[key]))
            keys.append(key)
        # The bucket or a measure may itself have needed a join.
        if (len(query._tables), len(query._joins)) != shape:
            return None

        query.groupby = bucket
        series = {key: {} for key in keys}
        for row in self.env.execute_query(query.select(*selects)):
            moment = row[0]
            if not moment:
                continue
            moment = moment.date() if hasattr(moment, 'date') else moment
            for index, key in enumerate(keys, start=1):
                series[key][(moment.year, moment.month)] = float(row[index] or 0.0)
        return series

    @api.model
    def _evaluate_alert(self, tile, value):
        """Has this tile's number crossed the threshold it was given?

        Works off a definition dict rather than a record, so the live preview in
        the editor lights up exactly like the real tile will.
        """
        operator = tile.get('alert_operator') or 'none'
        if operator not in ('gt', 'lt'):
            return False
        threshold = float(tile.get('alert_value') or 0.0)
        return value > threshold if operator == 'gt' else value < threshold

    def _history_trend(self, tile):
        """Sparkline drawn from the daily points the cron recorded."""
        tile_id = int(tile.get('id') or 0)
        if not tile_id:
            return []
        points = self.env['filter.tile.snapshot'].search(
            [('tile_id', '=', tile_id)], order='captured_on desc', limit=HISTORY_POINTS)
        if len(points) < 2:
            return []
        measure = tile.get('measure') or ''
        series = [
            round(point.value if measure else float(point.record_count), 2)
            for point in reversed(points)
        ]
        return series

    def _trend_field_name(self, Model, tile):
        name = tile.get('trend_field') or ''
        field = Model._fields.get(name) if name else None
        if field is not None and field.type in DATE_TYPES and field.store:
            return name
        for candidate in TREND_FIELD_CANDIDATES:
            field = Model._fields.get(candidate)
            if field is not None and field.type in DATE_TYPES and field.store:
                return candidate
        return ''

    def _compute_trend(self, Model, domain, tile, periods):
        """``periods`` monthly buckets ending with the current month."""
        field_name = self._trend_field_name(Model, tile)
        if not field_name or periods < 2:
            return []
        first_month = fields.Date.context_today(self).replace(day=1)
        window_start = first_month - relativedelta(months=periods - 1)

        measure = tile.get('measure') or ''
        field = Model._fields.get(measure) if measure else None
        if field is not None and field.type in NUMERIC_TYPES and field.store:
            aggregate_spec = f"{measure}:{tile.get('aggregate') or 'sum'}"
        else:
            aggregate_spec = '__count'

        groups = Model._read_group(
            domain + [(field_name, '>=', window_start)],
            [f'{field_name}:month'],
            [aggregate_spec],
        )
        buckets = {}
        for bucket, value in groups:
            if not bucket:
                continue
            bucket_date = bucket.date() if hasattr(bucket, 'date') else bucket
            buckets[(bucket_date.year, bucket_date.month)] = float(value or 0.0)
        series = []
        for index in range(periods):
            month = window_start + relativedelta(months=index)
            series.append(round(buckets.get((month.year, month.month), 0.0), 2))
        return series

    def _compute_delta(self, series):
        """Percent change of the last bucket against the one before it."""
        if len(series) < 2:
            return None
        previous, last = series[-2], series[-1]
        if not previous:
            return None
        return round((last - previous) / abs(previous) * 100.0, 1)

    # ------------------------------------------------------------------
    # Studio actions, called from the tile bar
    # ------------------------------------------------------------------
    @api.model
    def prepare_tile_defaults(self, model_name, domain=None, personal=False, row=1,
                              action_id=False):
        """Defaults for a tile created from the view the user is looking at.

        Returned as a context dict, ready to be handed to the form dialog - the
        filter currently on screen becomes the new tile's domain. ``personal``
        (or being anything but a manager) makes the new tile a private one, and
        ``row`` is the row of the ribbon the "+" was clicked on.
        """
        model = self.env['ir.model']._get(model_name)
        if not model:
            raise UserError(self.env._('Unknown model "%s".', model_name))
        siblings = self.search_count([('model_name', '=', model_name)])
        return {
            'default_model_id': model.id,
            'default_domain': domain or '[]',
            'default_color': AUTO_COLOR_CYCLE[siblings % len(AUTO_COLOR_CYCLE)],
            'default_icon': 'fa-filter',
            'default_sequence': (siblings + 1) * 10,
            'default_row': max(1, min(MAX_TILE_ROWS, int(row or 1))),
            'default_owner_id': self.env.uid if (personal or not self._is_tile_manager()) else False,
            # Offered, not imposed: the editor shows it and the user can clear
            # it to put the tile on every view of the model instead.
            'default_action_id': self._scoping_action(model_name, action_id),
        }

    @api.model
    def _scoping_action(self, model_name, action_id):
        """The action a new tile should be scoped to, if any.

        Only actions that *narrow* the model are proposed - the ones with a
        domain or a context of their own, like "Customer Invoices" on
        ``account.move``. A plain "all records" action would scope the tile for
        no reason, and the user would wonder why their tile is missing
        elsewhere.
        """
        if not action_id:
            return False
        action = self.env['ir.actions.act_window'].browse(int(action_id)).exists()
        if not action or action.res_model != model_name:
            return False
        narrows = (action.domain or '').strip() not in ('', '[]')
        if not narrows:
            context = (action.context or '').strip()
            narrows = any(key in context for key in ('search_default_', 'default_'))
        return action.id if narrows else False

    @api.model
    def get_status_fields(self, model_name):
        """Selection-like fields a tile set can be generated from."""
        if not model_name or model_name not in self.env:
            return []
        Model = self.env[model_name]
        candidates = []
        for name, field in Model._fields.items():
            if not field.store or field.type != 'selection':
                continue
            try:
                selection = field._description_selection(self.env)
            except Exception:  # noqa: BLE001 - a broken selection is just not a candidate
                continue
            # One value makes no ribbon, a dozen makes a wall of tiles.
            if not 2 <= len(selection) <= 12:
                continue
            candidates.append({
                'name': name,
                'string': str(field.string or name),
                'values': [{'value': value, 'label': str(label)} for value, label in selection],
            })
        # `state` first, then the shortest selections - they make the best tiles.
        candidates.sort(key=lambda c: (c['name'] != 'state', len(c['values']), c['name']))
        return candidates

    @api.model
    def _auto_icon(self, label, value):
        haystack = f'{label} {value}'.lower()
        for keywords, icon in AUTO_ICON_HINTS:
            if any(keyword in haystack for keyword in keywords):
                return icon
        return 'fa-circle-o'

    @api.model
    def autogenerate_tiles(self, model_name, field_name=None, replace=False, action_id=False,
                           row=1):
        """Create one tile per value of a status field. Returns created ids.

        ``action_id`` scopes the whole set to one action, which is what you want
        on a model reached through several menus: generating from "Customer
        Invoices" should not put a "Vendor Bill" tile on every journal entry
        list in the database.

        ``row`` is the row of the ribbon the set lands on, so a generated set
        can sit under the tiles that are already there instead of crowding them.
        """
        if not model_name or model_name not in self.env:
            raise UserError(self.env._('Unknown model "%s".', model_name))
        model = self.env['ir.model']._get(model_name)
        if not model:
            raise UserError(self.env._('Unknown model "%s".', model_name))

        candidates = self.get_status_fields(model_name)
        if not candidates:
            raise UserError(
                self.env._('"%s" has no status-like field to build tiles from. '
                  'Add a tile by hand instead.', model.name))
        source = None
        if field_name:
            source = next((c for c in candidates if c['name'] == field_name), None)
        source = source or candidates[0]

        action_id = int(action_id) if action_id else False
        # "Replace" only clears the ribbon it is replacing: the tiles of this
        # model *in this scope*, not somebody else's menu.
        scope = [('model_name', '=', model_name), ('action_id', '=', action_id)]
        if replace:
            self.search(scope).unlink()

        row = max(1, min(MAX_TILE_ROWS, int(row or 1)))
        # Sequences are per row, so a set added to an empty row starts at 10
        # rather than after everything on the row above it.
        existing = self.search(scope + [('row', '=', row)])
        start_sequence = max(existing.mapped('sequence') or [0]) + 10
        vals_list = []
        for index, option in enumerate(source['values']):
            vals_list.append({
                'name': option['label'],
                'sequence': start_sequence + index * 10,
                'row': row,
                'model_id': model.id,
                'action_id': action_id,
                'domain': repr([(source['name'], '=', option['value'])]),
                'color': AUTO_COLOR_CYCLE[index % len(AUTO_COLOR_CYCLE)],
                'icon': self._auto_icon(option['label'], option['value']),
                'tooltip': self.env._('%(field)s: %(value)s', field=source['string'], value=option['label']),
            })
        return self.create(vals_list).ids

    @api.model
    def resize_tile(self, tile_id, width):
        """Persist the width a user dragged a tile to.

        Clamped rather than refused: a drag that overshoots should stop at the
        edge, not throw a validation error at somebody who is just dragging.
        Returns the width actually stored, so the client can settle on it.
        """
        tile = self.browse(int(tile_id)).exists()
        if not tile or not tile._filtered_access('write'):
            return False
        width = int(width or 0)
        if width:
            width = max(MIN_TILE_WIDTH, min(MAX_TILE_WIDTH, width))
        tile.width = width
        return width

    @api.model
    def arrange_tiles(self, layout):
        """Persist a drag-and-drop across the whole ribbon, rows included.

        ``layout`` is what the bar reads back from the DOM after a drop::

            [{'row': 1, 'tiles': [3, 7]}, {'row': 2, 'tiles': [5]}]

        A user who owns only part of the ribbon still gets to arrange it: tiles
        they may not write keep the row and the place they had, so one shared
        tile in the way never blocks the tiles that did move.
        """
        arranged = []
        for group in layout or []:
            try:
                row = max(1, min(MAX_TILE_ROWS, int(group.get('row') or 1)))
                ids = [int(tile_id) for tile_id in group.get('tiles') or []]
            except (TypeError, ValueError):
                continue
            arranged.append((row, ids))

        tiles = self.browse([tile_id for _row, ids in arranged for tile_id in ids]).exists()
        writable = tiles._filtered_access('write')
        for row, ids in arranged:
            for index, tile_id in enumerate(ids):
                tile = tiles.browse(tile_id)
                if tile in writable:
                    tile.write({'row': row, 'sequence': (index + 1) * 10})
        return True

    @api.model
    def delete_row(self, model_name, row, action_id=False):
        """Remove one row from a model's ribbon.

        Tiles are never destroyed by this: a row is a layout choice, its tiles
        are configuration people built. The row's tiles join the row above (or
        the one below, when the first row goes), every row underneath moves up
        by one, and the row names move with them.

        Returns the number of tiles that were carried over.
        """
        row = max(1, min(MAX_TILE_ROWS, int(row or 1)))
        # The row being deleted is the row *as the user sees it*, which on a
        # scoped view is a mix of model-wide tiles and tiles pinned to that
        # action. Both move; a tile pinned to some other menu is not on this
        # ribbon at all and is left alone.
        scope = [('model_name', '=', model_name)]
        if action_id:
            scope.append(('action_id', 'in', [False, int(action_id)]))
        else:
            scope.append(('action_id', '=', False))

        moving = self.search(scope + [('row', '=', row)])
        target = row - 1 if row > 1 else 1
        if moving:
            # Land after whatever is already on the target row.
            siblings = self.search(scope + [('row', '=', target)]) - moving
            start = max(siblings.mapped('sequence') or [0]) + 10
            for index, tile in enumerate(moving._filtered_access('write')):
                tile.write({'row': target, 'sequence': start + index * 10})

        # Close the gap: everything below moves up one row.
        below = self.search(scope + [('row', '>', row)], order='row')
        for tile in below._filtered_access('write'):
            tile.row = max(1, tile.row - 1)

        self.env['filter.tile.row']._shift_names(model_name, row)
        return len(moving)

    def action_open_tiles(self):
        """Open the tile list, pre-filtered on this tile's model."""
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'dynamic_filter_tiles.action_filter_tile')
        action['domain'] = [('model_id', '=', self.model_id.id)]
        action['context'] = {'default_model_id': self.model_id.id}
        return action

    def action_publish(self):
        """Turn a personal tile into a shared one (managers only)."""
        if not self._is_tile_manager():
            raise AccessError(self.env._('Only a Filter Tiles manager can publish a tile to everybody.'))
        self.write({'owner_id': False})
        return True

    def action_copy_to_me(self):
        """Take a copy of a tile into your own ribbon.

        The way a user bends a shared tile to their own taste without touching
        what everybody else sees - the shared original stays untouched.
        """
        self.ensure_one()
        return self.copy({'owner_id': self.env.uid}).id

    def action_view_history(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('History of %s', self.name),
            'res_model': 'filter.tile.snapshot',
            'view_mode': 'list,graph',
            'domain': [('tile_id', '=', self.id)],
            'context': {'default_tile_id': self.id},
        }

    # ------------------------------------------------------------------
    # Breakdown - "what is inside this tile?"
    # ------------------------------------------------------------------
    def _breakdown_field_name(self, Model, tile):
        """The field a tile splits itself on: the configured one, or a guess.

        The guess prefers a status field (that is what people group by), then
        the responsible user, then any stored many2one - and always something
        the current user is allowed to read.
        """
        name = tile.get('breakdown_field') or ''
        field = Model._fields.get(name) if name else None
        if field is not None and field.store and field.type in BREAKDOWN_TYPES:
            return name
        for candidate in ('state', 'stage_id', 'status', 'priority', 'type',
                          'user_id', 'partner_id', 'company_id', 'category_id'):
            field = Model._fields.get(candidate)
            if field is not None and field.store and field.type in BREAKDOWN_TYPES:
                return candidate
        for candidate, field in Model._fields.items():
            if field.store and field.type in ('many2one', 'selection'):
                return candidate
        return ''

    @api.model
    def tile_breakdown(self, model_name, tile, base_domain=None, limit=8):
        """Split one tile into its top values, without leaving the view.

        Returns rows that are themselves filters: clicking one narrows the list
        to that slice, so a tile is a starting point rather than a dead end.
        Runs with the caller's rights, like every other number this module shows.
        """
        empty = {'field': '', 'label': '', 'rows': [], 'others': None}
        if not model_name or model_name not in self.env:
            return empty
        Model = self.env[model_name]
        field_name = self._breakdown_field_name(Model, tile or {})
        if not field_name:
            return empty
        field = Model._fields[field_name]

        try:
            domain = (self._eval_domain(base_domain)
                      + self._eval_domain((tile or {}).get('domain'))
                      + self._mine_leaf(Model, tile or {}))
            measure = (tile or {}).get('measure') or ''
            measure_field = Model._fields.get(measure) if measure else None
            if measure_field is not None and measure_field.type in NUMERIC_TYPES and measure_field.store:
                aggregate = (tile or {}).get('aggregate') or 'sum'
                if aggregate not in ('sum', 'avg', 'max', 'min'):
                    aggregate = 'sum'
                spec = f'{measure}:{aggregate}'
                aggregates = [spec, '__count']
            else:
                spec = '__count'
                aggregates = ['__count']
            groupby = f'{field_name}:month' if field.type in DATE_TYPES else field_name
            groups = Model._read_group(
                domain, [groupby], aggregates, order=f'{spec} DESC', limit=limit + 1)
            # Without a measure the value *is* the count; keep one row shape.
            if spec == '__count':
                groups = [(group[0], group[1], group[1]) for group in groups]
        except (AccessError, UserError, ValueError, SyntaxError, KeyError, TypeError) as err:
            _logger.info('Filter tile breakdown on %s failed: %s', model_name, err)
            return empty

        rows = []
        for group in groups[:limit]:
            raw, value, count = group[0], group[1], group[2]
            rows.append({
                'label': self._breakdown_label(field, raw),
                'value': float(value or 0.0),
                'count': int(count or 0),
                'domain': self._breakdown_domain(field, field_name, raw),
            })
        others = None
        if len(groups) > limit:
            others = {
                'value': float(sum(group[1] or 0.0 for group in groups[limit:])),
                'count': int(sum(group[2] or 0 for group in groups[limit:])),
            }
        return {
            'field': field_name,
            'label': str(field.string or field_name),
            'rows': rows,
            'others': others,
        }

    def _breakdown_label(self, field, raw):
        if field.type == 'many2one':
            return raw.display_name if raw else self.env._('None')
        if field.type == 'selection':
            selection = dict(field._description_selection(self.env))
            return str(selection.get(raw, raw)) if raw else self.env._('None')
        if field.type == 'boolean':
            return self.env._('Yes') if raw else self.env._('No')
        if field.type in DATE_TYPES:
            return fields.Date.to_date(raw).strftime('%b %Y') if raw else self.env._('Undated')
        return str(raw) if raw not in (False, None, '') else self.env._('None')

    def _breakdown_domain(self, field, field_name, raw):
        """A domain fragment the client can turn into an extra facet."""
        if field.type == 'many2one':
            return [(field_name, '=', raw.id)] if raw else [(field_name, '=', False)]
        if field.type in DATE_TYPES:
            if not raw:
                return [(field_name, '=', False)]
            start = fields.Date.to_date(raw)
            end = start + relativedelta(months=1)
            return [(field_name, '>=', str(start)), (field_name, '<', str(end))]
        return [(field_name, '=', raw if raw not in (None, '') else False)]

    # ------------------------------------------------------------------
    # History - the tile remembers its own numbers
    # ------------------------------------------------------------------
    def _capture_snapshot(self, day=None):
        """Store today's value for this tile. One point per tile per day."""
        self.ensure_one()
        if not self.model_name or self.model_name not in self.env:
            return False
        # A personal tile is measured through its owner's eyes; a shared one is
        # a company-wide number, so it is measured without record rules. Both
        # are documented on the field: history is not a per-reader number.
        if self._is_viewer_dependent() and not self.owner_id:
            # "Only my records" means a different number for every viewer, and a
            # history line records exactly one. A private tile has an owner to
            # measure through; a shared one has nobody, so it keeps no history.
            _logger.info('Filter tile %s counts per-viewer and is shared: '
                         'no history point recorded.', self.id)
            return False
        scoped = self.with_user(self.owner_id) if self.owner_id else self.sudo()
        payload = dict(scoped._tile_payload(), show_trend=False, key='snapshot')
        result = scoped.compute_tiles(self.model_name, [payload])
        entry = (result.get('tiles') or {}).get('snapshot')
        if not entry or entry.get('error'):
            return False
        day = day or fields.Date.context_today(self)
        Snapshot = self.env['filter.tile.snapshot'].sudo()
        point = Snapshot.search([('tile_id', '=', self.id), ('captured_on', '=', day)], limit=1)
        values = {'value': entry['value'], 'record_count': entry['count']}
        if point:
            point.write(values)
        else:
            Snapshot.create(dict(values, tile_id=self.id, captured_on=day))
        return True

    def action_capture_snapshot(self):
        """Record a point now - so a brand new history tile is not empty."""
        for tile in self:
            tile._capture_snapshot()
        return True

    @api.model
    def _cron_capture_snapshots(self):
        """Daily: one history point per tile that keeps a history."""
        tiles = self.sudo().search([('trend_source', '=', 'history')])
        for tile in tiles:
            try:
                tile._capture_snapshot()
            except Exception as err:  # noqa: BLE001 - one broken tile, not a broken cron
                _logger.warning('Filter tile %s could not be snapshotted: %s', tile.id, err)
        return True

    # ------------------------------------------------------------------
    # Alerts - the tile watches itself
    # ------------------------------------------------------------------
    def _alert_audience(self):
        """Who hears about this tile: its owner, its groups, or the managers."""
        self.ensure_one()
        if self.owner_id:
            return self.owner_id
        if self.group_ids:
            return self.group_ids.all_user_ids.filtered(lambda user: user.active)
        managers = self.env.ref(
            'dynamic_filter_tiles.group_tile_manager', raise_if_not_found=False)
        return managers.all_user_ids.filtered(lambda user: user.active) if managers else self.env['res.users']

    def _notify_alert(self, value):
        """Push the warning to its audience, live.

        Sent on the user's own partner channel - the one the web client is
        already subscribed to - so it lands as a notification wherever they are
        in Odoo, not only on the view the tile lives above.
        """
        self.ensure_one()
        users = self._alert_audience()
        if not users:
            return
        payload = {
            'tile_id': self.id,
            'title': self.name,
            'message': self.alert_message or self.env._(
                '%(name)s is at %(value)s.', name=self.name, value=round(value, 2)),
            'model': self.model_name,
            'model_label': self.model_id.name,
            'domain': self.domain or '[]',
            'value': value,
        }
        for user in users:
            if user.partner_id:
                self.env['bus.bus']._sendone(user.partner_id, 'filter_tiles.alert', payload)

    def _check_alert(self):
        """Evaluate one tile's threshold and flip its alert state.

        Only the rising edge notifies: a tile that sits above its threshold for
        a week is one notification, not a week of them.
        """
        self.ensure_one()
        if self._is_viewer_dependent() and not self.owner_id:
            # Same reasoning as history: a shared per-viewer tile has no single
            # number to hold against a threshold.
            return False
        scoped = self.with_user(self.owner_id) if self.owner_id else self.sudo()
        payload = dict(scoped._tile_payload(), show_trend=False, key='alert')
        result = scoped.compute_tiles(self.model_name, [payload])
        entry = (result.get('tiles') or {}).get('alert')
        if not entry or entry.get('error'):
            return False
        triggered = bool(entry.get('alert'))
        if triggered == self.alert_triggered:
            return triggered
        self.sudo().write({
            'alert_triggered': triggered,
            'alert_triggered_on': fields.Datetime.now() if triggered else False,
        })
        if triggered and self.alert_notify:
            self._notify_alert(entry['value'])
        return triggered

    @api.model
    def _cron_check_alerts(self):
        """Hourly: watch every tile that was given a threshold."""
        tiles = self.sudo().search([('alert_operator', '!=', 'none')])
        for tile in tiles:
            if not tile.model_name or tile.model_name not in self.env:
                continue
            try:
                tile._check_alert()
            except Exception as err:  # noqa: BLE001 - one broken tile, not a broken cron
                _logger.warning('Filter tile %s could not be checked: %s', tile.id, err)
        return True

    # ------------------------------------------------------------------
    # Overview - every ribbon in the database, on one screen
    # ------------------------------------------------------------------
    @api.model
    def get_overview(self, only_alerts=False):
        """All visible tiles, with their values, grouped by model.

        One batched call per model rather than one per tile, so a database with
        a hundred tiles across twenty models is twenty queries-worth of work,
        not a hundred round-trips.
        """
        registry = self.get_tile_registry()
        sections = []
        for model_name, definitions in registry['tiles'].items():
            if model_name not in self.env:
                continue
            values = self.compute_tiles(model_name, definitions)
            tiles = []
            for definition in sorted(definitions, key=lambda d: (d['sequence'], d['id'])):
                data = values['tiles'].get(definition['key']) or {}
                if only_alerts and not data.get('alert'):
                    continue
                tiles.append(dict(definition, data=data))
            if not tiles:
                continue
            sections.append({
                'model': model_name,
                'label': self.env['ir.model']._get(model_name).name or model_name,
                'total': values.get('total', 0),
                'tiles': tiles,
            })
        sections.sort(key=lambda section: section['label'])
        return {'sections': sections, 'can_manage': registry['can_manage']}

    # ------------------------------------------------------------------
    # Portability - move a ribbon between databases
    # ------------------------------------------------------------------
    @api.model
    def export_tiles(self, model_name=None, tile_ids=None):
        """A tile set as plain JSON-able data.

        Relations are exported by name (fields) and external id (groups) rather
        than by database id, so the file survives the trip to another database.
        """
        domain = []
        if model_name:
            domain.append(('model_name', '=', model_name))
        tiles = self.browse(tile_ids) if tile_ids else self.search(domain)
        payload = []
        for tile in tiles:
            values = {field: tile[field] for field in EXPORT_FIELDS}
            # See _tile_payload: field *names* are technical, not confidential.
            fields_sudo = tile.sudo()
            values.update({
                'model': tile.model_name,
                # By external id: a database id would point at something else
                # (or nothing) on the other side.
                'action': next(
                    (xmlid for xmlid in fields_sudo.action_id.get_external_id().values() if xmlid),
                    ''),
                'measure': fields_sudo.measure_field_id.name or '',
                'trend_field': fields_sudo.trend_field_id.name or '',
                'breakdown_field': fields_sudo.breakdown_field_id.name or '',
                'groups': [
                    xmlid for xmlid in fields_sudo.group_ids.get_external_id().values() if xmlid
                ],
            })
            payload.append(values)
        return {'version': 1, 'generator': 'dynamic_filter_tiles', 'tiles': payload}

    @api.model
    def import_tiles(self, payload, replace=False):
        """Recreate an exported tile set here.

        Anything the target database does not have - a missing model, a field
        that was renamed, a group from another edition - is skipped and
        reported, rather than failing the whole import.
        """
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError as err:
                raise UserError(self.env._('This is not valid JSON:\n%s', err)) from err
        if not isinstance(payload, dict) or not isinstance(payload.get('tiles'), list):
            raise UserError(self.env._('This file does not look like an exported tile set.'))

        created, skipped = self.browse(), []
        for entry in payload['tiles']:
            model_name = entry.get('model')
            model = self.env['ir.model']._get(model_name) if model_name else None
            if not model or model_name not in self.env:
                skipped.append(self.env._(
                    '%(name)s (unknown model %(model)s)',
                    name=entry.get('name') or '?', model=model_name or '?'))
                continue
            values = {key: entry[key] for key in EXPORT_FIELDS if key in entry}
            values['model_id'] = model.id
            for key, target in (('measure', 'measure_field_id'),
                                ('trend_field', 'trend_field_id'),
                                ('breakdown_field', 'breakdown_field_id')):
                field_name = entry.get(key)
                if field_name:
                    # Resolving a technical field name is not a data-access
                    # decision; creating the tile below still is.
                    field = self.env['ir.model.fields'].sudo()._get(model_name, field_name)
                    if field:
                        values[target] = field.id
            action_xmlid = entry.get('action')
            if action_xmlid:
                action = self.env.ref(action_xmlid, raise_if_not_found=False)
                if action and action._name == 'ir.actions.act_window':
                    values['action_id'] = action.id
                else:
                    skipped.append(self.env._(
                        '%(name)s: kept on every view (unknown menu %(action)s)',
                        name=entry.get('name') or '?', action=action_xmlid))
            groups = self.env['res.groups']
            for xmlid in entry.get('groups') or []:
                group = self.env.ref(xmlid, raise_if_not_found=False)
                if group and group._name == 'res.groups':
                    groups |= group
            values['group_ids'] = [(6, 0, groups.ids)]
            if replace:
                self.search([('model_name', '=', model_name),
                             ('name', '=', values.get('name'))]).unlink()
            created |= self.create(values)
        return {'created': created.ids, 'skipped': skipped}
