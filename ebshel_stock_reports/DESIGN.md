# Advanced Stock Reports — DESIGN (Phase 0)

Status: **Phase 0 delivered, waiting for review. No code written.**
Source read: Odoo 19.0 Community, `/home/ebin-pg/odoo/odoo19` (core commit `5b87cfa1`, 2026-08-13).
Every "confirmed" statement below was read in that source; file paths are relative to `addons/`.

Settings as they will be used (from the prompt's "Edit before running" table):

| Setting | Value |
| --- | --- |
| Technical name | `ebshel_stock_reports` (renamed from `advanced_stock_reports` before Phase 1) |
| Display name | Advanced Stock Reports |
| Author / maintainer | Ebshel Technologies |
| License | **to decide** — see Open questions |
| Odoo version | 19.0 |
| Edition | Community; must install cleanly on Enterprise (see Open questions: no Enterprise 19 source is available here to prove it) |
| India GST reports (#19, #20) | On |
| Benchmark volume | 1,000,000 done moves, 20,000 products |
| Dependencies | `stock`, `stock_account`, `mrp`, `purchase_stock`, `sale_stock` (all Community). `xlsxwriter` 3.1.9 is pinned in core `requirements.txt` (line 102) and is importable in the instance venv — confirmed. |
| Repository | `odoo-apps` (branch 19.0; it carries the OCA pre-commit config and `.pylintrc`; `pre-commit` is installed at `/home/ebin-pg/odoo/.venv/bin/pre-commit`) |

---

## 1. Phase 0 findings, in checklist order

### 1.1 Moves Analysis columns: Value, Remaining Quantity, Remaining Value

Model `stock.move`, module `stock_account` (`stock_account/models/stock_move.py`):

| Column | Field | Stored? | How it is computed |
| --- | --- | --- | --- |
| Value | `value` (Monetary, line 24) | **yes, plain field** (no compute) | Set by `_set_value()` (line 293) during `_action_done` (line 176) and again whenever something that feeds it changes (§1.4). "It's zero if the move is not valued." |
| Remaining Quantity | `remaining_qty` (line 44) | **no** — `compute='_compute_remaining_qty'`, no store | `product._get_remaining_moves()` (product.py line 372): walks the in-moves backwards from the newest until the product's **current** valued quantity is covered (`_run_fifo_get_stack`, line 592). |
| Remaining Value | `remaining_value` (line 46) | **no** | FIFO: `remaining_qty / quantity × value`; AVCO and standard: `remaining_qty × standard_price` (`_compute_remaining_value`, line 129). |

So the prompt's assumption "inbound moves also hold remaining quantity and remaining value" is only half true: they are **computed on demand for today**, never stored, and there is no at-date variant of `_get_remaining_moves` (the stack builder itself accepts `at_date`). Design consequence in §2.4 (aging).

The Moves Analysis valuation list is `stock_account.stock_move_view_list_valuation` (fields `value`, `standard_price` as "Unit Cost", `remaining_qty`, `remaining_value`), opened by `stock_account.stock_move_valuation_action` with domain `is_in or is_out`.

Internal moves (valued location → valued location) **carry no value**: `_set_value` only values moves whose `_is_in()` / `_is_out()` / dropship test is true; everything else keeps `value = 0`. Confirmed at lines 320–353.

Direction flags are **stored**: `is_in`, `is_out`, `is_dropship` (lines 39–41, `store=True`, recomputed from `state` and `move_line_ids`); `is_valued` is not stored.

### 1.2 How the Stock report computes value at a date and per warehouse

The Stock report is `stock.action_product_stock_view` (model `product.product`, list `stock.product_product_stock_tree`, path `stock-report`). `stock_account` adds the columns `avg_cost` ("Unit Cost") and `total_value` ("Total Value") (`stock_account/views/product_views.xml` lines 54–80). Both are computed by `product.product._compute_value` (`stock_account/models/product.py` lines 180–278), `@api.depends_context('to_date', 'company', 'warehouse_id')`.

Algorithm of `_compute_value`, per company in `env.companies`:

1. Valuation scope = `_with_valuation_context()` (line 366): context `location` = every location with `is_valued_internal` (= `company_id` set **and** `usage in ('internal', 'transit')`, `stock_location.py` line 40), `owners = [False, company.partner_id]`, `strict=True`.
2. Quantity at date: `qty_available` with context `to_date` → `_compute_quantities_dict` (`stock/models/product.py` lines 164–262): **current quants − done moves after the date** (`('state','=','done'), ('date','>', to_date)`, in moves and out moves of the location set, quantities converted to the product UoM with `uom._compute_quantity`, owner filtered via `move_line_ids.owner_id in owners`). A plain date is turned into `datetime.combine(date, time.max)` (naive UTC).
3. Value, per cost method (`products_to_value` is always taken **with `warehouse_id=False`**, i.e. company-wide):
   * `standard` → `_run_standard_batch` (line 393): `qty_available × price`, where price at a date is the last `product.value` row with `move_id = False`, `lot_id = False`, `company_id = company`, `date <= at_date` (`_get_last_product_value`, line 338), else the current `standard_price`.
   * `average` → `_run_average_batch` (line 404): with **no** date: `qty_available × standard_price`. With a date: a **full replay** of every `is_in | is_out | is_dropship` move of the product with `date <= at_date`, ordered `product_id, date, id`, starting from the last manual `product.value` (quantity at that moment × its price), accumulating `value += move.value` on in-moves (dropship in-values are recomputed with `_get_value(at_date=…, forced_std_price=average)`), `value -= out_qty × average_cost` on out-moves, with the "from negative quantity" rule (line 497). `average_cost` is kept at full precision — unlike the stored `standard_price`, which is rounded to the Product Price precision at every `_update_standard_price`.
   * `fifo` → `_run_fifo_batch` → `_run_fifo(qty_available, at_date=…)` (line 549): walk the `is_in` moves with `date <= at_date` **backwards** (`order='date desc, id desc'`) until their valued quantities cover `qty_available`; the oldest move in the stack counts pro rata; value = Σ `move.value` (current values!). If quantity ≤ 0: `qty × last in-move price unit` (`_get_last_in`), else `qty × standard_price`. If the stack runs out: the rest is extrapolated at the last move's unit price.
   * `lot_valuated` products: value = Σ `stock.lot.total_value` of their lots (lines 196–214).
4. **Warehouse filter** (the Stock report search panel puts `warehouse_id` in the context, `stock/static/src/views/search/stock_report_search_model.js` line 43): `ratio = qty_available(warehouse) / qty_available(company)`; `total_value(warehouse) = total_value(company) × ratio` (lines 239–250, 266–268). Special cases: warehouse quantity zero → 0; company quantity zero but warehouse quantity not → `standard_price × warehouse qty`. **A warehouse's value is a quantity share of the company value, never a FIFO run of its own.**
5. `avg_cost = total_value / valued quantity` (line 278).

Three consequences the prompt did not anticipate:

* **Company-level value is NOT the sum of move values** except for FIFO in the normal case. For `standard` and `average` products the value is `qty × price` and a standard-price change (which creates a `product.value` row and **no stock move**, `_change_standard_price`, line 302) revalues the whole stock silently. For AVCO at a past date the replay re-derives the average at full precision, so Σ(in values) − Σ(out values) differs from the Stock report by rounding drift and by every later Adjust Valuation on an in-move (out-moves keep the values they were given on their day). The design therefore stores Odoo's closing value per day (§2.2) and shows "Σ movement values" and "Odoo closing value" with an explicit reconciling line, instead of pretending they are the same number.
* **"Inventory at Date" is a datetime**, naive UTC (`stock.quantity.history.open_at_date`, `stock/wizard/stock_quantity_history.py` line 36: `to_date=self.inventory_datetime`). Our "closing at day D in the company's reporting timezone" = Odoo's figure with `to_date` = the UTC instant of D 23:59:59.999999 in that timezone. The acceptance tests call Odoo with exactly that instant.
* The quantity at a date is **anchored on the current quants** and walks back through done moves; it is not Σ lines from the beginning of time. On migrated databases (quants loaded without moves) the two differ. The engine anchors the same way (§2.2), so it matches Odoo even there.

### 1.3 Valued-location and in/out move rules in 19

* `stock.location._should_be_valued()` (`stock_account/models/stock_location.py` line 36): `bool(company_id) and usage in ('internal', 'transit')`. Searchable as `is_valued_internal`. **Transit locations that belong to the company are valued** (stock in transit is company stock); transit locations without a company are not.
* `stock.move._get_in_move_lines()` (`stock_move.py` line 536): move lines with `picked`, not excluded, `not location_id._should_be_valued() and location_dest_id._should_be_valued()`. `_get_out_move_lines()` (line 558) is the mirror. `_is_in()` additionally requires "not a returned dropship", `_is_out()` "not a dropship".
* **Owner exclusion**: `stock.move.line._should_exclude_for_valuation()` (`stock_move_line.py` line 39): `owner_id and owner_id != company.partner_id`; `stock.move._should_exclude_for_valuation()` (line 668) does the same with `restrict_partner_id`. Consignment is therefore excluded line by line, which is also what the Stock report's `owners=[False, partner]` context does.
* **Valued quantity** of a move = Σ `quantity_product_uom` of its in (or out) move lines (`_get_valued_qty`, line 442). `quantity_product_uom` is a stored field on `stock.move.line` (`stock/models/stock_move_line.py` line 40). `stock.move.quantity` is in the **move** UoM; `stock.move.product_qty` is the demand in product UoM. The summary therefore sums **move lines**, not moves.
* **Dropship**: `_is_dropshipped()` (line 584): source `supplier` (or company-less transit) **and** destination `customer` (or company-less transit); `_is_dropshipped_returned()` is the reverse. `stock_dropshipping` adds picking-type code `dropship` (`stock_dropshipping/models/stock.py` line 58) and `stock.picking.is_dropship`. Dropship moves are valued (`_set_value` values `is_dropship`) and take part in the AVCO replay, but never touch a valued location.

### 1.4 Write paths that change a done move's value

`value` is a plain stored field written through the ORM: a `move.value = x` assignment goes `Field.__set__` → `records.write({'value': x})` (`odoo/orm/fields.py` lines 1838–1842). So an override of `stock.move.write` sees **every** path below. (Stored computes such as `is_in` do **not** go through `write`; they are flushed from the compute cache. They only change when `state`/`move_line_ids` change, and those changes do go through `write`/`create` of the move or its lines.)

| Path | Trigger | What is written | Seen by `stock.move.write`? |
| --- | --- | --- | --- |
| Adjust Valuation (Moves Analysis) | `stock.move.action_adjust_valuation` (line 164) opens `product.value` with `default_move_id`; `product.value.create` (`product_value.py` line 84) calls `move._set_value()` | `value` on that move (manual value takes priority in `_get_value_data`, line 371) | yes |
| Move line edits on a done move | `stock.move.line.write` with `quantity`, `location_id`, `location_dest_id`, `owner_id`, `quant_id`, `lot_id` → `_update_stock_move_value` (`stock_move_line.py` line 23) | `value` of the in-move (recomputed) or out-move (`correction_quantity`) | yes, and the line change itself is seen by `stock.move.line.write` |
| Landed costs | `stock.landed.cost.button_validate` → `cost.valuation_adjustment_lines.move_id._set_value()` (`stock_landed_costs/models/stock_landed_cost.py` line 152); `_get_value_from_extra` adds Σ `additional_landed_cost` (`stock_landed_costs/models/stock_move.py` line 14) | `value` on the receipt moves | yes |
| Vendor bill / refund posted | `account.move._post` → `line_ids._get_stock_moves().filtered(is_in or is_dropship)._set_value()` (`stock_account/models/account_move.py` line 42); `_get_value_from_account_move` (`purchase_stock/models/stock_move.py` line 157) values the receipt at the **billed** price for **every cost method** | `value` on the receipt moves | yes |
| PO line price / quantity / UoM edited after receipt | `purchase.order.line.write` → `move_ids.filtered(is_valued)._set_value()` (`purchase_stock/models/purchase_order_line.py` line 122) | `value` | yes |
| Standard price change | `product.product.write` → `_change_standard_price` → `product.value` row (**product-level, no move**) (`product.py` line 302); skipped for FIFO | nothing on moves; changes the at-date value of standard/AVCO products from that moment | **no** — needs a hook on `product.value.create` |
| Lot standard price | `stock.lot` write → `product.value` with `lot_id` (`stock_lot.py` line 104) | lot-valuated products only | hook on `product.value.create` |
| Later moves (FIFO) | `_action_done` of an out-move runs `_run_fifo` on the in-moves | nothing on the in-moves; only the computed `remaining_qty` moves | n/a (the out-move's own `create/write` is seen) |
| Backdating | `stock.picking.write({'date_done'})` → done moves' `date` (`stock/models/stock_picking.py` line 1150); `stock.move.write({'date'})` → lines' `date` (line 882); `stock.quant._apply_inventory(date)` sets `moves.date = date` **after** `_action_done` (line 1028) | `date` of done moves | yes (`date` in vals, state done) |
| Returns | `_get_value_from_returns` (line 490): a return of an out-move is valued at the original move's unit value | `value` of the return move at its own done | yes |
| Manufacturing | `mrp.production._cal_price` sets `price_unit` on the finished move before it is done (`mrp_account/models/mrp_production.py` line 57); `_get_value_from_production` turns it into the value (`mrp_account/models/stock_move.py` line 11) | `value` at done; **not re-set by later work-order time changes** | yes |

Not found: any raw-SQL path that rewrites `stock_move.value`, and any recompute cron.

### 1.5 Remaining quantity/value under FIFO, AVCO and standard

Same mechanism for all three (`_get_remaining_moves` → `_run_fifo_get_stack`): the newest in-moves that cover the product's **current** valued quantity. Differences are only in `remaining_value` (§1.1). Σ `remaining_value` = `total_value` today for every method (FIFO: the same stack; AVCO/standard: `qty × standard_price`). `_run_fifo_get_stack` accepts `at_date` and `location` but `remaining_qty` never passes them. Lot-valuated products build one stack per lot.

### 1.6 Inventory-tracked flag and product types

`product.template.type`: `consu` ("Goods"), `service`, `combo` (`product/models/product_template.py` line 54). The tracked flag is `product.template.is_storable` ("Track Inventory", stored, `stock/models/product.py` line 827; `_search_remaining_qty` and the Stock report both filter on it; the Stock report domain is `[('is_storable','=',True)]`). `active=False` products keep their moves; `_compute_quantities` uses `active_test=False` on moves and quants.

### 1.7 When component moves are posted for MOs, backorders and work orders

* Raw-material moves are made done **only** in `mrp.production._post_inventory` (`mrp/models/mrp_production.py` line 1907), which runs from `button_mark_done` (line 2218). Raw moves that are not `picked` are **cancelled** there; done moves are kept. So a confirmed/in-progress MO has **no done component moves** (unless a move was added already done); consumption before "Produce" exists only as `stock.move.picked = True` plus `quantity` / move lines (`picked` = "consumed" in the MO form; work orders set it through `_set_qty_producing`).
* **Backorder**: `_split_productions` (line 1980) rewrites the original MO's `product_qty` to the quantity produced, scales each raw move's `product_uom_qty` by `unit_factor = product_uom_qty / initial product_qty` (line 2062) and creates copies of the moves for the backorder MO(s); then `_post_inventory` posts the original. So a done MO's raw `product_uom_qty` is already "the plan scaled to what was produced" **as last edited**.
* `_update_raw_moves(factor)` (line 1449) rescales `product_uom_qty` when the MO quantity changes; a user can also type a new "To Consume" on a confirmed MO. `_compute_move_raw_ids` (line 820) runs **only in draft**, so editing the BoM after confirmation does not touch the MO's moves. No field keeps the quantity the BoM asked for at confirmation: `unit_factor` (`mrp/models/stock_move.py` line 53) is recomputed from the current `product_uom_qty`.
* `qty_produced` is **not stored** (`_get_produced_qty`, line 700: Σ `quantity` of picked, non-cancelled finished moves of the MO product, in the MO UoM).

### 1.8 MO real cost

`mrp.production._cal_price(consumed_moves)` (`mrp_account/models/mrp_production.py` line 57), run inside `_post_inventory` (line 1947), right before the finished moves are done:

```
total_cost = Σ consumed_moves.value + Σ workorder._cal_cost() + extra_cost × quantity
finished_move.price_unit = total_cost × (1 − by-product cost share) / quantity   (FIFO/AVCO)
finished_move.price_unit = product.standard_price                               (standard)
by-product.price_unit    = total_cost × cost_share / 100 / qty                   (FIFO/AVCO)
```

`mrp.workorder._cal_cost(date=False)` (`mrp/models/mrp_workorder.py` line 638): Σ over `time_ids` (`mrp.workcenter.productivity`: `date_start`, `date_end`, `duration`, `workcenter_id`, `workorder_id`, `user_id`, `loss_id`) of the merged intervals (`Intervals`/`sum_intervals`) ended before `date`, × `workorder.costs_hour or workcenter.costs_hour`; when `cost_mode == 'estimated'` and the work order has started: `duration_expected`. Employee cost: **not in Community** (no `employee_id` on productivity lines; that is `mrp_workorder_hr`, Enterprise). Labour is posted to accounting at MO done by `_post_labour` (line 100).

The per-MO cost comparison exists in Community as the **MO Overview** (`report.mrp.report_mo_overview._get_report_data(production_id)`, `mrp/report/mrp_report_mo_overview.py` line 75) with `mo_cost`, `bom_cost`, `real_cost` per component and per operation. It is per MO (one call = one MO, ORM-heavy).

### 1.9 Scrap reasons, return links, count-apply flow

* **Scrap**: `stock.scrap` (`stock/models/stock_scrap.py`) has `scrap_reason_tag_ids` → `stock.scrap.reason.tag` (`name`, `sequence`, `color`; line 237). Reason tags exist in 19. Scrap moves carry `stock.move.scrap_id` (`btree_not_null`); the scrap location is `stock.scrap.scrap_location_id` (usage `inventory`; there is **no** `scrap_location` flag on `stock.location` in 19). `mrp` adds `production_id` / `workorder_id` on `stock.scrap`.
* **Returns**: `stock.move.origin_returned_move_id` / `returned_move_ids` (`stock_move.py` lines 155–158), `stock.picking.return_id` / `return_ids` (`stock_picking.py` line 566). The wizard `stock.return.picking` (+ `stock.return.picking.line`) has **no reason field**; `stock_account` adds `to_refund` on its lines.
* **Count apply**: `stock.quant._apply_inventory` (`stock/models/stock_quant.py` line 996) builds move values with `_get_inventory_move_values(qty, location_id, location_dest_id, …)` (line 1253) and validates them with `_action_done`; the move gets `is_inventory = True`, `picked = True`, `restrict_partner_id = owner`. At the moment `_get_inventory_move_values` runs, `self.quantity` (system quantity before) and `self.inventory_quantity` (counted) are both still on the quant — that is the one place to copy them onto the move. The "Apply" button goes through the wizard `stock.inventory.adjustment.name` (`inventory_name` is passed by context), which is where a reason can be asked for.

### 1.10 Promised-date fields

| Document | Field | Meaning |
| --- | --- | --- |
| `sale.order` | `commitment_date` (`sale/models/sale_order.py` line 87) | the date promised to the customer (typed by the user) |
| `sale.order` | `expected_date` (line 301; `sale_stock` line 52) | computed promise from lead times |
| `sale.order` | `effective_date` (`sale_stock` line 51, stored) | `min(date_done)` of the deliveries |
| `stock.move` | `date_deadline` (line 31) | the deadline propagated to the move; `date` = scheduled date until done, then the done date |
| `stock.picking` | `scheduled_date`, `date_deadline`, `date_done` (lines 595–606) | |
| `purchase.order.line` | `date_planned` (`purchase_order_line.py` line 25) | expected arrival per line |
| `purchase.order` | `date_planned` (min of lines), `date_approve`, `date_order`; `effective_date` = `min(picking.date_done)` (`purchase_stock` line 32, stored) | |

Community already has `vendor.delay.report` (`purchase_stock/report/vendor_delay_report.py`): per PO line, `qty_on_time` = move-line quantity received where `pol.date_planned::date >= move.date::date`, giving an **On-Time Delivery Rate**; and `purchase.report.days_to_arrival` ("Effective Days To Arrival" = first `date_done` of the order − `date_order`, `purchase_stock/report/purchase_report.py` line 13) next to `delay_pass` ("Days to Receive" = `date_planned − date_order`, i.e. the *promised* lead time, `purchase/report/purchase_report.py` line 35).

### 1.11 xlsxwriter and the 19 index API

* `XlsxWriter==3.1.9` (core `requirements.txt` line 102); importable in the ortho/lab venvs. Constant-memory mode is the standard `Workbook(path, {'constant_memory': True})`.
* Indexes and constraints are **class attributes** (`odoo/orm/table_objects.py`): `_x = models.Index("(col_a, col_b) WHERE …")`, `models.UniqueIndex("(…)", message)`, `models.Constraint('CHECK (…)', message)`. Examples in core: `stock.move._product_location_index = models.Index("(product_id, location_id, location_dest_id, company_id, state)")` (`stock_move.py` line 200), `stock.move.line._free_reservation_index` (partial, line 97), `stock.location._parent_path_id_idx`. `_sql_constraints` is gone (core uses `models.Constraint`). Index names are `{table}_{attribute without leading underscore}`; the definition is stored as the index comment so changes are detected on upgrade.

### 1.12 Existing indexes on `stock_move`, `stock_move_line`, `stock_quant`

* `stock_move`: `date`, `company_id`, `product_id`, `location_id`, `location_dest_id`, `partner_id`, `picking_id`, `state`, `origin_returned_move_id`, `orderpoint_id`; `btree_not_null` on `scrap_id`, `restrict_partner_id`, `account_move_id` (`stock_account`); composite `(product_id, location_id, location_dest_id, company_id, state)`.
* `stock_move_line`: `picking_id`, `move_id`, `company_id`, `product_id`, `lot_id`, `location_id`, `location_dest_id`; `btree_not_null` on `owner_id`, `package_history_id`; partial `_free_reservation_index` (open, unpicked lines only — useless for history).
* `stock_quant`: `product_id`, `location_id`, `lot_id`, `package_id`; `btree_not_null` on `owner_id`.
* `product_value`: `product_id`; `btree_not_null` on `move_id`.

No index covers `stock_move_line.date` nor `(company_id, date)` on `stock_move`; the summary builder scans by `stock_move.date` (btree) joined to lines by `move_id` — enough. Any further index will be proposed only with an EXPLAIN ANALYZE in PERFORMANCE.md.

### 1.13 Community availability of the Enterprise-looking reports

| Report | In Community 19? | Where |
| --- | --- | --- |
| Accounting › Review › Inventory Valuation | **The report exists, the menu does not.** `stock_account.action_report_stock_valuation` (client action `stock_valuation_report`, path `stock-valuation-closing`, `stock_account/report/stock_valuation_report.xml`) shows Initial Balance (accounting), Inventory Loss, Stock Variation, Ending Stock (inventory) per valuation account, from `res.company.stock_value()` (Σ `product.total_value` at date per valuation account) and `stock_accounting_value()` (posted balances of the valuation accounts at date) (`stock_account/models/res_company.py` lines 89–117); `sale_stock` / `purchase_stock` / `mrp_account` add "goods delivered/received not invoiced" and "cost of production" **totals** (not listings). No `menuitem` references the action in any Community module (Enterprise `stock_accountant` presumably adds it). | link to it from the Advanced menu |
| Bill To Receive / Invoices To Be Issued / Billed Not Received / Invoiced Not Delivered | **Not found** as reports or listings. Only the two totals above (`_compute_goods_received_not_invoiced`, `_compute_goods_delivered_not_invoiced`, both marked "TODO remove in master"). | build the four listings (#17), signed off by an accountant |
| Production Analysis (pivot of MO costs) | **Not found.** `mrp` Reporting has Work Orders and OEE only; `mrp_account` adds no reporting menu. | build (#18) |
| MO cost vs real cost view | **Per MO only**: the MO Overview report (§1.8). No cross-MO view. | build (#18) on the overview's cost logic |
| Vendor on-time rate | yes: `vendor.delay.report` (§1.10) | show beside #9 |
| Stock closing entry (periodic valuation) | yes: `res.company.action_close_stock_valuation`, cron `_cron_post_stock_valuation` (`inventory_period` manual/daily/monthly), `inventory_valuation` = `periodic` / `real_time` ("Perpetual (at invoicing)") — on **res.company** (lines 19–36), `product.category.property_valuation` still exists | nothing to build |
| WIP journal entry | yes, manual: `mrp.account.wip.accounting` wizard (§2.3) | nothing to build; #3 feeds it |

---

## 2. Design decisions that follow (what changes against the prompt)

### 2.1 The one engine: two daily tables, not one

The prompt's `asr.stock.move.daily` keeps movement sums per location. Valuation cannot live in those rows (§1.2: Odoo's value is a per-product, company-level, cost-method-specific figure that is not the sum of movement values). Two tables:

**`asr.stock.move.daily`** — one row per `(company_id, day, product_id, location_id, move_type)`:
`qty_in`, `qty_out` (product UoM, from `stock_move_line.quantity_product_uom`), `value_in`, `value_out` (company currency; each line gets `move.value × line_qty / move valued qty`, so a move split over several locations still sums to `move.value`), `line_count`.
`day` = `stock_move.date` converted to the company's reporting timezone (`asr_report_tz`), because that is the date Odoo's at-date quantity uses (§1.2). Only done moves; only lines whose owner is empty or the company's partner (`_should_exclude_for_valuation`) — excluded lines go to `move_type = 'consignment'` rows (quantity only, value 0) so consignment stock can be shown separately. Dropship moves are excluded from the ledger types and stored as `dropship` (quantity only) so registers can list them.

Move types, decided from the **line's** locations (the valued-location rule, not `usage = 'internal'`):

| `move_type` | Rule |
| --- | --- |
| `receipt` | in; source usage `supplier` |
| `customer_return` | in; source usage `customer` |
| `production` | in; source usage `production` (finished goods, by-products, unbuild recoveries) |
| `count_gain` | in; `move.is_inventory` |
| `other_in` | any other in (e.g. company-less transit) |
| `delivery` | out; destination usage `customer` |
| `vendor_return` | out; destination usage `supplier` |
| `consumption` | out; destination usage `production` |
| `scrap` | out; `move.scrap_id` set |
| `count_loss` | out; `move.is_inventory` |
| `other_out` | any other out |
| `transfer_out` / `transfer_in` | valued → valued: **two rows**, one on the source location (out) and one on the destination (in); value 0 (Odoo gives such moves no value). A transfer whose destination is a company transit location is the "in transit" leg; the transit location's own balance is the in-transit column. |
| `consignment`, `dropship` | see above |

**`asr.stock.value.daily`** — one row per `(company_id, product_id, day)` for days on which the product moved or was revalued:
`closing_qty` (company level, valued locations, anchored on the current quants exactly like `_compute_quantities_dict`: quants now − Σ summary after the day), `closing_value` (Odoo's `total_value` for that instant, by cost method, §1.2 mirrored), `avg_cost`, `cost_method`, `revaluation_value` (Σ of standard-price changes that day: `(new − old) × qty at that moment` for standard/AVCO products), `fifo_anchor_move_id` (the oldest move of the stack, kept so the next day's stack is built incrementally), `computed_at`.
The replay runs in cron (Python, per product in date order, batched by company and month) and **calls** Odoo for the two edge cases rather than mirroring them: dropship in-values inside an AVCO replay (`move._get_value(at_date, forced_std_price)`) and `lot_valuated` products (Σ `stock.lot.total_value`; such products are flagged `exact_slow_path`). Everything else is mirrored line for line from `_run_standard_batch`, `_run_average_batch`, `_run_fifo` / `_run_fifo_get_stack`.

Warehouse- and location-level **value** at a day = `closing_value(company) × qty(location set) / closing_qty(company)` with Odoo's two special cases — the ratio method of `_compute_value` (§1.2 point 4). Transfers therefore have no value of their own in the ledger; a warehouse ledger shows receipts/issues at move values, transfers at quantity (and, as an information column, at the day's average cost), and one **"valuation adjustment"** column = closing − opening − in + out, so every level foots to Odoo's figure and the gap is visible, not hidden.

### 2.2 Dirty queue — every path of §1.4

`asr.stock.dirty` keys `(company_id, product_id, day_from)` with `ON CONFLICT DO NOTHING`, inserted by:

* `stock.move.create` (state done) and `stock.move.write` when any of `state`, `date`, `value`, `quantity`, `location_id`, `location_dest_id`, `company_id`, `product_id`, `restrict_partner_id`, `scrap_id`, `is_inventory` is in `vals` and the move is or becomes done — key on `min(old date, new date)`;
* `stock.move.line.create/write` on done moves when `quantity`, `quantity_product_uom`, `location_id`, `location_dest_id`, `owner_id`, `lot_id`, `date` change (covers line edits that do not alter the move value);
* `product.value.create` — move-level rows (Adjust Valuation) key on the move's day; product-level rows (standard-price changes) key on the value's date; lot rows on the lot's product;
* `product.product.write` of `categ_id` or category `property_cost_method` changes (cost method switch) → full-product keys from the first move;
* `res.company.write` of `asr_report_tz` → full rebuild flag for the company.

Landed costs, bills, PO-price edits and returns need **no hook of their own**: they all end in `stock.move.write({'value'})` (§1.4). The hooks compute nothing and read nothing beyond the vals: one key check and one batched insert.

Cron `asr_recompute_dirty` (every 5 minutes, chunked): for each `(company, product)` take the earliest day, delete that product's movement rows from that day on, re-insert them from live moves with one `INSERT … SELECT`, then replay the valuation rows from that day on (FIFO stack rebuilt once at the day before, from in-moves ≤ that day, bounded by quantity — the mirror of `_run_fifo_get_stack`).

### 2.3 Definitions settled by the source

* **WIP value at a date** (#3) mirrors the WIP wizard (`mrp.account.wip.accounting._get_line_vals`, `mrp_account/wizard/mrp_wip_accounting.py` line 77): components issued = Σ over raw move lines with `picked` and `quantity` and `line.date <= date` of `quantity_product_uom × (lot standard_price if lot-valuated else product standard_price)` — note Odoo prices them at **today's** standard price, not the price at the date; overhead = `workorder_ids._cal_cost(date)`. Our default is exactly that ("as Odoo would post it"), with a company switch to price components at the day's `avg_cost` from `asr.stock.value.daily` (labelled). "Components already moved to production locations" is **not** a 19 concept (§1.7: raw moves are posted only at done), so the prompt's default collapses to the `picked` rule.
* **Past WIP** reads `asr.wip.snapshot` only (month-end cron, plus on-demand snapshot), as the prompt says; the snapshot stores the two amounts per MO and work center.
* **Planned consumption** (#2): a new stored field on raw moves, **per unit** of finished product, set at MO confirmation from the BoM line (`bom_line.product_qty / bom.product_qty`, converted to the product UoM) — per unit so backorder splits and quantity changes need no rewrite (the backorder's moves are copies and inherit it). Planned for the report = unit × quantity produced. MOs confirmed before install are backfilled from `product_uom_qty / product_qty` and flagged `estimated`.
* **Aging** (#4) valuation mode = Odoo's remaining stack, mirrored in SQL per product (newest in-moves until the current valued quantity is covered; FIFO value pro rata, AVCO/standard `remaining_qty × standard_price`) and bucketed by the in-move's `date`. It foots to the Stock report **today**; at a past date it is the same walk with `date <= D` and `closing_qty(D)` (what `_run_fifo` does), labelled as such. Physical mode uses `stock.quant.in_date` and `avg_cost`.
* **OTIF** (#8): promised date = `sale.order.commitment_date`, else `expected_date`, else the move `date_deadline`; delivery date = `date_done` of the first (default) or last delivery of the line, per setting; "in full" = delivered ≥ ordered − tolerance. (#9): promised = `purchase.order.line.date_planned`; received = first receipt `date_done`; shown beside `vendor.delay.report.on_time_rate` and `purchase.report.days_to_arrival`, with the note that Odoo compares dates, not datetimes.
* **Purchase price variance** (#10): the receipt move's value is the PO price (`_get_value_from_quotation`) and becomes the bill price once billed (§1.4) — for standard-cost products too; the price-difference journal line is only created under anglo-saxon accounting (`purchase_stock/models/account_invoice.py` line 37). So: PO price = `purchase_line._get_stock_move_price_unit()` (company currency, product UoM, taxes-included handled), billed price = `move.value / valued qty`, standard cost at receipt = last `product.value` (product-level) at the move date (`_get_standard_price_at_date` logic), variance = (billed-or-PO price − standard) × received qty. **`asr_receipt_value` is dropped**: nothing it would hold is missing.
* **"Issue"** for slow-moving / turnover / ABC = `delivery` + `consumption` rows only.
* **Day boundaries**: `asr_report_tz` per company (default: the company partner's `tz`, else UTC), applied in SQL as `(date AT TIME ZONE 'UTC' AT TIME ZONE tz)::date`.
* **UoM**: 19 has no UoM categories; conversion is `qty × from.factor / to.factor` (`uom/models/uom_uom.py` line 147) and one global rounding (`Product Unit` precision, line 62). Stored product-UoM quantities (`quantity_product_uom`, `product_qty`) are used wherever they exist; PO/SO/BoM quantities are converted with that formula and rounded `HALF-UP` like `_compute_product_qty`.

### 2.4 Final new-field list

| Field / model | On | Why | Change vs prompt |
| --- | --- | --- | --- |
| `asr.stock.move.daily` | new | movement pre-aggregate (§2.1) | as prompt |
| `asr.stock.value.daily` | new | Odoo's closing value per product and day (§2.1) | **added** — the prompt assumed value = Σ moves |
| `asr.stock.dirty` | new | recompute keys | as prompt |
| `asr_gst_category` (selection: lost, stolen, destroyed, written_off, gift, free_sample, other), `asr_usage` (scrap / return / count / any) | `stock.scrap.reason.tag` | 19 already has reason tags on scraps; extending them avoids a second reason master | **replaces** `asr.stock.reason` |
| `asr_reason_id` → `stock.scrap.reason.tag` | `stock.move` | one reason per move for #14/#19; set from the scrap's first tag, from a new field on `stock.return.picking`, and from a new field on `stock.inventory.adjustment.name` (carried by context like `inventory_name`) | as prompt, different comodel |
| `asr_qty_before`, `asr_qty_counted` | `stock.move` | copied in `stock.quant._get_inventory_move_values` (§1.9) | as prompt |
| `asr_bom_planned_qty_unit` | `stock.move` (raw moves) | per unit of finished product (§2.3) | per unit instead of per MO |
| `asr_planned_estimated` | `stock.move` | backfilled values flagged | as prompt ("estimated") |
| ~~`asr_receipt_value`~~ | — | not needed (§2.3) | **dropped** |
| `asr.product.class` | new | ABC/XYZ per company and product | as prompt |
| `asr_gst_stock_class` | `product.category` | Rule 56 grouping | as prompt |
| `asr.wip.snapshot` (+ lines) | new | month-end WIP | as prompt |
| `res.company`: `asr_report_tz`, `asr_aging_buckets`, `asr_slow_days`, `asr_abc_thresholds`, `asr_xyz_thresholds`, `asr_otif_basis`, `asr_otif_tolerance`, `asr_cover_days`, `asr_pdf_row_cap`, `asr_india_gst`, `asr_wip_component_price` | settings | as prompt (+ WIP pricing switch) |

Naming: fields on core models keep the `asr_` prefix the prompt asks for (collision safety); files, classes and the module's own models use plain descriptive names (`models/move_daily.py`, class `StockMoveDaily`), in line with the repo's convention.

### 2.5 Security

Groups `ebshel_stock_reports.group_user` (reports) and `group_manager` (settings, rebuild, health check); `group_see_values` gates every value field, column and export (fields declared with `groups=`, XLSX writers skip the columns, PDF templates test the group). Multi-company record rules on every stored model (`company_id in company_ids`).

---

## 3. Reports: what each one reads (unchanged where the prompt already matched the source)

| # | Report | Data path | Note from Phase 0 |
| --- | --- | --- | --- |
| 1 | Stock ledger / card | `asr.stock.move.daily` + `asr.stock.value.daily`; card mode live from move lines with a window function | closing value by level via the ratio method; "valuation adjustment" column |
| 2 | Planned vs actual consumption | done MOs, raw moves, `asr_bom_planned_qty_unit`, scraps with `production_id` | qty produced from finished move lines (not stored in core) |
| 3 | WIP | open MOs' picked raw lines + `_cal_cost(date)`; snapshots for past dates | definition §2.3 |
| 4 | Aging | remaining-stack mirror (valuation) / quants `in_date` (physical) | §2.3 |
| 5 | Slow / non-moving | summary last dates + live quants + `asr.product.class` | |
| 6 | ABC / XYZ | summary, monthly cron | |
| 7 | Turnover / days of cover | summary + `asr.stock.value.daily` month-ends | numerator labelled "issue value at cost" |
| 8 | Customer OTIF | SO lines, moves, pickings | §2.3 |
| 9 | Vendor OTIF | PO lines, moves; `vendor.delay.report` beside it | §1.10 |
| 10 | Purchase price variance | receipt moves, PO lines, `product.value` | §2.3 |
| 11 | MO component shortage | open MOs, `_compute_quantities_dict` for short products only, with `warehouse_id` context | |
| 12 | Open-order shortage | open SO lines, reservations, incoming moves | estimate only; Reception report stays the tool |
| 13 | Valued count variance | `is_inventory` moves, `asr_qty_before/counted` | older counts: difference only |
| 14 | Scrap and returns by reason | `scrap_id` moves, `origin_returned_move_id` moves, `asr_reason_id` | |
| 15 | Registers | done move lines by picking-type code (`incoming`, `outgoing`, `internal`; `dropship` listed separately) | |
| 16 | Production plan vs stock | **not built** (MPS is Enterprise; OCA `mrp_multi_level` only with written approval) | |
| 17 | Inventory vs GL + accruals | link to `stock_account.action_report_stock_valuation`; build the four listings from PO/SO lines (`qty_received − qty_invoiced`, `qty_delivered − qty_invoiced` at line prices and at cost) | §1.13 |
| 18 | MO cost / production analysis | build: per done MO, components (Σ raw move values), operations (`_cal_cost`), extra cost, by-product share, vs BoM cost (`report.mrp.report_mo_overview` logic reused for the BoM side); per-unit averages by product and month | §1.13 |
| 19 | GST stock register | #1 engine grouped by `asr_gst_stock_class`; reason categories from `asr_reason_id` | |
| 20 | Monthly production account | #2 data | |

Standard reports linked from the Advanced menu, not rebuilt: Stock (`stock.action_product_stock_view`), Moves History (`stock.stock_move_line_action`), Moves Analysis (`stock.stock_move_action`, valuation list `stock_account.stock_move_valuation_action`), Forecasted, Replenishment, Purchase/Sales Analysis, Traceability, Inventory Valuation closing (`stock_account.action_report_stock_valuation`), Vendor delay (`purchase_stock.action_purchase_vendor_delay_report`).

---

## 4. Testing and acceptance (how the identities will be checked)

* Scenario data as in the prompt, on a throwaway DB with demo data (`instances/odoo19`, `--with-demo`), plus the two edge cases found: a standard-price change on an AVCO product (revaluation without a move) and a backdated picking (`date_done` write).
* Reconciliation: for every scenario step, `asr.stock.value.daily.closing_value` for the step's instant equals `product.with_context(to_date=<UTC instant>).total_value`, and `closing_qty` equals `qty_available` under `_with_valuation_context()`; per warehouse, the ratio method equals `product.with_context(to_date, warehouse_id).total_value`. Σ aging (valuation mode) equals Σ `remaining_value` of `_get_remaining_moves()` and the Stock report total.
* Dirty-queue tests: each row of the table in §1.4 performed on a done move, then the cron, then the identity above again.
* Security tests, benchmark command and PERFORMANCE.md as in the prompt.

---

## 5. Open questions for review (answers change Phase 1)

1. **License**: LGPL-3 or OPL-1?
2. **Repository**: `odoo-apps/ebshel_stock_reports` (OCA hooks live there) — confirm; the dev instance `instances/odoo19` (:1919) only has `projects/odoo19` on its addons path, so Phase 1 will pass `--addons-path` explicitly.
3. **Enterprise install check**: the only Enterprise checkout on this machine is 18.0; "installs cleanly on Enterprise 19" cannot be proven here. The module avoids every name Enterprise 19 is likely to add (menus under its own `menu_advanced`, no `stock_accountant` ids). Accept this as "designed for, not tested on", or point me at an Enterprise 19 source.
4. **Reason master**: extend `stock.scrap.reason.tag` (recommended, §2.4) or keep a separate `asr.stock.reason` as the prompt listed?
5. **WIP component pricing**: default to Odoo's wizard rule (today's standard price) with the "day's average cost" switch — agreed?
6. **Lot-valuated products**: exact-but-slow path through Odoo's own `total_value` (no pre-aggregation) — acceptable, or should Phase 1 mirror per-lot stacks too?
7. **Ledger presentation**: the "valuation adjustment" column that makes every level foot to Odoo's closing value — agreed, or would you rather hide it and show only Odoo's closing?
8. **Benchmark database**: generate synthetic volume on the demo DB (the plan), or run the timings on a copy of a real database as well (ADL has 82k moves; Ortho far fewer)?
