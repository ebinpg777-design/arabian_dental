# Invoice Round Off (`ebshel_invoice_round_off`)

Odoo 20 module: a **Round Off** button on invoices and bills makes the total a
whole amount, and a company setting rounds off every new customer invoice as it
is created. Depends on `account` only (Invoicing, Community).

Totals like 650.01 or 399.99 come from tax-inclusive prices. Odoo's cash
rounding can already fix them - a rounding method adds a rounding line for the
difference, or adjusts the biggest tax, and books it on a profit or loss
account - but Odoo 20 applies a method by itself only when it is tied to a
currency, a partner category or a payment method. This module applies the
company's method on demand, or to every new customer invoice.

## What the user sees

* **Round Off** in the header of a draft invoice, credit note, receipt or bill:
  the rounding line appears, the total becomes 650.00 / 400.00, the standard
  tax totals show the rounding. **Remove Round Off** takes it away again.
* Accounting > Settings > Invoicing > **Round Off**: the rounding method the
  button uses (empty: the first rounding method of the company) and *Round Off
  New Customer Invoices* for the automatic version.

## Design notes

* Nothing new in the accounting: the button sets `invoice_cash_rounding_id`,
  Odoo's dynamic lines do the rest (`_sync_rounding_lines`), so the rounding
  line, its account and the tax report treatment are exactly the standard ones.
* The automatic option extends `_compute_invoice_cash_rounding_id`: Odoo's own
  matching runs first, the company's method fills the gap on draft customer
  documents. Bills are left alone, like Odoo's own automatic rounding.
* No rounding method at all: the button refuses with a message saying where to
  create one.

## Tests

`tests/test_round_off.py` (7): round off and remove, rounds up and posts, bills,
the company's method wins, only drafts, no method, automatic for new customer
invoices.
