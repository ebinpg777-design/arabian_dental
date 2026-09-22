# Rebuilding the user guide

Not part of the Odoo module: the scripts that turn `doc/USER_GUIDE.md` into
`doc/USER_GUIDE.pdf`.

1. **HTML** — `/usr/bin/python3 guide_html.py ../ /tmp/WHATSAPP_GUIDE.html`
   (markdown-it is installed for `/usr/bin/python3` only; tables are kept and any
   images are inlined as base64).
2. **PDF** — `/home/ebin-pg/odoo/instances/ebshel/venv/bin/python guide_pdf.py
   /tmp/WHATSAPP_GUIDE.html ../USER_GUIDE.pdf` (Chrome's print to PDF, A4, page
   numbers in the footer).

The guide has no screenshots: the setup screens hold live tokens and secrets, so
it describes the fields instead of picturing them.
