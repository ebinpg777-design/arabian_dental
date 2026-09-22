# Field Work — Guide for Field Executives

A user document for the **Field Executive** role of `lab_fieldwork`. It covers only
what an executive does: start the day, work the round, write case slips, record what
happened, close the day. Nothing about the manager or administrator desks.

## What is here

| File | What it is |
|---|---|
| `Field_Executive_Guide.pdf` | The guide, English. 36 pages, A4, ready to print or send. |
| `Field_Executive_Guide.html` | The same document as a web page. Edit this and re-make the PDF. |
| `Field_Executive_Guide_Malayalam.pdf` | The Malayalam edition. 36 pages. |
| `Field_Executive_Guide_Malayalam.html` | Its source; shares `images/` with the English one. |
| `Field_Executive_Guide.mp4` | 3:42 walkthrough, 1920×1080, subtitles burned in. No audio. |
| `Field_Executive_Guide.srt` | The same subtitles as a sidecar file, for players that show them. |
| `images/` | The 33 annotated screenshots both guides are built from. |

## The Malayalam edition

Same structure, same screenshots, same page order. Everything a field executive
**reads on the screen stays in English** — menu names, button labels, field names and
the error messages in the troubleshooting table are reproduced verbatim, because the
application itself is in English and a translated button name would send them looking
for something that is not there. Only the explanation is Malayalam.

Renders with Noto Sans Malayalam, which is already installed here; Chrome shapes it
through HarfBuzz, so conjuncts and chillu letters come out correctly in the PDF. The
CSS puts a Latin face first in the stack and lets Malayalam fall through, so English
terms inside a Malayalam sentence keep their own shapes.

> **Have a Malayalam speaker read it before it goes to staff.** The translation is
> mine, not a professional one. The technical content is accurate and the terms are
> deliberately the ones field staff already say out loud (ടാപ്പ്, സേവ്, ഫോട്ടോ)
> rather than formal equivalents, but wording and tone deserve a native eye.

## How the screenshots were made

They are **not mock-ups**, and since 2026-09-12 they are **not demo data either**.

`arabian_dental_live_280826` was dumped and restored into a throwaway database, `arabian_dental_shots`,
whose filestore is a hardlink farm of the original (`cp -al`, instant, no extra disk).
That copy was then neutralized — `odoo-bin neutralize`, plus the senders neutralize does
not know about: `epg_whatsapp` forced to simulation and off, the last active cron
disabled, outgoing mail servers deleted. Nothing in the capture can reach the outside
world, and **nothing is written to the staging database itself** — which matters,
because the capture performs a real day: attendance, odometer, check-in, a case slip
that becomes an order, a payment, a cash movement.

A real executive (`abhijith`, route EKM1, 112 clinics) was given a password **on the
copy only** and a round of five clinics from their own route. A Playwright script then
signed in as them on a 430px phone viewport and worked the day, photographing each
screen. Odoo's red "Database neutralized" strip and the website's "Neutralized" ribbon
are hidden in the browser for the captures — they are proof the copy is inert, not
something a real executive ever sees.

So the clinics, doctors, products and open jobs in this guide are the real ones, and
the arithmetic is the application's own: 48,210 → 48,263 is 53 km, and ₹159.00 is what
that pays at this executive's two-wheeler rate.

The numbered callouts are drawn from element bounding boxes measured in the live
browser during that run, not placed by hand, so re-running the capture moves the badges
with the layout instead of leaving them pointing at empty space.

> **This document contains real clinic and patient names.** That was a deliberate
> choice — staff recognise their own round — but it means the PDF should be handled
> like any other record with patient data on it.

## Re-making it

The capture scripts live in the session scratchpad and are not checked in. To redo the
screenshots you need the `arabian_dental_shots` copy, a server on port 8077, and a venv with
`playwright`, `pillow` and `imageio-ffmpeg`. The sequence is `shots_prep.py` (neutralise
the copy, pick the executive, plan the round) → `shots_reset.py` (put the day back to
the start, including anything an earlier run left behind) → `walk_shots.py` (drive the
browser, write `marks.json`) → `annotate.py` (draw the callouts) → `make_pdf.py`,
`make_pdf_ml.py`, `make_video.py`.

`shots_reset.py` earns its length: the first runs left case slips and their sale orders
behind, the duplicate-case guard then fired on the patient name the walkthrough uses,
and three screens photographed error dialogs instead of the flow.

To change wording only, edit the HTML and re-run the PDF step; the images are
independent of the text.

## What the guide leaves out

* **Deliveries / Scan / Collect** — these come from `lab_delivery`, not
  `lab_fieldwork`, and deserve their own guide.

## Cash into My Float

The first cut of this guide left that button out, because it failed for everyone with
*"Only Petty Cash Officers or Managers can post transactions"* — `action_cash_to_float`
posts the transaction under `sudo()`, and Odoo 19's `has_group` has no superuser
shortcut, so the check saw OdooBot. On the live database at the time: 4 visits had
collected cash and 0 had ever reached a float.

Fixed in `d1ff94f`, and the guide documents it. The screenshot in
`images/17b_cash_to_float.png` is a real posted movement made by pressing the button as
the executive. If you are reading this against a deployment older than that commit, the
step will not work there.

## Two things this round of screenshots found

* **Cash in** (`8f56dc3`) is new and now has its own section in both editions: money
  taken at a door with no visit behind it, which previously forced an executive to
  invent a visit to record a true number.
* **The visit cards were collapsing to 2px** on any database with deliveries — full
  text in the DOM, four invisible hairlines on screen. It could not show on a lab with
  no deliveries, which is every database the guide had been shot on before. Fixed in
  `93a918f`; without it none of the card screenshots in this guide would exist.
