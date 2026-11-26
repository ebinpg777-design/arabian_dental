# -*- coding: utf-8 -*-
"""The product categories of every order, filled in one statement.

`sale.order.product_categ_ids` is a stored computed many2many. Left to itself the
ORM would work it out order by order when the field first appears - eighty
thousand orders, with the site down while it does. A table that is already there
is not computed again, so it is made and filled here, before the field is loaded:
the same rows the compute gives, the categories of the products on the order's
lines. The ORM adds the foreign keys when it meets the table."""


def migrate(cr, version):
    cr.execute("SELECT to_regclass('sale_order_product_categ_rel')")
    if cr.fetchone()[0]:
        return
    cr.execute("""
        CREATE TABLE sale_order_product_categ_rel (
            order_id INTEGER NOT NULL,
            categ_id INTEGER NOT NULL,
            PRIMARY KEY (order_id, categ_id))
    """)
    cr.execute("""
        INSERT INTO sale_order_product_categ_rel (order_id, categ_id)
        SELECT DISTINCT line.order_id, template.categ_id
          FROM sale_order_line line
          JOIN product_product product ON product.id = line.product_id
          JOIN product_template template ON template.id = product.product_tmpl_id
         WHERE template.categ_id IS NOT NULL
    """)
    cr.execute("CREATE INDEX ON sale_order_product_categ_rel (categ_id, order_id)")
