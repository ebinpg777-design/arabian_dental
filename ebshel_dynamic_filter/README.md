# Dynamic Filter Tiles

Turn any filter into a one-click tile — on every list and kanban view, built from the UI in seconds.

Every team asks the same few questions about a list: how many are late, how many are waiting on me,
what did we win this month. Usually the answer is a filter somebody has to remember, or a dashboard
nobody can edit. This module puts the answers **on top of the list itself** — a ribbon of tiles, each
one a label, a live number, a trend, and a filter behind it.

Tiles are `filter.tile` records: created, coloured, sized and arranged by the people who use them,
on any model, with no XML and no code.

* Version: `19.0.2.3.0`
* Odoo 19.0 — **Community and Enterprise**
* Depends on: `web`, `bus` (no Enterprise-only module, no external library)
* Licence: OPL-1

---

## What you get

| | |
|---|---|
| **Tiles on any model** | Filter a list the usual way, hit the `+` ghost tile, and the tile is born with exactly that domain. |
| **Real filters** | A tile applies as an ordinary, removable search facet. Ctrl-click — or the multi-select checkbox beside "Add a row" — combines several, as *all* (AND, the default) or *any* (OR). `Alt` + `1…9` applies them, `Alt` + `0` clears them. |
| **Scope** | A tile is model-wide by default, or pinned to one action — so customer invoices, vendor bills and journal entries each get their own ribbon on the same model. |
| **Removing** | The bin on a tile you own removes it from the ribbon, behind a confirmation that says whether it is yours alone or everybody's. |
| **Values** | Record count, or sum / average / max / min of any stored numeric field, with a unit or the company currency. |
| **Trends** | A sparkline grouped on a date field of the records, *or* drawn from the tile's own recorded history (a nightly job stores one point a day). |
| **Goals** | Give a tile a target and the sparkline becomes a progress ring. |
| **Rows** | Stack up to 6 rows of tiles, each nameable in a pane down its left side (“Pipeline”, “Watch list”); drag a tile onto another row to move it. Each row scrolls and is sized on its own. |
| **Sizing** | Drag a tile's right edge for its width (stored on the tile, 90–520 px, double-click to reset); drag a row's bottom edge for its height (28–160 px, kept per row and per model in the browser). |
| **Breakdowns** | One click splits a tile by status, salesperson, country, month… and each row of the popover filters the view down to that slice. |
| **Group by** | A tile can carry a field to group the view by. One click then filters *and* groups — "Late", by salesperson; "Won", by month — as two ordinary facets: drop the tile and its grouping goes with it, drop the grouping and the filter stays. Works on the Tile Wall too. |
| **Thresholds** | A tile past its limit turns into a warning, and — if asked — pushes a notification to its audience from an hourly server check. |
| **The Tile Wall** | Every ribbon in the database on one screen, grouped by model, alerting tiles first. Live mode refreshes it every minute. |
| **Private & shared** | Any internal user can build tiles only they see. Managers publish tiles for everybody, restricted to groups, companies or view types. |
| **Portable** | Export a ribbon as JSON, import it into another database — fields are matched by name. |
| **Tile styles** | Four flat styles, picked per user under "Tile style" in the tile menu: Accent (default - white card, colour bar on the left), Card, Midnight (dark) and Pastel. |
| **Live changes** | A tile whose number moves at a refresh under the same filter flashes and shows the change ("+2") for a few seconds. |
| **Smart search panel** | Odoo's own search panel (the left column of Employees, Products…), slimmer (188 px), gets a *find in panel* box (Enter applies the first match), removable selection chips, count bars, starred values on top, select all / invert, sort by count or name, hide-empty, collapsible sections, saved selections, a one-click group-by, and pin-as-tile / section-to-tiles shortcuts. "Classic look" switches it off. |
| **Managing at scale** | "Manage all tiles" carries a search panel: drill down by model on the left, then narrow by owner, restricted-to group, trend source or company — each with a live count, no typing. |

---

## Installing

```bash
odoo-bin -d <database> -i ebshel_dynamic_filter
```

Nothing else to configure. Users of the *Settings* group are Filter Tiles managers out of the box;
everybody else can already build private tiles.

## Development / testing

This repository is developed against a dedicated instance rather than a production database:

```bash
# create the instance database and install the module
instances/odoo19/start.sh -d odoo19_tiles -i ebshel_dynamic_filter --stop-after-init --no-http

# run it
instances/odoo19/start.sh            # http://localhost:1919

# apply changes
instances/odoo19/start.sh -d odoo19_tiles -u ebshel_dynamic_filter --stop-after-init --no-http
```

The frontend has no node toolchain here; JS/OWL/SCSS are validated server-side by building the real
asset bundle (`ir.qweb._get_asset_bundle('web.assets_backend')`).

---

## How it works

* **Injection** — the bar is added by extending the *controller* templates `web.ListView` and
  `web.KanbanView`, not the renderers. The renderer is also used by x2many subviews; the controller
  is not. The component is exposed as an instance property set in a patched `setup()`, because
  subclasses (dashboard lists, upload lists, and every other controller built on the standard ones)
  snapshot `static components` at class-definition time and would miss a late addition to the base.
* **Layout** — the bar and the renderer are wrapped in a `.o_dft_layout` column rather than the bar
  being inserted beside the renderer. `Layout` renders both inside `<main class="o_content">`, and
  a view with a search panel makes that main a *row* flex container: an unwrapped bar becomes a
  third column standing next to the records. The wrapper uses `$0`, so the renderer node - and any
  patch another module applied to it - is re-inserted verbatim.
* **Definitions** — one cached RPC per session (`filter_tiles` service). A model with no tiles costs
  zero extra round-trips.
* **Values** — one batched `compute_tiles` call for the whole ribbon, recomputed (debounced) against
  whatever the search bar currently holds. `KeepLast` guarantees a stale answer never overwrites a
  fresh one. Server-side that call is **one SQL statement**, not one per tile: the tiles all count
  the same rows through different filters, so each contributes a `COUNT(*) FILTER (WHERE …)` over a
  single scan (`_gather_counts`), and tiles sharing a trend field share its statement too. Adding
  tiles to a ribbon does not add round-trips.
* **…except when it would be wrong.** A tile whose domain needs a join, or that filters on `active`,
  keeps its own query (`_gather_one_by_one`, which is also the reference the batch is tested
  against). A join added for one tile would change the rows its neighbours see; and `search_count`
  decides `active_test` from the whole domain it is given, so an "Archived" tile asked inside a
  batch built on the base domain alone would read zero.
* **Rights** — value queries run **without** `sudo()`. A tile can never surface a record its reader
  is not allowed to read. The two exceptions are documented in the code and in the UI: recorded
  history and the alert check run server-side (as the owner for a private tile, as superuser for a
  shared one), because they must produce one number for everybody.
* **Ownership** — `owner_id` empty means shared, set means private. Record rules, not the UI, are
  what enforce it.
* **Density** — a row's height is a number the user picked, so the strip carries a size class
  (`o_dft_row_xs` / `_sm` / `_lg`) and the tile drops what it can spare in order: sparkline,
  sub-label, then the value steps down. Nothing is ever clipped — `overflow: hidden` on a tile
  would otherwise cut the number in half.

* **Smart search panel** — `static/src/search_panel/` patches `SearchPanel.prototype` once, for
  every app. Values are filtered and sorted by replacing the collections the panel templates loop
  over, never by hiding rendered rows, and the search model is never modified except through its
  public methods (`toggleFilterValues`, `clearSections`, `createNewGroupBy`). The desktop
  template extended is `web.SearchPanel.Regular`, not `web.SearchPanelContent`: Regular is a
  *primary* copy of Content made when the `web` templates load, so an extension of Content from a
  later bundle block does not reach it. Odoo's `<section>` sets its class with `t-attf-class`, so
  an xpath must not use `hasclass()` on it. Preferences and saved selections live in
  `localStorage` (`ebshel_dynamic_filter.sp.*`).

## Styling note for contributors

Odoo builds Bootstrap with an **empty** variable prefix: the backend exposes `--danger`,
`--body-bg`, `--border-color` — **not** `--bs-danger` and friends. A `--bs-*` name does not error, it
silently resolves to nothing, and an invalid `var()` takes the whole declaration with it. Check any
new custom property against the running client before relying on it.

---

## Support

Ebin P G — <ebinpg777@gmail.com> — <https://www.linkedin.com/in/ebin-p-g>
