# -*- coding: utf-8 -*-
"""Give the 2026-08-18 recompute's victims their salesperson back.

A module upgrade that day re-registered ``_compute_invoice_default_sale_person``
and batch-recomputed ``invoice_user_id`` across the ledger. Inside a batch
recompute the field being computed reads as empty, so the "only fill blanks"
guard passed and the route's Team Leader was stamped over the real salesperson —
324 July invoices, ₹3,45,680 of one TVM salesperson's bookings among them.

The repair restores the ORIGINATING SALE ORDER's salesperson, and only where all
three marks of the accident line up: the invoice was (over)written that day, it
currently credits exactly its route's leader, and its own order says someone
else. A leader who genuinely booked the work, or a hand-picked salesperson from
any other day, is left untouched.
"""


def migrate(cr, version):
    cr.execute("""
        WITH order_user AS (
            SELECT DISTINCT ON (l.move_id) l.move_id, s.user_id
              FROM account_move_line l
              JOIN sale_order_line_invoice_rel r ON r.invoice_line_id = l.id
              JOIN sale_order_line sol ON sol.id = r.order_line_id
              JOIN sale_order s ON s.id = sol.order_id
             WHERE s.user_id IS NOT NULL
             GROUP BY l.move_id, s.user_id
             ORDER BY l.move_id, COUNT(*) DESC, s.user_id
        )
        UPDATE account_move m
           SET invoice_user_id = ou.user_id
          FROM order_user ou, crm_team t
         WHERE ou.move_id = m.id
           AND t.id = m.team_id
           AND m.move_type IN ('out_invoice', 'out_refund')
           AND m.write_date::date = '2026-08-18'
           AND m.invoice_user_id = t.user_id
           AND m.invoice_user_id != ou.user_id
        RETURNING m.id
    """)
    repaired = cr.rowcount
    if repaired:
        cr.execute("""
            INSERT INTO ir_logging
                   (create_date, create_uid, name, type, dbname, level,
                    message, path, func, line)
            SELECT now() at time zone 'UTC', 1, 'sale_custom', 'server',
                   current_database(), 'INFO',
                   'restored the order salesperson on ' || %s ||
                   ' invoices leader-stamped by the 2026-08-18 recompute',
                   'sale_custom/migrations/19.0.1.16.0/post-migrate.py',
                   'migrate', '0'
        """, [str(repaired)])
