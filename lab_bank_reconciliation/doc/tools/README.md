# Rebuilding the user guide

Not part of the Odoo module: the scripts that take the guide's screenshots on staging and
turn `doc/USER_GUIDE.md` into `doc/USER_GUIDE.pdf`.

1. **Screenshots** — with staging running on :8069 (admin login), run
   `/home/ebin-pg/odoo/instances/ebshel/venv/bin/python capture_screens.py`
   (that venv has Playwright). It replaces `doc/images/`. It works on a fresh
   CANERA BANK statement and deletes every statement, tick and query it created.
   Statements that already existed are only viewed.
2. **HTML** — `/usr/bin/python3 guide_html.py ../ /tmp/USER_GUIDE.html` (markdown-it
   is installed for `/usr/bin/python3` only; tables and images are inlined).
3. **PDF** — `/home/ebin-pg/odoo/instances/ebshel/venv/bin/python guide_pdf.py
   /tmp/USER_GUIDE.html ../USER_GUIDE.pdf` (Chrome's print to PDF, A4, page numbers).

Restart the server after changing the screen's JS or XML before taking screenshots:
the browser otherwise gets the old bundle.
