# -*- coding: utf-8 -*-
"""Dynamic Dashboards - one card of a board.

A ``dashboard.item`` is a question asked of a model: how many, how much, split
by what, over which period. The card that answers it (a number, a gauge, a
chart, a table, a top-N list, a note) is drawn by the browser from the payload
:meth:`DashboardItem.compute_values` returns.

Everything is computed **with the reader's own rights**: a card can never show a
number a record rule would hide, and clicking it opens exactly the records that
number counted.
"""
import json
import logging
import math
import time
from urllib.request import Request, urlopen

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.safe_eval import safe_eval, datetime as safe_datetime, time as safe_time

_logger = logging.getLogger(__name__)

# Palette keys. The hex values live in static/src/core/board_colors.js - the
# client owns the actual colours so light/dark theming stays pure CSS.
CARD_COLORS = [
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

# What a card can be. The order is the order of the picker in the form.
CARD_KINDS = [
    ('kpi', 'Number'),
    ('gauge', 'Gauge'),
    ('status', 'Status Light'),
    ('bar', 'Bar Chart'),
    ('hbar', 'Horizontal Bars'),
    ('stacked', 'Stacked Bars'),
    ('combo', 'Bars + Line'),
    ('waterfall', 'Waterfall'),
    ('pareto', 'Pareto'),
    ('bubble', 'Bubble'),
    ('treemap', 'Treemap'),
    ('lollipop', 'Lollipop'),
    ('calendar', 'Calendar Heat Map'),
    ('heatmap', 'Heat Map'),
    ('pivot', 'Pivot Table'),
    ('line', 'Line Chart'),
    ('area', 'Area Chart'),
    ('pie', 'Pie Chart'),
    ('donut', 'Donut Chart'),
    ('polar', 'Polar Chart'),
    ('radar', 'Radar Chart'),
    ('funnel', 'Funnel'),
    ('scatter', 'Scatter'),
    ('bullet', 'Bullet'),
    ('formula', 'Formula'),
    ('progress', 'Progress Bars'),
    ('table', 'Table'),
    ('list', 'Top List'),
    ('text', 'Note'),
]

# Kinds that show one number...
NUMBER_KINDS = ('kpi', 'gauge', 'status', 'bullet', 'formula')
# ...one series split by a field...
SPLIT_KINDS = ('bar', 'hbar', 'pie', 'donut', 'polar', 'radar', 'funnel', 'progress', 'table',
               # A waterfall bridges the same numbers a bar chart shows, and a
               # pareto is those bars sorted with their running share drawn
               # over them; a treemap is the same shares as nested rectangles
               # and a lollipop the same bars as stems with a dot: one reading,
               # five pictures.
               'waterfall', 'pareto', 'treemap', 'lollipop')
# ...along a date field...
# A calendar heat map is a series too - one bucket per day, laid out by
# week - so it needs the date field the line and the area need.
SERIES_KINDS = ('line', 'area', 'calendar')
# ...two splits at once...
STACKED_KINDS = ('stacked', 'heatmap', 'pivot')
# ...one record per point, two measures...
SCATTER_KINDS = ('scatter',)
# One split, two measures: bars for the first, a line for the second, each on
# its own axis if the two do not share a scale (revenue and a percentage).
COMBO_KINDS = ('combo', 'bubble')
# ...and the ones that need no model at all.
FREE_KINDS = ('text',)

# A calendar heat map over all time: fifty-two whole weeks, ending today.
CALENDAR_DAYS = 364
# An "unusual" badge: this many recordings looked at, at least this many
# needed, and this many standard deviations before a number is called out.
ANOMALY_DAYS = 30
ANOMALY_MIN_DAYS = 7
ANOMALY_SIGMAS = 2.0

# Every chart that splits records needs a field to split them by.
NEEDS_GROUP = SPLIT_KINDS + STACKED_KINDS + COMBO_KINDS

AGGREGATES = [
    ('count', 'Count'),
    ('sum', 'Sum'),
    ('avg', 'Average'),
    ('max', 'Maximum'),
    ('min', 'Minimum'),
]

# Periods a board - or a card of its own - can be read over.
PERIODS = [
    ('all', 'All Time'),
    ('today', 'Today'),
    ('yesterday', 'Yesterday'),
    ('tomorrow', 'Tomorrow'),
    ('this_week', 'This Week'),
    ('last_week', 'Last Week'),
    ('last_7', 'Last 7 Days'),
    ('last_30', 'Last 30 Days'),
    ('last_90', 'Last 90 Days'),
    ('last_365', 'Last 365 Days'),
    ('mtd', 'Month to Date'),
    ('this_month', 'This Month'),
    ('last_month', 'Last Month'),
    ('qtd', 'Quarter to Date'),
    ('this_quarter', 'This Quarter'),
    ('last_quarter', 'Last Quarter'),
    ('ytd', 'Year to Date'),
    ('this_year', 'This Year'),
    ('last_year', 'Last Year'),
    ('last_12_months', 'Last 12 Months'),
    ('next_7', 'Next 7 Days'),
    ('next_30', 'Next 30 Days'),
    ('next_90', 'Next 90 Days'),
]

COMPARE_MODES = [
    ('previous', 'The previous period'),
    ('year', 'The same period last year'),
]

SORT_ORDERS = [
    ('value_desc', 'Biggest first'),
    ('value_asc', 'Smallest first'),
    ('label_asc', 'A to Z'),
    ('label_desc', 'Z to A'),
]

TRANSFORMS = [
    ('none', 'As is'),
    ('cumulative', 'Running total'),
    ('moving', 'Moving average (3 points)'),
]

BACKGROUNDS = [
    ('plain', 'Plain'),
    ('tint', 'Tinted'),
    ('accent', 'Solid'),
]

# Names a formula variable may take: one letter, so `a / b * 100` reads.
VARIABLE_NAMES = 'abcdefghijklmnopqrstuvwxyz'

DRILL_VIEWS = [
    ('list', 'List'),
    ('kanban', 'Kanban'),
    ('graph', 'Graph'),
    ('pivot', 'Pivot'),
    ('calendar', 'Calendar'),
]

LEGEND_POSITIONS = [
    ('right', 'Right'),
    ('bottom', 'Bottom'),
    ('top', 'Top'),
    ('left', 'Left'),
    ('none', 'Hidden'),
]

# The colour ramp of one card. "board" is the usual answer - every card on a
# dashboard matching - and the rest are the board's own choices, offered here
# so one chart can stand apart on purpose.
CARD_PALETTES = [
    ('board', "The dashboard's"),
    ('default', 'Classic'),
    ('cool', 'Cool'),
    ('warm', 'Warm'),
    ('neon', 'Neon'),
    ('sunset', 'Sunset'),
    ('mono', 'Shades of the card colour'),
]

BAR_SHAPES = [
    ('rounded', 'Rounded'),
    ('square', 'Square'),
    ('pill', 'Pill'),
]

# Which bar or point is drawn in full while the rest step back.
EMPHASIS = [
    ('none', 'Nothing'),
    ('max', 'The biggest'),
    ('min', 'The smallest'),
    ('last', 'The latest'),
]

# What a conditional-formatting rule can ask of a column. Deliberately small:
# a colour rule nobody can read is worse than no colour at all.
CF_OPERATORS = [
    ('gt', 'Greater than'),
    ('lt', 'Less than'),
    ('eq', 'Equal to'),
    ('ne', 'Not equal to'),
    ('contains', 'Contains'),
    ('set', 'Is set'),
    ('unset', 'Is not set'),
]

# How a number is written. "Automatic" is what the module has always done:
# exact up to a hundred thousand, compact past it. The others say it plainly,
# always compact, or in the lakh-and-crore grouping most of India reads in.
NUMBER_SYSTEMS = [
    ('auto', 'Automatic - exact, compact past 100,000'),
    ('plain', 'Plain - 1,234,567'),
    ('short', 'Short - 1.2M'),
    ('indian', 'Indian - 12.3 L, 1.2 Cr'),
]

ALERT_OPERATORS = [
    ('none', 'No threshold'),
    ('gt', 'Above'),
    ('gte', 'At least'),
    ('lt', 'Below'),
    ('lte', 'At most'),
]

ALERT_LEVELS = [
    ('warning', 'Warning'),
    ('danger', 'Danger'),
]

NUMERIC_TYPES = ('integer', 'float', 'monetary')
DATE_TYPES = ('date', 'datetime')

# Field types a card may be split by. Same list the search bar's "Group By"
# accepts, so a drill-through lands on an ordinary grouped view.
GROUP_BY_TYPES = (
    'many2one', 'selection', 'boolean', 'char', 'integer', 'date', 'datetime',
)
GROUP_BY_INTERVALS = [
    ('day', 'Day'), ('week', 'Week'), ('month', 'Month'), ('quarter', 'Quarter'), ('year', 'Year'),
]
INTERVAL_STEP = {
    'day': relativedelta(days=1),
    'week': relativedelta(weeks=1),
    'month': relativedelta(months=1),
    'quarter': relativedelta(months=3),
    'year': relativedelta(years=1),
}

# Date fields tried, in order, when a card wants a period but names no field.
DATE_FIELD_CANDIDATES = (
    'date_order', 'invoice_date', 'date_deadline', 'date_begin', 'scheduled_date',
    'date', 'date_start', 'create_date',
)

# User fields tried, in order, when "Only My Records" is ticked but none named.
USER_FIELD_CANDIDATES = ('user_id', 'invoice_user_id', 'salesperson_id')

# Grid geometry. A board is twelve columns wide, like Bootstrap's - so "half"
# is 6 and "a third" is 4, and a row always adds up.
# Long enough for a chat server on another continent, short enough that a
# hanging endpoint cannot hold an hourly cron.
WEBHOOK_TIMEOUT = 3

BOARD_COLUMNS = 12
MIN_CARD_HEIGHT = 120
MAX_CARD_HEIGHT = 720

# Default height per kind, in pixels, when the card does not set its own.
DEFAULT_HEIGHT = {'kpi': 150, 'status': 150, 'gauge': 220, 'text': 150, 'bullet': 150,
                  'formula': 150}
DEFAULT_CHART_HEIGHT = 300

# How many points a line chart draws when it has no explicit limit.
DEFAULT_POINTS = 12

# How many stacks a stacked bar keeps apart before folding the rest together.
MAX_STACKS = 8

# How many recorded points a "history" sparkline draws.
HISTORY_POINTS = 12

# Fields carried by an exported card. Deliberately not the technical ones -
# an exported board must land in a database that knows nothing about this one.
EXPORT_FIELDS = (
    'name', 'kind', 'domain', 'aggregate', 'symbol', 'prefix', 'color', 'icon', 'width',
    'height', 'sequence', 'limit', 'target_value', 'compare', 'only_mine',
    'group_by_interval', 'tooltip', 'digits', 'drill_view', 'show_count', 'as_ratio',
    'period_mode', 'own_period', 'show_values', 'legend', 'multicolor', 'log_scale',
    'spark_source', 'note', 'alert_operator', 'alert_value', 'alert_level',
    'alert_message', 'alert_notify', 'compare_mode', 'sort', 'transform', 'multiplier',
    'semicircle', 'dual_axis', 'number_system', 'stack_percent', 'stack_horizontal', 'stepped',
    'show_bars',
    'rank_medals',
    'palette', 'bar_shape', 'show_grid', 'axis_titles', 'free_axis', 'center_total', 'emphasis',
    'cf_operator', 'cf_value', 'cf_color', 'formula', 'hide_operator', 'hide_value',
    'background', 'tab_name',
)


class DashboardItem(models.Model):
    _name = 'dashboard.item'
    _description = 'Dashboard Card'
    _order = 'sequence, id'

    name = fields.Char(string='Title', required=True, translate=True)
    board_id = fields.Many2one(
        'dashboard.board', string='Dashboard', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10, index=True)
    active = fields.Boolean(default=True)
    kind = fields.Selection(CARD_KINDS, string='Card', default='kpi', required=True)
    tab_id = fields.Many2one(
        'dashboard.tab', string='Tab', ondelete='set null', index=True,
        domain="[('board_id', '=', board_id)]",
        help="The page of the board this card sits on. Empty means the first page.")

    model_id = fields.Many2one(
        'ir.model', string='Model', ondelete='cascade',
        domain=[('transient', '=', False)],
        help="The records this card counts.")
    model_name = fields.Char(related='model_id.model', string='Model Name', store=True, index=True)
    domain = fields.Char(
        string='Filter', default='[]', required=True,
        help="Records the card is about. Same syntax as any Odoo filter; "
             "`uid`, `today` and `context_today()` are available.")
    action_id = fields.Many2one(
        'ir.actions.act_window', string='Opens', ondelete='set null',
        help="Action used when the card is clicked. Left empty, a plain view of "
             "the counted records opens.")
    drill_view = fields.Selection(
        DRILL_VIEWS, default='list', required=True, string='Open As',
        help="View the records open in when the card is clicked.")

    # --- what is measured -------------------------------------------------
    measure_field_id = fields.Many2one(
        'ir.model.fields', string='Measure',
        domain="[('model_id', '=', model_id), ('ttype', 'in', ['integer', 'float', 'monetary']), "
               "('store', '=', True)]",
        ondelete='cascade',
        help="Leave empty to count records.")
    measure_name = fields.Char(related='measure_field_id.name', string='Measure Field Name')
    measure_label = fields.Char(
        related='measure_field_id.field_description', string='Measure Label')
    measure2_field_id = fields.Many2one(
        'ir.model.fields', string='Second Measure',
        domain="[('model_id', '=', model_id), ('ttype', 'in', ['integer', 'float', 'monetary']), "
               "('store', '=', True)]",
        ondelete='cascade',
        help="The vertical axis of a scatter: one record, two numbers.")
    measure2_name = fields.Char(related='measure2_field_id.name', string='Second Measure Name')
    measure2_label = fields.Char(
        related='measure2_field_id.field_description', string='Second Measure Label')
    multiplier = fields.Float(
        string='Multiply By', default=1.0,
        help="Applied to every value before it is shown: 0.001 shows thousands as units.")
    formula = fields.Char(
        string='Formula',
        help="Arithmetic on the variables below, by their letters: `a / b * 100`. "
             "abs, min, max and round are available.")
    subvalue_ids = fields.One2many(
        'dashboard.item.subvalue', 'item_id', string='Sub-values', copy=True,
        help="Smaller numbers drawn under the headline, each over the card's "
             "filter plus one more condition.")
    variable_ids = fields.One2many(
        'dashboard.item.variable', 'item_id', string='Variables', copy=True)
    aggregate = fields.Selection(
        AGGREGATES, default='count', required=True, string='Aggregate')
    symbol = fields.Char(
        string='Unit', help="Shown after the number: %, h, kg, EUR...")
    prefix = fields.Char(
        string='Prefix', help="Shown before the number: $, EUR, ~ ...")
    number_system = fields.Selection(
        NUMBER_SYSTEMS, string='Number Format', default='auto', required=True,
        help="How the numbers on this card are written.")
    digits = fields.Integer(
        string='Decimals', default=0,
        help="Decimals shown on the number. Counts are always whole.")
    show_count = fields.Boolean(
        string='Show Record Count',
        help="Under an aggregate, also say how many records it covers.")
    as_ratio = fields.Boolean(
        string='As a Share of All Records',
        help="Show the number as a percentage of what the whole model gives "
             "over the same period, ignoring this card's own filter.")

    # --- how it is split --------------------------------------------------
    group_by_field_id = fields.Many2one(
        'ir.model.fields', string='Split By',
        domain="[('model_id', '=', model_id), ('ttype', 'in', "
               "['many2one', 'selection', 'boolean', 'char', 'integer', 'date', 'datetime']), "
               "('store', '=', True)]",
        ondelete='cascade',
        help="Field the chart is grouped by.")
    group_by_field_name = fields.Char(related='group_by_field_id.name', string='Split Field Name')
    group_by_field_label = fields.Char(
        related='group_by_field_id.field_description', string='Split Field Label')
    group_by_field_type = fields.Selection(
        related='group_by_field_id.ttype', string='Split Field Type')
    group_by_interval = fields.Selection(
        GROUP_BY_INTERVALS, default='month', string='Interval',
        help="Granularity used when the card is split by a date.")
    stack_field_id = fields.Many2one(
        'ir.model.fields', string='Stack By',
        domain="[('model_id', '=', model_id), ('ttype', 'in', "
               "['many2one', 'selection', 'boolean', 'char', 'integer']), "
               "('store', '=', True)]",
        ondelete='cascade',
        help="Second split of a stacked bar: each bar is divided by this field.")
    stack_field_name = fields.Char(related='stack_field_id.name', string='Stack Field Name')
    stack_field_label = fields.Char(
        related='stack_field_id.field_description', string='Stack Field Label')
    limit = fields.Integer(
        string='Points', default=8,
        help="How many bars, slices or rows the card shows. The rest is folded "
             "into an \"Others\" entry.")
    sort = fields.Selection(
        SORT_ORDERS, default='value_desc', required=True, string='Sort',
        help="The order of the bars, slices or rows.")
    list_field_ids = fields.Many2many(
        'ir.model.fields', 'dashboard_item_list_field_rel', 'item_id', 'field_id',
        string='Columns',
        domain="[('model_id', '=', model_id), ('store', '=', True), "
               "('ttype', 'not in', ['one2many', 'many2many', 'binary', 'html'])]",
        help="Extra columns of a top list, after the record's name.")

    # --- period -----------------------------------------------------------
    date_field_id = fields.Many2one(
        'ir.model.fields', string='Period Field',
        domain="[('model_id', '=', model_id), ('ttype', 'in', ['date', 'datetime']), "
               "('store', '=', True)]",
        ondelete='cascade',
        help="Date the board's period filter applies to. Without it the card "
             "ignores the period and always shows everything.")
    date_field_name = fields.Char(related='date_field_id.name', string='Period Field Name')
    date_field_label = fields.Char(
        related='date_field_id.field_description', string='Period Field Label')
    period_mode = fields.Selection(
        [('board', "The board's period"), ('own', 'Its own period')],
        default='board', required=True, string='Period',
        help="A card can ignore what the reader picked and always show, say, today.")
    own_period = fields.Selection(
        PERIODS, default='this_month', string='Own Period')
    compare = fields.Boolean(
        string='Compare',
        help="Shows how the number moved against the previous period of the "
             "same length. Needs a period field.")
    compare_mode = fields.Selection(
        COMPARE_MODES, default='previous', required=True, string='Compare With')
    transform = fields.Selection(
        TRANSFORMS, default='none', required=True, string='Transform',
        help="Turn a series into its running total or smooth it.")

    # --- decoration -------------------------------------------------------
    color = fields.Selection(CARD_COLORS, default='indigo', required=True)
    icon = fields.Char(default='fa-bar-chart')
    tooltip = fields.Char(string='Hint', translate=True, help="Shown when hovering the card.")
    width = fields.Integer(
        string='Width', default=3,
        help="Card width in columns, out of twelve: 3 is a quarter, 6 a half, 12 the full row.")
    height = fields.Integer(
        string='Height', default=0,
        help="Card height in pixels. 0 keeps the standard height of its kind.")
    target_value = fields.Float(
        string='Target',
        help="A number draws as progress towards it; a gauge fills towards it; "
             "a chart draws it as a reference line.")
    show_values = fields.Boolean(
        string='Values on the Chart', help="Write each value next to its bar or point.")
    legend = fields.Selection(LEGEND_POSITIONS, default='right', required=True)
    multicolor = fields.Boolean(
        string='One Colour per Bar',
        help="Bars take the palette in turn instead of the card's colour.")
    log_scale = fields.Boolean(
        string='Logarithmic Axis',
        help="For values spread over several orders of magnitude.")
    stack_horizontal = fields.Boolean(
        string='Sideways',
        help="Lays the stacked bars on their side - better for long labels "
             "and for many categories.")
    stack_percent = fields.Boolean(
        string='As Shares',
        help="Draws each bar to the same height, so the pieces read as "
             "percentages of that bar rather than as amounts.")
    stepped = fields.Boolean(
        string='Steps',
        help="Draws the line as steps rather than slopes - for a value that "
             "holds until it changes, like a headcount or a price.")
    show_bars = fields.Boolean(
        string='Bars in the Cells', default=True,
        help="Draws a bar behind each number in a list or a table, so the "
             "rows can be compared at a glance.")
    rank_medals = fields.Boolean(
        string='Medals',
        help="Marks the first three rows with gold, silver and bronze.")
    cf_field_id = fields.Many2one(
        'ir.model.fields', string='Colour When', ondelete='set null',
        domain="[('model_id', '=', model_id), ('store', '=', True)]",
        help="Colours a row of a list when this field meets the rule below.")
    cf_field_name = fields.Char(related='cf_field_id.name', store=True)
    cf_operator = fields.Selection(
        CF_OPERATORS, string='Is', default='gt', required=True)
    cf_value = fields.Char(
        string='Compared To',
        help="A number, a piece of text, or true/false - whatever the field holds.")
    cf_color = fields.Selection(
        CARD_COLORS, string='Row Colour', default='rose')
    dual_axis = fields.Boolean(
        string='Second Axis',
        help="Draws the line against its own axis on the right - for two "
             "measures that do not share a scale.")
    semicircle = fields.Boolean(
        string='Half Circle', help="Draw a pie, donut or polar chart as a half circle.")
    palette = fields.Selection(
        CARD_PALETTES, default='board', required=True, string='Colours',
        help="The colour ramp the slices, stacks and bars of this card take. "
             "Left to the dashboard, every card on it matches.")
    bar_shape = fields.Selection(
        BAR_SHAPES, default='rounded', required=True, string='Bar Ends',
        help="Softly rounded, square, or fully rounded like a pill.")
    show_grid = fields.Boolean(
        string='Grid Lines', default=True,
        help="The faint lines behind a chart. Off, the bars stand on a bare axis.")
    axis_titles = fields.Boolean(
        string='Axis Titles',
        help="Writes what each axis is: the split along one, the measure along "
             "the other.")
    free_axis = fields.Boolean(
        string='Axis Follows the Data',
        help="Lets the value axis start where the values do rather than at "
             "zero - for a line that moves in a narrow band far from zero, "
             "like a price or a temperature.")
    center_total = fields.Boolean(
        string='Total in the Middle',
        help="Writes the sum of the slices in the hole of the donut.")
    emphasis = fields.Selection(
        EMPHASIS, default='none', required=True, string='Stand Out',
        help="Draws one bar or point in full and the rest quieter: the "
             "biggest, the smallest, or the latest.")
    background = fields.Selection(
        BACKGROUNDS, default='plain', required=True, string='Background',
        help="Plain is the ordinary card; tinted washes it with the card "
             "colour; solid fills it with that colour and writes on it.")
    spark_source = fields.Selection(
        [('live', 'The records, by date'), ('history', 'Recorded history')],
        default='live', required=True, string='Sparkline',
        help="Where the small line under a number comes from: grouping the "
             "records by the period field, or the daily values recorded for this card.")
    note = fields.Html(
        string='Note', translate=True, sanitize=True,
        help="The text of a note card.")

    # --- scope ------------------------------------------------------------
    only_mine = fields.Boolean(
        string='Only My Records',
        help="Counts only the records assigned to whoever is looking.")
    user_field_id = fields.Many2one(
        'ir.model.fields', string='User Field',
        domain="[('model_id', '=', model_id), ('ttype', '=', 'many2one'), ('store', '=', True), "
               "('relation', '=', 'res.users')]",
        ondelete='cascade',
        help="Field holding the responsible user. Guessed when left empty.")
    user_field_name = fields.Char(related='user_field_id.name', string='User Field Name')
    group_ids = fields.Many2many(
        'res.groups', 'dashboard_item_group_rel', 'item_id', 'group_id',
        string='Visible To',
        help="Only these groups see the card. Empty means everybody who sees the board.")
    hide_operator = fields.Selection(
        ALERT_OPERATORS, default='none', required=True, string='Hide When Value Is',
        help="Hide the card from the board until its value matters: below a "
             "level, above a level...")
    hide_value = fields.Float(string='Hide Level')

    # --- thresholds -------------------------------------------------------
    alert_operator = fields.Selection(
        ALERT_OPERATORS, default='none', required=True, string='Threshold')
    alert_value = fields.Float(string='Threshold Value')
    alert_level = fields.Selection(
        ALERT_LEVELS, default='warning', required=True, string='When Crossed',
        help="How loudly the card should say it.")
    alert_message = fields.Char(
        string='Threshold Message', translate=True,
        help="Shown on the card while the threshold is crossed.")
    alert_webhook = fields.Char(
        string='Post To',
        help="A Slack, Teams or any other address to POST to the moment this "
             "card crosses its threshold. The message carries a `text` line "
             "and the numbers beside it.")
    alert_notify = fields.Boolean(
        string='Notify',
        help="Push a notification to the board's audience when the threshold "
             "is crossed - once, when it happens, not every hour it stays so.")
    alert_triggered = fields.Boolean(
        string='Crossed', readonly=True, copy=False,
        help="Whether the threshold was crossed at the last hourly check.")
    alert_triggered_on = fields.Datetime(string='Crossed Since', readonly=True, copy=False)

    # --- history ----------------------------------------------------------
    snapshot_count = fields.Integer(compute='_compute_snapshot_count', string='History Points')

    tab_name = fields.Char(related='tab_id.name', string='Tab Name')
    summary = fields.Char(compute='_compute_summary', string='In Plain Words')
    preview = fields.Char(compute='_compute_preview')

    def _compute_preview(self):
        for item in self:
            item.preview = str(item.id or 0)

    def _compute_snapshot_count(self):
        counts = {}
        if self.ids:
            grouped = self.env['dashboard.snapshot']._read_group(
                [('item_id', 'in', self.ids)], ['item_id'], ['__count'])
            counts = {item.id: count for item, count in grouped}
        for item in self:
            item.snapshot_count = counts.get(item.id, 0)

    @api.depends('kind', 'model_id', 'domain', 'aggregate', 'measure_field_id',
                 'group_by_field_id', 'stack_field_id', 'date_field_id', 'only_mine',
                 'period_mode', 'own_period', 'as_ratio', 'limit')
    def _compute_summary(self):
        for item in self:
            item.summary = item.describe()

    # ------------------------------------------------------------------
    # Constraints and defaults
    # ------------------------------------------------------------------
    @api.onchange('model_id')
    def _onchange_model_id(self):
        self.measure_field_id = False
        self.group_by_field_id = False
        self.stack_field_id = False
        self.date_field_id = False
        self.user_field_id = False
        self.action_id = False
        self.domain = '[]'

    @api.onchange('kind')
    def _onchange_kind(self):
        if self.kind in NUMBER_KINDS:
            self.width = self.width if 1 <= (self.width or 0) <= 4 else 3
        elif self.kind in FREE_KINDS:
            self.width = self.width or 12
        elif not self.width or self.width < 4:
            self.width = 6
        if self.kind in SERIES_KINDS and not self.date_field_id:
            self.date_field_id = self._guess_field(DATE_FIELD_CANDIDATES, DATE_TYPES)

    @api.onchange('only_mine')
    def _onchange_only_mine(self):
        if self.only_mine and not self.user_field_id:
            self.user_field_id = self._guess_field(USER_FIELD_CANDIDATES, ('many2one',),
                                                   relation='res.users')

    @api.onchange('date_field_id', 'compare')
    def _onchange_compare(self):
        if self.compare and not self.date_field_id:
            self.date_field_id = self._guess_field(DATE_FIELD_CANDIDATES, DATE_TYPES)

    def _guess_field(self, candidates, types, relation=None):
        """First stored field of ``types`` matching ``candidates``, as a record."""
        if not self.model_id:
            return False
        domain = [('model_id', '=', self.model_id.id), ('ttype', 'in', list(types)),
                  ('store', '=', True)]
        if relation:
            domain.append(('relation', '=', relation))
        found = self.env['ir.model.fields'].sudo().search(domain)
        by_name = {field.name: field for field in found}
        for name in candidates:
            if name in by_name:
                return by_name[name]
        return found[:1] or False

    @api.constrains('width')
    def _check_width(self):
        for item in self:
            if not 1 <= item.width <= BOARD_COLUMNS:
                raise ValidationError(self.env._(
                    'A card is between 1 and %(max)s columns wide.', max=BOARD_COLUMNS))

    @api.constrains('height')
    def _check_height(self):
        for item in self:
            if item.height and not MIN_CARD_HEIGHT <= item.height <= MAX_CARD_HEIGHT:
                raise ValidationError(self.env._(
                    'A card height is 0 (standard) or between %(min)s and %(max)s pixels.',
                    min=MIN_CARD_HEIGHT, max=MAX_CARD_HEIGHT))

    @api.constrains('kind', 'model_id')
    def _check_model(self):
        for item in self:
            if item.kind not in FREE_KINDS and not item.model_id:
                raise ValidationError(self.env._(
                    '"%(name)s" needs a model: only a note card has none.', name=item.name))

    @api.constrains('domain', 'model_id')
    def _check_domain(self):
        for item in self:
            if not item.model_id:
                continue
            try:
                parsed = item._eval_domain(item.domain)
                item.env[item.model_name].search_count(parsed, limit=1)
            except Exception as err:  # noqa: BLE001 - any bad domain must be reported as one
                raise ValidationError(self.env._(
                    'The filter of "%(name)s" is not a valid domain: %(error)s',
                    name=item.name, error=err)) from err

    @api.constrains('kind', 'group_by_field_id', 'date_field_id', 'stack_field_id',
                    'target_value')
    def _check_shape(self):
        for item in self:
            if item.kind in NEEDS_GROUP and not item.group_by_field_id:
                raise ValidationError(self.env._(
                    '"%(name)s" is a %(kind)s: it needs a field to split by.',
                    name=item.name, kind=dict(CARD_KINDS)[item.kind]))
            if item.kind in SERIES_KINDS and not item.date_field_id:
                raise ValidationError(self.env._(
                    '"%(name)s" runs along time: it needs a date field.',
                    name=item.name))
            if item.kind in STACKED_KINDS and not item.stack_field_id:
                raise ValidationError(self.env._(
                    '"%(name)s" crosses two fields: it needs a second field to split by.',
                    name=item.name))
            if item.kind in STACKED_KINDS and item.stack_field_id == item.group_by_field_id:
                raise ValidationError(self.env._(
                    '"%(name)s" cannot be split and stacked by the same field.',
                    name=item.name))
            if item.kind in ('gauge', 'bullet') and not item.target_value:
                raise ValidationError(self.env._(
                    '"%(name)s" is a %(kind)s: it needs a target to measure against.',
                    name=item.name, kind=dict(CARD_KINDS)[item.kind]))
            if item.kind in SCATTER_KINDS and not (item.measure_field_id and item.measure2_field_id):
                raise ValidationError(self.env._(
                    '"%(name)s" is a scatter: it needs two measures, one per axis.',
                    name=item.name))
            if item.kind in COMBO_KINDS and not item.measure2_field_id:
                raise ValidationError(self.env._(
                    '"%(name)s" reads two measures at once: it needs the second '
                    'one as well as the first.', name=item.name))
            if item.kind == 'formula' and not (item.formula or '').strip():
                raise ValidationError(self.env._(
                    '"%(name)s" is a formula card: write the formula.', name=item.name))
            if item.group_by_field_id and item.group_by_field_id.ttype not in GROUP_BY_TYPES:
                raise ValidationError(self.env._(
                    'A card cannot be split by a %(type)s field.',
                    type=item.group_by_field_id.ttype))

    @api.constrains('aggregate', 'measure_field_id', 'kind')
    def _check_measure(self):
        for item in self:
            if item.kind in SCATTER_KINDS:
                continue
            if item.aggregate != 'count' and not item.measure_field_id:
                raise ValidationError(self.env._(
                    '"%(name)s" aggregates a field, so it needs one.', name=item.name))

    @api.constrains('formula', 'variable_ids')
    def _check_formula(self):
        for item in self:
            if item.kind != 'formula':
                continue
            try:
                item._evaluate_formula({var.name: 1.0 for var in item.variable_ids})
            except Exception as err:  # noqa: BLE001 - whatever is wrong with it, say so
                raise ValidationError(self.env._(
                    'The formula of "%(name)s" cannot be computed: %(error)s',
                    name=item.name, error=err)) from err

    @api.constrains('alert_operator', 'kind')
    def _check_alert(self):
        for item in self:
            if item.alert_operator != 'none' and item.kind not in NUMBER_KINDS:
                raise ValidationError(self.env._(
                    'Only a number, a gauge or a status light can carry a threshold.'))

    # ------------------------------------------------------------------
    # Domain helpers
    # ------------------------------------------------------------------
    def _domain_eval_context(self):
        """The helpers the web client offers inside a domain."""
        return {
            'uid': self.env.uid,
            # An *empty* context, like ir.rule._eval_context, so a stored domain
            # evaluates here the way it would inside a record rule.
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

    # Shorthands anybody can type into a filter, standing for the expressions
    # the domain already understands. `%UID` is easier to remember than `uid`
    # is to discover, and easier still than looking up the company field.
    DOMAIN_SHORTHANDS = {
        '%UID': 'uid',
        '%MYCOMPANY': 'company_id',
        '%MYCOMPANIES': 'allowed_company_ids',
        '%TODAY': 'today',
        '%NOW': 'now',
    }

    def _eval_domain(self, domain):
        """Accept both a parsed domain and a stored domain string."""
        if not domain:
            return []
        if isinstance(domain, (list, tuple)):
            return list(domain)
        for shorthand, expression in self.DOMAIN_SHORTHANDS.items():
            domain = domain.replace(shorthand, expression)
        return safe_eval(domain, self._domain_eval_context()) or []

    @api.model
    def _period_range(self, period):
        """``(start, end)`` dates of a named period, or ``None`` for all time.

        ``end`` is exclusive, so a leaf pair is always ``>= start`` and
        ``< end`` - no "23:59:59" rounding to get wrong. A period may also be
        a custom range, ``{'start': 'YYYY-MM-DD', 'end': 'YYYY-MM-DD'}``, both
        days included.
        """
        if not period or period == 'all':
            return None
        if isinstance(period, dict):
            try:
                start = fields.Date.to_date(period.get('start'))
                end = fields.Date.to_date(period.get('end'))
            except (ValueError, TypeError):
                return None
            if not start or not end or end < start:
                return None
            return start, end + relativedelta(days=1)
        today = fields.Date.context_today(self)
        if period == 'today':
            return today, today + relativedelta(days=1)
        if period == 'yesterday':
            return today - relativedelta(days=1), today
        if period == 'tomorrow':
            return today + relativedelta(days=1), today + relativedelta(days=2)
        if period == 'this_week':
            start = today - relativedelta(days=today.weekday())
            return start, start + relativedelta(weeks=1)
        if period == 'last_week':
            start = today - relativedelta(days=today.weekday(), weeks=1)
            return start, start + relativedelta(weeks=1)
        if period == 'last_365':
            return today - relativedelta(days=364), today + relativedelta(days=1)
        if period == 'last_12_months':
            return today.replace(day=1) - relativedelta(months=11), \
                today.replace(day=1) + relativedelta(months=1)
        if period == 'mtd':
            return today.replace(day=1), today + relativedelta(days=1)
        if period == 'qtd':
            return today.replace(month=((today.month - 1) // 3) * 3 + 1, day=1), \
                today + relativedelta(days=1)
        if period == 'ytd':
            return today.replace(month=1, day=1), today + relativedelta(days=1)
        if period == 'last_quarter':
            start = today.replace(month=((today.month - 1) // 3) * 3 + 1, day=1) \
                - relativedelta(months=3)
            return start, start + relativedelta(months=3)
        if period in ('next_7', 'next_30', 'next_90'):
            days = int(period.split('_')[1])
            return today + relativedelta(days=1), today + relativedelta(days=days + 1)
        if period == 'last_7':
            return today - relativedelta(days=6), today + relativedelta(days=1)
        if period == 'last_30':
            return today - relativedelta(days=29), today + relativedelta(days=1)
        if period == 'last_90':
            return today - relativedelta(days=89), today + relativedelta(days=1)
        if period == 'this_month':
            start = today.replace(day=1)
            return start, start + relativedelta(months=1)
        if period == 'last_month':
            start = today.replace(day=1) - relativedelta(months=1)
            return start, start + relativedelta(months=1)
        if period == 'this_quarter':
            start = today.replace(month=((today.month - 1) // 3) * 3 + 1, day=1)
            return start, start + relativedelta(months=3)
        if period == 'this_year':
            start = today.replace(month=1, day=1)
            return start, start + relativedelta(years=1)
        if period == 'last_year':
            start = today.replace(month=1, day=1) - relativedelta(years=1)
            return start, start + relativedelta(years=1)
        return None

    def _effective_period(self, period):
        """The period this card is actually read over."""
        self.ensure_one()
        if self.period_mode == 'own':
            return self.own_period or 'all'
        return period

    def _period_leaves(self, period, shift=0):
        """Period restriction on this card's date field.

        ``shift`` steps the window back - by whole periods of the same length,
        or by whole years when the card compares with last year - which is
        what a comparison needs.
        """
        self.ensure_one()
        window = self._period_range(period)
        if not window or not self.date_field_name:
            return []
        start, end = window
        if shift and self.compare_mode == 'year':
            start, end = start - relativedelta(years=shift), end - relativedelta(years=shift)
        elif shift:
            span = end - start
            start, end = start - span * shift, end - span * shift
        name = self.date_field_name
        return [(name, '>=', fields.Date.to_string(start)),
                (name, '<', fields.Date.to_string(end))]

    def _mine_leaves(self, force=False):
        """The "and it is mine" restriction, validated against the model.

        ``force`` is the reader's own "only mine" switch on the board: it
        applies to any card that knows its user field, ticked or not.
        """
        self.ensure_one()
        if not (self.only_mine or force):
            return []
        name = self.user_field_name
        Model = self.env.get(self.model_name)
        field = Model._fields.get(name) if (Model is not None and name) else None
        if field is None or field.type != 'many2one' or field.comodel_name != 'res.users' \
                or not field.store:
            return []
        return [(name, '=', self.env.uid)]

    def _focus_leaves(self, focus):
        """The board's focus - one bar somebody clicked - as a domain on this card.

        A focus is a group value of one card, applied to every *other* card of
        the same model that has the field: click "Belgium" on a country pie and
        the whole board narrows to Belgium. The card it came from is exempt, so
        the reader can hop from one slice to the next without clearing first.
        Field name and model arrive from the browser and are validated here,
        never trusted.
        """
        self.ensure_one()
        if not isinstance(focus, dict) or focus.get('item_id') == self.id:
            return []
        if focus.get('model') != self.model_name:
            return []
        name = focus.get('field') or ''
        Model = self.env.get(self.model_name)
        field = Model._fields.get(name) if (Model is not None and name) else None
        if field is None or not field.store:
            return []
        return self._group_leaves(focus.get('key'), field_name=name,
                                  interval=focus.get('interval') or 'month')

    def _bar_leaves(self, filters):
        """The board's filter bar as a domain on this card.

        A filter is a field and a value the reader picked once, at the top of
        the page: "Country: Belgium". It reaches every card whose model has
        that field and leaves the others alone - a board mixing invoices and
        contacts narrows what it can and shows the rest whole.

        Field names arrive from the browser and are checked against the
        model here; an empty value, an unknown field or a field the card's
        model does not store is simply not a filter.
        """
        self.ensure_one()
        if not filters or not self.model_name:
            return []
        Model = self.env.get(self.model_name)
        if Model is None:
            return []
        leaves = []
        for entry in filters:
            if not isinstance(entry, dict):
                continue
            name = entry.get('field') or ''
            value = entry.get('value')
            if value in (None, '', False, []):
                continue
            field = Model._fields.get(name)
            if field is None or not field.store:
                continue
            if field.type == 'boolean':
                leaves.append((name, '=', value in (True, 'true', 'True', 1, '1')))
            elif field.type == 'many2one':
                try:
                    leaves.append((name, '=', int(value)))
                except (TypeError, ValueError):
                    continue
            else:
                leaves.append((name, '=', value))
        return leaves

    def item_domain(self, period=None, shift=0, focus=None, mine=False, own=True, filters=None):
        """Everything the card is restricted by, as one domain.

        ``own=False`` leaves the card's own filter out - the denominator of a
        ratio is "everything the model holds under the same period and focus".
        """
        self.ensure_one()
        return ((self._eval_domain(self.domain) if own else [])
                + self._period_leaves(period, shift=shift)
                + self._mine_leaves(force=mine)
                + self._focus_leaves(focus)
                + self._bar_leaves(filters))

    # ------------------------------------------------------------------
    # Reading the numbers
    # ------------------------------------------------------------------
    def _measure_spec(self):
        """The ``_read_group`` aggregate spec of this card."""
        self.ensure_one()
        if self.aggregate == 'count' or not self.measure_name:
            return '__count'
        aggregate = self.aggregate if self.aggregate in ('sum', 'avg', 'max', 'min') else 'sum'
        return f'{self.measure_name}:{aggregate}'

    def _group_spec(self):
        """The ``_read_group`` groupby spec of this card."""
        self.ensure_one()
        name = self.group_by_field_name
        if not name:
            return None
        if self.group_by_field_type in DATE_TYPES:
            return f'{name}:{self.group_by_interval or "month"}'
        return name

    def _payload(self):
        """The card's definition, as the browser needs it (no values)."""
        self.ensure_one()
        return {
            # A record being previewed from the form has no id yet.
            'id': self._origin.id or 0,
            'name': self.name,
            'kind': self.kind,
            'model': self.model_name or '',
            'color': self.color,
            'icon': self.icon or 'fa-bar-chart',
            'tooltip': self.tooltip or self.summary or '',
            'width': max(1, min(self.width or 3, BOARD_COLUMNS)),
            'height': self.height or DEFAULT_HEIGHT.get(self.kind, DEFAULT_CHART_HEIGHT),
            'symbol': self.symbol or '',
            'prefix': self.prefix or '',
            'digits': max(0, self.digits or 0),
            'number_system': self.number_system or 'auto',
            'target': self.target_value or 0.0,
            'compare': bool(self.compare and self.date_field_name),
            'aggregate': self.aggregate,
            'measure': self.measure_name or '',
            'measure_label': self.measure_label or '',
            'measure2': self.measure2_name or '',
            'measure2_label': self.measure2_label or '',
            'dual_axis': bool(self.dual_axis),
            'compare_mode': self.compare_mode or 'previous',
            'sort': self.sort or 'value_desc',
            'transform': self.transform or 'none',
            'multiplier': self.multiplier if self.multiplier not in (0.0, 1.0) else 1.0,
            'semicircle': bool(self.semicircle),
            'palette': self.palette or 'board',
            'bar_shape': self.bar_shape or 'rounded',
            'show_grid': self.show_grid is not False,
            'axis_titles': bool(self.axis_titles),
            'free_axis': bool(self.free_axis),
            'center_total': bool(self.center_total),
            'emphasis': self.emphasis or 'none',
            'stack_percent': bool(self.stack_percent),
            'stack_horizontal': bool(self.stack_horizontal),
            'stepped': bool(self.stepped),
            'show_bars': bool(self.show_bars),
            'rank_medals': bool(self.rank_medals),
            'cf_color': self.cf_color or 'rose',
            'background': self.background or 'plain',
            'formula': self.formula or '',
            'hide_operator': self.hide_operator or 'none',
            'group_by': self.group_by_field_name or '',
            'group_label': self.group_by_field_label or '',
            'group_type': self.group_by_field_type or '',
            'interval': self.group_by_interval or 'month',
            'stack_by': self.stack_field_name or '',
            'stack_label': self.stack_field_label or '',
            'date_field': self.date_field_name or '',
            'date_label': self.date_field_label or '',
            'periodic': bool(self.date_field_name),
            'period_mode': self.period_mode,
            'own_period': self.own_period if self.period_mode == 'own' else '',
            'show_count': bool(self.show_count),
            'as_ratio': bool(self.as_ratio),
            'show_values': bool(self.show_values),
            'legend': self.legend or 'right',
            'multicolor': bool(self.multicolor),
            'log_scale': bool(self.log_scale),
            'note': self.note or '',
            'alert_operator': self.alert_operator or 'none',
            'alert_value': self.alert_value or 0.0,
            'alert_level': self.alert_level or 'warning',
            'alert_message': self.alert_message or '',
            'sequence': self.sequence,
            'tab_id': self.tab_id.id or False,
        }

    def compute_values(self, period=None, focus=None, mine=False, filters=None):
        # A single card refreshed on its own, a preview, an alert check: each
        # gets a memo of its own so the repeated questions inside one card
        # (its value, its record count, its ratio) are asked once.
        if self.env.context.get('dashboard_memo') is None:
            return self.with_context(dashboard_memo={}).compute_values(period, focus, mine, filters)
        return self._compute_values(period, focus, mine, filters)

    def _compute_values(self, period=None, focus=None, mine=False, filters=None):
        """The card's numbers for ``period`` under ``focus``, added to its payload.

        Runs as the reader: a card shows what its reader is allowed to see, and
        nothing else. A card that cannot be computed at all comes back carrying
        ``error`` rather than breaking the board around it. ``focused`` says
        whether the board's focus narrowed this card; ``focus_source`` that it
        is the card the focus was taken from; ``mine`` is the reader's own
        "only mine" switch.
        """
        self.ensure_one()
        started = time.monotonic()
        payload = self._payload()
        if self.kind in FREE_KINDS:
            return payload
        period = self._effective_period(period)
        payload['period'] = period if isinstance(period, dict) else (period or 'all')
        payload['focused'] = bool(self._focus_leaves(focus))
        payload['focus_source'] = bool(isinstance(focus, dict) and focus.get('item_id') == self.id)
        Model = self.env.get(self.model_name)
        if Model is None:
            return dict(payload, error=True, value=0)
        try:
            Model.check_access('read')
        except Exception:  # noqa: BLE001 - no access is an empty card, not a crash
            return dict(payload, error=True, value=0)
        try:
            domain = self.item_domain(period, focus=focus, mine=mine, filters=filters)
            if self.kind in NUMBER_KINDS:
                payload.update(self._read_kpi(Model, domain, period, focus, mine, filters))
            elif self.kind in SERIES_KINDS:
                payload.update(self._read_line(Model, domain, period))
            elif self.kind in SPLIT_KINDS:
                payload.update(self._read_split(Model, domain))
            elif self.kind in STACKED_KINDS:
                payload.update(self._read_stacked(Model, domain))
            elif self.kind in COMBO_KINDS:
                payload.update(self._read_combo(Model, domain))
            elif self.kind in SCATTER_KINDS:
                payload.update(self._read_scatter(Model, domain))
            elif self.kind == 'list':
                payload.update(self._read_list(Model, domain))
            if self.compare and self.kind not in NUMBER_KINDS and self.date_field_name \
                    and period and period != 'all':
                previous = self._aggregate_value(
                    Model, self.item_domain(period, shift=1, focus=focus, mine=mine,
                                            filters=filters))
                payload['previous'] = previous
                payload['delta'] = payload.get('value', 0.0) - previous
        except Exception as err:  # noqa: BLE001 - one bad card must not empty the board
            _logger.info('Dashboard card %s (%s) could not be computed: %s',
                         self.id, self.model_name, err)
            return dict(payload, error=True, value=0)
        self._apply_multiplier(payload)
        if self.kind in NUMBER_KINDS:
            payload['alert'] = self._evaluate_alert(payload.get('value', 0.0))
            payload['since_snapshot'] = self._since_snapshot(payload.get('value', 0.0))
        payload['hidden'] = self._is_hidden(payload.get('value', 0.0))
        payload['ms'] = int((time.monotonic() - started) * 1000)
        return payload

    def _apply_multiplier(self, payload):
        """Scale every number the card shows. Counts of records stay counts."""
        factor = self.multiplier
        if not factor or factor == 1.0:
            return
        for key in ('value', 'previous', 'delta', 'whole', 'x', 'y'):
            if isinstance(payload.get(key), (int, float)):
                payload[key] = payload[key] * factor
        for point in payload.get('points') or []:
            for key in ('value', 'x', 'y'):
                if isinstance(point.get(key), (int, float)):
                    point[key] = point[key] * factor
        for point in payload.get('spark') or []:
            point['value'] = point['value'] * factor
        for row in payload.get('rows') or []:
            if isinstance(row.get('value'), (int, float)):
                row['value'] = row['value'] * factor
        for entry in payload.get('series') or []:
            entry['values'] = [value * factor for value in entry['values']]

    def _is_hidden(self, value):
        """Conditional visibility: the card stays off the board until its
        value crosses the level - a "late deliveries" card that only appears
        when there are any."""
        operator = self.hide_operator or 'none'
        if operator == 'none':
            return False
        level = self.hide_value or 0.0
        return {
            'gt': value > level, 'gte': value >= level,
            'lt': value < level, 'lte': value <= level,
        }.get(operator, False)

    def _since_snapshot(self, value):
        """How the number moved since the last recorded point, if any."""
        if not self._origin.id:
            return None
        last = self.env['dashboard.snapshot'].search(
            [('item_id', '=', self._origin.id)], order='day desc', limit=1)
        if not last:
            return None
        return {'day': fields.Date.to_string(last.day), 'value': last.value,
                'delta': value - last.value}

    company_ids = fields.Many2many(
        'res.company', 'dashboard_item_company_rel', 'item_id', 'company_id',
        string='Companies',
        help="Shown only while one of these companies is active in the "
             "switcher. Empty means every company the dashboard itself is "
             "shown in.")

    def _visible_to_reader(self):
        """Whether this card is offered to the reader at all.

        A card with companies is shown only while one of them is active in
        the switcher - for editors too, since a company-specific card is a
        fact about the company, not about who is looking. A card restricted
        to groups is offered to their members and to whoever may edit the
        board, or they could never find it again.
        """
        self.ensure_one()
        if self.company_ids and not (self.company_ids & self.env.companies):
            return False
        if not self.group_ids or self.board_id._can_edit():
            return True
        return bool(self.group_ids & self.env['dashboard.board']._user_groups())

    @api.model
    def preview_values(self, values, period=None):
        """The payload of a card that is still being edited.

        The form hands over its current values; a record is built in memory
        with :meth:`new` and computed exactly like a saved one, so the preview
        is the real card with the real number, not an approximation of it.
        """
        clean = {}
        for name, value in (values or {}).items():
            if name not in self._fields:
                continue
            if isinstance(value, (list, tuple)) and self._fields[name].type == 'many2one':
                value = value[0] if value else False
            clean[name] = value
        item = self.new(clean)
        if item.kind in FREE_KINDS:
            return item._payload()
        if not item.model_name:
            return {'empty': True}
        try:
            return item.compute_values(period or 'all')
        except Exception as err:  # noqa: BLE001 - a half-typed card must not error the form
            _logger.debug('Card preview could not be computed: %s', err)
            return dict(item._payload(), error=True, value=0)

    # Words that name the fields people actually build cards on. A field whose
    # name contains one of them is offered first - a heuristic, not a rule:
    # the full list of fields is one click away underneath.
    SUGGEST_SPLIT = ('state', 'stage', 'status', 'type', 'category', 'categ', 'team',
                     'user', 'partner', 'customer', 'country', 'company', 'priority',
                     'journal', 'product', 'department', 'job', 'source', 'medium',
                     'tag', 'currency', 'warehouse', 'location', 'analytic')
    SUGGEST_MEASURE = ('amount', 'total', 'price', 'subtotal', 'qty', 'quantity',
                       'weight', 'volume', 'hours', 'duration', 'days', 'margin',
                       'cost', 'revenue', 'balance', 'credit', 'debit')
    SUGGEST_DATE = ('date_order', 'invoice_date', 'date_deadline', 'date_start',
                    'date_planned', 'date', 'check_in', 'create_date', 'write_date')

    @api.model
    def suggest_fields(self, model_id):
        """The fields worth building a card on, for the model just picked.

        Choosing the field is the hard part of making a card: a model has two
        hundred of them and four are interesting. This ranks the ones people
        actually group and measure by - a status, a salesperson, an amount, a
        business date - so the form can offer each as a single click.

        Ranking, never filtering: everything remains reachable in the ordinary
        dropdown underneath.
        """
        model = self.env['ir.model'].browse(int(model_id)).exists() if model_id else None
        Model = self.env.get(model.model) if model else None
        if Model is None:
            return {'split': [], 'measure': [], 'date': []}
        try:
            Model.check_access('read')
        except Exception:  # noqa: BLE001 - no access, nothing to suggest
            return {'split': [], 'measure': [], 'date': []}
        rows = self.env['ir.model.fields'].sudo().search([
            ('model_id', '=', model.id),
            ('store', '=', True),
            ('name', 'not in', ('id', 'create_uid', 'write_uid', '__last_update')),
        ])
        split, measure, date = [], [], []
        for field in rows:
            entry = {'id': field.id, 'name': field.name, 'type': field.ttype,
                     'label': field.field_description}
            if field.ttype in GROUP_BY_TYPES and field.ttype not in DATE_TYPES:
                rank = self._suggest_rank(field.name, self.SUGGEST_SPLIT)
                if rank is not None:
                    split.append(dict(entry, rank=rank))
            if field.ttype in NUMERIC_TYPES:
                rank = self._suggest_rank(field.name, self.SUGGEST_MEASURE)
                if rank is not None:
                    measure.append(dict(entry, rank=rank))
            if field.ttype in DATE_TYPES:
                rank = self._suggest_rank(field.name, self.SUGGEST_DATE)
                date.append(dict(entry, rank=99 if rank is None else rank))

        def pick(found):
            ordered = sorted(found, key=lambda row: (row['rank'], row['name']))[:6]
            return [{key: value for key, value in row.items() if key != 'rank'}
                    for row in ordered]

        return {'split': pick(split), 'measure': pick(measure), 'date': pick(date)}

    @api.model
    def _suggest_rank(self, name, words):
        """How early a field name matches the words that make a good card."""
        lowered = name.lower()
        for index, word in enumerate(words):
            if word in lowered:
                return index
        return None

    @api.model
    def search_cards(self, term, limit=20):
        """Cards whose title contains ``term``, on boards the reader may open.

        Record rules already keep other people's personal boards out; the
        group restriction of a shared board is applied here, like the board
        list does, so quick find offers exactly what the sidebar offers.
        """
        term = (term or '').strip()
        if not term:
            return []
        Board = self.env['dashboard.board']
        user_groups = Board._user_groups()
        found = []
        for item in self.search([('name', 'ilike', term), ('board_id.active', '=', True)],
                                limit=max(1, int(limit)) * 3, order='board_id, sequence'):
            if not item.board_id._is_visible(user_groups):
                continue
            found.append({
                'id': item.id,
                'name': item.name,
                'kind': item.kind,
                'icon': item.icon or 'fa-bar-chart',
                'board_id': item.board_id.id,
                'board_name': item.board_id.name,
            })
            if len(found) >= limit:
                break
        return found

    @api.model_create_multi
    def create(self, vals_list):
        """A card is made by whoever may build on the board it goes to."""
        boards = self.env['dashboard.board'].browse(
            [vals.get('board_id') for vals in vals_list if vals.get('board_id')])
        for board in boards:
            if not board._can_edit():
                raise AccessError(self.env._(
                    'You cannot add a card to "%(name)s".', name=board.name))
        return super().create(vals_list)

    def write(self, vals):
        """...and changed by the same person.

        Both the card's current board and the one it is being moved to are
        checked: moving a card is a change to two dashboards.
        """
        boards = self.board_id
        if vals.get('board_id'):
            boards |= self.env['dashboard.board'].browse(vals['board_id'])
        for board in boards:
            if not board._can_edit():
                raise AccessError(self.env._(
                    'You cannot change the cards of "%(name)s".', name=board.name))
        return super().write(vals)

    def unlink(self):
        """A card is removed by whoever may change the board it sits on."""
        for item in self:
            if item.board_id and not item.board_id._can_edit():
                raise AccessError(self.env._(
                    'You cannot remove "%(name)s": the dashboard it is on is '
                    'not yours to change.', name=item.name))
        return super().unlink()

    @api.model
    def delete_card(self, item_id):
        """Remove one card, from the board rather than from a list view."""
        item = self.browse(int(item_id)).exists()
        if not item:
            return True
        item.unlink()
        return True

    @api.model
    def refresh_item(self, item_id, period=None, focus=None, mine=False, filters=None):
        """One card, recomputed on its own - the per-card refresh button."""
        item = self.browse(int(item_id)).exists()
        if not item:
            return False
        item.check_access('read')
        return item.compute_values(period, focus=focus, mine=mine, filters=filters)

    def _memoised(self, key, compute):
        """One answer per identical question, for the length of one page load.

        A board asks the same question more than once: the card that counts
        everything, the denominator of a ratio beside it, the previous period
        of a comparison a third card also compares against. At two hundred
        thousand rows each of those is a table scan, so `read_board` opens a
        memo (see its `dashboard_memo` context key) and identical questions
        are answered once. The key carries everything that changes the
        answer; the memo lives and dies with the one call, so nothing is ever
        served stale.
        """
        memo = self.env.context.get('dashboard_memo')
        if memo is None:
            return compute()
        if key not in memo:
            memo[key] = compute()
        return memo[key]

    def _count(self, Model, domain):
        """How many records the domain matches, asked once per page load."""
        return self._memoised(('count', Model._name, repr(domain)),
                              lambda: Model.search_count(domain))

    def _aggregate_value(self, Model, domain):
        """The single number of ``domain`` under this card's aggregate."""
        spec = self._measure_spec()
        # A counting card and the record count beside it are the same query:
        # share the memo entry rather than scanning the table twice.
        if spec == '__count':
            return float(self._count(Model, domain))
        return self._memoised(
            ('agg', Model._name, spec, repr(domain)),
            lambda: (lambda rows: float(rows[0][0] or 0.0) if rows else 0.0)(
                Model._read_group(domain, [], [spec])))

    def _read_kpi(self, Model, domain, period, focus=None, mine=False, filters=None):
        if self.kind == 'formula':
            return self._read_formula(Model, period, focus, mine)
        value = self._aggregate_value(Model, domain)
        result = {'value': value, 'count': self._count(Model, domain)}
        if self.as_ratio:
            whole = self._aggregate_value(
                Model, self.item_domain(period, focus=focus, mine=mine, own=False,
                                        filters=filters))
            result['whole'] = whole
            result['ratio'] = round(value / whole * 100, 1) if whole else 0.0
        if self.compare and self.date_field_name and period and period != 'all':
            previous = self._aggregate_value(
                Model, self.item_domain(period, shift=1, focus=focus, mine=mine,
                                        filters=filters))
            result['previous'] = previous
            result['delta'] = value - previous
            result['delta_percent'] = round((value - previous) / previous * 100, 1) \
                if previous else None
        if self.spark_source == 'history' and self._origin.id:
            result['spark'] = self._history_spark()
        elif self.date_field_name:
            result['spark'] = self._read_spark(Model, domain, period)
        anomaly = self._anomaly(value)
        if anomaly:
            result['anomaly'] = anomaly
        pace = self._pace(value, period)
        if pace:
            result['pace'] = pace
        if self.subvalue_ids:
            found = [sub._payload(value, period, focus=focus, mine=mine, filters=filters)
                     for sub in self.subvalue_ids.sorted(lambda s: (s.sequence, s.id))]
            result['subvalues'] = [entry for entry in found if entry]
        return result

    def _anomaly(self, value):
        """Is today's number out of line with this card's own recorded past?

        A z-score against the last :data:`ANOMALY_DAYS` daily recordings:
        two standard deviations or more away from their mean, with at least
        a week of them to compare against, is worth a word on the card. It
        is arithmetic anyone can check, which is the point - a badge that
        says "unusual" has to be able to say why.
        """
        if not self._origin.id:
            return None
        rows = self.env['dashboard.snapshot'].search(
            [('item_id', '=', self._origin.id), ('day', '<', fields.Date.context_today(self))],
            order='day desc', limit=ANOMALY_DAYS)
        values = rows.mapped('value')
        if len(values) < ANOMALY_MIN_DAYS:
            return None
        mean = sum(values) / len(values)
        sigma = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5
        if sigma <= 0:
            return None
        z = (value - mean) / sigma
        if abs(z) < ANOMALY_SIGMAS:
            return None
        return {
            'z': round(z, 1),
            'mean': round(mean, 2),
            'sigma': round(sigma, 2),
            'days': len(values),
            'direction': 'above' if z > 0 else 'below',
        }

    def _pace(self, value, period):
        """Where a number with a target stands against the calendar.

        Two fifths of the month gone and a third of the target reached is
        "behind"; the expected value is the target times the elapsed share of
        the period, and the status a plain word the card can colour.
        """
        target = self.target_value or 0.0
        window = self._period_range(period)
        if not target or not window or not self.date_field_name:
            return None
        start, end = window
        today = fields.Date.context_today(self)
        span = max(1, (end - start).days)
        elapsed = min(1.0, max(0.0, ((today - start).days + 1) / span))
        expected = target * elapsed
        ratio = value / expected if expected else (1.0 if value else 0.0)
        status = 'ahead' if ratio >= 1.1 else 'on_track' if ratio >= 0.9 else 'behind'
        return {
            'elapsed': round(elapsed * 100),
            'expected': round(expected, 2),
            'status': status,
            'days_left': max(0, (end - today).days - 1),
        }

    def _read_formula(self, Model, period, focus, mine):
        """A number computed from several: each variable is an aggregate over
        its own filter, the formula combines them."""
        values, listing = {}, []
        for variable in self.variable_ids:
            value = variable._value(Model, period, focus, mine)
            values[variable.name] = value
            listing.append({'name': variable.name, 'label': variable.label or variable.name,
                            'value': value})
        value = self._evaluate_formula(values)
        return {'value': value, 'count': 0, 'variables': listing}

    def _evaluate_formula(self, values):
        """Arithmetic on the variables, and nothing else."""
        context = {name: float(value or 0.0) for name, value in values.items()}
        context.update({'abs': abs, 'min': min, 'max': max, 'round': round,
                        'sqrt': math.sqrt, 'floor': math.floor, 'ceil': math.ceil})
        try:
            result = safe_eval((self.formula or '0').strip() or '0', context)
        except ZeroDivisionError:
            return 0.0
        if isinstance(result, bool) or not isinstance(result, (int, float)):
            raise ValueError(self.env._('a formula must give a number'))
        return float(result)

    def _read_spark(self, Model, domain, period):
        """The small line behind a number: the same measure along its date field.

        Inside a period the line covers the period, at a granularity that
        keeps it to a few dozen points at most; over all time it is the last
        twelve months. Empty buckets are zeroes, not gaps.
        """
        window = self._period_range(period)
        if window:
            days = (window[1] - window[0]).days
            interval = 'day' if days <= 31 else 'week' if days <= 140 else 'month'
            cursor, end = self._bucket_start(window[0], interval), window[1]
            starts = []
            while cursor < end and len(starts) < 60:
                starts.append(cursor)
                cursor += INTERVAL_STEP[interval]
        else:
            interval = 'month'
            last = self._bucket_start(fields.Date.context_today(self), 'month')
            starts = [last - relativedelta(months=offset) for offset in range(11, -1, -1)]
        spec = self._measure_spec()
        name = self.date_field_name
        rows = self._memoised(
            ('spark', Model._name, spec, name, interval, repr(domain)),
            lambda: Model._read_group(domain, [f'{name}:{interval}'], [spec]))
        found = {}
        for raw, measure in rows:
            key = self._raw_key(raw)
            if key is not None:
                found[key] = float(measure or 0.0)
        return [{
            'key': fields.Date.to_string(start),
            'label': self._date_label(start, interval),
            'value': found.get(fields.Date.to_string(start), 0.0),
        } for start in starts]

    def _history_spark(self):
        """The last recorded daily values of this card, oldest first."""
        points = self.env['dashboard.snapshot'].search(
            [('item_id', '=', self._origin.id)], order='day desc', limit=HISTORY_POINTS)
        return [{
            'key': fields.Date.to_string(point.day),
            'label': point.day.strftime('%d %b'),
            'value': point.value,
        } for point in reversed(points)]

    def _read_split(self, Model, domain):
        """Bars and slices: the measure grouped by the card's split field."""
        spec = self._measure_spec()
        group = self._group_spec()
        rows = Model._read_group(domain, [group], [spec, '__count'])
        field = Model._fields[self.group_by_field_name]
        points = []
        for raw, measure, count in rows:
            points.append({
                'key': self._raw_key(raw),
                'label': self._group_label(field, raw),
                'value': float(measure or 0.0),
                'count': count,
            })
        order = self.sort or 'value_desc'
        if order == 'value_asc':
            points.sort(key=lambda point: abs(point['value']))
        elif order == 'label_asc':
            points.sort(key=lambda point: str(point['label']).lower())
        elif order == 'label_desc':
            points.sort(key=lambda point: str(point['label']).lower(), reverse=True)
        else:
            points.sort(key=lambda point: abs(point['value']), reverse=True)
        limit = max(1, self.limit or 8)
        if len(points) > limit:
            rest = points[limit:]
            points = points[:limit]
            points.append({
                'key': None,
                'label': self.env._('Others'),
                'value': sum(point['value'] for point in rest),
                'count': sum(point['count'] for point in rest),
                'folded': True,
            })
        total = sum(point['value'] for point in points)
        for point in points:
            point['share'] = round(point['value'] / total * 100, 1) if total else 0.0
        return {'points': points, 'value': total,
                'count': sum(point['count'] for point in points)}

    def _read_stacked(self, Model, domain):
        """Bars split by one field and divided by another - the same matrix
        draws a heat map and a pivot table.

        One ``_read_group`` on both fields gives the whole matrix; categories
        keep the card's limit, stacks keep :data:`MAX_STACKS`, and whatever
        falls past either is folded into "Others" so the chart stays legible.
        """
        spec = self._measure_spec()
        group = self._group_spec()
        stack = self.stack_field_name
        rows = Model._read_group(domain, [group, stack], [spec, '__count'])
        group_field = Model._fields[self.group_by_field_name]
        stack_field = Model._fields[stack]
        totals, cells, labels = {}, {}, {}
        for raw_group, raw_stack, measure, count in rows:
            gkey, skey = self._raw_key(raw_group), self._raw_key(raw_stack)
            labels.setdefault(('g', gkey), self._group_label(group_field, raw_group))
            labels.setdefault(('s', skey), self._group_label(stack_field, raw_stack))
            totals[gkey] = totals.get(gkey, 0.0) + float(measure or 0.0)
            cells[(gkey, skey)] = cells.get((gkey, skey), 0.0) + float(measure or 0.0)
        ordered = sorted(totals, key=lambda key: abs(totals[key]), reverse=True)
        limit = max(1, self.limit or 8)
        kept, folded = ordered[:limit], ordered[limit:]
        stack_totals = {}
        for (gkey, skey), value in cells.items():
            stack_totals[skey] = stack_totals.get(skey, 0.0) + abs(value)
        stacks = sorted(stack_totals, key=stack_totals.get, reverse=True)
        kept_stacks, folded_stacks = stacks[:MAX_STACKS], stacks[MAX_STACKS:]

        def cell(gkeys, skeys):
            return sum(cells.get((g, s), 0.0) for g in gkeys for s in skeys)

        categories = [{'key': key, 'label': labels[('g', key)]} for key in kept]
        if folded:
            categories.append({'key': None, 'label': self.env._('Others'), 'folded': True})
        columns = [[key] for key in kept] + ([folded] if folded else [])
        series = []
        for skey in kept_stacks:
            series.append({'key': skey, 'label': labels[('s', skey)],
                           'values': [cell(col, [skey]) for col in columns]})
        if folded_stacks:
            series.append({'key': None, 'label': self.env._('Others'), 'folded': True,
                           'values': [cell(col, folded_stacks) for col in columns]})
        return {
            'categories': categories,
            'series': series,
            'value': sum(totals.values()),
        }

    def _read_line(self, Model, domain, period):
        """A series along the card's date field, gaps filled with zeroes."""
        spec = self._measure_spec()
        name = self.date_field_name
        # A calendar is one cell per day whatever the card was told to bucket by.
        interval = 'day' if self.kind == 'calendar' else (self.group_by_interval or 'month')
        rows = Model._read_group(domain, [f'{name}:{interval}'], [spec, '__count'],
                                 order=f'{name}:{interval} asc')
        found = {}
        for raw, measure, count in rows:
            key = self._raw_key(raw)
            if key is not None:
                found[key] = (float(measure or 0.0), count)
        points = []
        for start in self._series_starts(period, interval, found):
            key = fields.Date.to_string(start)
            measure, count = found.get(key, (0.0, 0))
            points.append({
                'key': key,
                'label': self._date_label(start, interval),
                'value': measure,
                'count': count,
            })
        total = sum(point['value'] for point in points)
        transform = self.transform or 'none'
        if transform == 'cumulative':
            running = 0.0
            for point in points:
                running += point['value']
                point['value'] = running
        elif transform == 'moving':
            raw = [point['value'] for point in points]
            for index, point in enumerate(points):
                window = raw[max(0, index - 2):index + 1]
                point['value'] = sum(window) / len(window)
        return {'points': points, 'value': total}

    def _series_starts(self, period, interval, found):
        """Bucket starts a line chart draws, in order.

        Inside a period the line covers the whole period even where nothing
        happened - a flat stretch is information. Over all time it draws the
        trailing buckets up to now, at least :data:`DEFAULT_POINTS` of them.
        """
        step = INTERVAL_STEP[interval]
        window = self._period_range(period)
        if window:
            start, end = window
            cursor = self._bucket_start(start, interval)
            starts = []
            while cursor < end and len(starts) < 400:
                starts.append(cursor)
                cursor += step
            return starts
        # Over all time: the trailing buckets up to the current one, contiguous,
        # so one busy month still sits on a twelve-month line and not alone. A
        # calendar over all time is the last fifty-two weeks.
        count = CALENDAR_DAYS if self.kind == 'calendar' else max(self.limit or 0, DEFAULT_POINTS)
        cursor = self._bucket_start(fields.Date.context_today(self), interval)
        starts = [cursor]
        while len(starts) < count:
            cursor = self._bucket_start(cursor - step, interval)
            starts.insert(0, cursor)
        return starts

    @api.model
    def _bucket_start(self, day, interval):
        if interval == 'week':
            return day - relativedelta(days=day.weekday())
        if interval == 'month':
            return day.replace(day=1)
        if interval == 'quarter':
            return day.replace(month=((day.month - 1) // 3) * 3 + 1, day=1)
        if interval == 'year':
            return day.replace(month=1, day=1)
        return day

    def _read_list(self, Model, domain):
        """The top records themselves, not an aggregate of them."""
        limit = max(1, self.limit or 8)
        measure = self.measure_name
        # NULLS LAST or the top of a "biggest first" list is every record
        # that has no value at all - Postgres sorts nulls first on DESC.
        order = f'{measure} desc nulls last' if measure and self.aggregate != 'count' else None
        if order and self.sort == 'value_asc':
            order = f'{measure} asc nulls last'
        records = Model.search(domain, limit=limit, order=order)
        columns = []
        for field in self.list_field_ids.sudo():
            if field.name in Model._fields and field.name != measure:
                columns.append({'name': field.name, 'label': field.field_description,
                                'type': field.ttype})
        rows = []
        for record in records:
            cells = []
            for column in columns:
                field = Model._fields[column['name']]
                cells.append(self._cell_text(field, record))
            rows.append({
                'id': record.id,
                'label': record.display_name,
                'value': float(record[measure] or 0.0) if measure else 0.0,
                'cells': cells,
                'flagged': self._cf_matches(record),
            })
        total = self._count(Model, domain)
        return {'rows': rows, 'columns': columns, 'value': float(total), 'count': total}

    def _cf_matches(self, record):
        """Does this row meet the card's colour rule?

        Evaluated here rather than in the browser: the server has the raw
        value, while the page only ever sees the string the cell shows.
        """
        name = self.cf_field_name
        if not name or name not in record._fields:
            return False
        operator = self.cf_operator or 'gt'
        value = record[name]
        if isinstance(value, models.Model):
            value = value.display_name if value else ''
        if operator == 'set':
            return bool(value)
        if operator == 'unset':
            return not value
        wanted = self.cf_value or ''
        if operator == 'contains':
            return wanted.lower() in str(value or '').lower()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                wanted_number = float(wanted)
            except (TypeError, ValueError):
                return False
            return {'gt': value > wanted_number, 'lt': value < wanted_number,
                    'eq': value == wanted_number, 'ne': value != wanted_number}.get(operator, False)
        text, wanted = str(value or '').lower(), wanted.lower()
        return {'gt': text > wanted, 'lt': text < wanted,
                'eq': text == wanted, 'ne': text != wanted}.get(operator, False)

    def _cell_text(self, field, record):
        """A field value as the list shows it."""
        value = record[field.name]
        if field.type == 'many2one':
            return value.display_name if value else ''
        if field.type == 'selection':
            return dict(field._description_selection(self.env) or {}).get(value, value or '')
        if field.type == 'boolean':
            return self.env._('Yes') if value else self.env._('No')
        if field.type in NUMERIC_TYPES:
            return value or 0
        if value is False or value is None:
            return ''
        return str(value)

    def _read_combo(self, Model, domain):
        """One split, two measures: the bars and the line over them.

        The second measure is read in the same group-by as the first - one
        query, not two - so the line always lines up with the bars under it.
        """
        spec = self._measure_spec()
        group = self._group_spec()
        second = self.measure2_name
        spec2 = f'{second}:{self.aggregate if self.aggregate in ("sum", "avg", "max", "min") else "sum"}' \
            if second else '__count'
        rows = Model._read_group(domain, [group], [spec, spec2, '__count'])
        field = Model._fields[self.group_by_field_name]
        points = []
        for raw, measure, measure2, count in rows:
            points.append({
                'key': self._raw_key(raw),
                'label': self._group_label(field, raw),
                'value': float(measure or 0.0),
                'value2': float(measure2 or 0.0),
                'count': count,
            })
        points.sort(key=lambda point: abs(point['value']),
                    reverse=self.sort != 'value_asc')
        if self.sort == 'label_asc':
            points.sort(key=lambda point: str(point['label']).lower())
        elif self.sort == 'label_desc':
            points.sort(key=lambda point: str(point['label']).lower(), reverse=True)
        limit = max(1, self.limit or 8)
        if len(points) > limit:
            rest = points[limit:]
            points = points[:limit]
            points.append({
                'key': None, 'label': self.env._('Others'), 'folded': True,
                'value': sum(point['value'] for point in rest),
                'value2': sum(point['value2'] for point in rest),
                'count': sum(point['count'] for point in rest),
            })
        total = sum(point['value'] for point in points)
        for point in points:
            point['share'] = round(point['value'] / total * 100, 1) if total else 0.0
        return {'points': points, 'value': total,
                'value2': sum(point['value2'] for point in points),
                'count': sum(point['count'] for point in points)}

    def _read_scatter(self, Model, domain):
        """One point per record: the first measure across, the second up."""
        limit = max(1, self.limit or 8) * 5
        x_name, y_name = self.measure_name, self.measure2_name
        records = Model.search(domain, limit=limit, order=f'{x_name} desc')
        points = [{
            'id': record.id,
            'label': record.display_name,
            'x': float(record[x_name] or 0.0),
            'y': float(record[y_name] or 0.0),
            'value': float(record[y_name] or 0.0),
        } for record in records]
        return {'points': points, 'value': sum(point['y'] for point in points),
                'count': self._count(Model, domain)}

    # ------------------------------------------------------------------
    # Thresholds
    # ------------------------------------------------------------------
    def _evaluate_alert(self, value):
        """The level the card should shout at, or ``False``."""
        self.ensure_one()
        operator = self.alert_operator or 'none'
        if operator == 'none':
            return False
        threshold = self.alert_value or 0.0
        crossed = {
            'gt': value > threshold,
            'gte': value >= threshold,
            'lt': value < threshold,
            'lte': value <= threshold,
        }.get(operator, False)
        return (self.alert_level or 'warning') if crossed else False

    def _alert_audience(self):
        """Who hears about a crossed threshold.

        The owner of a personal board; on a shared board the members of the
        groups it is restricted to, or the dashboard managers when it is open
        to everybody - a notification to every internal user is noise.
        """
        self.ensure_one()
        board = self.board_id
        if board.owner_id:
            return board.owner_id
        groups = board.group_ids or self.env.ref('ebshel_dashboard.group_dashboard_manager')
        return self._group_users(groups.sudo())

    @api.model
    def _group_users(self, groups):
        """The users of some groups, implied membership included, whatever the
        core calls that field: 19.0 keeps explicit and implied members apart
        (`user_ids` / `all_user_ids`), 18.0 stores both in `users`."""
        for name in ('all_user_ids', 'user_ids', 'users'):
            if name in groups._fields:
                return groups.mapped(name)
        return self.env['res.users']

    def _post_webhook(self, level, value):
        """Tell an outside service that a threshold was crossed.

        One POST of JSON, at most three seconds, and never an exception that
        reaches the caller: a dashboard is not a delivery guarantee, and a
        chat server that is down must not stop a cron or a page from
        finishing. The body carries a `text` line, which is all Slack and
        Teams need to render it, and the numbers beside it for anything that
        wants to do more.
        """
        self.ensure_one()
        url = (self.alert_webhook or '').strip()
        if not url.startswith(('http://', 'https://')):
            return False
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url') or ''
        message = self.alert_message or self.env._(
            '%(card)s is now %(value)s', card=self.name, value=value)
        body = json.dumps({
            'text': '%s · %s: %s' % (self.board_id.name, self.name, message),
            'board': self.board_id.name,
            'card': self.name,
            'level': level,
            'value': value,
            'threshold': self.alert_value,
            'operator': self.alert_operator,
            'url': '%s/odoo/action-ebshel_dashboard.action_dashboard_open' % base,
        }).encode('utf-8')
        request = Request(url, data=body, method='POST',
                          headers={'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=WEBHOOK_TIMEOUT) as response:
                _logger.info('Dashboard webhook for card %s: HTTP %s', self.id, response.status)
            return True
        except Exception as err:  # noqa: BLE001 - an unreachable hook is not our failure
            _logger.warning('Dashboard webhook for card %s failed: %s', self.id, err)
            return False

    def action_test_webhook(self):
        """The "Send a test" button: the same POST the threshold would make."""
        self.ensure_one()
        if not (self.alert_webhook or '').strip():
            raise UserError(self.env._('Write the address to post to first.'))
        ok = self._post_webhook(self.alert_level or 'warning',
                                self.compute_values('all').get('value', 0))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success' if ok else 'warning',
                'message': self.env._('Test posted.') if ok else self.env._(
                    'That address did not accept the message - see the server log.'),
            },
        }

    def _notify_alert(self, level, value):
        """Push the crossing to the audience through the bus - once."""
        self.ensure_one()
        message = self.alert_message or self.env._(
            '%(card)s is now %(value)s', card=self.name, value=value)
        for user in self._alert_audience():
            if not user.partner_id:
                continue
            self.env['bus.bus']._sendone(user.partner_id, 'simple_notification', {
                'type': 'danger' if level == 'danger' else 'warning',
                'title': self.env._('%(board)s: %(card)s', board=self.board_id.name, card=self.name),
                'message': message,
                'sticky': True,
            })

    def _check_alert_now(self):
        """Evaluate one card's threshold as the recorder would, and notify on
        the rising edge only: crossing is news, staying crossed is not."""
        self.ensure_one()
        Snapshot = self.env['dashboard.snapshot']
        payload = Snapshot._as_recorder(self).compute_values('all')
        if payload.get('error'):
            return False
        level = payload.get('alert') or False
        triggered = bool(level)
        if triggered and not self.alert_triggered:
            if self.alert_notify:
                self.sudo()._notify_alert(level, payload.get('value'))
            if self.alert_webhook:
                self.sudo()._post_webhook(level, payload.get('value'))
        if triggered != self.alert_triggered:
            self.sudo().write({
                'alert_triggered': triggered,
                'alert_triggered_on': fields.Datetime.now() if triggered else False,
            })
        return triggered

    @api.model
    def _cron_check_alerts(self):
        """Hourly: every number card with a threshold, on an active board."""
        Snapshot = self.env['dashboard.snapshot']
        items = Snapshot._capturable_items().filtered(lambda i: i.alert_operator != 'none')
        for item in items:
            try:
                item._check_alert_now()
            except Exception:  # noqa: BLE001 - one broken card must not stop the hour
                _logger.info('Threshold of dashboard card %s could not be checked', item.id,
                             exc_info=True)
        return True

    # ------------------------------------------------------------------
    # Labels, plain words and drill-through
    # ------------------------------------------------------------------
    @api.model
    def _raw_key(self, raw):
        """A JSON-safe key for a ``_read_group`` group value."""
        if isinstance(raw, models.Model):
            return raw.id or None
        if hasattr(raw, 'strftime'):
            return fields.Date.to_string(raw)
        if raw is False or raw is None:
            return None
        return raw

    def _group_label(self, field, raw):
        """What the legend shows for one group value."""
        if isinstance(raw, models.Model):
            return raw.display_name or self.env._('Undefined')
        # A boolean is answered before the empty check on purpose: `False` is
        # one of its two real values, not a missing one, and "No" is what the
        # reader clicked on.
        if field.type == 'boolean':
            return self.env._('Yes') if raw else self.env._('No')
        if raw is False or raw is None:
            return self.env._('Undefined')
        if field.type == 'selection':
            selection = dict(field._description_selection(self.env) or [])
            return selection.get(raw, raw)
        if hasattr(raw, 'strftime'):
            return self._date_label(raw, self.group_by_interval or 'month')
        return str(raw)

    @api.model
    def _date_label(self, day, interval):
        day = fields.Date.to_date(day)
        if interval == 'day':
            return day.strftime('%d %b')
        if interval == 'week':
            return day.strftime('W%V %Y')
        if interval == 'month':
            return day.strftime('%b %Y')
        if interval == 'quarter':
            return f'Q{(day.month - 1) // 3 + 1} {day.year}'
        return str(day.year)

    def describe(self):
        """What this card computes, in one plain sentence.

        Shown in the form while a card is built and as the card's default
        hint: "Sum of Total of Sales Orders where State = Sales Order, split by
        Salesperson, this month". A stored domain is read leaf by leaf; the
        sentence is a summary, not a parser, so nested logic is flattened.
        """
        self.ensure_one()
        if self.kind in FREE_KINDS:
            return self.env._('A note')
        if not self.model_id:
            return ''
        model_label = self.model_id.sudo().name or self.model_name
        if self.kind == 'formula':
            return self.env._('%(formula)s over %(model)s, where %(vars)s',
                              formula=self.formula or '?', model=model_label,
                              vars=', '.join(f'{v.name} = {v.label or v.name}'
                                             for v in self.variable_ids) or '-')
        if self.kind in SCATTER_KINDS:
            return self.env._('%(model)s by %(x)s and %(y)s', model=model_label,
                              x=self.measure_label or '?', y=self.measure2_label or '?')
        if self.aggregate == 'count' or not self.measure_name:
            what = self.env._('Count of %(model)s', model=model_label)
        else:
            what = self.env._('%(aggregate)s of %(field)s of %(model)s',
                              aggregate=dict(AGGREGATES).get(self.aggregate, self.aggregate),
                              field=self.measure_label or self.measure_name, model=model_label)
        parts = [what]
        where = self._describe_domain()
        if where:
            parts.append(self.env._('where %(condition)s', condition=where))
        if self.only_mine:
            parts.append(self.env._('assigned to me'))
        if self.group_by_field_name:
            parts.append(self.env._('split by %(field)s', field=self.group_by_field_label))
        if self.stack_field_name and self.kind in STACKED_KINDS:
            parts.append(self.env._('crossed with %(field)s', field=self.stack_field_label))
        if self.date_field_name:
            if self.period_mode == 'own':
                parts.append(str(dict(PERIODS).get(self.own_period, self.own_period)).lower())
            else:
                parts.append(self.env._("over the board's period"))
        if self.as_ratio:
            parts.append(self.env._('as a share of all %(model)s', model=model_label))
        return ', '.join(str(part) for part in parts)

    def _describe_domain(self):
        """The stored domain, leaf by leaf, in words."""
        Model = self.env.get(self.model_name)
        if Model is None:
            return ''
        try:
            domain = self._eval_domain(self.domain)
        except Exception:  # noqa: BLE001 - an unparsable domain is described as such
            return self.env._('(filter could not be read)')
        joiner = self.env._(' or ') if (domain and domain[0] == '|') else self.env._(' and ')
        words = []
        operators = {
            '=': '=', '!=': '≠', '>': '>', '>=': '≥', '<': '<', '<=': '≤',
            'in': self.env._('is one of'), 'not in': self.env._('is none of'),
            'ilike': self.env._('contains'), 'like': self.env._('contains'),
            'not ilike': self.env._('does not contain'), '=?': '=',
            'child_of': self.env._('under'), 'parent_of': self.env._('above'),
        }
        for leaf in domain:
            if not isinstance(leaf, (list, tuple)) or len(leaf) != 3:
                continue
            path, operator, value = leaf
            field = Model._fields.get(str(path).split('.')[0])
            label = str(field.string) if field is not None else str(path)
            if isinstance(value, bool) or value in (None, False):
                if operator in ('=', '=?'):
                    text = self.env._('is not set') if not value else self.env._('is set')
                elif operator == '!=':
                    text = self.env._('is set') if not value else self.env._('is not set')
                else:
                    text = f'{operators.get(operator, operator)} {value}'
                words.append(f'{label} {text}')
                continue
            if field is not None and field.type == 'selection' and isinstance(value, str):
                selection = dict(field._description_selection(self.env) or [])
                value = selection.get(value, value)
            elif field is not None and field.type == 'many2one' and isinstance(value, int):
                record = self.env[field.comodel_name].browse(value).exists()
                value = record.display_name if record else value
            elif isinstance(value, (list, tuple)):
                value = ', '.join(str(entry) for entry in value)
            words.append(f'{label} {operators.get(operator, operator)} {value}')
        return joiner.join(words)

    def _group_leaves(self, key, field_name=None, interval=None):
        """The extra domain that isolates one bar, slice or point."""
        self.ensure_one()
        name = field_name or self.group_by_field_name
        Model = self.env.get(self.model_name)
        field = Model._fields.get(name) if (Model is not None and name) else None
        if field is None:
            return []
        if key is None:
            return [(name, '=', False)]
        if field.type in DATE_TYPES:
            interval = interval or self.group_by_interval or 'month'
            start = fields.Date.to_date(key)
            return [(name, '>=', fields.Date.to_string(start)),
                    (name, '<', fields.Date.to_string(start + INTERVAL_STEP[interval]))]
        return [(name, '=', key)]

    @api.model
    def drill_action(self, item_id, period=None, part=None, focus=None, mine=False, filters=None):
        """The action opened when a card - or one of its parts - is clicked.

        ``part`` is the piece of the card that was clicked, as
        ``{'key': <group value>, 'on_date': <bool>}``; ``None`` means the card
        as a whole. It has to be a container rather than a bare ``key``,
        because ``None`` is itself a group value - the "Undefined" bucket - and
        "the unset ones" must not collapse into "all of them". ``focus`` and
        ``mine`` are the board's, so the records that open are the ones the
        card counted.
        """
        item = self.browse(int(item_id)).exists()
        if not item or item.kind in FREE_KINDS:
            return False
        item.check_access('read')
        period = item._effective_period(period)
        domain = item.item_domain(period, focus=focus, mine=mine, filters=filters)
        if isinstance(part, dict) and part.get('subvalue'):
            # A small number under the headline: its own records, not a group
            # of the card's split field.
            sub = item.subvalue_ids.filtered(lambda s: s.id == int(part['subvalue']))
            if sub:
                domain = sub._drill_domain(period, focus=focus, mine=mine, filters=filters)
        elif isinstance(part, dict):
            key = part.get('key')
            if part.get('on_date'):
                domain += item._group_leaves(key, field_name=item.date_field_name,
                                             interval=item.group_by_interval)
            else:
                domain += item._group_leaves(key)
        first = item.drill_view or 'list'
        views = [(False, first)] + [(False, view) for view in ('list', 'form')
                                    if view != first]
        context = {'create': False}
        if first in ('graph', 'pivot') and item.group_by_field_name:
            context['graph_groupbys'] = [item.group_by_field_name]
            context['pivot_row_groupby'] = [item.group_by_field_name]
            if item.measure_name and item.aggregate != 'count':
                context['graph_measure'] = item.measure_name
                context['pivot_measures'] = [item.measure_name]
        action = {
            'type': 'ir.actions.act_window',
            'name': item.name,
            'res_model': item.model_name,
            'domain': domain,
            'context': context,
            'views': views,
            'view_mode': ','.join(view for _id, view in views),
            'target': 'current',
        }
        if item.action_id:
            base = item.action_id.sudo().read()[0]
            action = dict(base, domain=domain, context=dict(
                safe_eval(base.get('context') or '{}', item._domain_eval_context()),
                **context))
            action['name'] = item.name
        return action

    def action_drill(self):
        """Open this card's records from the form view."""
        self.ensure_one()
        return self.drill_action(self.id, period=self.board_id.period)

    def action_duplicate(self):
        """A copy of this card on the same board, right after it."""
        self.ensure_one()
        copy = self.copy({'name': self.env._('%(name)s (copy)', name=self.name),
                          'sequence': self.sequence + 1})
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'dashboard.item',
            'res_id': copy.id,
            'views': [(False, 'form')],
            'target': 'current',
        }

    def action_view_history(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('History of %(name)s', name=self.name),
            'res_model': 'dashboard.snapshot',
            'domain': [('item_id', '=', self.id)],
            'context': {'default_item_id': self.id},
            'views': [(False, 'list'), (False, 'graph')],
        }

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    @api.model
    def _export_field_names(self):
        """Plain (non-relational) fields carried by an exported card."""
        return list(EXPORT_FIELDS)

    def _export_dict(self):
        """One card as a plain dict, portable between databases."""
        self.ensure_one()
        data = {name: self[name] for name in EXPORT_FIELDS}
        data['model'] = self.model_name or False
        for source, target in (('measure_field_id', 'measure'),
                               ('measure2_field_id', 'measure2'),
                               ('group_by_field_id', 'group_by'),
                               ('stack_field_id', 'stack_by'),
                               ('date_field_id', 'date_field'),
                               ('user_field_id', 'user_field')):
            field = self[source]
            data[target] = field.name if field else False
        data['variables'] = [{
            'name': variable.name, 'label': variable.label or '', 'domain': variable.domain,
            'aggregate': variable.aggregate,
            'measure': variable.measure_field_id.name if variable.measure_field_id else False,
        } for variable in self.variable_ids]
        data['subvalues'] = [{
            'name': sub.name,
            'domain': sub.domain,
            'aggregate': sub.aggregate,
            'measure': sub.measure_name or '',
            'color': sub.color,
            'show_share': sub.show_share,
            'sequence': sub.sequence,
        } for sub in self.subvalue_ids]
        return data
