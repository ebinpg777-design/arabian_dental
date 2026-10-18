# Advanced Petty Cash (`ebshel_petty_cash`)

Odoo 20 module for imprest petty cash: funds, vouchers, approvals, cash
advances, replenishment, transfers and cash counts. Depends on `account` only
(Invoicing, Community).

## Design

* **A fund is a cash journal with its own cash account.** The journal and its
  account are created with the fund when none is given. Two funds cannot share
  a journal or an account.
* **The balance is read from the ledger**: the sum of the posted journal items
  on the fund account (in fund currency for a foreign-currency fund). Nothing
  is stored, so the fund cannot drift from the books, and entries posted by
  hand on the account count too.
* **Every document ends in one entry in the fund journal**, posted as
  superuser after the module has checked the user's petty cash rights - custodians
  need no accounting rights. Entries carry `petty_cash_fund_id`; they cannot be
  reset to draft from accounting, the document is cancelled instead (which
  reverses the entry).
* **Taxes** use Odoo 20's tax engine exactly like `hr_expense` (company-paid):
  `_prepare_base_line_for_taxes_computation` with `special_mode`
  `total_included` / `total_excluded`, `_round_base_lines_tax_details`,
  `_prepare_tax_lines`. Voucher totals include the rounding deltas, so the
  voucher total is the cash credit, cent for cent.

## Documents

| Model | Entry |
| --- | --- |
| `ebshel.petty.cash.expense` (voucher) | Dr expense lines + tax lines (+ payable of paid vendor bills, reconciled) / Cr fund account, or Cr advance account when booked against an advance |
| `ebshel.petty.cash.advance` | Pay out: Dr advance account (holder) / Cr fund. Settlement: the difference back to the fund or to the holder; the advance account is reconciled |
| `ebshel.petty.cash.replenishment` | Top-up: Dr fund / Cr counterpart; return to bank: the reverse. Counterpart defaults to the bank journal's outstanding payments (receipts) account, else the company transfer account |
| `ebshel.petty.cash.transfer` | Dr destination fund / Cr source fund (same company and currency) |
| `ebshel.petty.cash.count` | Difference only: cash over / short on the journal's cash difference accounts |

## Workflow and controls

* Vouchers and advances: draft → to approve → approved → paid (→ settled for
  advances); refused / cancelled. Auto-approval up to a fund limit; named fund
  approvers; second approval by a manager (another person) from a company
  threshold; no self-approval unless allowed; optional pay on approval.
* Replenishments are approved and posted by managers (approval can be turned
  off per company); "Load Vouchers" attaches the paid, not yet replenished
  vouchers and the claim report totals them per category.
* Limits: per voucher, per category line, receipts above an amount or per
  category, allowed categories, payee per category, no overdraft (unless
  allowed), duplicate detection, overdue advances block new ones.
* Daily cron: low balance reminder (+ automatic replenishment request), cash
  count reminder, overdue advance reminder - each with its own activity type.

## Security

| Group | Can |
| --- | --- |
| User | Funds they hold or belong to; their own vouchers and advances; custodians see, pay, count and request top-ups for their fund |
| Approver | Everything of every fund (read), approve and refuse vouchers and advances |
| Administrator | Configuration, second approval, replenishment approval and posting, count differences, transfers. Implies read-only accounting. Accounting administrators are petty cash administrators |

Multi-company rules on every document.

## Tests

`tests/` runs on `AccountTestInvoicingCommon` (55 tests): ledger balance,
journal creation and constraints, tax-included / excluded and global rounding,
foreign-currency funds, approvals (limits, named approvers, second approval,
self-approval, refusal), controls, vendor bill payment and its cancellation,
advances (return, reimbursement, reconciliation, overdue), replenishment
claims, counts, transfers, statement, record rules, a custodian without
accounting rights, and rendering of every report.

```
odoo-bin -d <db> -i ebshel_petty_cash --test-tags /ebshel_petty_cash --stop-after-init
```

## Limits

* A transfer needs two funds of the same currency.
* Vendor bills paid from the box must be in the fund currency.
* Exchange differences on foreign-currency funds follow Odoo's own revaluation.
