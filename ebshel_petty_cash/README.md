# Advanced Petty Cash (`ebshel_petty_cash`)

Odoo 20 module for imprest petty cash: funds, vouchers, an approval matrix
with multi-level approvals, risk scoring, monthly budgets, cash advances,
replenishment, transfers, cash counts and printed reports. Depends on
`account` only (Invoicing, Community).

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
  `total_included` (a voucher line is a receipt total), `_round_base_lines_tax_details`,
  `_prepare_tax_lines`. Voucher totals include the rounding deltas, so the
  voucher total is the cash credit, cent for cent.

## Documents

| Model | Entry |
| --- | --- |
| `ebshel.petty.cash.expense` (voucher) | Dr expense lines + tax lines / Cr fund account, or Cr advance account when booked against an advance |
| `ebshel.petty.cash.advance` | Pay out: Dr advance account (holder) / Cr fund. Settlement: the difference back to the fund or to the holder; the advance account is reconciled |
| `ebshel.petty.cash.replenishment` | Top-up: Dr fund / Cr counterpart; return to bank: the reverse. Counterpart defaults to the bank journal's outstanding payments (receipts) account, else the company transfer account |
| `ebshel.petty.cash.transfer` | Dr destination fund / Cr source fund (same company and currency) |
| `ebshel.petty.cash.count` | Difference only: cash over / short on the journal's cash difference accounts |

## Approval matrix

Vouchers, advances and replenishments go through draft → to approve →
approved → paid / posted (→ settled for advances); refused / cancelled.

* **Approval levels** (`ebshel.petty.cash.approval.level`, Configuration >
  Approval Levels): document types, *applies from* amount (company currency),
  funds, voucher categories (e.g. entertainment needs the director), and who
  approves - any petty cash approver, the administrators, the fund custodian
  or named users - with **one of them** or **each of them** needed.
* On submission every matching level becomes an **approval step**
  (`ebshel.petty.cash.approval.step`), approved in sequence. No matching
  level: approved on submission (this replaces the old auto-approve limit).
* **Segregation of duties**: no self-approval (setting), and a person who
  approved one level cannot approve another level of the same document
  (setting, on by default).
* **Escalation**: a level waiting longer than its days is opened to its
  escalation user (cron every 2 hours), with an activity and a chatter note.
* **Delegation** (Approvals > Delegations): an approver hands his approvals
  to a colleague for a period; the step records whom the delegate acted for.
* **Approvals inbox** (Approvals > My Approvals): every step waiting for the
  user, any document type, with approve / refuse buttons and mass actions.
  Approval Analysis shows the time each level and approver takes to decide.
* Default data: one "Approver" level (vouchers and advances, any approver)
  and one "Replenishment Approval" level (administrators).

## Risk scoring and budgets

* Each voucher is scored (0-100) on submission: possible duplicate, no
  receipt, weekend date, round amount, late submission, over budget, just
  under an approval threshold, **split spending** (same requester, fund and
  day crossing a threshold together), and amounts above three times the
  requester's 90-day average. Findings show on the voucher and in the
  chatter; approvers filter and group by risk.
* **Risk-based review** (setting): a high-risk voucher gets an extra review
  by the administrators when its chain has no administrators' level.
* **Monthly budget** per fund (warn = risk flag, or block) and **monthly limit
  per person**; the dashboard shows the budget used.

## Controls

* Receipts required above an amount or per category, allowed categories,
  payee per category, limit per voucher and per category line, no overdraft,
  duplicate detection, overdue advances block new ones.
* Replenishments: the paid vouchers not covered yet are loaded on
  submission; the claim report totals them per category. Posted by
  administrators.
* Daily cron: low balance reminder (+ automatic replenishment request), cash
  count reminder, overdue advance reminder - each with its own activity type.

## Reports

Voucher, advance slip, replenishment claim and cash count sheet (with the
approval chain and a QR code linking to the document); Fund Position (from
the funds); and, from Reporting > Printed Reports: Cash Book (ledger,
running balance), Spending Summary (by category, fund against budget,
requester and payee) and Advance Aging (by holder and days overdue).

## Security

| Group | Can |
| --- | --- |
| User | Funds they hold or belong to; their own vouchers and advances; custodians see, pay, count and request top-ups for their fund |
| Approver | Everything of every fund (read); approves the levels they are an approver of; delegates their approvals |
| Administrator | Configuration and approval levels, the administrators' levels, posting replenishments, count differences, transfers. Implies read-only accounting. Accounting administrators are petty cash administrators |

A fund custodian who is only a User can still approve a "fund custodian" level.

Multi-company rules on every document.

## Tests

`tests/` runs on `AccountTestInvoicingCommon` (69 tests): ledger balance,
journal creation and constraints, tax-included receipts and global rounding,
foreign-currency funds, the approval matrix (levels by amount, fund and
category, one / each approver, segregation of duties, self-approval,
delegation, escalation, inbox, refusal), risk scoring and review, budgets
and personal limits, controls, advances (return, reimbursement,
reconciliation, overdue), replenishment claims, counts, transfers,
statement, spending and aging data, record rules, a custodian without
accounting rights, and rendering of every report.

```
odoo-bin -d <db> -i ebshel_petty_cash --test-tags /ebshel_petty_cash --stop-after-init
```

## Upgrading from 20.0.1

Removed: payment of vendor bills from vouchers, tax-excluded voucher amounts,
negative fund balances, "pay on approval", the fund's auto-approve limit and
named approvers, and the company's second-approval and replenishment-approval
switches. The migration turns a company's second approval into an
administrators' level from the same amount, archives the replenishment level
when no company approved replenishments, and builds the approval chain of the
documents waiting for an approval. Named fund approvers and auto-approve
limits are not converted: set them up as approval levels.

## Limits

* A transfer needs two funds of the same currency.
* Vendor bills paid from the box must be in the fund currency.
* Exchange differences on foreign-currency funds follow Odoo's own revaluation.
