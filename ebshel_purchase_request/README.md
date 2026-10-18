# Purchase Request Management

**Full procurement lifecycle for Odoo 19** — from an internal purchase request,
through multi‑level approvals and multi‑vendor RFQs, to award, purchase order,
receipt and billing — with complete traceability at every step.

* **Version:** 19.0.1.0.2
* **Category:** Purchase
* **License:** OPL‑1
* **Author:** Ebshel Technologies — <https://ebshel.com>
* **Support:** ebinpg777@gmail.com

---

## 1. Overview

This module adds a complete, self‑contained procurement workflow on top of the
standard Odoo **Purchase**, **Stock**, **Analytic** and **Accounting** apps:

```
Purchase Request  →  Multi‑level Approval  →  RFQ (multi‑vendor)
       →  Vendor Quotations  →  Comparison & Award  →  Purchase Order(s)
       →  Receipts & Bills  →  Closure
```

Every record is linked end‑to‑end, so from any purchase request you can trace
the RFQs it generated, the vendor quotations received, the purchase orders
raised, the goods received and the amounts billed.

---

## 2. Key Features

### 2.1 Purchase Requests
- Multi‑line requests with product, quantity, UoM, estimated unit price and
  estimated totals.
- **Department / Cost‑Centre** classification through analytic accounts, seeding
  each line's analytic distribution at 100%.
- **Source tracking** (manual, auto‑reorder, job card, OEM…) for unified intake
  and reporting, plus a free‑text source document reference.
- **Priority**, required‑by date, specifications and per‑line remarks.
- Per‑line cancellation/restore while still in preparation, with automatic
  auto‑reject of a request once all its lines are cancelled.
- **Request form "cockpit":** live insight band showing workflow progress, the
  estimated value, a colour‑coded *required‑by* countdown, and receipt
  fulfilment — plus a **department budget** card and an over‑budget warning
  (see §2.7).
- Request Details and Product Lines become read‑only once the request leaves
  Draft, protecting approved content.

### 2.2 Multi‑Level Approval Workflow
A fully configurable approval engine driven by **Approval Levels**:
- **Routing conditions** — each level applies based on:
  - an **amount band** (min / max of the request total);
  - a **department / cost‑centre** filter (analytic accounts);
  - **category‑wise amount limits** — restrict a level to specific product
    categories within a spend band (e.g. route IT spend over a threshold to a
    dedicated approver).
- **Approval policy per level** — *Any one*, *All approvers*, or a configurable
  **Quorum** (N of the eligible approvers must sign off).
- Eligible approvers resolved from **approver groups** plus named **users**.
- **Escalation SLA** (target turnaround days) and optional pending notifications.
- **Approval log** — an immutable history of who approved/rejected what, when,
  and at which level (the level name is stored so history survives config
  changes).
- **Routing Simulator** — a what‑if tool: enter an amount and department to
  preview exactly which levels a request would pass through, before it is
  submitted.

### 2.3 RFQ Management (one or many requests → one RFQ)
- Create an RFQ from the approved lines of **one or several** purchase requests
  via the *Create RFQ* wizard, with support for **partial / split sourcing**
  (only part of a line's quantity may be placed on an RFQ).
- RFQ tracks lines, invited vendors, the quotation deadline and a procurement
  officer.
- **RFQ "cockpit":** status ribbon, workflow progress, deadline countdown,
  vendor response rate, best quote, price spread, and smart contextual alerts
  (e.g. savings opportunity, overdue deadline).
- Reusable **Terms & Conditions** templates, seeded from a company default and
  shown to vendors and on the comparison PDF.
- RFQ emails sent to vendors through configurable mail templates.

### 2.4 Vendor Quotations & Comparison
- Capture multiple **vendor quotations**, each with priced lines, discounts,
  taxes, delivery lead time, payment terms, incoterms and validity dates.
- **New (prospective) vendors** can be quoted without first creating the
  partner — they are **auto‑registered** as a vendor when awarded.
- **Advanced OWL comparison dashboard** (client action) with:
  - side‑by‑side vendor scorecards with scores and ranking;
  - automatic **risk & quality flags** (expired quote, partial coverage,
    highest price, slowest delivery, new vendor);
  - a **recommendation** with human‑readable reasons;
  - an **optimal split basket** (cheapest vendor per line) with projected
    savings;
  - **what‑if exclusion** of vendors from the analysis;
  - CSV export and print.

### 2.5 Award & Purchase Order Generation
- **Single winner** — crown one vendor (with justification) and generate a PO.
- **Split award** — *Allocate & Order*: split each line's quantity across
  multiple vendors and generate **one confirmed purchase order per vendor** in a
  single step. Awarded quantities are capped at the requested quantity.
- POs are linked back to the originating request lines, and source requests are
  automatically **closed** once all their lines are fully ordered.

### 2.6 Receipt & Billing Traceability
- **Allocations** link request lines to PO lines and stock moves, so each
  request line tracks **ordered**, **in‑transit**, **received** and **billed**
  quantities and the pending quantity to receive.
- Roll‑up **reception** and **billing** status badges (not / partial / full) on
  each line, plus a dedicated **Fulfilment** tab on the request.

### 2.7 Budget Awareness
- When the Odoo **Budget** module is in use, the request form shows the selected
  department's **current‑period budget**: budgeted amount, committed, available,
  % used, and the amount available **after** this request.
- An **over‑budget warning** appears when the request would exceed the
  department's available budget. Only **Open (confirmed)** budgets are counted.

### 2.8 Dashboards & Analytics
- **Procurement Dashboard** (manager client action) — KPIs and trends across the
  procurement pipeline.
- **Department / Cost‑Centre Insight** — a per‑department cockpit (committed
  spend, status mix, monthly trend, top products/vendors, recent requests),
  opened directly from a request.
- **KPI list dashboards** on Purchase Requests and RFQs, mirroring the standard
  Purchase order list experience.

---

## 3. Roles & Security

| Group | Capabilities |
|-------|--------------|
| **User** | Create and manage own purchase requests. |
| **Approver** | Approve / reject requests pending at their level. |
| **Procurement Officer** | Create RFQs, capture quotations, award and generate POs. |
| **Manager** | Full visibility across the company; dashboards & insights. |
| **Administrator** | Configure approval levels, terms, settings. |

Record rules enforce *user sees own*, *approver sees pending approvals*, and
*manager sees all in company*, with multi‑company isolation on requests, RFQs and
vendor quotations.

---

## 4. Typical Workflow

1. **Create** a purchase request and add product lines (User).
2. **Submit** — the approval engine resolves the applicable levels by amount,
   department and product category, and routes the request (User → Approvers).
3. **Approve** through each level per its policy (Approvers); rejections capture
   a reason and reset the request.
4. **Create RFQ** from the approved lines, add vendors and send (Procurement
   Officer).
5. **Capture quotations** as vendors respond; mark them received.
6. **Compare & Award** in the comparison dashboard — single winner or split
   across vendors.
7. **Generate Purchase Order(s)** — confirmed automatically and linked back.
8. **Receive & Bill** through standard Odoo flows; the request tracks fulfilment
   and **closes** when fully ordered.

---

## 5. Configuration

Found under **Purchase Requests → Configuration** (Administrator):

- **Settings** — module‑level options and company defaults.
- **Approval Levels** — define routing, policy, approvers, SLA, department and
  category limits.
- **Routing Simulator** — preview approval routing for any amount / department.
- **RFQ Terms & Conditions** — reusable T&C templates.

---

## 6. Menus

- **Dashboard** — Procurement Dashboard (Manager).
- **Purchase Requests** — Requests and Request Lines.
- **Approvals** — *My Approvals* and *All Pending Approvals*.
- **Procurement** — New RFQ, RFQs, Vendor Quotations, Vendors.
- **Configuration** — Settings, Approval Levels, Routing Simulator, RFQ Terms.

---

## 7. Technical Notes

**Key models**

| Model | Purpose |
|-------|---------|
| `ebshel.purchase.request` / `.line` | Purchase request header & lines |
| `ebshel.purchase.request.approval.config` | Approval level definition |
| `ebshel.pr.approval.category.limit` | Per‑category amount band on a level |
| `ebshel.purchase.request.approval.log` | Approval history |
| `ebshel.pr.approval.simulator` | Routing what‑if tool (transient) |
| `ebshel.purchase.rfq` / `.line` | RFQ header & lines |
| `ebshel.purchase.vendor.quotation` / `.line` | Vendor quotations & priced lines |
| `ebshel.purchase.request.allocation` | Request‑line ↔ PO‑line ↔ stock‑move link |
| `ebshel.rfq.terms` | T&C templates |

**Client‑side components (OWL):**
`ebshel_procurement_dashboard`, `ebshel_rfq_comparison`, `ebshel_department_insight`
(client actions) and `rfq_vendor_comparison` (field widget).

**Reports (QWeb PDF):** Purchase Request status, RFQ, and RFQ vendor comparison.

**Performance:** heavy roll‑up fields (amounts, quantities, fulfilment status)
are stored computed fields with precise dependencies; list KPIs use batched
reads to avoid N+1 queries; approval routing computes per‑category spend once per
evaluation (independent of line count); and `company_id` / date fields are
indexed for large‑database dashboard and list queries.

---

## 8. Dependencies

`purchase`, `stock`, `analytic`, `mail`, `account` (all standard Odoo).
Budget figures on the request integrate with the optional **Budget** module when
installed (gracefully ignored when absent).

---

## 9. Installation

1. Copy the `ebshel_purchase_request` folder into your Odoo addons path.
2. Update the apps list and install **Purchase Request Management**.
3. Assign users to the appropriate groups (User / Approver / Procurement Officer
   / Manager / Administrator).
4. Define your **Approval Levels** under *Configuration*.

---

## 10. Testing

Automated tests live in `tests/`:

- `test_split_award.py` — RFQ → split‑award → multi‑PO generation (per‑line,
  intra‑line split, new‑vendor auto‑registration, storable products,
  over‑allocation guard).
- `test_performance.py` — approval‑routing query count is independent of the
  number of request lines.

Run them with:

```bash
odoo-bin -d <db> -u ebshel_purchase_request \
         --test-enable --test-tags /ebshel_purchase_request --stop-after-init
```

---

## 11. Support

Ebshel Technologies
Website: <https://ebshel.com> · Email: ebinpg777@gmail.com

© 2025‑2026 Ebshel Technologies. License OPL‑1.
