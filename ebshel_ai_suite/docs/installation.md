# Installation

## Requirements

* Odoo **19.0 Community** (no Enterprise module is needed or used).
* Modules `base`, `web`, `mail` (installed automatically).
* Python `requests` (already required by Odoo). PDF extraction uses the PDF library Odoo
  itself ships with (`PyPDF2`/`pypdf`).
* Outbound HTTPS from the Odoo server to your AI provider, unless you use a local engine.

## Install

```bash
# add the directory containing ebshel_ai_suite to --addons-path, then:
./odoo-bin -d test_ai_db -i ebshel_ai_suite --stop-after-init
```

or from **Apps**, search "Ebshel AI Suite".

## Upgrade

```bash
./odoo-bin -d test_ai_db -u ebshel_ai_suite --stop-after-init
```

Upgrades never overwrite your configuration: shipped records (assistant, capabilities,
prompt templates, privacy rules, crons, default parameters) are declared `noupdate`, and
conversations, connections (including stored keys), usage and audit data are kept.
Future schema changes will ship migration scripts under `migrations/<version>/`.

## After installing

1. Assign the **Ebshel AI User** group to the people who should use AI (Settings →
   Users → *Ebshel AI*). Only the administrator gets access automatically.
2. Create a connection (see [configuration](configuration.md)).
3. Optional: add knowledge sources and search targets, adapt the Business Assistant.

## Running the tests

```bash
./odoo-bin -d test_ai_db -i ebshel_ai_suite --test-enable \
    --test-tags /ebshel_ai_suite --stop-after-init
```

The browser tours additionally need Chrome or Chromium on the `PATH` and
`pip install websocket-client`. No API key or network access is required.

## Production notes

* Run Odoo with workers (`--workers`) so that long AI calls and streaming responses do not
  block other users. Each streaming answer keeps one worker busy while it is generated;
  size `--workers` and `--limit-time-real` accordingly (a slow model may need 120 s+).
* If a reverse proxy buffers responses, disable buffering for `/ebshel_ai/stream`
  (the endpoint already sends `X-Accel-Buffering: no` for nginx).
* Keep the default *Allow Private Network URLs* setting off unless you know why you need it.
