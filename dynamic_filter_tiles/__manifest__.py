# -*- coding: utf-8 -*-
{
    'name': 'Dynamic Filter Tiles',
    'version': '19.0.2.1.0',
    'summary': 'Turn any filter into a one-click tile on every list and kanban view - live counts, '
               'KPIs, trends, thresholds, breakdowns and a tile wall. Built from the UI, no code.',
    'description': """
Dynamic Filter Tiles
====================
Every team has the same three or four questions about a list: how many are late,
how many are waiting on me, what did we win this month. Today the answer is a
filter somebody has to remember, or a dashboard nobody can edit.

This module puts those answers **on top of the list itself**, as a ribbon of
clickable tiles: a label, a live number, a trend - and a filter behind it.
Tiles are ordinary records, so anyone can build, colour, size and share them
straight from the view they are already looking at.

Works on **every model** in Odoo Community and Enterprise: contacts, sales
orders, tasks, invoices, tickets, repairs, your own custom models.

The Tile Studio
---------------
* **Capture the current filter** - filter your list the usual way, hit the "+"
  ghost tile, and the tile is born pre-filled with exactly that domain.
* **Auto-generate** - one click reads the model's status field and creates a
  full, colour-coded, icon-matched tile set (states, stages, priorities...).
* **Live preview** - the tile editor renders the real tile, with the real count
  from your database, updating as you type the domain.
* **Drag to reorder** - grab a tile and drop it; the order is saved for everyone.
* **Icon & colour pickers** - searchable FontAwesome grid and a swatch palette.
* **Export / import** - move a whole ribbon to another database as a JSON file.

The tile widget
---------------
* Animated count-up values, record counts or sum / avg / max / min of any field.
* Inline **sparkline** plus a period-over-period delta - grouped on a date field
  of the records, or drawn from the tile's own **recorded history**.
* **Break a tile open**: one click splits it by status, salesperson, stage...
  and every row of the popover filters the view down to that slice.
* **Thresholds**: give a tile a limit and it turns into a warning when the
  number crosses it - on screen, and as a push notification if you want one.
* **Share bar** showing what slice of the current view each tile holds, or a
  **progress ring** when the tile is given a target.
* **Multi-select**: ctrl / cmd-click, or a multi-select mode for touch screens;
  selected tiles combine as *any* of them (OR) or *all* of them (AND).
* **Keyboard**: Alt + 1…9 applies a tile, Alt + 0 clears them.
* Tiles apply as ordinary, removable search facets - nothing is hidden from the
  user, and every other filter keeps working.
* Tiles stay live: they recompute against whatever the search bar currently
  holds, so they always describe the records in front of you.
* **Several rows**: stack up to six rows of tiles and drag tiles between them;
  each row scrolls and is sized on its own, and can be given a name.
* Drag a tile's edge to set its width, a row's edge to set its height.
* Compact / comfortable density, auto-refresh, and a collapsible bar, all
  remembered per model.

The Tile Wall
-------------
* Every ribbon in the database on one screen, grouped by model, alerting tiles
  first - and each tile still opens its records, filtered.
* A "Live" mode that refreshes itself: a wall-mounted screen, for free.

Governance
----------
* **Personal tiles**: every user can build tiles only they can see, without
  touching what the team sees.
* **Shared tiles**: published by the "Filter Tiles / Manager" group, restricted
  to user groups, to a company, to list views or kanban views - and, when a
  model is reached through several menus, to a single action.
* Every number is counted with the reader's own rights - a tile can never
  surface a record its reader is not allowed to see.

Compatibility
-------------
* Odoo 19.0, **Community and Enterprise**.
* Depends on ``web`` and ``bus`` only: no Enterprise-only module, no external
  Python package, no external JavaScript library.
* Adds nothing to existing views - the ribbon is injected by extending the list
  and kanban controller templates, so views defined by other modules (including
  Studio-made ones) inherit it as well.
    """,
    'category': 'Productivity',
    'author': 'Ebin P G',
    'maintainer': 'Ebin P G',
    'website': 'https://www.linkedin.com/in/ebin-p-g',
    'support': 'ebinpg777@gmail.com',
    'license': 'OPL-1',
    'price': 59.00,
    'currency': 'USD',
    'depends': ['web', 'bus'],
    'data': [
        'security/filter_tiles_groups.xml',
        'security/ir.model.access.csv',
        'data/filter_tile_cron.xml',
        'views/filter_tile_views.xml',
        'views/tile_overview_views.xml',
    ],
    'demo': [
        'demo/filter_tile_demo.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'dynamic_filter_tiles/static/src/core/tile_colors.js',
            'dynamic_filter_tiles/static/src/core/tile_domain.js',
            'dynamic_filter_tiles/static/src/core/tile_registry_service.js',
            'dynamic_filter_tiles/static/src/core/tile_alert_service.js',
            'dynamic_filter_tiles/static/src/components/**/*.js',
            'dynamic_filter_tiles/static/src/components/**/*.xml',
            'dynamic_filter_tiles/static/src/fields/**/*.js',
            'dynamic_filter_tiles/static/src/fields/**/*.xml',
            'dynamic_filter_tiles/static/src/views/**/*.js',
            'dynamic_filter_tiles/static/src/views/**/*.xml',
            'dynamic_filter_tiles/static/src/scss/filter_tiles.scss',
        ],
    },
    'images': [
        'static/description/banner.png',
    ],
}
