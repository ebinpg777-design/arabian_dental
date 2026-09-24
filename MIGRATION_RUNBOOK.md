# Arabian Dental Lab — Odoo 17 → 19 Migration Runbook

Repeatable procedure for bringing the lab's **live Odoo 17 database** (`adl_prod` on
`server.arabiandental.com`) into a fresh Odoo 19 database running this suite, with the
`lab_migration` module. The lab's 68 Odoo 17 add-on modules are **not** installed on the
target; their data is mapped onto the suite's own fields (the table is in `README.md`,
*Migrating the lab's Odoo 17 database*).

First full run: **2026-09-23**, dump of the same day, source 1.5 GB / 71,475 orders.

> The engine was written for the ortho lab's Odoo 10 → 19 move and re-pointed at Odoo 17.
> The two-pass upsert, `migration.map`, batching and resume behaviour are unchanged; every
> source-side query is now Odoo 17's. The earlier runbook's Odoo 10 figures no longer apply.

---

## 0. What comes across, and what does not

**Masters** — users (passwords, groups by xml-id), company (with its bank account and
inventory / purchase policy), units, product categories (+ cost method and valuation),
shades → colours, districts → partner tags, chart of accounts and taxes (adopted from
l10n_in by code / name; the lab's own are created), payment terms with lines, fiscal
positions, sales routes, partners (+ doctor child contacts, payment term and GST position
properties), products (+ cost prices) and vendor pricelists, warehouse, operation types,
stock locations, reordering rules, departments, jobs, employees, sequences, parameters,
journals.

**Documents, from `Migrate Documents From` onwards** — sale orders, invoices and refunds
(posted, linked to their order lines), every other posted journal entry line for line,
the source reconciliation replayed partial by partial, the cheque register, purchase
orders, transfers with their moves (exact type, real locations, sale / purchase line
links, backorders), inventory adjustments, move lines (replayed on the done moves, which
rebuilds on-hand per location from the history), material requests (linked to their
transfers), attachments and images, source timestamps, and an inventory cross-check
against the source as of the dump.

**Left behind on purpose** — the 68 modules (the material-request one was rewritten for
this suite and its rows DO migrate); OM accounting-kit data (assets, budgets, payslips, follow-up levels);
`smbg_contact` enquiries; TDS/TCS settings (0 partners); e-invoice credentials; the
754,000 chatter notifications (4 real notes existed); the `sale.order` fields that were
never filled (work_type, impression_*, opposing/pre-cutting model, wax_bite).

---

## 1. Take the backup (on the server, as root)

```
mkdir -p /root/adl_backup && cd /tmp
sudo -u postgres pg_dump -Fc adl_prod > /root/adl_backup/adl_prod.dump
tar czf /root/adl_backup/adl_prod_filestore.tgz -C /opt/odoo17/filestore/filestore adl_prod
tar --exclude='*.pyc' --exclude='__pycache__' -czf /root/adl_backup/custom_addons.tgz \
    -C /opt/odoo17 custom-addons-prod
```

`pg_dump` as the postgres user cannot write into `/root`: redirect from the root shell as
above rather than `-f`. Pull the files with **rsync over ssh** (`--partial
--append-verify`); the paramiko SFTP helper managed 190 KB/s and dropped mid-file.

## 2. Restore and neutralise locally

```
createdb arabian_dental_v17_placeholder   # name it adl_prod_v17 — never migrate against the server
docker run --rm --network host -v $PWD/backups/adl_20260923:/b:ro -e PGPASSWORD=odoo \
  postgres:14 pg_restore -h 127.0.0.1 -U odoo -d adl_prod_v17 --no-owner --no-privileges -j 3 /b/adl_prod.dump
instances/arabian_dental/venv/bin/python projects/arabian_dental/tools/neutralise_source.py adl_prod_v17
tar xzf backups/adl_20260923/adl_prod_filestore.tgz -C backups/adl_20260923/filestore
```

`odoo-bin neutralize` needs Odoo 17 code — there is a checkout at `odoo17/` (used by the
`instances/adl17` copy on :1717) — and it skips custom modules either way; the script
applies the core `neutralize.sql` equivalents and blanks the e-invoice portal credentials,
so it works with or without that checkout. Confirm afterwards:

```sql
SELECT count(*) FROM ir_cron WHERE active;          -- 1 (autovacuum)
SELECT name, active FROM ir_mail_server;            -- only 'neutralization - disable emails'
```

## 3. Establish the data window

```sql
SELECT min(date), max(date) FROM account_move_line;   -- 2025-01-01 .. dump day
SELECT min(date_order)::date, max(date_order)::date FROM sale_order;   -- 2025-04-01 ..
```

Nothing exists before 2025-01-01, so the defaults (`Opening Balance Date` 2024-12-31,
`Migrate Documents From` 2025-01-01) migrate **everything as documents** and the
opening carries no balance. To cut over on a balance instead, set the opening date to
the FY end and the documents date to the day after — the engine posts one itemised
opening entry per partner and migrates only later documents.

## 4. Build the target

```
createdb -T arabian_dental_clean arabian_dental_live
instances/arabian_dental/venv/bin/python odoo19/odoo-bin -c instances/arabian_dental/arabian_dental.conf \
    -d arabian_dental_live -u lab_migration,sale_custom --stop-after-init
```

`arabian_dental_clean` is the suite installed on an empty Indian company (chart `in`).
The company country must be India **before** accounting installs, which the template
already guarantees.

## 5. Run

```
export MIG_FILESTORE=$PWD/backups/adl_20260923/filestore/adl_prod
printf 'exec(open("projects/arabian_dental/tools/run_migration.py").read())' | \
  instances/arabian_dental/venv/bin/python odoo19/odoo-bin shell \
    -c instances/arabian_dental/arabian_dental.conf -d arabian_dental_live --no-http \
    > backups/adl_20260923/full_run.log 2>&1
```

Phases, in order: `master, config, opening, numbering, gl, ops, inventory, timestamps`
(`gl` = orders, purchases, invoices, entries, reconciliation, cheques; `ops` = MOs,
transfers, adjustment moves, move lines, material requests, attachments; `stock` re-runs
only the three stock phases).
`MIG_PHASES` picks a subset, `MIG_LIMIT=50` smoke-tests with fifty documents per phase,
`MIG_RESUME=1` skips documents already mapped. **Export the variables** — a
`VAR=x printf … | python` prefix reaches only `printf`.

Each phase commits as it goes (documents every 200), so a stopped run is resumed with
`MIG_PHASES` set to what is left. The same phases are buttons on
**Migration ▸ Odoo 17 Source**.

## 6. Verify

```sql
-- counts, source vs target
SELECT (SELECT count(*) FROM sale_order), (SELECT count(*) FROM account_move WHERE move_type='out_invoice' AND state='posted');
-- trial balance ties: receivable total
SELECT round(sum(balance)) FROM account_move_line l JOIN account_account a ON a.id=l.account_id
 WHERE a.account_type='asset_receivable' AND l.parent_state='posted';
-- payment states follow the replayed reconciliation
SELECT payment_state, count(*) FROM account_move WHERE move_type='out_invoice' AND state='posted' GROUP BY 1;
```

Then open a clinic, an order with teeth and technicians, an invoice with its payment
widget, a cheque in the register, and the stock of a department store.

## 7. Known limits

* A browser-side detail: the lab's per-executive cash journals ("Cash from Murali") come
  across as journals; the field-work cash handover feature starts fresh.
* `account.payment` records are not recreated; payments arrive as their journal entries,
  matched to their invoices. Cheques received are in the cheque register.
* Groups of the modules not installed here (payroll, expenses, projects, leaves) are
  dropped from users; the log names them.
* Attachments are copied only for the models listed in `_ATTACHMENT_MODELS`.
* Material requests: on Odoo 17 approving a request moved the goods at once; here
  approval raises the transfer and the store validates it. Migrated requests therefore
  come across with their v17 transfer linked, each move on its line, and an approved
  request whose transfer is done as **Delivered**. The link is completed on any later
  pass (resume mode included), so the phase order does not matter for it.
* Thirteen vendor bills / refunds were booked in the B2B *sale* journal on Odoo 17; Odoo 19
  refuses that, so they post in the default purchase journal (BILL) under their own numbers.
