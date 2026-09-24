# Arabian Dental Lab — Odoo 19 suite

Custom addons for **Arabian Dental Lab**, Manjeri, Kerala (Odoo 19.0 Community).

The suite was forked from the Ortho Creation lab suite on 2026-09-22 and renamed: every
`ortho_*` module is now `lab_*`, every `ortho.*` model is `lab.*`, and the branding,
address, phone numbers and web addresses are the lab's own. The business model is the
same — field executives on routes, clinic visits, cash collection, work-centre scanning,
WhatsApp notifications, a doctor portal — so the two suites still share their shape, but
they are separate codebases from here on.

## Modules

| Area | Modules |
|---|---|
| Sales & orders | `sale_custom`, `lab_order_control`, `lab_rework`, `lab_portal`, `lab_track` |
| Field work | `lab_fieldwork`, `lab_collections`, `lab_delivery`, `lab_incentive`, `lab_ceo_dashboard`, `lab_dashboards`* |
| Production | `lab_workcenter_scan`, `lab_reports`, `epg_sticker_print`, `epg_product_label`, `epg_barcode_fallback` |
| Finance | `lab_finance_ops`, `lab_bank_reconciliation`, `petty_cash`, `epg_direct_payment`, `epg_outstanding_discount`, `epg_partner_statement`, `eh_account_*`, `excel_report_builder`, `stock_xls_report` |
| Messaging | `epg_whatsapp`, `lab_whatsapp` |
| Web | `lab_website`, `lab_pwa`, `lab_home`, `web_responsive`, `zxs_entp_theme`, `widget_preview_image`, `dynamic_filter_tiles` |
| Ops | `lab_access_control`, `lab_migration`, `auto_odoo_db_and_file_backup`** |
| Store | `material_request` — department requisitions from the main store (board, availability per line, partial approval, reject with reason, reorder, consumption analysis, slip) |

\* needs `spectrum_dashboard`, which is not part of this repository.
\*\* needs the `dropbox`, `boto3` and `pydrive` Python packages; ship a fresh
`auto_odoo_db_and_file_backup/models/client_secrets.json` for the lab's own Google
project if Drive backups are wanted.

Every `lab_*` module, `sale_custom` and `epg_direct_payment` carry an icon in one
family (a coloured tile per area, a FontAwesome glyph, the ADL shield in the corner);
they are generated, so a new module gets one by adding a line to the generator. The
website module wears the real logo.

## Website

`lab_website` is the public site, photo-led and built on the lab's own photographs and
logo (from its listings):

| URL | Page |
|---|---|
| `/` | Home — three-slide hero, services bento, live numbers, why clinics stay, the five case stages, gallery rail, technology, academy, Google rating, map |
| `/services`, `/services/<slug>` | Overview and a page per line: crown & bridge, implant prosthetics, veneers & aesthetics, removable prosthetics, digital dentistry, orthodontic & speciality |
| `/technology` | Scan intake → CAD → milling → printing → layering → quality gate, as a timeline |
| `/gallery` | Filterable masonry gallery with a lightbox |
| `/academy` | ADL Dental Academy programmes, the Crown Conference Hall, enquiry form |
| `/about-us` | The lab, numbers, values, people, map |
| `/send-a-case` | Three ways to send (route, courier, scan), the checklist, a call-back form |
| `/careers` | Roles the lab hires for, an application form |
| `/faq` | Accordion of the questions clinics ask first |
| `/contactus` | Odoo's contact form with the lab's details and a map |
| `/my/cases` | Doctor login (from `lab_portal`) |

Every fact — address, phone, email, hours, Instagram, rating — lives in `LAB` in
`lab_website/models/website.py`; the services, hero slides, FAQ, programmes, roles and
gallery captions are data in the same file. Service design counts are read from the
product catalogue at request time by matching category names (`match` words). Enquiry
forms are Odoo's own website forms writing a `mail.mail` to the lab's address. On
install (and on upgrade, via the migration) the hook names the website after the
company, points `/` at the homepage and fills in blank company fields.

## The home screen

`lab_home` takes over the screen every user lands on.

The **wallpaper** is the lab's own photography: navy, with layered ceramic work
in the bottom right and the wordmark in the opposite corner. It is one composed
image, built by `tools/make_home_wallpaper.py` from the website's photographs and
committed alongside the module. Re-run that script to change it. The theme paints
the home screen from two custom properties and the module redefines only those, so
nothing here fights the theme for the background.

Above the apps sits a **warning strip**, for the things nobody would otherwise go
looking for.

| Warning | Raised when | Goes to |
| --- | --- | --- |
| The last database backup did not work | The newest row in **Auto DB Backups Status** does not say Success | That log |

Only users who can act on a warning are shown one; today that means Settings
access, because the strip reports on the database's backups. The warnings are
worked out on the server as the page loads and travel on the session, so nothing
polls. A warning can be dismissed for the page, not for good: a backup that is
failing is still failing tomorrow.

Success is what is matched, and everything else counts as a failure. The backup
module controls the wording of a good run ("Local: Success", "SFTP: Success", …)
and nothing else; a wording nobody anticipated therefore raises the alarm instead
of passing quietly. To add a warning of your own, return one more dict from
`ir.http._home_alerts` in `lab_home/models/ir_http.py`.

## The day-sheet map

A field executive's day sheet (**Field Work ▸ Day Sheets**, and the same form from either
desk) draws the round on a map: every fix the phone recorded — visit check-in and
check-out, delivery hand-overs — joined **along the roads** in the order they happened,
with each clinic's own pin beside the door so drift is visible. Colours match the
timeline above it: green at the door, red away from it, blue clinic-not-pinned.

Two figures sit in the caption: the odometer from the trip and the road distance
between the doors. They measure the same thing, so a day claiming 109 km whose doors are
5 km apart is visible at a glance.

Leaflet is vendored (`lab_fieldwork/static/src/lib/leaflet/`) — Odoo Community ships no
map view. Two system parameters configure the rest:

| Parameter | Default | Notes |
|---|---|---|
| `lab_fieldwork.map_tiles` | OpenStreetMap | Any `{s}/{z}/{x}/{y}` tile URL; needs internet **from the browser**. |
| `lab_fieldwork.map_attribution` | © OpenStreetMap contributors | Shown on the map, as the tile licence requires. |
| `lab_fieldwork.routing_url` | OSRM public **demo** server | Needs internet **from the Odoo server**. Blank it to turn road routing off (the map then draws dashed straight lines). |

**Before going live**, point `routing_url` at your own router rather than the public demo,
which has no SLA and rate-limits. A Kerala-only OSRM is one container:

```
wget https://download.geofabrik.de/asia/india/southern-zone-latest.osm.pbf
docker run -t -v "$PWD:/data" osrm/osrm-backend osrm-extract -p /opt/car.lua /data/southern-zone-latest.osm.pbf
docker run -t -v "$PWD:/data" osrm/osrm-backend osrm-partition /data/southern-zone-latest.osrm
docker run -t -v "$PWD:/data" osrm/osrm-backend osrm-customize /data/southern-zone-latest.osrm
docker run -d -p 5000:5000 -v "$PWD:/data" osrm/osrm-backend osrm-routed --algorithm mld /data/southern-zone-latest.osrm
# then set lab_fieldwork.routing_url = http://localhost:5000/route/v1/driving/
```

Each route is worked out once and stored on the sheet, so the two desks opening the same
day cost one round trip; a service that is down is asked once, not on every form open,
and the map says so with a *try the routing again* link.

## Recording the route

Beneath the doors, the map draws the route the phone actually took, in blue. It is
recorded between **Start my day** and **End the day** and at no other time.

Three rules are enforced on the server, not promised by the app:

* **On duty only.** A position is kept only if its own timestamp falls inside one of
  that day's `hr.attendance` intervals — the same record payroll reads. A fix taken a
  minute before the day was started, or a minute after it was ended, is discarded even
  though the phone sent it.
* **Movement only.** A phone left on a desk repeats itself; those are dropped. A
  position is kept when it is more than 50 m from the last one, or when five minutes
  have passed and a heartbeat is worth having. A fix accurate only to 250 m or worse is
  a cell tower and is thrown away.
* **Not kept for ever.** A nightly cron deletes trails past the retention window. The
  day sheet keeps its visits and its odometer permanently; only the between-the-doors
  trail goes.

Nobody in the field can edit a recorded position — not the executive, not either
manager. A route that can be tidied afterwards proves nothing, and a day sheet resting
on one would be worth less than no map at all. An executive sees their own trail and no
one else's; the managers see the team's.

The executive is told while it is happening: the My Day card shows a **route recording**
badge next to their hours for as long as the watch is on.

| Parameter | Default | Notes |
|---|---|---|
| `lab_fieldwork.track_live` | on | Off records nothing at all, at once. |
| `lab_fieldwork.track_interval` | 90 s | How often the phone offers a position. Shorter draws a finer route and costs battery. |
| `lab_fieldwork.track_keep_days` | 90 | Nightly retention window for trails. |

**The limit, stated plainly:** a browser only runs while its page is open. Lock the
phone, switch to another app, or let the tab be discarded, and the operating system
suspends the watch. The trail then has a hole in it, and the map draws that hole as a
break in the line with both ends marked — it never bridges a gap with a straight line,
because that would put a road on the screen that nobody can say was taken. Genuine
background tracking needs a native app holding a foreground-location permission, which
is a decision about people rather than a change to a map.

## Demo data

`tools/seed_demo.py` builds an orthodontic demo lab on a database installed **without**
Odoo's demo data: four field executives on routes across Malappuram and Kozhikode, ~60
clinics with their doctors, a 39-appliance catalogue (removable, fixed, functional,
aligners, retainers) with a bench flow per family across seven work centres, and ~90
days of history — visits, case slips, maker-checker verification, manufacturing orders
scanned through the stations, deliveries, invoices, cash and cheques, day sheets, cash
handovers, incentive sheets, bench targets, doctor calls and holds, reworks, portal case
requests. Every record is created through the suite's own workflows, so the desks, the
station board, the collections screens and the CEO hub all agree.

```
instances/arabian_dental/venv/bin/python odoo19/odoo-bin shell \
    -c instances/arabian_dental/arabian_dental.conf -d arabian_dental --no-http \
    < projects/arabian_dental/tools/seed_demo.py          # SEED_SCALE=0.15 for a quick run
```

Demo logins (password `demo`): executives `rahul` `faisal` `sneha` `anas`; ops desk
`shafeeq`; marketing desk `nimmy`; counter `priya`; checker `suresh`; accounts `latha`;
production `vishnu`; bench `sajid` `arjun` `deepa` `manoj` `salma` `jithin`; CEO
`rasheed`. Doctor portal logins are the doctors' e-mail addresses.

## Migrating the lab's Odoo 17 database

The lab ran Odoo 17 (l10n_in) with 68 add-on modules. **None of those modules come
across.** `lab_migration` reads the Odoo 17 database directly and writes its data
into this suite, mapping each custom field onto the field the suite already has.

### Where the custom fields went

| Odoo 17 (dental_sale & co.) | Odoo 19 (this suite) |
|---|---|
| order / line `patient_name` | `sale.order.patient` (from the line when the order has none; several patients are listed) |
| order `shade_name` (res.shade, 878 values) | `sale.order.line.color_scheme` (product.colour, matched on the cleaned name) |
| order `material_name` (res.material) | `sale.order.instruction` = "Material: VITA" |
| order `sale_priority` high / medium / low | `sale.order.priority` urgent / normal / low |
| order `technitian_name` (m2m, 146,951 links) | `sale.order.technician_ids` **(new field)** |
| order `create_uid` | `sale.order.register_person_id` |
| line `jaw` upper / lower / upper_lower | `sale.order.line.ul` U / L / UL |
| line `quad1..quad4`, `t_no`, `quarter` | `sale.order.line.teeth` **(new field, FDI: "14, 13, 46")** |
| invoice line `patient_name`, `jaw`, quadrants | `account.move.line.patient`, `.ul`, `.teeth` |
| partner `doctor_name` + `doctor_contact_no` | a child contact of the clinic flagged **Doctor** |
| partner `district_id` (21 spellings) | a partner **tag** per district (spellings normalised) |
| partner `is_customer` / `is_supplier` | `customer_rank` / `supplier_rank`; every customer is a **Clinic**, one named "DR …" is a Doctor too |
| partner `mobile` (dropped in v18) | `phone`, or a "Mobile:" note when both differ |
| partner `customer_id` (smbg_contact) | `ref` |
| partner `l10n_in_pan`, `vat` | `pan_number`, `gst_number` (and the l10n_in fields) |
| partner `journal_id` B2B / B2C | nothing: the invoices keep their own B2B / B2C journals |
| `ir_property` payment term / fiscal position | `property_payment_term_id`, `property_account_position_id` |
| company `acc_number`, `bank_ifsc`, `acc_bank_name` | a `res.partner.bank` on the company |
| payment `cheque_nos/dates/status` (post_dated_cheque) | **Cheque register** (`lab.cheque`): received → Received, submitted → Deposited, accepted → Cleared |
| department stock locations (Z- Ceramic Department …) and consumption sinks (Z-Acrylic Manufacturing Dept, Marketing — type *inventory*) | the same locations, every type; Odoo's own adopted by full name |
| warehouse, operation types, reordering rules | adopted by code (WH, IN/OUT/INT/PICK/PACK…) so every transfer keeps its exact type and sequence; rules created |
| product cost, category cost method / valuation (`ir_property`) | `standard_price` on the product, `property_cost_method` / `property_valuation` on the category |
| transfers, their moves and **move lines** (11,163), adjustments outside any transfer (668) | the same, states as they were; done move lines are replayed in date order, so every transfer reads as it did in the source |
| on-hand quantities (72,205 units over 1,313 product/location pairs) | copied from the source's own **quants** as of `On-hand As Of`, then anything the source does not stock is taken back to zero. Re-deriving the position from the moves instead put 15,141 units too many on the shelves, because a move carries what was asked for and a quant carries what is there |
| move ↔ sale line, move ↔ purchase line, bill line ↔ purchase line, backorder ↔ transfer | the same links, so delivered / received / billed quantities on orders come out of the documents |
| `hr.employee` (+ departments, jobs) | the same, linked to the migrated users |
| attachments on orders, partners, products, employees | copied from the source filestore (`Source Filestore` on the connection) |
| `material.request` + lines (1,015 requisitions) | the rewritten `material_request` module: same model names, transfers linked back, delivered quantities from the moves, approved + delivered → **Delivered** |

Two fields were added to the suite for this, because the lab cannot work without
them and nothing existing could carry them: **Teeth (FDI)** on order and invoice
lines (printed on the tax invoice and the registration acknowledgement) and
**Technicians** on the order.

**Deliberately left behind** (empty, or a feature the suite replaces): `work_type`,
`impression_*`, `opposing_model`, `pre_cutting_model`, `wax_bite`, `model_preparation`
(0 rows filled); `smbg_contact` registration enquiries (0 rows);
TDS/TCS on partners (0 partners); `om_account_followup` levels (Odoo 19 has its own
follow-ups); asset/budget/payroll data from the OM accounting kit (19 assets, 7
payslips — not carried); e-invoice portal credentials; the 754,000 chatter
notifications (4 real notes existed).

### What is migrated

Users (with passwords and groups) · company · units · product categories · shades →
colours · districts → tags · chart of accounts and taxes (adopted from l10n_in by
code and name) · payment terms with their lines · fiscal positions · sales routes ·
partners (+ doctor contacts, properties) · products and vendor pricelists · stock
locations · departments, jobs, employees · sequences, parameters, journals, bank
accounts · then every document from `Migrate Documents From` onwards: sale orders
(with their lines' teeth, U/L, colour and technicians), purchase orders, invoices,
refunds and bills (posted, linked back to their order and purchase lines), every
other posted journal entry line for line, **the ledger's reconciliation replayed
partial by partial** (so paid / partial / in-payment states and open balances match
the source), the cheque register, transfers with their moves (exact operation type,
real locations, sale and purchase line links, backorders), inventory adjustments,
**move lines replayed to rebuild the stock**, material requests, attachments and
images, and finally the source create/write timestamps.

### Running it

```
# 1. Restore the Odoo 17 dump under a new name and neutralise it (no mail, no crons)
docker run --rm --network host -v $PWD/backups/adl_20260923:/b:ro -e PGPASSWORD=odoo \
  postgres:14 pg_restore -h 127.0.0.1 -U odoo -d adl_prod_v17 --no-owner -j 3 /b/adl_prod.dump
# neutralise: tools/neutralise_source.py  (mail servers, crons, queued mail, tokens)

# 2. A fresh target from the clean install
createdb -T arabian_dental_clean arabian_dental_live

# 3. Run the phases from an Odoo shell (each phase commits as it goes)
export MIG_FILESTORE=$PWD/backups/adl_20260923/filestore/adl_prod   # export: a prefix would only reach printf
printf 'exec(open("projects/arabian_dental/tools/run_migration.py").read())' | \
  instances/arabian_dental/venv/bin/python odoo19/odoo-bin shell \
    -c instances/arabian_dental/arabian_dental.conf -d arabian_dental_live --no-http
```

`MIG_PHASES=master,config` runs only the masters; `MIG_LIMIT=50` migrates fifty
documents per phase for a smoke test; `MIG_RESUME=1` skips documents already in.
The same phases are buttons on **Migration ▸ Odoo 17 Source**.

Dates: the source holds nothing before 2025-01-01, so the default opening date
(2024-12-31) carries no balance and every entry arrives as the entry it was. To
cut over on a balance instead, set `Opening Balance Date` to the FY end and
`Migrate Documents From` to the day after.

## Running locally

```
instances/arabian_dental/start.sh                      # http://localhost:8079, DB arabian_dental
instances/arabian_dental/start.sh -u lab_website --stop-after-init
instances/arabian_dental/start.sh -d arabian_dental -u lab_website --test-enable \
    --test-tags /lab_website --stop-after-init --http-port=8199 --gevent-port=8198
```

## Inherited documents

`MIGRATION_RUNBOOK.md` now describes this lab's Odoo 17 → 19 migration. `docs/` and
`Field_Executive_Guide/` were written for the
original lab and carry its history; the text has been rebranded but the figures,
database names and screenshots are from that project. Treat them as reference for how
the modules work, not as a record of this lab.
