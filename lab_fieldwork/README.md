# Lab Field Work

A replacement for `lab_field_force`, rebuilt around one idea: **ordinary people use
this**, so the module must be small enough to hold in your head.

## Why it is smaller

| | old | new |
|---|---|---|
| models | 13 | **4** |
| menus an executive sees | 23 | **4** |

The old module asked an executive to create visits, tasks, travel logs and targets as
separate things. Their actual day is one loop — go to a clinic, do something, leave — so
that loop is the module.

## The four things

* **Beat** — a round of clinics and the weekday it is worked. A manager describes it
  once; *Plan This Week* generates the visits. An executive never types a record to say
  they intend to go somewhere, which is the least valuable data entry in any field
  system and the first thing that stops happening.
* **Visit** — one clinic call, from planned to closed. Orders, money, location and notes
  all live here, so it is the only form to understand.
* **Trip** — the day's travel. Two odometer readings; distance and claim computed. One
  per person per day, enforced, so there is never a question which trip today's
  kilometres belong to.
* **Target** — a month's number. Achievement is **measured, never typed**: the moment
  someone can key in what they achieved, a target stops being a measurement.

## My Day

The executive's whole application: today's clinics as cards, **one button each**. What
the button does depends on where the visit is — *I'm Here*, then *Finish* — so there is
never a choice to make. One RPC fills the screen, because it is opened on a phone at a
clinic door on a poor connection.

## Roles

Each role is a different application, not the same one with items greyed out.

| | menus | can |
|---|---|---|
| **Executive** | 4 | own visits and trips; reads own beat and target |
| **Manager** | 10 | whole team, approves trips, sets targets, plans beats |
| **Administrator** | 12 | the policy: visit radius, mileage rates, photo rule |

Administrator is deliberately separate from Manager — *the person a rule constrains
should not be the person who can relax it*. Holding Odoo's Settings access alone does
not open the app at all.

## Control

* **Read-only by state is enforced in `write()`**, not the form. A closed visit refuses
  edits from a list, an import, a server action or RPC. The form banner explains the
  lock *before* the user types, and both come from the same `_lock_states` so they
  cannot drift.
* **Field visibility** by group — an executive sees the mileage rate they are paid but
  cannot set it.
* **Menu visibility** by group, repeated on every child: a parent's groups do not
  cascade in Odoo, and an ungated child is the commonest way a menu tree leaks.
* **Location is advisory.** A check-in far from the clinic is recorded and must be
  explained before closing — never blocked. Blocking would only mean the visit goes
  unrecorded, which is worse than an explained one. A missing GPS fix is reported as
  *no location*, never as *wrong place*: they are different failures and conflating them
  punishes the honest case.

## Three roles, three applications — not three levels

The roles are **parallel groups, not a stack**. Manager does not imply Executive, so
"My Day", "My Visits", "My Travel", "My Cash" and "My Target" never appear on a
manager's screen. A manager does not work a beat, and a menu inviting them to record
their own field work is worse than clutter.

| | Executive (5, flat) | Manager (13, grouped) | Administrator (16) |
|---|---|---|---|
| Own work | My Day · My Visits · My Travel · My Cash · My Target | — | — |
| | | Control Tower | ✓ |
| Operations | | Visits · Travel Approvals · Field Cash | ✓ |
| Planning | | Beats · Targets · Clinics | ✓ |
| Analysis | | Executive Performance · Team Performance · Clinic Coverage | ✓ |
| Configuration | | — | Settings · Field Roles |

The executive's tree is deliberately **flat** — five things used every hour should be
one tap. The manager's is **grouped**, because thirteen items used a few times a day are
easier to find under three headings than in one list.

Dropping `implied_ids` from the XML is not enough and fails quietly: Odoo never resets a
field a data file stops mentioning, so the stored link survives, every existing manager
keeps the Executive group it granted, and every new manager gets it again on create. The
security file reads as though the roles are separate while the running system still
stacks them. `migrations/19.0.2.0.0/post-migrate.py` breaks the stored link and takes the
inherited group off the people who only ever had it that way.

## Control Tower (manager and admin)

Every other manager screen answers a question about the past. This one answers the
question a manager actually asks during the day — *is everyone out, and is anything going
wrong* — which is worthless the moment it is a day old. It refreshes itself and shows
only what can still be acted on today.

One row per person, and **nothing on it is typed**: at a clinic / on the road / finished /
not started is derived from the visits and the trip. Each row carries how far through
their own plan they are, what they have collected, and how much of that is **still only in
a pocket** — the number a manager most needs and no report shows.

The alert list is deliberately short. A dashboard listing twenty warnings trains its
reader to dismiss all twenty, so anything merely interesting belongs in a report; each
entry here is something a person has to decide about today.

Anyone with a visit today appears whether or not they still hold the role. Without that,
moving somebody to a different role at lunchtime deletes their morning from the screen
while the alerts it caused remain — and the totals, which are summed from these rows,
would contradict the alerts sitting directly above them.

## Registering a case

An executive gets a **case slip** (`lab.case`), not a sale order form. A sale order
carries pricing, taxes, delivery, invoicing policy and a dozen fields that are somebody
else's job, and putting that in front of a person standing at a clinic counter with the
doctor waiting is how you get orders that have to be corrected afterwards. The slip is
only what the doctor actually hands over:

* **the patient** — name, age, gender;
* **the work** — one line per appliance, each with the arch (U / L / UL, per
  `sale_custom`), quantity and colour;
* **what came in the bag** — screw, bite, bands, wires, teeth, face bow;
* **the registration details** — impression type and tray, scanned, wax bite, send
  through, priority, modifications, special instructions, a photo.

The form's order *is* the order of the conversation at the counter.

On the visit, **Cases** means the slips and **Orders** means the sale orders — they are
separate buttons and separate tabs. Pointing "Cases" at sale orders showed an executive
zero for the entire time they were actually registering cases, since no order exists
until check-out. The visit also states, while it is open, how many slips are waiting to
become orders.

**Orders are created at Finish Visit, not on save.** An executive fills a slip in while
the doctor is still talking and edits it twice before leaving; creating the order on save
would push a half-entered case into verification and turn a correction into somebody
else's problem instead of a tap on the same screen. Check-out is the moment the executive
says they are finished, so it is the only honest point to commit. Each slip becomes a
`sale.order` with `verification_state = to_verify`, submitted through
`lab_order_control`'s own entry point so its maker-checker rules apply.

It is **idempotent**: a slip that already made an order returns it. Finish Visit is a
button people press twice on a bad connection, and preventing duplicate orders is the
entire point.

### An ordered case is history

Once a slip has produced an order it **cannot be deleted by anyone** — not by the
executive, not by a manager, not by an administrator. The order would still be sitting in
verification with nothing to say who registered it, at which visit, from which doctor;
only the explanation for it would be gone. The way to undo a registered case is to cancel
its order, which leaves a record of the decision.

Not a permission level, an integrity rule: seniority does not make an orphaned order
acceptable, so the manager's edit-lock bypass deliberately does not apply here.

Three routes had to be closed, because the obvious one is not the only one:

* the slip itself — the lock mixin guards *editing*, not deletion, so `unlink()` needed
  its own guard;
* **the visit** — `lab.case.visit_id` is `ondelete='cascade'`, which is a Postgres
  foreign key, so deleting the visit would have removed the slips without
  `lab.case.unlink()` ever running;
* **the lines** — the slip was protected while its lines were not, so a line could be
  deleted straight out of a registered case and the slip would no longer describe the
  order it produced.

A slip that has **not** been ordered can still be deleted, by design — that is how a
duplicate is resolved.

### Duplicate cases

Matched on **clinic + patient + a configurable window** (default 7 days), and the clinic
means the *commercial* partner — a practice with three branch contacts is still one
doctor, and a case entered against a different contact is exactly the duplicate worth
catching.

The patient name is normalised into `patient_key`, stored and indexed on both the slip
and the order. It survives how people actually write names at a counter: honorifics
(`Mr.`, `Baby`, `Dr`), punctuation, double spaces, capitalisation, a stray initial, and
**word order** — `Raj Anu` and `Anu Raj` collide, because at one clinic in one week that
is the same child far more often than it is two. Deliberately no fuzzier than that:
anything looser starts merging siblings, and `Anu Raj` vs `Arun Raj` must stay apart.

Indexed on the order rather than computed at search time because the check runs while the
doctor is waiting, and a duplicate warning that is slow is one nobody waits for.

When a match is found the slip shows a warning naming **the earlier spelling** — the one
the executive can recognise, not their own keystrokes read back — with a link to the
earlier order. Finish Visit then **blocks** until they either delete the slip or tick
*Different case*. Never silently dropped and never silently duplicated: the person at the
counter is the only one who can tell which it is, and they are still there. The visit
stays open so it can be fixed on the spot.

## Attendance comes first

An executive marks attendance on My Day before anything else works. Checking in at a
clinic and starting the day's travel both refuse until the day has been started, and the
refusal says what is missing and how to fix it — *"Mark your attendance before visiting a
clinic. Open My Day and press Start my day."* An access error on a phone at a clinic
door, with a doctor waiting, is how somebody learns to stop using the app, not how they
learn to mark attendance.

It is recorded on **Odoo's own `hr.attendance`**, never a parallel model. Payroll, leave,
overtime and every HR report already read that table; a second record of when somebody
started work would be a second answer to the same question, and the two would disagree
the first time anyone corrected one of them. The GPS fix goes on with it, so a day
started in the field is located exactly as a clinic call is.

One card, one button, both directions — the state decides what happens, so there is never
a wrong button to press. On duty it shows *"Since 09:12 · 2h 40m today"* with a live
pulse; off duty it says visits and travel stay locked until you start. The card is the
only thing on the screen that changes colour when it needs attention, and it says so in
words as well.

Switchable off in Settings for a lab that does not run attendance.

## Built for a phone held one-handed

The executive's half of this module is used standing at a clinic door, so the design
constraint throughout is: **never make the keyboard appear if a tap will do.** The
on-screen keyboard covers half the display, moves the field being edited, and is the
single biggest reason field staff quietly stop filling records in properly.

Four widgets exist for that reason alone:

| Widget | Replaces | Why |
|---|---|---|
| `fw_choice` | a dropdown | Purpose, outcome, payment mode and vehicle become chips big enough to hit while walking. Tapping the current choice clears it, so a mis-tap is undone with the same finger. |
| `fw_amount` | a number field | Cash at a clinic arrives in round numbers, so `+100 / +500 / +1000 / +5000` covers almost every real amount in four taps. The figure is readable at arm's length; typing is the fallback, and gets a number pad rather than a full keyboard. |
| `fw_camera` | the image widget | `capture="environment"` makes the phone open the **rear camera directly** instead of the gallery — one tap instead of four — with a big target, preview and retake. Used for the visit proof photo and both odometer photos. |
| `fw_contact_bar` | nothing | **Navigate, Call, WhatsApp.** These are phone jobs, not form jobs, and they were previously not in the app at all. |
| `fw_arch` | a U/L dropdown | The arches themselves, as two toggles that combine. "U", "L" and "UL" is how the data is stored but not how anyone thinks — a technician asks "is the upper in this job". It also makes the impossible fourth state, neither, unreachable rather than merely discouraged. |
| `fw_stepper` | a number field | Quantity as − / + at 44 px, **and a typeable field**. Stepping alone is fine for a quantity that is almost always 1 or 2 and absurd for an age: reaching 34 would be thirty-four taps. The buttons are for the small adjustment, the field for everything else, and it still asks the phone for a numeric pad. |
| `fw_toggles` | six checkboxes | What came in the bag, as six chips. Six checkboxes is six precise taps; six chips is six targets the width of a thumb. |

`map_url`, `call_number` and `whatsapp_number` are computed **on the server**, not
assembled in the browser: a wrong number or a broken map link means a person standing in
the street, and this way it is testable. Two consequences worth knowing:

* A ten-digit Indian number gets its country code filled in from the partner's country,
  then the company's. `wa.me` silently opens an *empty chat* for a number without one
  rather than reporting an error, so this cannot be left to chance.
* An unpinned clinic still gets directions from its address. No pin is not the same as no
  directions, and a missing map button is exactly the moment somebody is lost.

The same three actions appear on each My Day card, so the common case never needs the
record opened at all. On a screen under 768 px the contact bar becomes **sticky** — it
stays under the thumb however far the form is scrolled — chips reflow to two columns, and
every control keeps a 44 px minimum target.

## Field Health (administrator only)

A separate question from the manager's, which is why it is a separate screen. The
Control Tower asks *what is happening today*; this asks **are the rules I set actually
right, and is the setup behind them sound** — a question normally answered by nobody,
because the evidence for it is spread across records no one reads together.

**Is the visit radius right?** Everywhere else in the module a distant check-in reads as
a person's failure. Here the same data is read the other way round, as evidence about
the rule. A histogram of where people actually stand, the median and 90th percentile,
and a verdict in words:

* a quarter of check-ins outside the radius is not a quarter of the field force lying —
  it is 300 m being wrong for clinics inside hospitals and complexes, where the fix lands
  in the car park;
* almost nothing outside it, with the 90th percentile at half the radius, means the check
  is not really being made.

It recommends a round number one tap away, and **never applies it automatically**: a
system that silently retunes its own policy is one nobody can be held to. Below twenty
check-ins it refuses to judge at all, because three check-ins is an anecdote.

**Setup gaps.** Every one of these switches a feature off without producing an error —
an unpinned clinic reports "clinic not pinned" for ever, a beat with no clinics generates
nothing, an executive with no float has nowhere to put the cash they collect. Each gap
carries the action that closes it; a list of problems with no way to act on them is a
list nobody opens twice.

**Return on the field force.** Travel is the only cost the lab pays per visit and case
value is the only thing the visit is for, but nothing ever put them on one screen — so
the ratio, the number that says whether the operation is worth running, was invisible to
the person who signs off the rates. Guarded against division by zero: a lab that
reimburses nothing is not infinitely efficient.

**Six months, three shapes** — inline SVG sparklines, no charting library, with the
direction stated in words because a shape alone is not a reading.

## Worth a stop (executive)

Coverage is only worth measuring if somebody acts on it, and the person who can act is
standing outside a clinic — not the manager reading a monthly report. So My Day offers
the quiet clinics on that executive's **own beats**, ordered by **value at stake rather
than days elapsed**: a clinic that never ordered and one that ordered heavily until it
went quiet are both "overdue", and only one is worth the detour. One tap adds it to
today; tapping twice on a slow connection does not make two visits.

## Settings the administrator can actually open

`res.config.settings` is gated on `base.group_system`, so a Field Work Administrator
could not open it — the Configuration menu simply vanished for the one role meant to own
the configuration. Granting that role access to `res.config.settings` would have fixed
the symptom and opened *every other app's* settings at the same time, so Field Work has
its own page (`lab.fieldwork.settings`) writing exactly nine parameters and nothing
else. The equivalent section still exists under General Settings for a system
administrator; both write the same `ir.config_parameter` keys and cannot drift.

Changing a coverage band rebuilds the `lab.coverage` SQL view **and invalidates its
cache** — the rebuild changes what a row means without changing which rows exist, so
anything already read in that request would otherwise keep its old status and the
setting would look saved and ignored.

## Analysis (manager and admin)

* **Field Performance** — one row per executive per month with effort, output and
  discipline together. Separate reports answer these separately; the interesting cases
  only appear in the combination — many visits and few cases is a coaching problem, many
  cases and poor location compliance is a reporting-integrity problem, high travel with
  few visits is neither. Three saved filters name those shapes. Pivot and graph included.
* **Team Performance** — the same data pivoted by sales team and month.
* **Clinic Coverage** — clinics banded by how long they have gone unvisited, with a
  *Rescue List* filter: still ordering, ordering less, and nobody has been to see.

## Cash the executive is carrying

A cash collection on a visit goes into that executive's petty cash float with one button.
The float hangs off the partner behind their **user** — a float is carried and spent by
somebody who logs in.

Collected cash counts as **allocated** to the holder (they are accountable for it exactly
as for an advance), and is broken out as *Collected in Field* so the accountant can answer
the only question that matters when the cash is counted: how much of this is company money
we advanced, and how much is customer money still to be banked. Every movement points back
at the clinic and the visit it came from.

Only cash. A cheque or online payment reaches the bank directly and is reconciled there —
booking it here would count the same money twice, and the error says so.

### Into the float by itself, back to the office by code (2026-09-18)

Nobody on the live database had a float, so the button refused everyone and cash sat in
pockets with no record. Now:

* **The float opens itself** the first time an executive takes cash — a *custody* float
  in their name, with no advance and no journal. Setting *Open floats automatically* off
  hands that back to accounts.
* **Cash goes in at check-out**, without a tap. If it cannot (a float in the wrong company),
  the reason is written on the visit and the button is still there.
* **One number.** *Cash with you* on My Day, the row on the Ops Desk and the reminders all
  read `lab.cash.handover.cash_position()`: pocket + float − counted back by the office.
  Two "cash in hand" figures used to sit on My Day, and neither could go down.
* **Hand over cash** on My Day raises a *handover* (`lab.cash.handover`): amount
  pre-filled with everything they hold, an optional note-count sheet that fills the amount
  in, and a six-letter **code** (no O/0, I/1) with a QR that opens the slip. The envelope is
  still theirs — *waiting to be counted* — until the office confirms.
* **The office counts against the code** — *Receive Cash by Code* on the Ops menu and in
  the Petty Cash app (operational managers and petty cash officers), or the button on the
  slip. Confirming posts a *Handed to Office* movement for the **counted** amount, so the
  float, the card and the desk all drop together. A count that differs needs a reason; a
  shortfall stays on the executive's float and raises a to-do in their name. Received slips
  lock. A receipt PDF carries the code, both counts and two signature lines.
* **Chasing.** Cash older than *Hand over within (days)* or at/above *Hand over above*
  turns the card red, marks the person's row on the Ops Desk, and a daily cron raises one
  to-do for the executive.

None of this touches the ledger: a field collection enters the float with no journal entry
and a handover leaves it the same way. Accounts still books the receipt against the doctor's
account when the money lands.

A trap fixed on the way: Odoo 19 hands a computed field's `search` method `('in',
OrderedSet([True]))` for `= True` and `('not in', …)` for `= False` — never `'='`. The old
`_search_cash_banked` read `in` as a negative, so **both** filters returned every visit, and
banked cash still counted as in the pocket for ever.

## Migration from lab_field_force

The old module is still installed and `lab_ceo_dashboard` still depends on it. Nothing
has been removed. To switch over: point the CEO dashboard's field-force queries at
`lab.visit`, move any live data, then uninstall. Do that deliberately — this module
does not attempt it for you.
