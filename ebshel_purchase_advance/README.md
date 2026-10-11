# Purchase Advance Payments (`ebshel_purchase_payment`)

Register **advance / down payments** directly from a Purchase Order and let Odoo
**settle them automatically** against the vendor bills generated from the same
order. The module is built entirely on top of native Odoo accounting
reconciliation, so it adds capability **without changing any standard behaviour**.

> Odoo 18.0 · Depends on `purchase`, `account`

---

## Why this module

Out of the box, Odoo lets you pay a vendor before billing, but there is no
guided way to:

- raise a prepayment **from the Purchase Order** itself,
- keep a clear, per-PO view of *how much was advanced, settled and still
  available*, and
- have those advances **automatically applied** to the vendor bill the moment
  it is created from the PO.

`ebshel_purchase_payment` adds exactly that, in a way that is innovative,
user-friendly and flexible — while leaving the default purchase and accounting
flows untouched.

---

## Key features

### 1. One-click advance payment from the PO
A **Register Advance Payment** button appears in the header of every confirmed
Purchase Order. It opens a focused wizard where you choose how to size the
advance:

| Advance type | Behaviour |
|---|---|
| **Percentage of Order** | e.g. 30 % of the order total |
| **Fixed Amount** | type the exact figure |
| **Full Remaining Amount** | whatever has not yet been advanced |

You also pick the **payment date**, **journal** and **payment method**, add an
optional memo, and the wizard creates **and posts** a standard vendor payment
(`account.payment`, outbound / supplier) linked back to the order.

### 2. Live advance dashboard on the PO
A colour-coded banner and a smart button show, in the order currency:

- **Advance Paid** — total posted advances,
- **Advance Applied to Bills** — the part already reconciled with bills,
- **Advance Available** — what is still open to apply to future bills,
- an *Advance Usage* badge: *Advance Unapplied → Partially Applied → Fully
  Applied*.

> The badge describes how much of the **advance** has been used up — it is
> **not** the bill's payment status. "Advance Fully Applied" simply means no
> advance balance is left; the bill itself can still be partly unpaid.

### 3. Automatic settlement on bill creation
When you press **Create Bill** on the PO, the module reconciles the open
advances against the new vendor bill automatically (configurable). The bill's
payment status moves to *In Payment / Paid* with no manual reconciliation.

### 4. Manual settlement safety net
On any posted vendor bill linked to a PO, a **Settle Advances** button lets you
apply available advances on demand — handy for bills created outside the PO
button or when auto-settle is disabled.

### 5. Flexible, robust handling of every scenario
- **Advance larger than the bill** → the bill is fully paid and the remainder
  stays available for the next bill.
- **Advance smaller than the bill** → the bill is partially paid; register more
  advances or pay the balance normally.
- **Several bills from one PO** → advances are consumed bill by bill, oldest
  first.
- **Several advances** → reconciled in date order.
- **Bill reset to draft / cancelled** → Odoo automatically un-reconciles and the
  advance returns to *Available* (the figures recompute live).
- **PO cancelled with open advances** → a chatter note warns you so nothing is
  lost; the cancel itself is never blocked.
- **Currency** → only same-currency advances are auto-applied; mismatches are
  left for manual handling instead of guessing.
- **Cash journals (the Odoo 18 `in_process`/no-move case)** → the engine only
  reconciles posted move lines, so it degrades gracefully.

---

## How it works (accounting)

An advance payment to a vendor posts a **debit on the Accounts Payable account**
(and a credit to the outstanding-payments / bank account). The vendor bill posts
a **credit on the same payable account**. Settlement is simply the native
reconciliation of those two payable lines — exactly the mechanism behind Odoo's
"Outstanding debits" widget on a bill. Because the residual figures are computed
from the live reconciliation state, they are always correct, even after a bill is
reversed or a payment is unreconciled.

No journal entries are invented, and no bill lines are modified.

---

## Configuration

**Settings → Purchase → Advance Payments**

| Setting | Default | Purpose |
|---|---|---|
| **Auto-settle Advances** | On | Reconcile advances automatically when a bill is created from a PO. |
| **Limit Advance to Order Amount** | On | Prevent advancing more than the remaining order amount. |
| **Default Advance Journal** | — | Journal pre-selected in the wizard. |

All settings are per-company.

---

## Usage

1. Confirm a Purchase Order.
2. Click **Register Advance Payment**, choose the amount type, journal and date,
   then **Create & Post Payment**.
3. Watch the advance banner update on the PO.
4. Receive the goods/service and click **Create Bill** — the advance is settled
   automatically.
5. Check the bill: its payment status reflects the applied advance.

---

## Security

- The advance wizard is available to **Purchase Users** and **Billing** users.
- The header / smart buttons that create payments are restricted to users with
  **Billing** rights (`account.group_account_invoice`).

---

## Technical notes

- New fields on `account.payment`: `purchase_order_id`, `is_purchase_advance`.
- New fields on `purchase.order`: `advance_payment_ids`,
  `advance_payment_total/reconciled/residual/count/status`.
- New field on `account.move`: `purchase_advance_available`.
- New transient model: `purchase.advance.payment.register`.
- New company settings on `res.company` / `res.config.settings`.
- Only two methods are overridden — `purchase.order.action_create_invoice`
  (super first, then optional settlement) and `purchase.order.button_cancel`
  (super first, then a chatter warning). Neither changes existing return values
  or core logic.

---

## Compatibility

- Odoo **18.0** (Community & Enterprise).
- No clash with multi-currency, multi-company, or the standard vendor payment
  register.

---

© 2025-2026 **Ebshel Technologies** — https://ebshel.com · License: OPL-1
