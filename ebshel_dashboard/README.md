# Dynamic Dashboards

Build dashboards from the interface, on any model, and click every number down
to the records behind it.

A dashboard here is not an XML file and not a report. It is a page of **cards**,
and a card is one question asked of one model: how many, how much, split by
what, over which period. Anybody who can filter a list can build one.

## Twenty-nine kinds of card

| Card | Shows |
|------|-------|
| **Number** | A count, or the sum / average / max / min of a numeric field; optional target ring, comparison, sparkline, ratio, record count, goal pacing against the calendar, and **sub-values** - smaller numbers underneath, each a further condition, with its share of the headline |
| **Gauge** | A half dial filling towards a target |
| **Bullet** | A value bar against a target mark and its range |
| **Status light** | A light that turns amber or red when a threshold is crossed |
| **Formula** | A number made of others: `a / b * 100`, each letter an aggregate over its own filter |
| **Bar / Horizontal bars** | The measure split by any field |
| **Stacked bars** | Split by one field, divided by another |
| **Bars + line** | One measure as bars, a second as a line over them, with its own axis if you like |
| **Waterfall** | Each bar starts where the last ended: what added up to the total, and what took away |
| **Pareto** | The bars in order with the running share over them - which few make up most of it |
| **Bubble** | Two measures place each group, the number of records sizes it |
| **Treemap** | The shares as nested rectangles - the biggest fills the most room |
| **Lollipop** | Bars as thin stems with a dot on top |
| **Calendar heat map** | One cell per day, laid out by week, darker where more happened |
| **Heat map** | Two fields crossed: the darker the cell, the bigger the number |
| **Pivot** | Two fields crossed as a table with row and column totals |
| **Line / Area** | The measure along a date field, by day … year, as is, cumulative or smoothed, with a dashed forecast to the end of the period |
| **Pie / Donut / Polar** | The split as shares of the whole, full or half circle |
| **Radar** | A few categories compared around a wheel |
| **Funnel** | Stages narrowing from the widest to the smallest |
| **Scatter** | One point per record, two measures |
| **Progress bars** | One bar per value, with its share |
| **Table** | Value, count and share per group, with totals |
| **Top list** | The records themselves, ranked by the measure, with the columns you choose, bars in the cells, medals on the first three, and a colour rule that marks the rows that matter |
| **Note** | Words on the board - no model needed |

Every chart can be shown as a table in place, and downloaded as an image, as
CSV, as JSON, as a **PDF** or as a **spreadsheet** from its own menu. Numbers
can be written plainly, short (1.2M) or in the Indian grouping (12.3 L,
1.2 Cr).

## Everything is clickable

* Click a number and the records it counted open, as a **list, kanban, graph,
  pivot or calendar**, your choice per card.
* Click a bar, a slice, a point or a table row and the **whole board narrows
  to it**: every card on the same model follows, a chip says what the focus
  is and opens those records, `Esc` clears it.
* A **filter bar** asks the same question every time the board opens - a
  company, a salesperson, a stage - as dropdowns under the header. The values
  offered come from the data, and every card on that model follows the pick.
* Point a card at an existing action and the drill-through opens that
  action's own views instead of a plain list.

## The page

A collapsible sidebar lists the dashboards and the tools; a header carries
the period, the "Mine" switch, a ticking "Updated … ago", Arrange and + Card;
tile search narrows the grid; saved views keep a period, a focus and the
"mine" switch under a name.

* **Rename it from its title**: click the name in the header and type. A
  menu entry, if the board has one, is renamed with it.
* **Tabs**: several pages under one dashboard; a card sits on the Overview or
  on a tab, and a search looks across all of them.
* **It tells its story**: two or three plain sentences under the header -
  the biggest mover, thresholds crossed, a number out of line with its own
  past, a goal running behind - written from the numbers on the page.
* **It opens at once**: the layout arrives in one quick call and the numbers
  fill in four cards at a time, the page you are looking at first. On a table
  of 200,000 records the grid is on screen in about ten milliseconds of
  server time, whether the board has nine cards or forty.
* **Watchlist**: pin any number card and it follows you to every dashboard.
* **My notes**: a private drawer per board, saved as you type.
* **Quick find** (`Q`): boards, cards and actions by name, keyboard driven.
* A four-step tour on the first opening; skeleton cards while a board loads.
* **Presentation mode** fills the screen, fits the board to it and, with a
  slideshow time, moves on to the next dashboard. Print view for paper.
* Keyboard: `R` refresh, `F` present, `E` arrange, `M` only mine, `P` print,
  `Q` find, `Shift`+arrows switch boards, `Esc` clear.

## One period for the page

Twenty-three periods, from today to the next 90 days, or any two dates. The
period pill opens a panel that groups them by the unit they span - days,
weeks, months, quarters, years - with the custom range at the bottom, and
applies to every card that names a date field. A card can keep a period of
its own, and compare with the previous period or with the same period last
year. Cards that name no date field ignore the period and always show
everything.

## Built from the interface

* **Build a dashboard from a model** in one click: the cards come from the
  model's own fields - its status, its business date, its amount, the user it
  is assigned to.
* The card editor shows a **live preview** computed on real data as you type,
  pinned beside the fields, says **in plain words** what the card computes,
  draws **a sketch of every kind** in its own shape rather than an icon, and
  **suggests the fields worth building on** the moment you pick a model - the
  status, the salesperson, the amount, the business date, one click each.
  Visual pickers for the width on the grid, the colour and the icon
  (searchable by meaning).
* **Rename a dashboard, and change its icon, from the page showing it**: click
  the title to rename, click the icon for a searchable picker (search by
  meaning: revenue, late, team). Its menu entry follows the new name.
* **Arrange mode**: drag cards into place, drag their corner to resize them,
  duplicate them, move them to another dashboard, on a six, eight or twelve
  column grid.
* **"How it is drawn"** reads back what is set as a line of chips - "Split by
  Country, every month", "Above 10: danger" - each opening the tab that holds
  it; every option is a tile that explains itself in a sentence and lights up
  while it is on, and every rule is a sentence you read across. Only what the
  chosen kind can use is offered.
* Per card: sort order, multiplier, background, "hide when" rule, groups it
  is visible to, drill view, values on the chart, legend position, colours -
  its own palette, bar ends, grid lines, axis titles, an axis that follows the
  data, the total in the middle of a donut, and one bar made to stand out.
* Per board: a colour palette for the charts, density, auto-refresh from 15
  seconds, and **a menu of its own** - under Dashboards, under any existing
  menu, or as an app on the top bar, named and placed by you.
* **Export** a board to JSON and **import** it elsewhere; cards whose model is
  not installed in the target database are reported, not silently dropped.

## On paper and in a spreadsheet

**Download as PDF** from the dashboard's menu (or from a single card's) and
the file carries the charts you are looking at - the browser hands them over -
laid out on the grid the board uses, with the period, the focus and the "only
mine" switch named in the header. The numbers are recomputed on the server
with your own rights, so the file can never show a record you may not see.
No wkhtmltopdf is involved: the PDF is composed with reportlab, which Odoo
already requires. Print view (`P`) is still there for the browser's own
printer dialog.

**Download as a spreadsheet** hands over the numbers rather than the picture:
one sheet per card - the points, the crossed matrix, a list's own columns, a
number with its comparison, target and sub-values - each headed with the
period, the "only mine" switch and the filter bar that produced it.

## For the wall and the inbox

* **Digests**: a dashboard posts itself by e-mail - daily, weekly or monthly,
  at the hour you choose - with its headline numbers in the body and the whole
  page attached as a PDF. Every user on the list gets it computed with their
  own rights. "Send now" for a test.
* **Thresholds** colour a number and, on request, push a notification to the
  board's audience - or **POST to Slack, Teams or any address** - the moment
  they are crossed, once, on the crossing.
* Number cards are **recorded once a day**, so a sparkline can draw the real
  history, a card can say how it moved since the last point, a threshold has
  a rising edge to fire on - and a number two standard deviations from its
  own last thirty days wears an **"unusual"** badge that shows its working.

## Rights

A dashboard, or a single card, can name the **companies** it belongs to:
it is shown only while one of them is active in the company switcher, and
with none named it is shown everywhere.

Building a dashboard is a role: **Dashboards / Manager**, and the menu
follows it - a reader sees *Dashboard* and nothing else under the app, while
*Build from a Model* and everything under *Configuration* belong to the role.
Only that role
makes, changes or deletes dashboards and cards - in the models, not merely in
the interface, so a hidden button and a raw RPC are refused alike. A
**personal board** stays its owner's alone, even from another manager.

Everybody else **reads**: they open boards, narrow them with the filter bar
and the period, click through to the records, keep their own notes,
favourites, watchlist and saved views, and download the PDF. Every number is
computed with the reader's own rights: a card can never show a record its
reader is not allowed to see.

## Requirements

Odoo 19.0, Community or Enterprise. Depends on `web` and `bus` only: no
Enterprise module, no external Python package, no external JavaScript
library. Charts use the Chart.js build that ships inside Odoo, loaded only
when a board is opened.

## Tests

```
odoo-bin -d <db> -u ebshel_dashboard --test-enable --test-tags /ebshel_dashboard
```
