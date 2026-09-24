# Roadmap

What is built, what is next. One line per feature; the version that shipped it,
or the increment it is planned for.

## Shipped in 1.0.0

- Boards and cards as records, built from the interface
- Number, bar, line, pie and top-list cards
- Drill-through from every card and every part of a chart
- Twelve periods applied to each card's own date field
- Previous-period comparison on numbers
- Target ring on numbers
- Only My Records
- Arrange mode: drag to reorder, widen/narrow
- Export / import as JSON, with a wizard that names what it skipped
- Personal and shared boards, group and company restriction
- Auto-refresh
- Demo board on Contacts

## Shipped in 1.1.0

- Cross-filter ("focus"): click a bar, slice or point and every card of the
  same model follows; chip to open or clear; source card highlighted
- Board generator: a ready-made board from any model's own fields
- Sparklines inside number cards, live or from recorded history
- Presentation mode (full screen), print view, keyboard shortcuts
- Seven more charts: horizontal bars, stacked bars, area, donut, polar,
  radar, funnel
- Five more tile types: gauge, status light, progress bars, table, note
- Live preview in the card form, computed on real data
- Visual pickers: kind, width on the grid, colour, icon (searchable by
  meaning, grouped, recent first)
- Drag the corner of a card to resize it
- Thresholds with warning/danger levels and a badge
- Threshold notifications on the rising edge (hourly cron)
- Daily history of number cards (nightly cron) and a history view
- Ratio mode (share of all records), record count under an aggregate,
  prefix before the number
- Per-card period override
- Comparison on every card kind
- Drill-through view choice: list, kanban, graph, pivot, calendar
- Target as a reference line on bar and line charts
- Values written on bars and points; legend position; multicolour bars;
  logarithmic axis
- Per-card refresh
- Reader-side "Only mine" switch for the whole board
- Favourite board per user, opened first
- Grid density (6 / 8 / 12 columns) and compact mode per board
- Plain-English summary of what each card computes
- One-click card duplication

## Shipped in 1.2.0

- The shell: collapsible sidebar with the board list and tools, hero header
  with greeting and "updated … ago", tile search, one "more" menu
- KPI watchlist across dashboards; private notes per board; quick find
- Arrange mode: duplicate a card, move it to another dashboard, tidy the
  layout; duplicate a dashboard
- A real editor: two columns, preview pinned, numbered steps, kinds grouped
  by family with missing-field hints, tips per kind
- Facts under every chart, an info icon on every card
- Rows of one height; sparkline column; polished tiles; the board's colour
  as the page accent
- Bullet, formula (variables + safe arithmetic) and scatter cards
- Eleven more periods; compare with the same period last year; sort order;
  running total and moving average; multiplier; half circles; list columns;
  card backgrounds; "hide when" rules; card-level groups; change since the
  last recording; compute time; legend top/left
- Board palettes; slideshow; a menu entry per board; auto-refresh from 15 s
- Card menu: chart as a table, download as image / CSV / JSON
- Saved views (period + focus + mine) per reader
- Presentation mode fits the board to the screen
- Numbers count up
- Download the board, or one card, as a PDF - the charts from the screen,
  the figures recomputed on the server
- Scheduled e-mail digest: the board's numbers and its PDF, per recipient
- Progressive loading: the layout first, the numbers four cards at a time
- One query per question inside a page load
- A filter bar of the board's own questions, values read from the data
- Bars-and-line card, with a second axis
- Slack / Teams / any webhook on a threshold crossing
- Waterfall, pareto and bubble charts; stacked bars as shares; stepped lines
- Conditional row colouring, cell bars and medals on lists and tables
- Suggested fields and drawn card kinds in the editor
- Sub-values under a number card, with shares and drill-through
- Companies on boards and on single cards; empty means everywhere
- Treemap, lollipop and calendar heat map; sideways stacks
- An "unusual" badge from the card's own recordings; the board's narrative
- Number systems, including the Indian lakh-and-crore grouping
- Spreadsheet (.xlsx) export of a board or a card
- %UID, %MYCOMPANY, %TODAY shorthands in a card's filter
- Board tabs: several pages under one dashboard, cards moved between them
  from Arrange mode
- Any two dates as the period; saved views keep the range
- Heat-map and pivot cards: two fields crossed
- Goal pacing on numbers: where the value stands against the calendar
- Forecast line to the end of the period on line and area charts
- Skeleton cards while a board loads; a four-step tour on first opening;
  actions in quick find
- Fixes: HiDPI canvas sizing, sidebar direction, widget widths, double error

## Planned for 1.3.0

- Public read-only link for a wall display without a login
- Card comments with @mentions (chatter)
- Map card (country / state) from a partner address
- Timeline card for date ranges
- Trend arrow on every list row (vs previous period)
- Card templates gallery: common questions per installed app
- Board versions: undo the last arrangement
- Per-card SLA colouring (age of the oldest record)
- "Compare two periods" side-by-side board mode
