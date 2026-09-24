# -*- coding: utf-8 -*-
{
    'name': 'Dynamic Dashboards',
    'version': '19.0.1.2.0',
    'summary': 'Build dashboards from the UI on any model - seventeen kinds of card, live '
               'numbers and charts, every one of them clickable down to the records behind it.',
    'description': """
Dynamic Dashboards
==================
Every team ends up asking the same handful of questions: how much did we sell
this month, how many tickets are still open, who is behind. Answering them
today means a developer, an XML file and a deployment - or an export to a
spreadsheet that is stale the moment it is sent.

This module turns those questions into **cards on a page you build yourself**,
from the interface, on any model in the database.

Seventeen kinds of card
-----------------------
* **Number**, with a target ring, a comparison against the previous period and
  a sparkline; **Gauge** filling towards a target; **Status light** that turns
  amber or red when a threshold is crossed.
* **Bar**, **Horizontal bars**, **Stacked bars** (two splits), **Line**,
  **Area**, **Pie**, **Donut**, **Polar**, **Radar** and **Funnel** - drawn
  with the Chart.js build that ships inside Odoo.
* **Progress bars**, a small **Table** with value, count and share, a ranked
  **Top list** of the records themselves, and a **Note** for the words a
  board needs.

Everything is clickable
-----------------------
* Click a number and the records it counted open - as a list, a kanban, a
  graph, a pivot or a calendar, your choice per card.
* Click a bar, a slice or a point and the **whole board narrows to it**: every
  card on the same model follows, a chip says what the focus is, and one more
  click opens those records.
* "Only My Records" makes one card answer differently for each person; the
  reader's own "Only mine" switch does it for the whole board.

Built from the interface
------------------------
* A **live preview** in the card form, computed on real data as you type;
  visual pickers for the kind of card, its width on the grid, its colour and
  its icon (searchable by meaning, grouped, with your recent picks first).
* **Build a dashboard from a model** in one click: the cards are chosen from
  the model's own fields - its status, its business date, its amount, the
  user it is assigned to.
* Arrange mode: drag cards into place, drag their corner to resize them,
  on a six, eight or twelve column grid.
* Every card says what it computes **in plain words**, so nobody has to read
  a domain to trust a number.

For the wall and the inbox
--------------------------
* **Presentation mode** fills the screen; auto-refresh keeps it current;
  keyboard shortcuts drive it; a print view lays it out on paper.
* **Thresholds** colour a number and, if you ask, push a notification to the
  board's audience the moment they are crossed.
* Number cards are **recorded once a day**, so a sparkline can show the real
  history and a threshold has a rising edge to fire on.
* A favourite board opens first; a period of the reader's choice applies to
  every card that names a date field, or a card keeps a period of its own.

Governance
----------
* **Personal boards**: every user can build boards only they can see.
* **Shared boards**: published by the "Dashboards / Manager" group, restricted
  to user groups and to a company.
* Every number is computed with the reader's own rights - a card can never show
  a record its reader is not allowed to see.
* Export a board to a JSON file and import it into another database; cards
  whose model is not installed there are reported, not silently dropped.

Compatibility
-------------
* Odoo 19.0, **Community and Enterprise**.
* Depends on ``web`` and ``bus`` only: no Enterprise-only module, no external
  Python package, no external JavaScript library.
    """,
    'category': 'Productivity',
    'author': 'Ebshel Technologies',
    'maintainer': 'Ebshel Technologies',
    'website': 'https://ebshel.com',
    'support': 'ebinpg777@gmail.com',
    'license': 'OPL-1',
    'price': 199.00,
    'currency': 'USD',
    'depends': ['web', 'bus'],
    'data': [
        'security/dashboard_groups.xml',
        'security/ir.model.access.csv',
        'data/dashboard_cron.xml',
        'views/dashboard_item_views.xml',
        'views/dashboard_board_views.xml',
        'views/dashboard_snapshot_views.xml',
        'wizard/dashboard_import_views.xml',
        'wizard/dashboard_build_views.xml',
        'wizard/dashboard_menu_views.xml',
        'views/dashboard_menus.xml',
    ],
    'demo': [
        'demo/dashboard_demo.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'ebshel_dashboard/static/src/core/board_colors.js',
            'ebshel_dashboard/static/src/core/number_format.js',
            'ebshel_dashboard/static/src/dashboard/**/*.js',
            'ebshel_dashboard/static/src/dashboard/**/*.xml',
            'ebshel_dashboard/static/src/fields/**/*.js',
            'ebshel_dashboard/static/src/fields/**/*.xml',
            'ebshel_dashboard/static/src/scss/dashboard.scss',
            'ebshel_dashboard/static/src/scss/fields.scss',
        ],
    },
    'images': [
        'static/description/banner.png',
    ],
}
