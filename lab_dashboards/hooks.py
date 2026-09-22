# -*- coding: utf-8 -*-
"""Seed the Management boards.

Seeded in Python rather than shipped as XML data for one reason: a board has to name the
menu it hangs under, and that id lives inside `config_data` as JSON. XML data has no way
to resolve `ref()` into the middle of a JSON string, so the whole record would have to be
patched afterwards anyway.

Two properties matter more than the tiles themselves:

* **It only creates what is missing.** These are ordinary `spectrum.dashboard.config`
  records that anyone with the Spectrum Administrator role is expected to edit from the
  builder. Rewriting them on every upgrade would quietly throw that work away, so an
  existing board is left exactly as it is.
* **A tile whose model is not installed is dropped at seed time**, rather than shipped to
  render as "No model selected". A board is read at a glance; a broken tile on it costs
  more than the tile was worth.
"""
import json
import logging

_logger = logging.getLogger(__name__)

PARENT_MENU = 'lab_ceo_dashboard.menu_lab_ceo_root'

# Only what is genuinely open — an order still with the lab, not one already delivered.
OPEN_WO = "[('state','not in',['done','cancel'])]"
SOLD = "[('state','in',['sale','done'])]"
CUSTOMER_INVOICE = "[('move_type','=','out_invoice'),('state','=','posted')]"


def _boards():
    """The four boards, in the order they should appear under Management."""
    return [
        {
            'key': 'lab_dash_sales',
            'name': 'Sales & Cases',
            'icon': 'fa-shopping-cart',
            'color': 'blue',
            'items': [
                {'item_type': 'kpi', 'name': 'Cases Booked', 'model_name': 'sale.order',
                 'aggregation': 'count', 'domain': SOLD,
                 'kpi_icon': 'fa-cube', 'kpi_color': 'blue',
                 'metric_def': 'Sale orders in Sales Order or Done state.'},
                {'item_type': 'kpi', 'name': 'Revenue', 'model_name': 'sale.order',
                 'aggregation': 'sum', 'measure_field': 'amount_total', 'domain': SOLD,
                 'kpi_icon': 'fa-inr', 'kpi_color': 'green',
                 'metric_def': 'Total of confirmed orders, taxes included.'},
                {'item_type': 'kpi', 'name': 'Awaiting Verification',
                 'model_name': 'sale.order', 'aggregation': 'count',
                 'domain': "[('verification_state','=','to_verify')]",
                 'kpi_icon': 'fa-hourglass-half', 'kpi_color': 'amber',
                 'metric_def': 'Cases an executive has sent in that nobody has checked yet.'},
                {'item_type': 'chart', 'name': 'Cases by Status', 'model_name': 'sale.order',
                 'aggregation': 'count', 'groupby_field': 'state', 'chart_type': 'bar'},
                {'item_type': 'chart', 'name': 'Revenue by Month', 'model_name': 'sale.order',
                 'aggregation': 'sum', 'measure_field': 'amount_total', 'domain': SOLD,
                 'groupby_field': 'date_order', 'groupby_interval': 'month',
                 'chart_type': 'line'},
                {'item_type': 'chart', 'name': 'Top Clinics', 'model_name': 'sale.order',
                 'aggregation': 'sum', 'measure_field': 'amount_total', 'domain': SOLD,
                 'groupby_field': 'partner_id', 'chart_type': 'donut', 'limit': 8},
                {'item_type': 'list', 'name': 'Latest Cases', 'model_name': 'sale.order',
                 'list_fields': 'name,partner_id,patient,amount_total,state', 'limit': 10,
                 'sort_field': 'date_order', 'sort_dir': 'desc'},
            ],
        },
        {
            'key': 'lab_dash_field',
            'name': 'Field Force',
            'icon': 'fa-map-marker',
            'color': 'teal',
            'items': [
                {'item_type': 'kpi', 'name': 'Visits', 'model_name': 'lab.visit',
                 'aggregation': 'count', 'date_field': 'date',
                 'kpi_icon': 'fa-map-marker', 'kpi_color': 'teal'},
                {'item_type': 'kpi', 'name': 'Cash Collected', 'model_name': 'lab.visit',
                 'aggregation': 'sum', 'measure_field': 'collected', 'date_field': 'date',
                 'kpi_icon': 'fa-money', 'kpi_color': 'green',
                 'metric_def': 'Cash taken from clinics during visits.'},
                {'item_type': 'kpi', 'name': 'Case Slips', 'model_name': 'lab.case',
                 'aggregation': 'count',
                 'kpi_icon': 'fa-file-text-o', 'kpi_color': 'indigo'},
                {'item_type': 'kpi', 'name': 'Distance (km)', 'model_name': 'lab.trip',
                 'aggregation': 'sum', 'measure_field': 'distance', 'date_field': 'date',
                 'kpi_icon': 'fa-road', 'kpi_color': 'purple'},
                {'item_type': 'chart', 'name': 'Visits by Executive',
                 'model_name': 'lab.visit', 'aggregation': 'count',
                 'groupby_field': 'user_id', 'chart_type': 'bar'},
                {'item_type': 'chart', 'name': 'What Came of Them',
                 'model_name': 'lab.visit', 'aggregation': 'count',
                 'groupby_field': 'outcome', 'chart_type': 'donut'},
                {'item_type': 'chart', 'name': 'Visits by Day', 'model_name': 'lab.visit',
                 'aggregation': 'count', 'groupby_field': 'date',
                 'groupby_interval': 'day', 'chart_type': 'line'},
                {'item_type': 'list', 'name': 'Latest Visits', 'model_name': 'lab.visit',
                 'list_fields': 'name,partner_id,user_id,date,outcome', 'limit': 10,
                 'sort_field': 'date', 'sort_dir': 'desc'},
            ],
        },
        {
            'key': 'lab_dash_floor',
            'name': 'Production Floor',
            'icon': 'fa-cogs',
            'color': 'purple',
            'items': [
                {'item_type': 'kpi', 'name': 'Open Work Orders',
                 'model_name': 'mrp.workorder', 'aggregation': 'count', 'domain': OPEN_WO,
                 'kpi_icon': 'fa-wrench', 'kpi_color': 'purple',
                 'metric_def': 'Work orders not yet done or cancelled.'},
                {'item_type': 'kpi', 'name': 'Jobs in Progress',
                 'model_name': 'mrp.production', 'aggregation': 'count',
                 'domain': "[('state','in',['confirmed','progress','to_close'])]",
                 'kpi_icon': 'fa-industry', 'kpi_color': 'blue'},
                {'item_type': 'kpi', 'name': 'Finished', 'model_name': 'mrp.production',
                 'aggregation': 'count', 'domain': "[('state','=','done')]",
                 'date_field': 'date_finished',
                 'kpi_icon': 'fa-check', 'kpi_color': 'green'},
                {'item_type': 'chart', 'name': 'Where the Work Is',
                 'model_name': 'mrp.workorder', 'aggregation': 'count', 'domain': OPEN_WO,
                 'groupby_field': 'workcenter_id', 'chart_type': 'bar',
                 'metric_def': 'Open jobs per station — the tallest bar is the bottleneck.'},
                {'item_type': 'chart', 'name': 'Jobs by Status',
                 'model_name': 'mrp.production', 'aggregation': 'count',
                 'groupby_field': 'state', 'chart_type': 'donut'},
                {'item_type': 'chart', 'name': 'Finished by Month',
                 'model_name': 'mrp.production', 'aggregation': 'count',
                 'domain': "[('state','=','done')]", 'groupby_field': 'date_finished',
                 'groupby_interval': 'month', 'chart_type': 'line'},
                {'item_type': 'list', 'name': 'Open Work Orders',
                 'model_name': 'mrp.workorder', 'domain': OPEN_WO,
                 'list_fields': 'name,production_id,workcenter_id,state', 'limit': 10},
            ],
        },
        {
            'key': 'lab_dash_money',
            'name': 'Money',
            'icon': 'fa-money',
            'color': 'green',
            'items': [
                {'item_type': 'kpi', 'name': 'Owed to Us', 'model_name': 'account.move',
                 'aggregation': 'sum', 'measure_field': 'amount_residual',
                 'domain': "[('move_type','=','out_invoice'),('state','=','posted'),"
                           "('payment_state','not in',['paid','reversed'])]",
                 'kpi_icon': 'fa-exclamation-circle', 'kpi_color': 'red',
                 'metric_def': 'Unpaid balance on posted customer invoices.'},
                {'item_type': 'kpi', 'name': 'Invoiced', 'model_name': 'account.move',
                 'aggregation': 'sum', 'measure_field': 'amount_total',
                 'domain': CUSTOMER_INVOICE, 'date_field': 'invoice_date',
                 'kpi_icon': 'fa-file-text', 'kpi_color': 'blue'},
                {'item_type': 'kpi', 'name': 'Received', 'model_name': 'account.payment',
                 'aggregation': 'sum', 'measure_field': 'amount',
                 'domain': "[('payment_type','=','inbound')]", 'date_field': 'date',
                 'kpi_icon': 'fa-inr', 'kpi_color': 'green'},
                {'item_type': 'kpi', 'name': 'Petty Cash in Hand',
                 'model_name': 'petty.cash.allocation', 'aggregation': 'sum',
                 'measure_field': 'amount_balance',
                 'domain': "[('state','=','allocated')]",
                 'kpi_icon': 'fa-inbox', 'kpi_color': 'amber'},
                {'item_type': 'chart', 'name': 'Invoices by Payment Status',
                 'model_name': 'account.move', 'aggregation': 'count',
                 'domain': CUSTOMER_INVOICE, 'groupby_field': 'payment_state',
                 'chart_type': 'donut'},
                {'item_type': 'chart', 'name': 'Invoiced by Month',
                 'model_name': 'account.move', 'aggregation': 'sum',
                 'measure_field': 'amount_total', 'domain': CUSTOMER_INVOICE,
                 'groupby_field': 'invoice_date', 'groupby_interval': 'month',
                 'chart_type': 'bar'},
                {'item_type': 'chart', 'name': 'Who Owes Most',
                 'model_name': 'account.move', 'aggregation': 'sum',
                 'measure_field': 'amount_residual',
                 'domain': "[('move_type','=','out_invoice'),('state','=','posted'),"
                           "('payment_state','not in',['paid','reversed'])]",
                 'groupby_field': 'partner_id', 'chart_type': 'bar', 'limit': 8},
                {'item_type': 'list', 'name': 'Unpaid Invoices',
                 'model_name': 'account.move',
                 'domain': "[('move_type','=','out_invoice'),('state','=','posted'),"
                           "('payment_state','not in',['paid','reversed'])]",
                 'list_fields': 'name,partner_id,invoice_date,amount_residual',
                 'limit': 10},
            ],
        },
    ]


def _usable(env, item):
    """A tile is only worth shipping if its model and every field it names exist."""
    model = item.get('model_name')
    if not model or model not in env:
        return False
    fields = env[model]._fields
    named = [item.get('measure_field'), item.get('groupby_field'), item.get('date_field'),
             item.get('sort_field')]
    named += [c.strip() for c in (item.get('list_fields') or '').split(',') if c.strip()]
    return all(f in fields for f in named if f)


def post_init_hook(env):
    parent = env.ref(PARENT_MENU, raise_if_not_found=False)
    if not parent:
        _logger.warning("lab_dashboards: %s not found — boards not seeded", PARENT_MENU)
        return

    Config = env['spectrum.dashboard.config']
    Data = env['ir.model.data']
    admin_group = env.ref('spectrum_dashboard.spectrum_user', raise_if_not_found=False)

    for sequence, board in enumerate(_boards(), start=10):
        xmlid = 'lab_dashboards.%s' % board['key']
        if env.ref(xmlid, raise_if_not_found=False):
            continue        # already seeded — leave whatever it has become alone

        items = [i for i in board['items'] if _usable(env, i)]
        dropped = len(board['items']) - len(items)
        if not items:
            _logger.info("lab_dashboards: %r skipped, no usable tiles", board['name'])
            continue
        if dropped:
            _logger.info("lab_dashboards: %r dropped %s tile(s) for missing "
                         "models/fields", board['name'], dropped)

        record = Config.create({
            'name': board['name'],
            'sequence': sequence,
            'is_dashboard': True,
            'state': 'active',
            'group_ids': [(6, 0, admin_group.ids)] if admin_group else False,
            'config_data': json.dumps({
                'color': board['color'],
                'icon': board['icon'],
                'menu_id': parent.id,
                'menu_name': board['name'],
                'period': '',
                'items': items,
            }),
        })
        Data.create({
            'name': board['key'],
            'module': 'lab_dashboards',
            'model': 'spectrum.dashboard.config',
            'res_id': record.id,
            # Seed data, not managed data: an upgrade must never overwrite a board
            # somebody has since rearranged in the builder.
            'noupdate': True,
        })
        _logger.info("lab_dashboards: seeded %r with %s tile(s)",
                     board['name'], len(items))
