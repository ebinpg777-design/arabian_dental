# Analytic Budgets (`ebshel_account_budget`)

Odoo 20 Community module for budgets per analytic account, in the spirit of
the Enterprise Budget app (`account_budget`). Depends on `account`, `analytic`
and `mail`.

## Design

* **A budget** (`ebshel.account.budget`) covers a period (fiscal year by
  default) and is an Expense or a Revenue budget. It holds one line per
  analytic account: a department, a cost centre, a project.
* **A budget line** (`ebshel.account.budget.line`) plans an amount for its
  analytic account over the budget's period. Its figures are computed live,
  nothing is stored:
  * **Achieved**: analytic items of posted journal entries (items with a
    general account) in the period, on any analytic plan. Costs count positive
    on an expense budget, income on a revenue budget. One query per analytic
    plan, whatever the number of lines.
  * **Committed**: a hook (`_compute_committed_amount`) for modules that reserve
    budget before it is booked. `ebshel_purchase_budget` fills it with open
    purchase requests and orders.
  * **Theoretical**: the budget pro rata over the days elapsed in the period.
  * **Available**: budget minus achieved minus committed; a line is over budget
    when achieved plus committed exceeds it.
* **Workflow**: Draft → Open → Done, or Cancelled. **Revise** an open budget to
  get a draft copy linked to it; the original is kept as Revised.
* `_get_open_lines(analytic_account_id, company_id, date)` returns the lines of
  open budgets covering a date, for other modules to check against.

## Screens

* **Accounting → Accounting → Analytic Budgets → Budgets**: the budgets, their
  lines and totals, a link from each line to the analytic items behind it.
* **Accounting → Accounting → Analytic Budgets → Budget Analysis**: every line
  of the open budgets covering today, with sums, grouped by budget, analytic
  account or plan.
* **Analytic account form**: a Budgets tab and a smart button.

## Access

Accounting readers (`account.group_account_readonly`) read budgets; accounting
administrators (`account.group_account_manager`) create and change them.
Records are limited to the user's companies.

`ebshel_account_advanced` has its own account-based budgets (with monthly
phasing); this module is the analytic budget that purchase control reads. The
two can be installed together.

## Tests

`python odoo-bin -d <db> -i ebshel_account_budget --test-enable --test-tags=/ebshel_account_budget`
