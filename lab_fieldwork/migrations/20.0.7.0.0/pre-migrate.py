# -*- coding: utf-8 -*-
"""Field cash leaves the petty_cash module (client, 2026-10-22).

Until 20.0.7.0.0 an executive's cash lived on petty_cash floats, as two movement
types this module added to petty.cash.transaction. This version stops extending that
model, and Odoo then deletes the movements of those types - so they are copied aside
here first, with the links visits, door collections and handovers held to them. The
links are cleared: their columns are about to point at lab.cash.movement instead.
post-migrate.py books them there.
"""


def migrate(cr, version):
    cr.execute("SELECT to_regclass('petty_cash_transaction'), to_regclass('petty_cash_allocation')")
    if not all(cr.fetchone()):
        return
    cr.execute("""
        CREATE TABLE fw_mig_cash AS
        SELECT t.id, t.type, t.state, t.amount, t.date, t.partner_id, t.visit_id,
               t.handover_id, t.company_id, t.note, a.partner_id AS holder_partner_id
          FROM petty_cash_transaction t
          JOIN petty_cash_allocation a ON a.id = t.allocation_id
         WHERE t.type IN ('collection', 'handover')
    """)
    for table, column in (('lab_visit', 'cash_txn_id'),
                          ('lab_cash_collection', 'cash_txn_id'),
                          ('lab_cash_handover', 'txn_id')):
        cr.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS fw_mig_{column} integer")
        cr.execute(f"UPDATE {table} SET fw_mig_{column} = {column}, {column} = NULL "
                   f"WHERE {column} IS NOT NULL")
