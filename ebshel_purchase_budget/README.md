# Purchase Budget Control (`ebshel_purchase_budget`)

Odoo 20 Community bridge between **Purchase Request Management**
(`ebshel_purchase_request`) and **Analytic Budgets** (`ebshel_account_budget`).
Installed automatically when both are installed.

## What it does

* **Purchase requests read the analytic budget.** The department budget card,
  the over-budget warning, the budget approval and the hard block of
  `ebshel_purchase_request` take their figures from the open expense budgets
  covering the request date for the request's department (analytic account):
  budget, achieved (posted bills), committed (open requests and orders),
  available before and after the request.
* **Purchase orders are checked the same way** when they are created from
  requests, unless the company allows over-budget purchasing
  (*Allow to create even if the department budget exceeded*).
* **Budget lines show the commitments**: *Purchased* (open purchase requests),
  *Invoiced* (open purchase orders not billed yet) and *Committed* (their sum).
  Available deducts them. Once a bill is posted the amount moves to Achieved.

## How

* `purchase.analytic.line._budget_figures` is overridden to read
  `ebshel.account.budget.line`. With no open analytic budget for the account it
  falls back to the other sources (the Enterprise `account_budget` module).
  Achieved is read once for the whole window, so two lines on the same
  analytic account never count a bill twice.
* `ebshel.account.budget.line._compute_committed_amount` reads the purchase
  commitment ledger (`purchase.analytic.line`) for the line's account and
  period.

## Tests

`python odoo-bin -d <db> -i ebshel_purchase_budget --test-enable --test-tags=/ebshel_purchase_budget`
covers the request budget card, the reservation of approved requests, the
hard block, the budget approval, closed budgets and the move from Purchased to
Invoiced when the purchase order is created.
