# -*- coding: utf-8 -*-
"""Give the migrated opening entries the date they actually carried.

Every opening-balance line is dated 2026-04-01 - the day the Odoo 10 position was
brought across, not the day the item arose - so a statement read as though a
doctor's whole history began on one April morning. The line's own label is the
original invoice number (OC098462 and so on), which is what makes the real date
recoverable.

Where the date comes from
-------------------------
``data/opening_dates.csv.gz``: 1,23,446 pairs of (invoice number, invoice date)
read out of the Odoo 10 database arabian_dental_v10_live, covering every OC-numbered
opening line but 215. It is shipped as data rather than read live, because the
Odoo 10 database sits on the staging server and is not something a production
upgrade can be made to depend on. The pairs are historical fact and will not
change again.

NOT ``date_maturity``, which the migration did preserve: that is the DUE date. It
matches Odoo 10's due date on 100% of these lines but the invoice date on only
98.6% - on 1,883 items credit terms moved the two apart (invoiced 29 Apr, due
1 May), and a statement column headed with the document's date must not quietly
show when it fell due instead.

The 1,787 "Balance carried forward (before 2023-04-01)" lines are left alone on
purpose: they are aggregates of many old documents, so there is no single date
they could honestly carry.
"""
import gzip
import logging

from odoo.tools import file_path

_logger = logging.getLogger(__name__)

DATA = 'epg_partner_statement/data/opening_dates.csv.gz'


def migrate(cr, version):
    if not version:
        return
    try:
        path = file_path(DATA)
    except FileNotFoundError:
        _logger.warning("epg_partner_statement: %s missing, opening dates not set", DATA)
        return
    with gzip.open(path, 'rt') as fh:
        pairs = [line.split(',', 1) for line in fh.read().splitlines() if ',' in line]
    if not pairs:
        return

    cr.execute("""CREATE TEMP TABLE epg_opening_dates
                  (name varchar PRIMARY KEY, doc_date date) ON COMMIT DROP""")
    # psycopg2's batched VALUES: 1,23,000 rows as single INSERTs would take minutes
    from psycopg2.extras import execute_values
    execute_values(cr._obj,
                   "INSERT INTO epg_opening_dates (name, doc_date) VALUES %s "
                   "ON CONFLICT (name) DO NOTHING",
                   pairs, page_size=5000)

    # Restricted to opening entries: a CURRENT invoice's receivable line is also
    # named with its invoice number, and those already carry the right date.
    # `original_date IS NULL` keeps it idempotent and never overwrites a correction.
    cr.execute("""
        UPDATE account_move_line l
           SET original_date = t.doc_date
          FROM epg_opening_dates t, account_move m
         WHERE m.id = l.move_id
           AND m.move_type = 'entry'
           AND left(m.ref, 15) = 'Opening Balance'
           AND l.name = t.name
           AND l.original_date IS NULL
    """)
    _logger.info("epg_partner_statement: dated %s migrated opening lines from their "
                 "Odoo 10 invoice date", cr.rowcount)
