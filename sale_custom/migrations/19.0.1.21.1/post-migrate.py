# -*- coding: utf-8 -*-
"""Fill `account_move.product_names` on the invoices that already exist.

Odoo computes a newly added stored field for records written afterwards, not for the
20,485 invoices already on the books — so without this the statement of account, which
now reads this column for its Description, would show the item names on new invoices
and nothing at all on the whole history.

Done in SQL rather than by recomputing through the ORM: the compute walks every invoice
line of every invoice, and there are ~250,000 of them. This is the same answer — the
distinct product names, in the order they first appear on the invoice — in one pass.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'account_move' AND column_name = 'product_names'
    """)
    if not cr.fetchone():
        return

    # MIN(line id) per (invoice, name) reproduces "the order they first appear on it",
    # and grouping by the name itself is what makes each work named once.
    cr.execute("""
        WITH items AS (
            SELECT l.move_id                       AS move_id,
                   COALESCE(pt.name ->> 'en_US',
                            pt.name ->> 'en_GB')   AS item_name,
                   MIN(l.id)                       AS first_line
              FROM account_move_line l
              JOIN product_product p  ON p.id = l.product_id
              JOIN product_template pt ON pt.id = p.product_tmpl_id
             WHERE l.display_type = 'product'
               AND l.product_id IS NOT NULL
             GROUP BY 1, 2
        )
        UPDATE account_move m
           SET product_names = agg.names
          FROM (
                SELECT move_id,
                       string_agg(item_name, ', ' ORDER BY first_line) AS names
                  FROM items
                 WHERE item_name IS NOT NULL AND item_name <> ''
                 GROUP BY move_id
               ) agg
         WHERE m.id = agg.move_id
           AND m.product_names IS DISTINCT FROM agg.names
    """)
    _logger.info("sale_custom: named the items on %s invoices for the statement",
                 cr.rowcount)
