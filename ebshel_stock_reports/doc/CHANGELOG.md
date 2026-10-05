# Changelog

## 1.0.0 - 2026-10-04

First release.

### Engine
* Daily movement summary (`asr.stock.move.daily`) per company, day, product,
  location and movement type, built by SQL from the done move lines with the
  valued-location rule and owner exclusion of `stock_account`.
* Daily valuation summary (`asr.stock.value.daily`): closing quantity anchored
  on the current quants, closing value mirrored from `_run_standard_batch`,
  `_run_average_batch` and `_run_fifo`, lot-valuated products read from Odoo.
* Dirty queue fed by `stock.move`, `stock.move.line`, `product.value`,
  product category and company hooks; five-minute cron; on-demand refresh
  from the reports; health check; full rebuild.

### Reports
* Stock ledger / stock card (ledger, daily card, move-line card).
* Planned vs actual consumption, WIP with month-end snapshots, MO cost /
  production analysis, MO component shortage.
* Inventory aging, slow / non-moving, ABC / XYZ, turnover / days of cover.
* Customer OTIF, vendor OTIF, purchase price variance, open order shortage.
* Valued count variance, scrap and returns by reason, movement registers.
* Inventory vs GL with the four accrual listings.
* GST stock register, monthly production account.

### Data captured
* Reason on scraps, returns and count adjustments (reason tags extended with
  usage and GST category).
* System and counted quantity on count adjustment moves.
* BoM planned quantity per unit frozen on raw moves at MO confirmation.
