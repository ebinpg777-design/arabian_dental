# Phase 2 — Codebase Mapping (Phase 0 deliverable)

> **Inherited document.** Written for the Ortho Creation project this suite was forked from (2026-09-22); the wording has been rebranded but the figures, database names and screenshots are that project's. Use it for how the modules work, not as a record of Arabian Dental Lab.

**Doc ref:** EPG/OC/2026/PH2-REQ-01 · Mapping produced 2026-08-09
**Codebase:** `/home/ebinpg/odoo/odooebin/custom/arabian_dental` (28 modules installed)

> Purpose: record what already exists so Phase 2 **extends** it instead of building a
> parallel set of models. Several epics turn out to be substantially implemented
> already; the spec's proposed names are superseded by the "Actual" column below.

---

## Discovery answers (D1–D10)

### D1 — Case spine and states
**Model: `lab.case`** (`lab_fieldwork`), plus `lab.case.line`.
States today: `draft` (Being Entered) → `submitted` → `registered` (Order Created) → `cancel`.

The case is a *field slip*: an executive records patient + work lines on a phone, and on
check-out it creates the `sale.order`. Link is `lab.case.sale_order_id` (m2o, readonly).

**The verification gate is NOT on the case — it is on `sale.order`** (see D-below), which
is the important discovery for Epic C.

| Spec state (§5) | Actual | Action |
|---|---|---|
| draft | `lab.case.draft` | reuse |
| submitted | `lab.case.submitted` | reuse |
| verified | `sale.order.verification_state = verified` | **reuse (SO-level)** |
| correction_needed | `sale.order.verification_state = rejected` | reuse, rename label only |
| on_hold | — | **ADD** (Epic C-3) |
| confirmed / in_production / qc / ready | `sale.order.state` + `mrp.production` | reuse |
| out_for_delivery / delivered | — | **ADD** (Epic B) |

`lab.case` also has `lab.lock.mixin` (`_lock_states = ('submitted','registered','cancel')`)
which already makes a submitted slip read-only for executives — the spec's C-1 "readonly
after submit" requirement is satisfied by this existing mixin, not by new `write()` guards.

### D2 — Field visits, purpose/outcome
**Model: `lab.visit`** (`lab_fieldwork`). Both fields are single-value **Selection**:
- `purpose`: `round`, `order`, `payment`, `complaint`, `new`
- `outcome`: `order`, `payment`, `followup`, `absent`, `complaint`

No "Delivery" value in either. → **Epic A-2 applies as written** (convert to m2m masters
+ post-migration mapping). Sequence `lab.visit` exists.

### D3 — Case ↔ SO ↔ MRP
`lab.case.sale_order_id` → `sale.order` → `mrp_production_ids` → `workorder_ids`
(standard Odoo). `lab_portal` already derives a 5-stage customer view from exactly this
chain (`_portal_stage`), and `lab_workcenter_scan` adds station/bench tracking on
`mrp.workorder`. **The Epic D-4 tracking wizard should reuse `lab_portal`'s timeline
logic rather than re-deriving it.**

### D4 — Delivery / dispatch
**None.** Delivery today is `stock.picking` only, plus
`sale_custom.report_dental_delivery_slip`. → **Epic B is greenfield.**

### D5 — Security groups (reuse these; do NOT create the spec's proposed names)

| Spec role | **Actual group to use** |
|---|---|
| Sales Executive | `lab_fieldwork.group_fieldwork_executive` |
| Registration User | `lab_order_control.group_lab_order_checker` (verify rights) and `…group_lab_order_entry` (entry) |
| Manager | `lab_fieldwork.group_fieldwork_manager` |
| Config manager | `lab_access_control.group_lab_config_manager` |
| CEO/Exec dashboards | `lab_ceo_dashboard.group_lab_executive` |
| WhatsApp | `epg_whatsapp.group_whatsapp_user` / `…_admin` |

Note the collision trap already resolved in this codebase: `group_lab_executive`
(CEO-level) is **not** the field executive. Do not conflate them.

### D6 — Rework today
Only `sale.order.is_rework` (Boolean) + `sale.rework` `ir.sequence` + an `active` flag
(v10 archived reworks). No reason master, no responsibility, no link to origin, no report.
→ **Epic E is essentially greenfield but must keep `is_rework` as the flag.**

### D7 — WhatsApp connector
**`epg_whatsapp` exists and is a full Meta Cloud API connector** (community, `epg.*`):
`epg.whatsapp.account` / `.template` / `.message` / `.conversation` / `.composer`, an
HMAC-verified webhook at `/whatsapp/webhook`, delivery/read receipts, the 24-hour session
window, STOP opt-out and a retry cron. `lab_whatsapp` is the lab-event layer on top.

`epg.whatsapp.account` is **already multi-number** (name, phone_number_id, waba id, token
under `base.group_system`, company, active, per-account webhook token/app secret).

**Missing for Epic F R17 (small delta, not a build):**
- `user_ids` m2m on the account (who may send from it)
- `res.users.epg_whatsapp_account_id` (the user's own number)
- account resolution order in the send path + server-side "not your account" check
- `is_default` per company

### D8 — Incentives
**Nothing exists.** → Epic G greenfield.

### D9 — List View Dashboard module
**Present and v19-native: `list_view_dashboard` 19.0.1.1.0** (depends `web` only).

Configured by **pure XML inside a `<list>` arch** — no Python, no JS, no config records:
```xml
<list>
  <listdashboard>
    <rowcard string="Optional heading">
      <card title="To Verify" domain="[('verification_state','=','to_verify')]"
            color="#e0a458" icon="fa-check" measure="" aggregate="sum"/>
    </rowcard>
  </listdashboard>
  …
</list>
```
Card attrs: `title`, `domain`, `color`, `icon`, `symbol`, `measure`, `aggregate`
(sum/avg/max/min), `user_field`. Cards are click-to-filter and recompute against the
user's current search. **The spec's D-3 fallback is NOT needed.**

### D10 — Sequences and templates
Existing `ir.sequence` codes: `lab.visit`, `lab.trip`, `lab.cheque`, `sale.rework`,
`mrp.workcenter.scan`, `petty.cash.*`, `pdc`, `account.bulk.payment`,
`account.recurring.invoice`. **No** `lab.case` sequence (case `name` is a compute) and
**no** `lab.delivery` → add for Epic B.

---

## The headline finding: Epic C already exists (on the sale order)

`lab_order_control` (installed) implements the maker–checker gate the spec asks for in
C-1, but on `sale.order` rather than `lab.case`:

| Spec C-1 item | Actual |
|---|---|
| `submitted → verified` | `verification_state`: `draft` → `to_verify` → `verified` / `rejected` |
| `verified_by_id`, `verified_date` | `verified_by_id`, `verification_date` ✔ |
| `correction_note` | `verification_note` ✔ |
| *Submit for Verification* | `action_submit_for_verification()` ✔ |
| *Verify* (registration only) | `action_verify()`, gated on `group_lab_order_checker` ✔ |
| *Return for Correction* | `action_reject_verification()`, note required ✔ |
| **SO confirm blocked before verified** | `action_confirm()` override raises ✔ |
| Self-verification ban | `_allow_self_verification()` config ✔ |
| Scope | `verification_needed` — only orders entered by configured roles (added 2026-08-08) |

**Decision:** keep the gate on `sale.order`. Moving it to `lab.case` would duplicate a
working, tested control and break the existing `action_confirm` block. Epic C therefore
reduces to the genuinely missing parts:

- **C-2 doctor calls** — entirely new
- **C-3 pending-information bucket** — entirely new (`on_hold`)
- **C-1 gaps** — `days_waiting` column, a *To Verify* menu/queue

---

## Revised epic effort

| Epic | Verdict | Notes |
|---|---|---|
| A — quick entry, purpose/outcome | mostly new | `lab.case` duplicate control already exists as a **non-blocking warning** (`duplicate_warning` + `duplicate_ack`) — spec A-1.1 is already satisfied; do not "relax a constraint" that isn't there |
| B — delivery | greenfield | biggest single build |
| C — verification / calls / hold | **~60% exists** | extend `lab_order_control` |
| D — priority, tiles, tracking | part exists | tiles = XML only; tracking wizard reuses `lab_portal` timeline |
| E — rework | greenfield (keep `is_rework`) | |
| F — WhatsApp multi-number | **~85% exists** | add `user_ids` + resolution order only |
| G — incentives, doctor discounts | greenfield | |

## Module placement (spec §13.6 adjusted to the real split)

| Epic | Module |
|---|---|
| A | `lab_fieldwork` (visit + case live here) |
| B | new `lab_delivery` |
| C | `lab_order_control` |
| D-1 priority | `lab_order_control` + `lab_fieldwork` |
| D-2/3 tiles | the module owning each view (XML only) |
| D-4 tracking | new `lab_track` (or `lab_order_control`) |
| E | new `lab_rework` |
| F | `epg_whatsapp` (engine) — user mapping only |
| G | new `lab_incentive` |

## Deviations from the spec, for client confirmation
1. Verification stays on `sale.order`, not `lab.case` (working control already there).
2. Spec's proposed group names replaced by the existing ones in D5.
3. A-1.1 "remove the uniqueness constraint" — no such constraint exists; the existing
   duplicate handling is already a warning, as the spec wants.
4. `list_view_dashboard` is present and v19-native, so no fallback dashboard is needed.
