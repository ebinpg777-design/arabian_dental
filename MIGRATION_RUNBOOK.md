# Lab Odoo 10 → 19 Migration Runbook

> **Inherited document.** Written for the Ortho Creation project this suite was forked from (2026-09-22); the wording has been rebranded but the figures, database names and screenshots are that project's. Use it for how the modules work, not as a record of Arabian Dental Lab.

Repeatable procedure for migrating a **live Odoo 10 Arabian Dental Lab database** into a fresh Odoo 19
Enterprise database using the `lab_migration` module.

Last validated: **2026-08-02**, migrating `arabian_dental_v10` (12 GB, Odoo 10.0.1.3, 2 companies) → `arabian_dental_v19`.

> **Read §1 and §3 before you start.** Two steps are order-dependent and silently produce a wrong
> database if done out of order. They are the difference between an exact migration and one that
> *looks* balanced but isn't.

---

## 0. What this migration does and does not do

**Migrates (master data + configuration):**
companies · UoM · product categories · appliance styles · colours · send-through · chart of accounts ·
tax groups · taxes · payment terms · **sales teams** · partners · product templates & variants ·
vendor pricelists · **work centers** · BoMs & BoM lines · **BoM operations (from v10 routings)** ·
product routes · users (with passwords & groups) · banks · sequences · system parameters ·
decimal precision · currencies · company financials · journals.

**NOT migrated — master data still missing from `ENTITY_SPECS`** (audited 2026-08-02 against the v10
source; counts are from that database). Add specs if your rollout needs them:

| v10 table | rows | notes |
|---|---|---|
| `hr_employee` | 276 | `hr` **is** installed in the target — likely the biggest real gap |
| `hr_department` / `hr_job` | 3 / 9 | prerequisites for employees |
| `account_account_tag` | 93 | tax/report tagging |
| `stock_location` | 24 | v19 auto-creates its own; adopting needs care |
| `stock_location_route` | 12 | only Manufacture/MTO/Buy are mapped, by name |
| `stock_incoterms` | 15 | |
| `res_partner_category` | 9 | partner tags |
| `product_uom_categ` | 8 | |
| `mrp_workcenter_productivity_loss` | 7 | |
| `res_partner_title` | 5 | |
| `product_pricelist` (+items) | 2 / 2 | |
| `stock_warehouse` | 2 | v19 auto-creates one per company |
| `product_removal`, `sale_layout_category` | 2 / 2 | |

Not applicable: `crm_*` and `account_asset_*` (those modules are not installed in the target), and
v10 has **no** quality tables at all despite `quality_control` being installed in v19.

**Plus:** accounting opening balances, inventory opening quantities, and invoice/MO numbering
continuation.

**Does NOT migrate transactions.** `migration_spec.py::ENTITY_SPECS` contains no sale orders,
purchase orders, pickings, manufacturing orders, invoices or journal entries. This is a
**balance-forward cutover**, not a history migration. If you need documents carried over, new specs
must be written first — see §8.

---

## 1. Pre-flight

```bash
# 1a. Restore the live v10 dump under a NEW name (never migrate against production directly)
PGPASSWORD=odoo createdb -h 127.0.0.1 -U odoo arabian_dental_v10_live
PGPASSWORD=odoo pg_restore -h 127.0.0.1 -U odoo -d arabian_dental_v10_live -j4 /path/to/lab_live_<date>.dump

# 1b. Confirm it is really Odoo 10 and see what you are dealing with
PGPASSWORD=odoo psql -h 127.0.0.1 -U odoo -d arabian_dental_v10_live -c \
  "SELECT latest_version FROM ir_module_module WHERE name='base';"     -- expect 10.0.x
PGPASSWORD=odoo psql -h 127.0.0.1 -U odoo -d arabian_dental_v10_live -c \
  "SELECT id, name FROM res_company ORDER BY id;"
```

**1c. Establish the real data cut-off — do this every time.** A dump is only as current as the day it
was taken; the 2026-08-02 run found a source that stopped on 2026-01-13 while the request assumed live
data.

```sql
SELECT 'sale_order' t, max(date_order)::date FROM sale_order
UNION ALL SELECT 'purchase_order', max(date_order)::date FROM purchase_order
UNION ALL SELECT 'stock_picking',  max(date)::date       FROM stock_picking
UNION ALL SELECT 'mrp_production', max(date_planned_start)::date FROM mrp_production
UNION ALL SELECT 'account_move',   max(date)::date       FROM account_move;
```

**1d. Pick the opening date.** Indian FY convention → `YYYY-04-01`. The opening entry is dated
`opening_date` and carries every posted balance **`<= opening_date`**. To get "opening on 1-Apr based
on the closing of 31-Mar", confirm nothing is dated exactly on 1-Apr, otherwise it lands in the
opening *and* in the new year:

```sql
SELECT count(*) FROM account_move_line WHERE date = '2026-04-01';   -- want 0
```

---

## 2. Environment

Runs on the `odooebin` instance (`/home/ebinpg/odoo/odooebin`, port 10190) whose addons path includes
`custom/arabian_dental`. Any Odoo 19 Enterprise instance works provided `custom/arabian_dental` is on its `addons_path`.

If the new DB name does not match the instance's `dbfilter`, add it, **and add it to `db_name` too** —
`dbfilter` does not scope the cron worker:

```ini
dbfilter = ^(odooebin|arabian_dental_v19).*$
db_name  = odooebin_test,arabian_dental_v19
```

---

## 3. Build the target database — ORDER IS LOAD-BEARING

### 3a. Create with `base` only

```bash
cd /home/ebinpg/odoo/odooebin
./start.sh -d arabian_dental_v19 -i base --stop-after-init --log-level=warn
```

### 3b. Set the company country to India BEFORE accounting installs

> **Critical.** Odoo picks the chart of accounts from the company country at accounting-install time.
> With no country it loads `generic_coa` (US). Every subsequent account match is then wrong, and there
> is no clean way back — you must drop and start over.

```bash
printf "%s\n" \
  "c = env.company" \
  "c.partner_id.country_id = env.ref('base.in')" \
  "c.name = 'Arabian Dental Lab'" \
  "env.cr.commit()" \
  "print('OK', c.name, c.partner_id.country_id.code)" \
| ./venv/bin/python odoo/odoo-bin shell -c odooebin.conf -d arabian_dental_v19 --log-level=warn
```

> `odoo-bin shell` takes the subcommand **first** (`odoo-bin shell -c <conf>`), so it cannot be run
> through `start.sh`, which puts `-c` ahead of your arguments.

### 3c. Install the modules

```bash
./start.sh -d arabian_dental_v19 \
  -i lab_migration,epg_sticker_print,epg_partner_statement,stock_xls_report \
  --stop-after-init --log-level=warn
```

### 3d. Verify the chart before going further — do not skip

```bash
PGPASSWORD=odoo psql -h 127.0.0.1 -U odoo -d arabian_dental_v19 -tAc \
  "SELECT chart_template FROM res_company WHERE id=1;"        # MUST print: in
```

If this prints `generic_coa`, **drop the database and restart from 3a**.

---

## 4. Run the migration

Save as `run_sync.py`:

```python
Backend = env['migration.backend']
b = Backend.search([], limit=1)
vals = {
    'name': 'Odoo 10 Source',
    'db_host': '127.0.0.1', 'db_port': 5432,
    'db_name': 'arabian_dental_v10_live',          # <-- the restored live dump
    'db_user': 'odoo', 'db_password': 'odoo',
    'opening_date': '2026-04-01',          # <-- your cutover date
}
if b:
    b.write(vals)
else:
    b = Backend.create(vals)
env.cr.commit()
b.action_test_connection()
print(">>> RUNNING action_sync_all() ...", flush=True)
b.action_sync_all()
env.cr.commit()
print("<<< SYNC COMPLETE")
print(b.log)
```

```bash
./venv/bin/python odoo/odoo-bin shell -c odooebin.conf -d arabian_dental_v19 \
  --log-level=warn < run_sync.py 2>&1 | tee sync.log
```

Takes roughly 10–20 minutes for ~6.5k partners / 2.4k products / 1M source move lines.
`action_sync_all()` = master specs → configuration → opening accounting → opening inventory →
invoice/MO numbering.

The module is **idempotent**: re-running updates in place (`created=0 updated=N`) and never duplicates.
Opening moves are deleted and rebuilt each run, so the sync can be re-run safely to refresh data.

---

## 5. Verification — always run this

### 5a. Zero failures in the log

```bash
grep -E "failed=[1-9]" sync.log        # expect no output
grep -c "pass1 account.account" sync.log   # expect 0
```

`Chart of Accounts` must read `failed=0`. **Any account failure silently corrupts the opening
balances** (see §7.1).

### 5b. Master-data counts, v10 vs v19

```bash
Q10() { PGPASSWORD=odoo psql -h 127.0.0.1 -U odoo -d arabian_dental_v10_live -tAc "$1"; }
Q19() { PGPASSWORD=odoo psql -h 127.0.0.1 -U odoo -d arabian_dental_v19      -tAc "$1"; }
for t in res_partner product_template crm_team account_account account_tax \
         product_category product_supplierinfo mrp_bom mrp_bom_line mrp_workcenter res_users; do
  printf "%-22s v10=%-8s v19=%-8s\n" "$t" \
    "$(Q10 "SELECT count(*) FROM $t")" \
    "$(Q19 "SELECT count(*) FROM $t WHERE x_odoo10_id IS NOT NULL")"
done
```

### 5c. Opening balances — the one that matters

Compares the v19 opening entry against the same aggregation computed on the v10 side.
**Line counts, debit totals and the equity plug must match exactly, per company.**

```bash
PGPASSWORD=odoo psql -h 127.0.0.1 -U odoo -d arabian_dental_v10_live -c "
WITH rp AS (
  SELECT am.company_id, aml.account_id, sum(aml.debit)-sum(aml.credit) AS bal
  FROM account_move_line aml JOIN account_move am ON am.id=aml.move_id AND am.state='posted'
  JOIN account_account aa ON aa.id=aml.account_id
  JOIN account_account_type aat ON aat.id=aa.user_type_id
  WHERE aml.date <= '2026-04-01' AND aat.type IN ('receivable','payable')
  GROUP BY am.company_id, aml.account_id, aml.partner_id
), oth AS (
  SELECT am.company_id, aml.account_id, sum(aml.debit)-sum(aml.credit) AS bal
  FROM account_move_line aml JOIN account_move am ON am.id=aml.move_id AND am.state='posted'
  JOIN account_account aa ON aa.id=aml.account_id
  JOIN account_account_type aat ON aat.id=aa.user_type_id
  WHERE aml.date <= '2026-04-01' AND aat.include_initial_balance
    AND aat.type NOT IN ('receivable','payable')
  GROUP BY am.company_id, aml.account_id
), allb AS (SELECT * FROM rp UNION ALL SELECT * FROM oth)
SELECT company_id,
       count(*) FILTER (WHERE abs(bal)>=0.005) AS expected_lines,
       round(sum(bal) FILTER (WHERE bal>0),2)  AS expected_debit,
       round(sum(bal),2)                        AS expected_plug
FROM allb GROUP BY company_id ORDER BY 1;"

PGPASSWORD=odoo psql -h 127.0.0.1 -U odoo -d arabian_dental_v19 -c "
SELECT m.company_id, count(l.id)-1 AS actual_lines, round(sum(l.debit),2) AS actual_debit
FROM account_move m JOIN account_move_line l ON l.move_id=m.id
WHERE m.ref='Opening Balance (Odoo 10 migration)'
GROUP BY m.id, m.company_id ORDER BY 1;"
```

`actual_lines` = expected_lines (the extra line is the equity plug).
A **plug much larger than expected** means account balances were dropped → §7.1.

### 5d. Idempotency proof (re-run safety)

```bash
snap() { PGPASSWORD=odoo psql -h 127.0.0.1 -U odoo -d arabian_dental_v19 -tAc "
 SELECT 'partner='||count(*) FROM res_partner
 UNION ALL SELECT 'product='||count(*) FROM product_template
 UNION ALL SELECT 'account='||count(*) FROM account_account
 UNION ALL SELECT 'team='||count(*)    FROM crm_team
 UNION ALL SELECT 'bom='||count(*)     FROM mrp_bom
 UNION ALL SELECT 'openlines='||count(*) FROM account_move_line l
   JOIN account_move m ON m.id=l.move_id WHERE m.ref='Opening Balance (Odoo 10 migration)'
 ORDER BY 1;"; }
snap > /tmp/a.txt
./venv/bin/python odoo/odoo-bin shell -c odooebin.conf -d arabian_dental_v19 --log-level=warn < run_sync.py
snap > /tmp/b.txt
diff /tmp/a.txt /tmp/b.txt && echo "IDEMPOTENT — no duplicates"
```

---

## 6. Reference baseline (2026-08-02 run)

Use as a sanity check; your live numbers will be larger.

| Entity | v10 | v19 | Note |
|---|---|---|---|
| partners | 6492 | 6492 | |
| product templates | 2417 | 2417 | |
| chart of accounts | 276 | 276 | `failed=0` after the §7.1 fix |
| taxes | 121 | 121 | |
| sales teams | 46 | 34 | v10 has duplicate names, consolidated by name; **all shared** (§7.8) |
| work centers | 10 | 10 | |
| BoM operations | 39 routing lines | 1203 | one op per (BoM × routing line); 2 BoMs excluded (§7.3) |
| vendor pricelists | 1314 | 1314 | |
| BoMs | 294 | 292 | 2 fail company consistency (§7.3) |
| users | 113 | 110 | `default`/`public`/`portaltemplate` correctly skipped |

Opening at 2026-04-01 — Arabian Dental Lab: 3121 lines, debit ₹243,459,099.42, plug ₹125,047,027.35.
Aligners: 89 lines, debit ₹16,282,902.00, plug ₹8,679,336.00. Both exact.

A large equity plug is **normal** — the opening carries balance-sheet accounts only, so the plug is
accumulated retained earnings. Judge it against the computed expectation in §5c, never by size.

---

## 7. Known issues and fixes

### 7.1 Accounts failing → silently wrong opening balances *(FIXED 2026-08-02)*

**Symptom.** `Chart of Accounts ... failed=89`, log full of
`The code must be set for every company to which this account belongs.` and
`Bank & Cash accounts cannot be shared between companies.`

**Cause.** `_sync_pass1` scoped the `match` domain to a company only for `company_field == 'company_id'`.
`account.account` uses `company_ids`, so a second-company account matched the first company's account
**by code** and was adopted and shared. In v19 `account.code` is computed from `code_store`
(`company_dependent=True`, keyed by `env.company.root_id`), so the shared account had no code for the
other company.

**Why it is dangerous.** `_opening_accounting` resolves accounts via `_resolve`; unmapped accounts
return falsy and the line is **skipped silently**. The move still balances because the difference is
absorbed by the equity plug — so a materially wrong opening entry looks perfectly healthy.

**Fix** (already applied in `migration_backend.py`): scope the match with
`('company_ids','in',tgt_cid)` and perform create/search/write through `Model.with_company(tgt_cid)`;
same `with_company` in `_opening_equity_account`.

> **A corrupted map cannot be repaired by re-running.** `_resolve` reuses the bad
> `migration_map` rows. Drop the target database and rebuild from §3a.

### 7.2 `res.partner` has no `team_id` in v19

The partner→sales-team link is dropped (5623 partners in the 2026-08-02 run). Teams themselves migrate
fine. The field was removed in v19; carrying it needs a custom field plus a spec change.

### 7.3 BoMs failing company consistency

`Uh-oh! You've got some company inconsistencies here` — a v10 BoM on company B references a product on
company A. v19 forbids this. It is a **source data defect**: fix in v10 (align BoM and product company)
and re-run, or accept the loss.

### 7.4 Benign re-run warning

`pass1 account.account id=25: You cannot change the type of an account set as Bank Account on a
journal…` on the *second* run only. The savepoint rolls back one redundant update; the record is
already correct. Confirm with a balance spot-check and ignore.

### 7.5 UoM fallback

Only UoMs whose **name** matches a v19 built-in are adopted; the rest fall back to Units. In the
validated run 3 products lost ml/Liter/Bottle. Check with:

```sql
SELECT u.name->>'en_US', count(*) FROM product_template pt
JOIN uom_uom u ON u.id=pt.uom_id WHERE pt.x_odoo10_id IS NOT NULL GROUP BY 1;
```

### 7.6 Product routes

`_sync_product_routes` maps v10 routes to v19 built-ins **by name** (Manufacture / MTO / Buy).
Company-prefixed v10 routes (e.g. `Arabian Dental Lab Aligners: Buy`) are not mapped. In the validated run
these were mostly a v10 misconfiguration (company-9 routes on company-1 products) that v19 would reject
anyway.

### 7.7 `env.company` is the WRONG company during the sync *(FIXED 2026-08-02)*

The single highest-value thing to understand here. `_migration_env()` sets
`allowed_company_ids` to **all** companies, so `env.company` becomes whichever sorts first —
**Aligners, not Arabian Dental Lab**. Anything that silently falls back to `env.company` therefore lands on
the wrong company:

- `_company_val` used `env.company.id` when the v10 row had `company_id IS NULL`. 24 of 46 v10 sales
  teams are NULL (= shared) and were all stamped onto Aligners. Fixed by dropping `company_field` from
  the `crm_team` spec and forcing `'static': {'company_id': False}` (see §7.8).
- Any `check_company=True` default resolves against the wrong company: **all 10 work centers failed**
  with *"Uh-oh! You've got some company inconsistencies here"* because
  `mrp.workcenter.resource_calendar_id` defaulted to Aligners' calendar while `company_id` was 1.
- `account.account.code_store` (company-dependent) was written into the wrong company's slot (§7.1).

**Fix:** `_sync_pass1` now always does create/search/write through `Model.with_company(tgt_cid)` for
**both** `company_id` and `company_ids` specs. When adding a new spec, assume `env.company` is wrong.

Check for NULL source companies before trusting a company-scoped spec:

```sql
SELECT count(*) FROM <table> WHERE company_id IS NULL;   -- NULL means "shared"
```

### 7.8 Sales teams are common to both companies

They must end up with `company_id = NULL` (shared). Verify:

```sql
SELECT COALESCE(company_id::text,'shared'), count(*) FROM crm_team
WHERE x_odoo10_id IS NOT NULL GROUP BY 1;      -- expect a single 'shared' row
```

`'static'` is applied on create **and** on re-sync, so an already-migrated database self-heals on the
next run — no rebuild needed for this one.

### 7.9 v10 routings → v19 BoM operations

v19 deleted `mrp.routing`; operations attach to the BoM (`mrp.routing.workcenter.bom_id`). A v10
routing is shared by many BoMs (routing 5 → 108 BoMs), so `_sync_bom_operations` creates one operation
**per BoM per routing line** — 39 v10 lines became 1203 operations. Idempotency key is
`(bom_id, x_odoo10_id)`, not `x_odoo10_id` alone. Work centers must sync first or every operation is
skipped. `working_state` is computed+stored in v19 — never write it.

### 7.10 Shell traps

- `pkill -f odooebin` matches **your own shell** (its command line contains the pattern) and kills the
  session. Filter by pid and skip `$$`/`$PPID`.
- `set -o pipefail` + `... | grep -v ...` aborts the script when grep filters everything out and
  returns 1. Append `|| true`.

---

## 8. If you need transactions migrated

Not supported today. It requires new entries in `migration_spec.py::ENTITY_SPECS` (plus hooks) for
`sale_order`/`sale_order_line`, `purchase_order`/`_line`, `stock_picking`/`stock_move`,
`mrp_production`, `account_move`/`account_move_line` — respecting v19 changes: `stock.move.name`
removed, `sale.order.line.product_uom_id`, `account.move` uses `sequence.mixin` (not `ir.sequence`),
and `mrp.production.name` comes from the operation type's sequence.

Decide first whether you want **all history** (very large: 269k SOs / 268k pickings / 237k MOs / 247k
invoices) or only **open documents** by state. Balance-forward + open documents is almost always the
right call.

---

## 9. Post-migration checklist

- [ ] `chart_template` = `in`
- [ ] `failed=0` for every entity in the sync log
- [ ] Opening entries **posted**, dated `opening_date`, one per company
- [ ] §5c reconciliation exact for every company
- [ ] Idempotency diff clean (§5d)
- [ ] Work centers present, and every routed BoM has operations:
      `SELECT count(*) FROM mrp_workcenter WHERE x_odoo10_id IS NOT NULL;`
      `SELECT count(DISTINCT bom_id) FROM mrp_routing_workcenter WHERE x_odoo10_id IS NOT NULL;`
- [ ] Sales teams all shared — `SELECT count(*) FROM crm_team WHERE company_id IS NOT NULL AND x_odoo10_id IS NOT NULL;` returns 0
- [ ] Invoice/MO numbering continues the v10 series (bottom of the sync log)
- [ ] Spot-check a few partners, products and a receivable balance in the UI
- [ ] Users can log in with their **v10 passwords** (hashes are copied across)
