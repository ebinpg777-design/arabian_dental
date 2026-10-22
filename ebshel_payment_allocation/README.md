# Payment Allocation to Invoices & Bills (`ebshel_payment_allocation`)

Odoo 20 module: a customer payment or vendor payment lists the partner's open
invoices or bills - each with its open amount - on the payment form itself. Tick
what the money is for; confirming the payment reconciles it with those invoices.
Depends on `account` only (Invoicing, Community).

Odoo's own way to pay specific invoices is the Register Payment wizard, which
starts from the invoices. This module adds the other direction: start from the
money.

## What the user sees

* An **Open Invoices & Bills** tab on a draft payment with a partner: one row per
  open item with Select, Invoice / Bill, Date, Due Date, Reference, Amount, Open
  Amount and Allocate; overdue items in red, settled ones greyed out.
* Figures above the list: how many items are open, the open total, what is
  allocated and what is not. **Select All**, **Clear** and **Refresh** buttons.
* A warning when the allocations do not fit (more than an item's open amount,
  or more than the payment), a note when part of the payment stays unallocated.
* After confirmation the tab keeps only the items the payment settled; the
  standard "Invoices" / "Bills" stat buttons work as usual.

## How the amount and the allocations relate

* While the amount **follows the allocations** (the default), ticking an item
  allocates its whole open amount and the payment amount becomes the total;
  lower an allocation for a part payment.
* Once the user **types an amount** it is theirs: ticking an item fills only what
  is left of the amount; lowering the amount trims the newest allocations first;
  raising it leaves the rest unallocated - it stays as an open credit on the
  partner's account; clearing the amount hands it back to the allocations.

## What confirmation does

1. Checks the allocations against the live open amounts: an item paid elsewhere
   since the list was built, or an allocation above its open amount, is refused
   with a message naming the invoice.
2. Books the payment's receivable or payable side as Odoo does: **one journal
   item**.
3. Reconciles that item with each ticked invoice for **exactly the amount
   allocated to it** - a part payment typed on a later invoice stays a part
   payment, rather than Odoo's oldest-first fill - so the invoice's payment state
   follows. What is not allocated stays open on the same item, as an open credit.
4. Drops the lines that were not ticked.

Reset to draft unreconciles the item as usual; the allocations stay and are
applied again on the next confirmation.

**Upgrading from 20.0.1.0.0**, which booked one journal item per allocated
invoice: the upgrade merges each such entry into one item and reconciles it with
the same invoices for the same amounts. Only the journal entry is reset and posted
again (it keeps its number and date), not the payment, so no confirmation is sent
again, and the invoices end as paid as they were.

## Design notes

* `account.payment.allocation` is one open item on one payment: `move_line_id`
  (the receivable/payable journal item), `selected`, `allocate`; the rest is
  related or computed live (`amount_open` is the item's current residual).
* The list is built by **onchange**, not by a stored compute: payments made by
  the Register Payment wizard, the bank reconciliation or any code never load a
  partner's open items - only a form does, and only while the payment is draft.
* Open items = posted, unreconciled journal items on the payment's counterpart
  account, for the commercial partner, in the payment's currency, with the sign
  the payment direction settles (money in: positive residuals - invoices, vendor
  refunds; money out: negative - bills, credit notes, a customer's overpayment).
  Instalments of one invoice are separate items. At most 1,000 items are listed.
* The amounts: Odoo reconciles two items for the smaller of their open amounts.
  `account.move.line._reconcile_up_to(other, amount)` reconciles one pair at a
  time under a `reconcile_cap` context that `_prepare_reconciliation_single_partial`
  reads for that pair only, so the partial is the allocation; matching numbers,
  payment states and exchange differences stay Odoo's own.
* `ebshel_multi_partner_payment`: a multi-partner payment cannot be allocated to
  invoices (its counterpart is split by partner instead); the confirmation says
  so if items were ticked.
* Access: billing users (`account.group_account_invoice`) create and change
  allocations, read-only accounting users read them; multi-company rule on the
  payment's company.

## Tests

`tests/test_allocation.py` (16): listing and ordering, other currency not
listed, instalments, partner change, ticking adds up and settles, lowering the
amount trims, typed amount fills what is left, overpayment stays unallocated,
clearing the amount, Select All / Clear, vendor bills, reset to draft and confirm
again, too much on one item, an item paid meanwhile, wizard payments untouched.

Run: `odoo-bin -d <db> -i ebshel_payment_allocation --test-enable --test-tags /ebshel_payment_allocation --stop-after-init`
