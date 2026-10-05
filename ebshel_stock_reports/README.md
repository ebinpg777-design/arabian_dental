# Advanced Stock Reports

Nineteen inventory and manufacturing reports for Odoo 19 Community, built on
one engine that mirrors how Odoo values stock, so every figure foots to
**Inventory > Reporting > Stock** and says so when it cannot.

* Odoo **19.0** Community; designed to install on Enterprise
* Depends on `stock_account`, `mrp_account`, `purchase_stock`, `sale_stock`
* `xlsxwriter` (ships with Odoo); no JavaScript, no external library

---

## Installation

1. Copy `ebshel_stock_reports` into your addons path.
2. Update the app list and install **Advanced Stock Reports**.
3. The first build of the daily tables is queued at installation and run by
   the cron *Advanced Stock Reports: recompute daily summaries* (every five
   minutes). On a large database it runs in chunks; the settings page shows
   the state and the number of products still waiting. A manager can click
   **Recompute now** there.
4. Open **Inventory > Advanced Reports**.

## User roles

| Group | Can |
| --- | --- |
| **Advanced Stock Reports / User** | Open every report, see quantities. |
| **Advanced Stock Reports / See Values** | Also see cost and value columns, on screen, in Excel and in PDF. |
| **Advanced Stock Reports / Manager** | Settings, rebuild, health check, product classes, WIP snapshots, the engine tables. |

Inventory administrators get the Manager group out of the box. Every stored
table carries a company and is only visible inside it.

## Settings (Inventory > Configuration > Settings > Advanced Stock Reports)

| Setting | Meaning |
| --- | --- |
| Reporting timezone | Day boundaries of every report (default: the company timezone). Changing it rebuilds the tables. |
| Aging buckets | Upper bounds in days, e.g. `30,60,90,180`. |
| Slow moving after | Days without an issue before a product is slow; twice that is non-moving. |
| ABC / XYZ thresholds | Cumulative issue value share closing A and B; coefficient of variation closing X and Y. |
| Days of cover window | Days used for the average daily issue. |
| OTIF | First or last delivery of a line; in-full tolerance. |
| WIP component pricing | Today's standard price (as Odoo's WIP entry) or the day's average cost. |
| PDF row cap | A PDF stops after this many rows and says so. |
| India GST reports | Show the GST stock register and the production account. |

## The engine

Two tables, kept current by a queue:

* **Daily movement summary** (`asr.stock.move.daily`): one row per company,
  day, product, location and movement type (receipt, customer return,
  production, count gain, other in, delivery, vendor return, consumption,
  scrap, count loss, other out, transfer in/out, consignment, dropship).
  Quantities in product unit, values in company currency; each move line
  carries its share of the move value. The valued-location rule and the owner
  exclusion are the ones `stock_account` applies.
* **Daily valuation summary** (`asr.stock.value.daily`): for every day a
  product moved or was revalued, the company closing quantity (anchored on
  the current quants, like Odoo's quantity at date) and the closing value
  Odoo's `total_value` returns for the last instant of that day: standard
  price history, AVCO replay, FIFO stack. Lot-valuated products are read from
  Odoo (slow, flagged).
* **Recompute queue** (`asr.stock.dirty`): fed by every write path that changes
  a done move, its value or a product's valuation (DESIGN.md §1.4): adjust
  valuation, move line edits, landed costs, vendor bills, purchase price edits,
  standard price changes, cost method changes, backdating, returns. Reports
  process small queues on the spot; the cron processes the rest.

Warehouse and location values use Odoo's own ratio method (a quantity share
of the company value). Transfers carry no value. The ledger therefore shows a
**valuation adjustment** column (closing - opening - in + out) so every level
foots to Odoo's figure and the gap is visible, not hidden.

A **health check** (settings) compares the tables with Odoo's live figures
and re-queues anything that differs.

## Reports

| Menu | Report | Reads |
| --- | --- | --- |
| Stock | Stock ledger / stock card (ledger, daily card, live move-line card) | engine |
| Stock | Inventory aging (valuation stack / physical in-date) | moves, quants, engine |
| Stock | Slow / non-moving stock | engine, quants, classes |
| Stock | ABC / XYZ analysis | engine, `asr.product.class` |
| Stock | Turnover / days of cover | engine |
| Stock | Valued count variance | count moves (`asr_qty_before`, `asr_qty_counted`) |
| Stock | Scrap and returns by reason | scrap and return moves, reasons |
| Stock | Movement registers (inward / outward / internal / dropship) | move lines |
| Manufacturing | Planned vs actual consumption | done MOs, `asr_bom_planned_qty_unit` |
| Manufacturing | Work in progress | open MOs live; snapshots for past dates |
| Manufacturing | MO cost / production analysis | done MOs, work orders, BoM cost |
| Manufacturing | MO component shortage | open MOs, quantities per warehouse |
| Purchase and Sales | Customer OTIF, Vendor OTIF (beside Odoo's vendor delay rate), Purchase price variance, Open order shortage | orders, moves |
| Accounting | Inventory vs GL and the four accrual listings; links to Odoo's Inventory Valuation | order lines, GL |
| India GST | GST stock register (Rule 56 grouping by category class, loss categories by reason), Monthly production account | engine, reasons, MOs |

Every report opens as a wizard with **View** (list, pivot and graph where
useful), **Excel** and **PDF**. Standard Odoo reports are linked from the same
menu, not rebuilt.

## Data the module adds

* **Reasons**: Odoo's scrap reason tags, extended with a usage (scraps,
  returns, counts, any) and a GST category. A reason is asked on the return
  wizard and on the count "Apply" wizard and stored on the move.
* **Count quantities**: the system quantity and the counted quantity are
  copied onto every count adjustment move.
* **Planned consumption**: the BoM quantity per unit of finished product is
  frozen on raw moves when an MO is confirmed (backorders inherit it). Orders
  confirmed before the install are backfilled and flagged *estimated*.
* **GST stock class** on product categories.

## Design and performance

`DESIGN.md` is the Phase 0 design with the confirmed facts about the Odoo 19
source and the decisions that follow. `doc/PERFORMANCE.md` describes the
benchmark command and the measured timings. `doc/CHANGELOG.md` lists the
changes per version.

### Phase 1 decisions on the open questions of DESIGN.md §5

1. License: OPL-1, like the other Ebshel modules of this repository.
2. Repository: `odoo-apps/ebshel_stock_reports` (renamed from `advanced_stock_reports`).
3. Enterprise: designed for, not tested on (no Enterprise 19 source available).
4. Reason master: `stock.scrap.reason.tag` extended (no second master).
5. WIP component pricing: Odoo's wizard rule by default, average cost as a switch.
6. Lot-valuated products: exact but slow path through Odoo's own `total_value`.
7. Ledger: the valuation adjustment column is shown.
8. Benchmark: synthetic volume generated on a demo database.
