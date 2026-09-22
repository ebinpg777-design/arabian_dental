# Phase 2 — Changelog

> **Inherited document.** Written for the Ortho Creation project this suite was forked from (2026-09-22); the wording has been rebranded but the figures, database names and screenshots are that project's. Use it for how the modules work, not as a record of Arabian Dental Lab.

## M0 — Phase 0 discovery (2026-08-09)
`docs/PH2_MAPPING.md` written. Ten discovery questions answered. Headline findings:
the verification gate already exists on `sale.order` (not the case), `epg_whatsapp` is
already a multi-account Meta connector, `list_view_dashboard` is present and v19-native
(pure-XML tiles, no fallback needed), and the case duplicate control is already the
non-blocking warning the spec asks for. Epics B, E and G are greenfield.

## M1 — Epic C: doctor calls + pending information (2026-08-09)
Module: `lab_order_control`.

**New**
- `lab.doctor.call.log` — one record per attempt to reach the doctor (response,
  who called, remarks, try-again time). Logs to the order's chatter and schedules the
  next call as an activity.
- `lab.hold.wizard` — putting an order on hold asks for a reason *and* a follow-up
  date, because a hold without one is how an order gets forgotten.
- `verification_state` gains **`on_hold` (Pending Information)**.
- Doctor-call fields on `sale.order`: `call_doctor_required/reason/note/resolved`,
  `call_pending` (stored, drives banner + filters + gate), `doctor_call_count`.
- Hold fields: `hold_reason`, `hold_note`, `hold_date`, `next_followup_date`,
  `days_on_hold` (stored), `hold_warned`; plus `days_waiting` for the verify queue.
- Cron **"Lab: Pending-information follow-up"** (daily 02:30): ages holds, raises the
  day's follow-up activities, warns managers **once** per hold past the threshold.
- Settings: `doctor_call_attempts_before_hold` (3), `hold_warning_days` (5).
- KPI tiles (Epic D-2, via `list_view_dashboard`) on the verification queue: To Verify /
  Pending Information / Doctor Call Pending / Sent Back.
- Menus: *Sales ▸ Verification ▸ Pending Information*, *… ▸ Doctor Calls*.

**Behaviour**
- `action_verify()` now refuses an order with an unresolved doctor query, or one on hold
  (resume it first — so the hold is closed deliberately, not bypassed).
- Resolving a query needs evidence: an answered call or a written outcome.
- N unanswered attempts (default 3) park the order in Pending Information by themselves
  and tell a manager. Answered calls never count towards it.
- Resuming returns the order to **To Verify**, never straight to verified.

**Deviations from spec §5/§8**
- The gate stays on `sale.order`; `lab.case` is untouched. Duplicating it onto the case
  would fork a working, tested control and break the existing `action_confirm` block.
- No `case_id` on the call log: `lab.case` lives in `lab_fieldwork`, which this
  module deliberately does not depend on. The slip is reachable via
  `lab.case.sale_order_id`.
- Existing groups reused (`group_lab_order_checker` = the spec's "Registration").

**Tests** `tests/test_ph2_epic_c.py` — 11 cases; suite total 27, all green.

## Symlink removal (2026-08-09)
The six modules that lived in `/home/ebinpg/odoo/odoo_19/custom/odoo` and were symlinked
into `custom/arabian_dental` are now **real directories here**: `lab_ceo_dashboard`,
`lab_fieldwork`, `lab_finance_ops`, `lab_order_control`, `lab_whatsapp`,
`lab_workcenter_scan`. (`lab_access_control` was already a real directory.)

They were **untracked in git**, so the source was the only copy — backed up to
`lab-modules-before-move.tar.gz` before moving. No instance config referenced the old
path, so nothing else broke. 356 tests green after the move.

## M2 (part) — Epic D-1: urgency (2026-08-09)

**Priority extended, not renumbered.** The lab already had `low / normal / urgent` on
both `sale.order` and `lab.case`; the missing level was **`emergency`**, so that value
was added. The spec's `0/1/2` keys were deliberately NOT adopted: several modules compare
against `'urgent'` by name (`sale_custom.compute_emergency`, the production flow board,
the case line defaults) and every existing order and case would have needed migrating —
breakage for no behavioural gain.

- `is_urgent_open` (stored) on `sale.order` and `lab.case` — true only while the work
  is genuinely outstanding, so the urgent list cannot silt up with finished jobs.
- EMERGENCY / URGENT `web_ribbon`s, a danger banner for emergencies, `decoration-danger`
  + `decoration-bf` and a `priority` widget column on the order list.
- Search filters *Urgent*, *Emergency*, *Urgent — In Progress*; menu **Sales ▸ Urgent Works**.
- Setting `emergency` raises a confirm warning — everything downstream reacts to it, and
  a priority set by accident teaches the floor to ignore the colour.

**Epic C gap closed at the same time.** `lab.case.needs_doctor_call` previously reached
the order only as prose inside `instruction`, which nothing reads programmatically — so a
query raised at the clinic never lit the banner or blocked verification. The case now
passes `call_doctor_required` / `call_doctor_note` as real fields.

**Tests** `tests/test_ph2_epic_d1.py` — 6 cases; module suite 33, all green.

### Still to do in M2
Epic A-1 (quick case entry wizard) and A-2 (visit purpose/outcome → m2m masters with the
"Delivery" value, plus the legacy-selection migration).

---

## D-4 — Track a Work (R18)

New module **`lab_track`**. One search box, one screen, no new application: the answer
to "where is my patient's case" without walking six menus while a doctor waits.

**Model** `lab.track` (AbstractModel, no table).

- `search_work(ref)` accepts whatever reference the caller happens to be holding — sale
  order, delivery note, rework slip, invoice number, or a patient name. Each resolves to
  the same case. An exact match goes straight through; a partial or a shared patient name
  returns up to 12 candidates rather than guessing, because picking the wrong case for
  somebody reading a number down the phone is worse than one more tap.
- `get_work(order_id)` returns the whole picture in **one round trip** — header, journey
  timeline, every manufacturing order with its work orders station by station, deliveries
  with outcome and lateness, invoices with what is still owed, reworks, doctor calls. The
  screen issues no follow-up requests, so nothing arrives late on a bad connection.
- `recent_works()` fills the cold screen with what the lab is working on now; a bare
  search box teaches people the screen only works if you already know the number.

**Access.** Child records are read elevated so a manager gets the *answer* without also
being handed Manufacturing and Inventory rights they have no other use for. The order
itself is **not** elevated: `get_work` takes a bare id from the browser, so it calls
`check_access('read')` first — otherwise a field executive could walk the id range and
read every clinic's patients and prices. Note that a CEO expected to track *any* case
needs Sales / Administrator; Sales / User is scoped to their own documents by Odoo's own
record rule, and this screen deliberately does not override that.

**UI** OWL client action `lab_track`, hero card (patient, stage, EMERGENCY/URGENT/REWORK
chips, progress, value) over an all-open section grid. The station rail scrolls sideways
rather than wrapping — the order of the benches *is* the information. Every section is
expanded by default; clicks are only ever for drilling into a record.

**Menus** Management ▸ Track a Work (seq 1) and Sales ▸ Orders ▸ Track a Work (seq 24).
No new root menu.

**Tests** `lab_track/tests/test_track.py` — 17 cases, all green. Full custom-module
regression: **404 tests, 0 failed, 0 errors**.

---

## Deliveries on My Day, and the visit ↔ delivery link

**The link.** `lab.delivery.visit_id` → `lab.visit.delivery_ids` / `delivery_count`.
Until now the two records only met by coincidence of clinic and date, which is not
something a report can be built on. The stamp is written at the moment of handover
(`_attach_visit`, called from the Delivered wizard) because that is the only point at
which it is knowable rather than guessed — and it only ever attaches the *carrier's own*
visit to that clinic, today. Surfaced as a stat button and Deliveries page on the visit,
a stat button and field on the delivery, and a *Visit* group-by / *Handed over on a
visit* filter.

**Creation from My Day.** New wizard `lab.delivery.new.wizard`, reached from a *Hand
over* button in the quick-action bar, a *Hand over* button on each open visit card, and
*Hand Over Work* in the visit's own header. It offers confirmed work for that clinic that
nobody is already carrying, as checkboxes — one tap each, answered standing up. Raised
deliveries start `assigned` to whoever raised them, since making the person about to
carry the box press *Assign* next is theatre. Field-work executives gained `create` on
`lab.delivery` (not `unlink` — a delivery is cancelled, never erased).

**The bag on the home screen.** `get_day` now also returns `deliveries` and
`delivery_summary`, and each visit card carries `deliveries_pending` for its clinic.
Rendered as an *In your bag* section above the visits with Navigate / Call, an
emergency and late edge, and one button whose meaning follows the state (*Set off* →
*Delivered*). Added from `lab_delivery` rather than `lab_fieldwork` so My Day keeps
working for a lab that never installs deliveries.

## Save / Discard on a phone

Reported from the field: the stock control is a pair of 16px unlabelled icons in the
breadcrumb — the one corner of a 6" screen a hand holding the phone cannot reach — and
executives were losing entries to it.

`lab_fieldwork.MobileSaveBar` extends `web.FormStatusIndicator` to render a **second
presentation of the same control**: a fixed bottom bar, 48px targets, labelled in words,
Save weighted two-to-one over Discard, disabled with *Check the form* while the record is
invalid, and respecting `env(safe-area-inset-bottom)`. It inherits the existing
component's `save` / `discard` / `indicatorMode`, so there is no second source of truth
to drift. Below 992px only; desktop is untouched. `:has()` reserves the space so a clean
form does not end in a strip of dead screen.

**Tests** `lab_delivery/tests/test_my_day_delivery.py` — 20 cases including two that
drive a real headless browser. Those are not decoration: OWL template inheritance is
resolved in the *browser*, so a mistyped xpath passes every server-side check and then
white-screens the whole web client. Both bugs found this way:

- the delivery cards reuse `.o_fw_meta` and are inserted *above* the visit list, so a
  class-only xpath landed in the wrong `t-foreach` and blew up on `v.deliveries_pending`
  (fixed by anchoring on `//div[@t-foreach='state.data.visits']`);
- the visit notebook is hidden while a visit is `planned`, so the test had no field to
  dirty.

Full custom-module regression: **416 tests, 0 failed, 0 errors**.

---

## Courier tracking

`courier_name` was free text and `courier_awb` was a number that led nowhere: "DTDC",
"Dtdc" and "dtdc courier" were three couriers, and a consignment number you cannot open
is a consignment number you have to ring somebody about.

**`lab.courier`** — the couriers the lab actually uses, each with its tracking page as
a template (`{awb}` marks where the number goes), a phone number and usual transit days.
Validated on save: a URL with nowhere to put the number, or one that is not a URL, is
refused rather than silently producing dead links. Seeded with DTDC, Blue Dart,
Professional, Delhivery and India Post under `noupdate="1"` — couriers move these pages
without warning, and a module upgrade must not put a corrected one back.

**`lab.courier.event`** — checkpoints. The lab has no API into any courier, so this is
not a scraped feed: it is what the office learns and writes down. `record_event()` on the
delivery is the single door in, so a future integration writes where a person types.

**On the delivery** — `courier_id`, `courier_tracking_url` (computed, URL-quoted),
`courier_status` and `courier_last_event_at` (stored, rolled up from the checkpoints),
`courier_expected_date`, `courier_is_stale`. The parcel's state is simply its latest
checkpoint **by time, not by id**: the office types this morning's scan and then goes
back to fill in yesterday's, and a parcel must not travel backwards because of the order
somebody entered things.

**Two automatic consequences.** A `delivered` checkpoint closes the delivery outright —
asking the office to also walk the handover wizard is asking them to say the same thing
twice, and the second saying is the one that gets skipped. An `exception` or `returned`
checkpoint raises an activity for the executive instead, because those need a person.

**The stalled watch** (`_cron_courier_stale_check`, daily) is deliberately *not* the
delay engine. That one asks "is it late"; this one asks "has anything happened at all".
A parcel can be silent for four days and not yet be late, and that is exactly when
ringing the courier still works. Chased once, not nightly — a channel that repeats gets
muted. Configurable under Settings (default 3 days).

**Where it shows.** Delivery form gets a Courier block, a Tracking page and *Send by
Courier* / *Tracking Update* buttons; filters for *Sent by courier*, *not moving* and
*courier exception*; group-by courier and consignment status. Track a Work shows the
courier, a clickable consignment number, status and last scan. My Day swaps Navigate for
**Track** on a couriered card — nobody drives to a parcel. Dispatch optionally posts the
number and link to the doctor's order thread (HTML-escaped; the courier name and URL are
admin-entered and land in a chatter that renders HTML).

`courier_name` is kept, hidden and labelled legacy, so nothing that was typed is lost.

**Tests** `lab_delivery/tests/test_courier.py` — 22 cases. Full custom-module
regression: **438 tests, 0 failed, 0 errors**.

### Dispatch button on the sale order

The lab delivery smart button was `fa-truck` labelled *Deliveries*, sitting in the same
button box as `sale_stock`'s `fa-truck` labelled *Delivery*. Two trucks a word apart is a
button nobody can aim at. Now **`fa-cube` / Dispatch** — `fa-cube` is the parcel
everywhere else in the module (My Day, Track a Work, the visit form), so the icon means
one thing throughout. Two tests pin that this button never again shares an icon or a
label with anything else in that box.

---

## Full functional + performance pass

**Functional.** A 47-check end-to-end run against real records covering every feature —
visit, quick entry, origin-based verification, doctor calls and hold, handover, delay
engine, near-place outcome, courier dispatch/checkpoints/stalled watch, rework,
incentive, Digital Rx, Track a Work — all passing, rolled back afterwards. Script kept
at `scratchpad/e2e_full.py`.

### Two real bugs, both found only by running as a real user

Every earlier test ran as **admin**, which is why both survived 440 green tests.

**1. My Day crashed for field executives.** `get_day` reached through a delivery to
`sale_order_id.name`. Sales/User is "Own Documents Only" and the office raises the
orders, so the executive could not read the order behind a parcel they were carrying —
`AccessError`, whole home screen dead. Child records are now read elevated (the search
is already scoped to that executive, so nothing extra becomes visible).

The first test written for this *passed while production crashed*: the order's name was
still warm in the ORM cache from creation, so the read never reached the ACL. The test
now flushes and invalidates first — a cold read, like every real request. Verified by
reverting the fix and watching it fail.

**2. Handover did not work for the only people who use it.** `lab.delivery.new.wizard`
listed candidate orders under the user's own rights, so an executive saw an empty list
for every clinic, and ticking one raised `AccessError` on the m2m write. Fixed with a
**read-only record rule**: field executives may read orders in `sale`/`done`. Draft
quotations and the pricing behind them stay out of sight, and writing still needs their
own name on the order. Both limits are now tests.

### Performance

Measured with 400 orders / 308 deliveries / 301 checkpoints seeded in a savepoint, cold
cache, counting SQL round trips as well as wall time — the query count is the number
that grows with the data.

| Path | Time | Queries |
|---|---|---|
| My Day `get_day()` | 53 ms | 43 |
| Track `search_work()` | 15 ms | 18 |
| Track `get_work()` (heavy case) | 15 ms | 17 |
| Dispatch board, 80 rows | 8 ms | 5 |
| Delivery list incl. courier columns | 5 ms | 4 |
| Handover candidates | 7 ms | 7 |
| Courier stale cron, 100 consignments | 5 ms | 3 |
| Sale order list, 80 rows | 19 ms | 8 |

Every screen is flat in the data volume. The one problem was the **delay cron**: 10.2 s
and 7,229 queries for 300 parcels going late at once (~24 each), in a single
transaction. Steady state was always cheap (2 queries) — the cost is entirely the moment
a parcel first goes late, and after any downtime they cross the line in a crowd.

Fixed by batching what was per-record: activities scheduled a whole executive at a time,
chatter written with `_message_log_batch`, the "no longer late" reset done as one write,
grace read twice instead of once per row, and managers sent **one digest** rather than
forty notifications nobody reads. Now 4.8 s / 3,520 queries. The run is also **bounded
to 400 parcels** with an immediate re-trigger for the remainder, so it can never sit past
the cron worker's time limit and be killed having notified no one.

Full regression: **444 tests, 0 failed, 0 errors**.
