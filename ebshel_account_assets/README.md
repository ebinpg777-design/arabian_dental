# Assets and Depreciation

Fixed assets from purchase to disposal, for Odoo 20 Community.

* Odoo **20.0** Community
* Depends on `account` and `ebshel_account_reports` (for the Depreciation Schedule report)

---

## Installation

1. Copy `ebshel_account_assets` (and `ebshel_account_reports`) into your addons path.
2. Install **Assets and Depreciation**.
3. Create the asset categories under **Accounting › Configuration › Assets**:
   accounts, journal, method, period, duration, prorata rule and default salvage.

## What it does

* **Categories** carry the accounts, journal and method: straight line,
  declining balance, or declining then straight line; monthly or yearly
  (yearly periods follow the company's fiscal year); prorata by days or months.
* **Assets** build their depreciation schedule and post it entry by entry or by
  the nightly job. They pause and resume, change duration or salvage, take a
  revaluation (a gross increase, posted to the account you choose) or an
  impairment, and are disposed of by sale, scrap, donation or loss with the gain
  or loss posted.
* **Vendor bills** create assets by themselves when a line lands on an asset
  account, as drafts or already running, one per line or one per unit. A bill
  whose assets have posted depreciation cannot be reset or cancelled behind
  their back.
* **Custody and cover**: holder, location, serial, warranty and insurance dates
  with reminders before cover runs out; a QR label per asset.
* A **lifecycle timeline** and depreciation curve on every asset, a **dashboard**
  (book value by category, additions, disposals, charge, expiring cover), a
  **method simulator** and the **Depreciation Schedule** report.

## Access

| Odoo group | Can |
| --- | --- |
| Accounting / Read-only | Read assets, categories and schedules |
| Invoicing | Create and run assets, post depreciation |
| Accounting / Administrator | Categories, groups, delete |

## Tests

```
odoo-bin -c <conf> -d <db> -u ebshel_account_assets --test-enable \
    --test-tags /ebshel_account_assets --stop-after-init --http-port=<free port>
```

## License

OPL-1 (see `LICENSE`). © Ebshel Technologies.
