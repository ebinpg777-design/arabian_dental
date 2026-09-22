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
| Web | `lab_website`, `lab_pwa`, `web_responsive`, `zxs_entp_theme`, `widget_preview_image`, `dynamic_filter_tiles` |
| Ops | `lab_access_control`, `lab_migration`, `auto_odoo_db_and_file_backup`** |

\* needs `spectrum_dashboard`, which is not part of this repository.
\*\* needs the `dropbox`, `boto3` and `pydrive` Python packages; ship a fresh
`auto_odoo_db_and_file_backup/models/client_secrets.json` for the lab's own Google
project if Drive backups are wanted.

## Website

`lab_website` is the public site: home, **What We Make** (`/restorations`, families and
counts read live from the product catalogue), **Academy** (`/academy`, ADL Dental Academy
and the Crown Conference Hall), **About** (`/about-us`) and the doctor login. On install
it names the website after the company, points `/` at the new homepage and fills in the
lab's address, phone and Instagram wherever those company fields are still empty.

The lab's facts live in one place — `lab_website/models/website.py` (`LAB`) — and the
family-to-category matching is in the same file (`FAMILIES`).

## Running locally

```
instances/arabian_dental/start.sh                      # http://localhost:8079, DB arabian_dental
instances/arabian_dental/start.sh -u lab_website --stop-after-init
instances/arabian_dental/start.sh -d arabian_dental -u lab_website --test-enable \
    --test-tags /lab_website --stop-after-init --http-port=8199 --gevent-port=8198
```

## Inherited documents

`MIGRATION_RUNBOOK.md`, `docs/` and `Field_Executive_Guide/` were written for the
original lab and carry its history; the text has been rebranded but the figures,
database names and screenshots are from that project. Treat them as reference for how
the modules work, not as a record of this lab.
