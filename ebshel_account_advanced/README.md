# Advanced Accounting

Everything the accounting team does after the entries are booked, on one set
of screens inside the Accounting app, for Odoo 20 Community.

* Odoo **20.0** Community
* Depends on `account`, `analytic`, `mail`, `ebshel_account_reports`, `ebshel_account_assets`
* No top-level menu: every screen sits where it belongs in Accounting

---

## Installation

1. Copy the three `ebshel_account_*` modules into your addons path.
2. Install **Advanced Accounting**.

## The screens

| Screen | Where | What |
| --- | --- | --- |
| Collections Desk | Customers | Follow-up levels, promise-to-pay, call log, reminders with the statement and invoices attached, days to pay |
| Matching Desk | Customers | Open items paired by amount, reference or sum with a confidence score, applied in one click; bank lines to invoices |
| Bank Reconciliation | Accounting › Bank | Paste the bank statement: what the books hold is ticked, the rest matched, written off or taught to rules; reconciliation statement (PDF) at any date |
| Entry Studio | Accounting › Transactions | Journal entries typed like a spreadsheet, pasted from Excel, balanced in one key; templates; analytic redistribution |
| Budgets, Budget Board | Accounting › Planning | Budgets per account and analytic account, phased by month; actual, committed, theoretical; Budget vs Actual report |
| Cash Forecast | Accounting › Planning | Thirteen weeks ahead from open items, planned items and history, with what-if sliders |
| Deferrals | Accounting › Closing | Revenue and expense recognised over the service period from the invoice line; Deferral Schedule report |
| Close Cockpit | Accounting › Closing | Month-end checklist with live counts; sets the lock date on close |
| Ledger Health | Accounting › Closing | Nightly scan: duplicate bills, sequence gaps, unusual amounts, stale drafts, unapplied credits and more |
| Finance Cockpit | Reporting | P&L waterfall, balance sheet stacks, twelve months of trend, ratios, the work waiting |

Reminders are never e-mailed automatically until a follow-up level is marked
automatic; they are sent from the company's e-mail address.

## Access

Accounting users work the desks; Accounting administrators configure levels,
rules and checklists and close periods. Customers' follow-up status is visible
to accounting groups only.

## Tests

```
odoo-bin -c <conf> -d <db> -u ebshel_account_advanced --test-enable \
    --test-tags /ebshel_account_advanced --stop-after-init --http-port=<free port>
```

Use a real HTTP port: the reminder PDF tests deadlock under `--no-http`.

## License

OPL-1 (see `LICENSE`). © Ebshel Technologies.
