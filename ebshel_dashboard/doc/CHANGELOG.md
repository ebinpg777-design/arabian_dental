# Changelog

All notable changes to *Dynamic Dashboards*.

## 19.0.1.2.0 (in progress)

### Added

* **The dashboard's icon, changed where it is shown.** Click the icon beside
  the title and the same searchable picker the card editor uses opens under
  it - search by meaning ("revenue", "late", "team"), recent icons first. The
  title and the sidebar wear the new one at once; the server takes only a
  FontAwesome class.

* **A store page with screenshots.** `static/description/index.html` now
  carries seven pictures of the running module - the board, a card's menu,
  the period panel, the editor, the kind picker, the drawing options and the
  number sample - and an "Everything in the box" section listing every
  feature by subject: what a card counts, how it is drawn, the page, what
  comes out of it, and who may do what.

* **"How it is drawn", rebuilt.** The card editor's third section no longer
  opens on a wall of checkboxes. A line of chips at the top says what is
  already set - "Split by Country, every month", "Compared with the previous
  period", "Above 10: danger" - and clicking one opens the tab that holds it.
  The tabs are chips with an icon each; inside them every subject is a titled
  panel, every option a tile with a plain-words sentence under its name that
  lights up while it is on, and every rule a sentence you read across:
  "When the value is above 10, turn the card to danger and say ...". Choices
  that used to be dropdowns - the comparison, the threshold's loudness, the
  legend's place, the view a click opens - are segmented controls. Only what
  the chosen kind can actually use is offered, so a number card is not asked
  about bar ends.

* **Six new drawing options.** A **palette per card** (its own ramp, or the
  dashboard's, shown as the colours it would paint rather than named);
  **bar ends** rounded, square or pill; **grid lines** on or off; **axis
  titles**; an **axis that follows the data** instead of starting at zero,
  for a price or a rate that moves in a narrow band; the **total written in
  the middle of a donut**; and **"stand out"**, which draws the biggest, the
  smallest or the latest bar or point in full and steps the rest back.

* **How the number will read**, on the Numbers tab: the prefix, unit,
  grouping, decimals and multiplier applied to three sample values, updating
  as they are typed. It uses the very code the card uses, so the sample
  cannot drift from the board.

* **A header that gives the title its room.** The tile search rests as a
  magnifier and opens on a click; the period and "Mine" are joined into one
  control; the secondary buttons are ghosts so only "+ Card" is filled; and
  the refresh button carries a ring that fills up to the next automatic
  refresh.

* **A shell for the board.** A collapsible sidebar lists every dashboard the
  reader may open (icon, favourite star, personal marker, a filter box past
  six boards) with a "Build a dashboard" call to action and the tools: quick
  find, watchlist, notes, present, print. A hero header carries a greeting,
  the board title with its star and a Personal/Shared tag, pill controls for
  the period and "Mine", a ticking "Updated 40s ago" refresh, Arrange and
  + Card, and one "more" menu for everything else.
* **Tile search** narrows the grid as you type; `Esc` clears it.
* **Tabs.** A dashboard can have several pages: a card sits on the Overview
  or on a tab, the strip under the header switches between them with a count
  per page, Arrange mode adds a tab and moves cards onto one, and a tile
  search looks across every page. Tabs travel with the export and the copy.
* **A real period picker.** The browser's own dropdown is gone: the pill now
  opens a panel with "All time" on top, the twenty-two other periods in five
  columns - Days, Weeks, Months, Quarters, Years - the current one ticked,
  and "Any two dates" with both fields prefilled at the bottom. Escape or a
  click outside closes it. A saved view keeps a range; a board can switch
  ranges off.
* **Heat-map and pivot cards.** Two fields crossed: the heat map colours each
  cell by its size in the card's colour, the pivot adds row and column
  totals. A row click focuses the board like any other split.
* **Goal pacing.** A number with a target and a period shows a bar with a
  marker where the calendar stands: "on track", "ahead" or "behind", with the
  expected value today and the days left.
* **Forecast line.** Line and area charts on a period that is not over draw a
  dashed least-squares projection to the last bucket; the facts strip names
  the forecast at the end of the period.
* **Skeleton cards** while a board arrives, instead of a blank page or the
  previous board dimmed.
* **A tour on the first opening**, four steps, and "Show the tour" in the more
  menu.
* **Actions in quick find.** `Q` now offers refresh, present, print, arrange,
  add a card, save a view, build, export and the tour, next to boards and
  cards - a command palette.

* **Download as PDF**, for a whole dashboard and for one card. The charts in
  the file are the ones on screen - the browser hands its canvases over - and
  every figure beside them is recomputed on the server with the reader's own
  rights. The page keeps the grid: a card twelve columns wide is a full-width
  box, a number card is a box with its number, a table is a table, and a card
  the reader may not see never reaches the file. Landscape A4, the board name,
  the period, the focus and "only mine" in the header, who printed it and when
  in the footer. No wkhtmltopdf involved: the file is composed with reportlab,
  which Odoo already requires.

* **Digests.** A dashboard can post itself: every day, every week or on a day
  of the month, at an hour you pick, to a list of users and to plain
  addresses. The mail carries the headline numbers with their movement as a
  small table and the whole board attached as a PDF. Each user on the list
  gets it computed **with their own rights**, so two people on the same digest
  can rightly receive different numbers; plain addresses see it as the board's
  owner does. "Send now" posts the same mail at once. It goes through
  `ir.mail_server`, which lives in `base` - the module still depends on `web`
  and `bus` only.

* **Progressive loading.** The page no longer waits for the last card before
  showing the first. The board's layout comes back in one quick call - 7 to
  13 ms on a table of 200,000 rows, whether the board has nine cards or forty
  - and the numbers follow four cards at a time, the tab you are looking at
  first. Cards wear a quiet placeholder until their figures land.
* **One question, one query.** Within a page load, cards that ask the same
  thing of the same model now share the answer: a counting card and the
  record count beside it were two identical table scans, a top list ran its
  count twice, and every ratio re-counted what the card next to it had just
  counted. On 200,000 contacts that took a nine-card board from 880 ms to
  636 ms cold, and cut exactly-repeated queries from 10 to 4.

* **A filter bar.** A board can carry questions it asks every time it opens -
  "which company?", "whose?", "which stage?" - as dropdowns under the header.
  Pick a value and every card on that model follows, in the numbers, in the
  chunked reload, in the drill-through and in the PDF; cards on another model
  are left whole rather than emptied. A chip says what is narrowing the page,
  `Esc` clears it, and the values offered are read from the data itself, with
  the reader's rights, so a filter never lists a company nobody has a record
  for.
* **Bars and a line together.** A new card kind draws one measure as bars and
  a second as a line over them, both from a single group-by, with an optional
  second axis on the right for two measures that do not share a scale.
* **Webhooks on a threshold.** A card can POST to Slack, Teams or any address
  the moment it crosses its threshold - the same rising edge the notification
  uses, so a card that stays crossed does not keep shouting. The body carries
  a `text` line and the numbers beside it; a "Send a test" button posts one on
  demand. Three seconds, and an unreachable endpoint is logged, never raised.

* **A menu of its own, wherever you want it.** "Add to the Menu…" now asks
  where: under Dashboards, under any menu already in the database (Sales,
  Inventory, whatever the board is about), or as an app of its own on the top
  bar with an icon. You name the entry, place it in the order, and choose
  whether it is shown only to the groups the board is restricted to. Running
  it again on a board that already has a menu moves and renames that entry
  rather than leaving a second one behind, and removing it takes the client
  action with it.

* **Three more charts.** A **waterfall**, where each bar starts where the
  last one ended and a final bar is the total - what added up and what took
  away. A **pareto**, the bars in order with the running share over them on
  its own 0-100 axis, and a fact that reads "80% comes from 3 of 14". A
  **bubble**, where two measures place each group and the number of records
  behind it sizes the circle by area, not radius.
* **Two more switches on the charts they belong to.** *As shares* draws every
  stacked bar to the same height, so the pieces read as percentages; *Steps*
  draws a line as steps rather than slopes, for a value that holds until it
  changes.
* **Rows that stand out.** A list or a table can colour a row when a field of
  it meets a rule - greater than, less than, equal, contains, set, not set -
  in any of the card's eleven colours, evaluated on the server where the raw
  value is. Beside that: bars drawn behind the numbers in the cells, and gold,
  silver and bronze on the first three rows.
* **The card form knows the model.** Picking a model now offers the fields
  worth building on as one click each - the status, the salesperson, the
  amount, the business date - ranked by the words that name them, with the
  full dropdown still underneath for everything else.
* **Every card kind is drawn, not iconed.** The kind picker shows a small
  sketch of each chart in its own shape *and in its own colours* - a pie with
  its slices, a waterfall rising in green and falling in red, a pareto with
  its line over the bars - so the picture you want is the picture you click.
  Selection is said with the button rather than by repainting the drawing.

* **Remove a card, delete a dashboard.** A tile can be removed from the board
  itself - a bin in Arrange mode, "Remove this card" in its menu - and a whole
  dashboard from the more menu. Both ask first, by name. Deleting a board
  takes its cards, tabs and filters with it, and its menu entry and client
  action too: a menu pointing at a board that is not there is worse than no
  menu. The page then opens whichever dashboard comes next rather than showing
  a hole.
* **Done and Discard while arranging.** Dragging writes as it happens - that
  is what makes it feel immediate - so Discard means "put it back the way I
  found it": the arrangement is remembered when Arrange opens and restored on
  demand, cards, widths and tabs together.
* **An edit button on every tile.** The pencil is on the card itself for
  anyone who may edit the board, not only while arranging, and "Edit this
  card" sits in the card's own menu beside the downloads.
* **One role builds, everybody else reads.** *Dashboards / Manager* is now the
  single gate for making, changing and deleting dashboards and cards -
  enforced in the models, so a bare RPC is refused the same way the buttons
  are hidden. A personal dashboard stays its owner's alone, even from another
  manager. Readers keep everything that is theirs: opening boards, narrowing
  them, clicking through to the records, saving views, notes, favourites and
  the watchlist, and taking a PDF.

* **Small numbers under a big one.** A number card can carry sub-values:
  the questions that come straight after the headline - how many are late,
  how many are mine, what are they worth - each the card's own filter plus
  one more condition, drawn small underneath in its own colour, saying what
  share of the headline it is with a bar that width, and opening exactly the
  records it counted. They follow the period, the focus, "only mine" and the
  filter bar with the card, travel with it in export, import and copy, and
  two that ask the same question share one query.
* **Fewer queries per page.** The sidebar no longer computes every filter's
  dropdown values for every board it lists (a group-by per filter per board,
  on every load); the reader's favourite is looked up once per page rather
  than once per board; and the value chunks are cut so that cards asking the
  same questions - numbers together, charts by what they split on - land in
  the same request, where the memo answers each question once.

* **Companies, for dashboards and for tiles.** A board can name the
  companies it belongs to and is shown only while one of them is active in
  the company switcher; so can a single card, so one board can carry a tile
  that only one company sees. Empty means every company. The rule holds in
  the models and in the record rules alike, so the Configuration lists follow
  it too, and the header wears the company as a tag. A board pinned to its one
  company in 1.1 keeps that company: the upgrade carries it over.

* **Three more charts, drawn by the card.** A **treemap** lays the shares
  out as nested rectangles, squarified so the shapes stay close to square,
  the biggest labelled with its value; a **calendar heat map** puts one cell
  per day into weeks, Monday at the top, darker where more happened - a year
  of them when no period is set - and a cell focuses the board on that day; a
  **lollipop** draws the bars as thin stems with a dot on each. Stacked bars
  can lie **sideways**, for long labels and many categories.
* **"Unusual", with the arithmetic.** A number card compares today's value
  with its own last thirty daily recordings and, two standard deviations or
  more from their mean with at least a week to go on, wears a badge that says
  so - and its tooltip says exactly why: "3.1 standard deviations above the
  mean of the last 30 days (42 ± 3.9)". No model, no training: a z-score
  anyone can check.
* **The board tells its story.** Under the header, two or three plain
  sentences written from the numbers the page already holds: the biggest
  mover against the previous period and how many others moved, thresholds
  crossed, a number out of line with its past, a goal running behind. Nothing
  is asked of the server for it, and it says nothing when there is nothing to
  say.

* **The insight strip.** "What stands out" is now a row of chips rather than
  a paragraph: one per insight, each in the tone of what it says - green for
  a rise, red for a fall or a crossing, amber for a goal running behind - and
  each a button that scrolls to the card it names and flashes it. Hiding it
  hides it for that dashboard, not for all of them.
* **Number systems.** A card can be written plainly (1,234,567), short
  (1.2M), in the Indian grouping (12.3 L, 1.2 Cr), or left automatic - exact
  up to a hundred thousand, compact past it, which is what it always did. The
  choice reaches the card, the PDF and the spreadsheet alike.
* **Download as a spreadsheet.** A board, or one card, as .xlsx: one sheet
  per card carrying the numbers behind the picture - the points, the crossed
  matrix, the list's own columns, a number's comparison, target and
  sub-values - with the period, the "only mine" switch and the filter bar
  written at the top of each sheet. Built with xlsxwriter, which Odoo already
  requires.
* **Shorthands in a filter.** `%UID`, `%MYCOMPANY`, `%MYCOMPANIES`, `%TODAY`
  and `%NOW` can be typed straight into a card's filter, standing for the
  expressions the domain already understands.

* **Rename a dashboard from its own title.** Click the name in the header,
  type, press Enter - and if the board carries a menu entry, that entry is
  renamed with it, because the two should never disagree about what the page
  is called. Escape leaves it alone.
* **Add to the menu from the board.** The wizard that places a dashboard in
  the menu is now one click from the page itself, not only from its settings
  form.
* **A new icon and banner**, in Odoo's own idiom: a rounded tile with the
  product's own shapes on it, and a banner that shows the cards rather than
  describing them. The store page carries every feature since 1.1.

* **The workshop is behind the role.** "Build from a Model" and the whole
  Configuration menu - Dashboards, Cards, Card History, Import - are shown
  only to *Dashboards / Manager*. A reader is offered "Dashboard" and nothing
  that would refuse them. The role's own description in Settings now says what
  it grants.

### Fixed in this increment

* The header stayed one row only while it fitted: below about 1300px the
  tools wrapped onto a second line that sat over the first row of cards. It
  never wraps now - the title truncates, the tile search steps aside under
  1100px and the labels under 900px - and it is opaque, so what scrolls
  under it is hidden.
* `min(440px, 90vw)` and `min(92vw, 720px)` each broke the stylesheet build:
  libsass will not mix units inside `min()`, and a failed build makes the
  whole backend fall back to its last good CSS.
* `"%%"` in a translated string stays doubled on 18; the percent is passed
  as an argument now.
* A top list ordered by a measure was led by the records that have no value
  at all: Postgres sorts nulls first on a descending order, so the ordering
  now says NULLS LAST.
* A restored arrangement could name a tab of another board; it is back to
  the overview instead.
* A sub-value with an unreadable filter is refused when it is written, not
  when the board is read.
* The insight strip was styled inside the card block, so its rules never
  matched the strip; it lives with the shell now, where it is drawn.
* **KPI watchlist.** Pin any number card with its thumb-tack and it follows
  the reader to every dashboard, computed over its own board's period, in a
  strip above the grid. Stored on the reader's preference row.
* **My notes.** A private drawer beside the grid, one note per reader per
  board, saved as you type.
* **Quick find** (`Q`): boards at once, cards from the server, keyboard
  driven; picking a card opens its board and flashes the card into view.
* **Arrange mode** gains per-card Duplicate and "Move to…" another
  dashboard; the more menu gains Duplicate dashboard and Tidy the layout.
* **A real editor for cards.** Numbered sections, the live preview pinned on
  the right, the kind picker grouped into families that say what they need
  and what the chosen kind still lacks, a tip box per kind, the filter at
  full width, and a dialog titled after the card.
* **Facts under every chart** (total, groups, top group with its share, or
  peak and average for a line) and an info icon on every data card.
* **Three more kinds of card.** A **bullet** (value bar, target mark, range),
  a **formula** (a number made of others: each variable is an aggregate over
  its own filter, named by a letter, combined with `a / b * 100`), and a
  **scatter** (one point per record, two measures).
* **Eleven more periods**: tomorrow, last week, last quarter, month / quarter
  / year to date, last 12 months, last 365 days, next 7 / 30 / 90 days.
* **Per card**: compare with the previous period *or the same period last
  year*; sort bars and slices biggest first, smallest first, A-Z or Z-A; a
  running total or a moving average on a line; a multiplier; a half-circle
  pie, donut or polar; extra columns on a top list; a background (plain,
  tinted, solid); a "hide when" rule that keeps a card off the board until
  its value matters; groups the card is visible to; the change since the
  last recorded point; how long it took to compute (in the info tooltip);
  legend at the top or left.
* **Per board**: a colour palette for the charts (default, cool, warm, neon,
  sunset, or shades of one colour), a slideshow time for presentation mode,
  a menu entry of its own under Dashboards, and auto-refresh from 15 seconds.
* **Card menu**: show any chart as a table in place, download it as an
  image, its numbers as CSV, or the whole payload as JSON.
* **Saved views**: a period, a focus and the "mine" switch under a name, as
  chips under the header; save from the more menu, apply with a click.
* **Presentation mode** fits the board to the screen and, with a slideshow
  time, moves on to the next dashboard.
* Numbers count up to their new value instead of jumping.

### Changed

* The sidebar opens as an icon rail by default - the board gets the width,
  the tools stay one click away - and a reader who expands it is remembered
  in that browser.
* Rows of one height: a card's height is a minimum and the grid stretches
  every card of a row to the tallest, so rows have one bottom edge.
* Number cards wear a small-caps caption, a bigger number and a delta pill;
  the sparkline is a column beside the numbers with a date caption.
* A line or area over all time draws the trailing twelve buckets up to now.
* Tiles have a 12px radius, a soft shadow and a lift on hover; the board's
  colour is the accent of the whole page.

### Fixed

* Charts blew out of their cards on HiDPI screens: the canvas was pinned with
  `width: auto`, and an absolutely positioned replaced element with `auto`
  takes its intrinsic size - the bitmap, `css size × devicePixelRatio`. The
  canvas now sits in a sized box and keeps the CSS size Chart.js gives it.
  Measured at 1×, 1.44× and 2×.
* The sidebar sat above the page: the web client's `.o_action` rule lays an
  action out as a column, and the row direction had to say so louder.
* The kind tiles stacked one per row and the preview took a third of the
  form: a bare field widget in a sheet is laid out inline by the form.
* A card that cannot be computed said so twice.

## 19.0.1.1.0

### Added

* **Cross-filter.** Click a bar, a slice, a point or a row of a table and the
  whole board narrows to that value: every card on the same model follows, a
  chip in the toolbar says what the focus is and opens those records, the
  card the focus came from stays whole (dimmed elsewhere, so the reader can
  hop from one slice to the next), and `Esc` clears it. The field name sent
  by the browser is validated against the model, never trusted.
* **Twelve more kinds of card.** Charts: horizontal bars, stacked bars (two
  splits, with "Others" folding on both axes), area, donut, polar, radar,
  funnel. Tiles: gauge (a half dial towards a target), status light (turns
  amber or red on a threshold), progress bars, a table with value, count and
  share, and a note card that needs no model at all.
* **Build a dashboard from a model.** A wizard - also reachable from the
  toolbar and from the empty state - reads the model's fields (status,
  business date, amount, responsible user) and builds a sensible board from
  them. Different models give different boards.
* **Live preview in the card form**, computed on real data as you type, with a
  period selector of its own; the very same component and engine the board
  uses. Visual pickers for the kind of card, its width on the twelve-column
  grid, its colour, and its icon - searchable by name *and meaning*, grouped,
  recent picks first, shown in the card's own colour.
* **Drag the corner of a card to resize it.** Width snaps to columns, height is
  free within bounds; a live label says what you are about to get.
* **Sparklines** inside number cards, from the records along the date field
  or from the card's recorded history.
* **Daily history.** A nightly cron records every number card once (cards that
  answer per reader are left out); a history view and graph sit under
  Configuration.
* **Thresholds** with warning and danger levels: the card changes colour and
  carries a badge and a message. **Notifications** on the rising edge, hourly,
  to the board's audience: the owner of a personal board, the restricted
  groups of a shared one, or the dashboard managers.
* **Presentation mode** (full screen, larger type), **print view**, and
  **keyboard shortcuts**: R refresh, F present, E arrange, M only mine,
  P print, Shift+arrows switch boards, Esc clear.
* **Reader switches**: an "Only mine" toggle that narrows every card with a
  user field to the reader's own records, and a favourite board (star) that
  opens first.
* **Per card**: a period of its own, a ratio mode (share of all records), the
  record count under an aggregate, a prefix before the number, a comparison
  on every kind (delta badge on charts), the drill-through view of your choice
  (list, kanban, graph, pivot, calendar), a target drawn as a reference line
  on bar and line charts, values written on bars and points, legend position,
  one colour per bar (negatives in red), logarithmic axis, a refresh button,
  and one-click duplication.
* **Per board**: grid density (6, 8 or 12 columns), compact mode, and whether
  the "Only mine" switch is offered.
* **Plain-English summary** of what every card computes ("Sum of Total of
  Sales Orders where State = Sales Order, split by Salesperson, over the
  board's period"), shown in the form and used as the card's default hint.
* A second demo board, **Card Showcase**, with one of every kind.

### Changed

* Export format version 2 carries the board's grid settings and every new
  card field; version 1 files still import.
* The module now depends on ``bus`` (for threshold notifications).

### Notes

* 107 tests, `--test-tags /ebshel_dashboard`.
* Odoo's hotkey service whitelists letters, digits, navigation keys, `Esc`
  and (on 19.0) `<` `>` only - which is why boards are switched with
  Shift+arrows and not with brackets.

## 19.0.1.0.0

First release.

### Added

* **Boards and cards as records.** A `dashboard.board` is a page; a
  `dashboard.item` is one question asked of one model - filter, aggregate,
  split, period. Built from the interface, no XML and no upgrade.
* **Five kinds of card.** A number (count, sum, average, max, min), a bar
  chart, a line chart, a pie, and a top-N list of the records themselves.
* **Drill-through everywhere.** Clicking a number opens the records it counted;
  clicking a bar, a slice or a point opens exactly that slice, under the same
  domain the chart used. A card can also be pointed at an existing action, and
  then the drill opens that action's own views.
* **One period for the page.** Twelve periods from "today" to "last year",
  applied to each card's own date field. A number card can compare against the
  previous period of the same length.
* **Targets.** A number with a target draws as a progress ring.
* **Only My Records.** One card, one number per reader, and clicking it opens
  that reader's own records. The user field is validated against the model
  rather than trusted from the browser.
* **Arrange mode.** Drag cards into order and widen or narrow them on a
  twelve-column grid; the layout is saved for everybody who sees the board.
* **Export / import.** A board travels as a JSON file. Cards whose model is
  missing in the target database are listed in the result instead of failing
  the import.
* **Personal and shared boards.** A regular user's board is stamped as theirs;
  a manager publishes one for everybody, optionally restricted to user groups
  or to a company. Every number is computed with the reader's own rights.
* **Auto-refresh** from 30 seconds to 15 minutes, off by default.
* A demo board on `res.partner`, so the module shows something real straight
  after installation whatever apps are installed.

### Notes

* Charts are drawn with the Chart.js build that ships inside Odoo
  (`web.chartjs_lib`), loaded on demand: a database that never opens a
  dashboard never pays for it.
