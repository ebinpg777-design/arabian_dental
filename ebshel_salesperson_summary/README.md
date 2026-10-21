# Salesperson Summary

Receivables by salesperson for a period: **Accounting ▸ Reporting ▸ Salesperson Summary**.
Pick the period (presets or dates), optionally the salespersons, and the report:

| Report | What it lists |
|---|---|
| Summary | one line per salesperson: customers, opening, billed, received, returns & adjustments, outstanding, collected %, share of the outstanding; a billed-against-received chart |
| Detailed | the summary, then every salesperson with their customers (town and sales team under the name), optionally a new page per salesperson |

**View** shows the report on screen, **Print PDF** prints it (A4 portrait, the company's
document layout), **Export Excel** gives a *Summary* and a *Customers* sheet with filters.

* Customers are counted under the salesperson on the customer (the commercial partner);
  customers without one are listed last as *No salesperson*.
* Figures are posted receivable journal items: opening = balance before the period;
  billed = debits in the period; received = credits booked in bank, cash or card journals
  (or by a payment); returns & adjustments = every other credit (credit notes, write-offs);
  outstanding = opening + billed - received - returns & adjustments.
* Collected = received / (opening + billed), coloured from 60 % (good) and 30 % (fair).
* A customer with no balance and no movement in the period is left out.

A database with its own letterhead overrides `salesperson.summary.wizard._layout()` and
fills the empty `ebshel_salesperson_summary.letterhead` template.

Depends on `account`; needs `xlsxwriter` (shipped with Odoo).
