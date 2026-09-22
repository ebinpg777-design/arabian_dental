# Changelog

All notable changes to *Dynamic Filter Tiles*.

## 19.0.2.1.0

### Added

* **Only My Records.** One shared tile, a different number for every viewer: tick the box, and
  the tile counts the records of whoever is looking - 12 for one salesperson, 3 for another -
  and clicking filters to their own records too. The person field ("my" means the salesperson,
  the assignee, ...) is picked automatically where the model has an obvious one, and can be any
  stored user field. Works in the ribbon, the tile wall, the breakdown popover and the live
  preview in the form.
* **`uid` in typed domains.** The tile domain has always evaluated `uid`, `user`, `today`, `now`,
  `context_today()` and `relativedelta` server-side - the same names a record rule may use; the
  clicked-tile facet and the tile wall now evaluate them identically in the browser. "Only My
  Records" is the same power without typing an expression.

### Changed

* A **shared** per-viewer tile keeps no history and sends no alerts - there is no single number
  to record. A **private** one still does both, measured through its owner's eyes; the form says
  so next to the switch.

## 19.0.2.0.0

The release that turns the ribbon from a filter bar into something that watches your data.

### Added

* **Private tiles.** Every internal user can now build tiles only they can see, without touching
  what the team sees. Managers still own the shared ribbon and publish to everybody. Enforced by
  record rules on `owner_id`, not by the UI.
* **Thresholds and alerts.** A tile can be given a limit: it turns into a warning as soon as its
  number crosses it, and an hourly scheduled action pushes a notification (with a "Show me" button)
  to the tile's audience. Only the crossing notifies, never the state.
* **Recorded history.** A tile can store its own value once a day, so its sparkline shows how the
  number moved rather than when the records were dated - and works on models with no date field.
  New model `filter.tile.snapshot`, new nightly scheduled action, `Record a Point Now` on the form.
* **Breakdowns.** One click splits a tile by status, salesperson, country, stage or month, in a
  single grouped read; every row of the popover filters the view down to that slice.
* **The Tile Wall.** A client action showing every ribbon in the database, grouped by model, with an
  "only alerts" filter and a "Live" mode that refreshes itself every minute.
* **Keyboard shortcuts.** `Alt` + `1…9` applies a tile, `Alt` + `0` clears them.
* **Auto refresh** for the bar (off / 30s / 1min / 5min), remembered per model.
* **Export / import** of a tile set as JSON, matching fields by name so a ribbon survives the move
  to another database.
* **Copy to My Tiles**: take a private copy of a shared tile and bend it to your own taste.
* **Multiple rows.** A ribbon can be stacked on up to six rows (new `row` field): "Add a row"
  from the studio menu or the bar, a "+" per row that creates a tile there, and drag-and-drop
  of a tile onto another row. The whole arrangement - row and order - is persisted in one
  `arrange_tiles` call, skipping tiles the user may not write.
* **Action scope.** A tile can be pinned to one action instead of the whole model (new optional
  `action_id`), because customer invoices, vendor bills and journal entries are all the same model
  and should not share a ribbon. Capturing a filter on a menu that narrows the model offers the
  scope; the generator offers it too, and "replace" only clears the ribbon in the same scope.
* **Explicit multi-select**: a checkbox beside "Add a row", always visible, plus a checkbox on
  each tile while it is on - Ctrl-click was invisible and unusable on touch. Remembered per
  model. Selected tiles combine as **all** (AND, the new default) or **any** (OR), and a chip
  counts the selection and clears it.
* **A search panel on "Manage all tiles"**: a category down the left drills the list to one
  model at a time, with checkbox facets below it for owner, restricted-to group and trend
  source (company too, once there is more than one) - every entry carries a live count, the
  same pattern as the enterprise data-cleaning list. Tile History got one too, by tile.
* **Remove a tile from the ribbon**: a bin on the tiles you may edit, behind a confirmation whose
  wording depends on whether the tile is private or shared.
* **The generator picks its row** (and defaults to a new one) instead of piling every generated set
  onto row 1.
* **Delete a row** from the ribbon. Its tiles are never destroyed with it: they join the
  neighbouring row and everything below moves up, names included.
* **Named rows.** A row can be given a name ("Pipeline", "Operations", "Watch list") by typing in
  the pane down its left side, where it wraps over two lines and costs no height. New
  `filter.tile.row` model, managers only; clearing the name drops the record. The pane stays hidden
  on a plain single-row ribbon.
* **Tighter ribbon.** Bar padding, row gaps, tile padding and the chevron / gear columns were all
  trimmed; two named rows now occupy 250px where they used to take close to 300.
* **Sizing by hand.** Drag a tile's right edge to set its width (new `width` field, 120-520 px,
  double-click to reset, only offered on tiles you may write, clamped server-side rather than
  refused). Drag the bottom edge of the bar to set the row height (56-320 px) - that one is a
  viewing preference, kept per row and per model in the browser like the density and collapse
  settings.
* Apps-store packaging: icon, banner, description page, translation template, licence, this file.

### Performance

* **A ribbon now costs one query, not one per tile.** Every tile counts the same rows through a
  different filter, so `compute_tiles` gathers the whole ribbon with conditional aggregation -
  `COUNT(*) FILTER (WHERE …)` per tile over a single scan - instead of a `search_count` (plus a
  second query for a measure, plus a third for a sparkline) each. Tiles that share a trend field
  share its statement too. A 10-tile ribbon went from 21 queries to 2, a 40-tile one from 81 to 2,
  and on a 300 000-row table a search recomputed in **66 ms instead of 363 ms**. This is what made
  searching slow on a list with a ribbon: the tile call and the view's own query compete for the
  same workers, so 20 sequential scans of the table delayed the records themselves.
* The batch is a fast path, never a second opinion: a tile whose domain needs a **join** keeps its
  own query, because a join added for one tile changes the rows every other tile in the statement
  would see. Same for a tile that filters on **`active`** - `search_count` decides whether to add
  `active IS TRUE` from the whole domain it is handed, so an "Archived" tile has to be asked on its
  own or it silently reads zero. Each such tile pays only for itself: its plain neighbours stay in
  the batch (a real 7-tile MRP ribbon with one dotted domain costs 4 queries, not 15). Both paths
  are checked against each other in the test suite.
* Record rules are unaffected: each tile is a `FILTER` over the *base* query, which is where the
  rules already live, so a tile still cannot count a record its reader may not see.

### Fixed

* **The scope dropdown repeated itself.** A model carries several act_window records that share a
  name - three "Invoices" on `account.move`, three "Customers" on `res.partner` - and most are
  never navigated to. The list now offers each name once, represented by the action a menu actually
  points at, and always keeps the tile's current one.
* **The bar controls floated between the rows.** The collapse chevron and the studio gear were
  centred on the whole bar, so on a multi-row ribbon they belonged to neither row. They now line up
  with the first one.
* Removed `resequence_tiles` (superseded by `arrange_tiles`, which also carries rows), the unused
  `is_personal` / `action_name` fields and the `measure_type` payload key, and folded the registry
  service's `getTiles` / `canManage` back into `getRegistry` - one cached call instead of three.

* **The ribbon stood vertically on any view with a search panel** (Apps, Inventory, Sales…). The
  Layout puts the renderer inside `<main class="o_content">`, and a search panel turns that main
  into a *row* flex container - so the bar became a third column, full height, next to the records.
  The bar and the renderer are now wrapped in a column of their own (`$0` re-inserts the renderer
  untouched, so every prop and every other module's patch survives).
* The tile palette referenced `--bs-*` CSS variables, which **do not exist** in Odoo's backend
  (Bootstrap is built there with an empty variable prefix). Every colour now uses the real names
  (`--danger`, `--body-bg`, `--border-color`, …). Symptom: borders, tints and accents silently
  falling back to browser defaults.
* An alerting tile could not repaint itself: the alert colour was applied by a CSS class, which can
  never win against the inline `--dft-color` the tile is painted from. It is now set inline.
* Breakdown bars were `<span>`s without `display: block`, so their height was ignored and they never
  rendered.
* Reordering the ribbon no longer fails when it contains tiles the current user may not write; those
  simply keep their place.
* **A short row clipped its tiles**: the value was cut in half, because the tile's internals kept
  their fixed sizes and `overflow: hidden` did the rest. Tile content now adapts to the room it
  was given - the sparkline drops out first, then the sub-label and the delta, then the value
  steps down a size - and a wide tile lets its label wrap onto a second line instead of
  ellipsising it.

## 19.0.1.0.0

Initial release.

* Filter tiles on any list or kanban view, defined as `filter.tile` records.
* Capture the current filter as a tile, auto-generate a tile set from a status field, live preview,
  drag to reorder, icon and colour pickers.
* Count or sum / average / max / min of any stored numeric field, sparkline with a
  period-over-period delta, share bar, goal ring.
* Multi-select with ctrl-click, compact / comfortable density, collapsible bar.
* Visibility by user group, company and view type; management restricted to a technical group.
