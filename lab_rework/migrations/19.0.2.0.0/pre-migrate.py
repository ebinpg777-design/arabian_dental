# -*- coding: utf-8 -*-
"""Fold the lab.rework document into the sale order it spawned.

A rework used to be its own record pointing at two orders (the original and the remake).
It is now just the remake order with `rework_origin_id` / `rework_reason_id` on it, so the
data is copied across BEFORE the old model's columns disappear; the model, its lines and
its menus go with the module upgrade.
"""
import logging

_logger = logging.getLogger(__name__)


def _table(cr, name):
    cr.execute("SELECT 1 FROM information_schema.tables WHERE table_name = %s", (name,))
    return bool(cr.fetchone())


def migrate(cr, version):
    if not version or not _table(cr, 'lab_rework'):
        return
    # new columns may not exist yet (fields are created after pre-migrate) - add them here
    for column, ddl in (('rework_origin_id', 'integer'), ('rework_reason_id', 'integer'),
                        ('rework_responsibility', 'varchar'), ('rework_note', 'varchar')):
        cr.execute("ALTER TABLE sale_order ADD COLUMN IF NOT EXISTS %s %s" % (column, ddl))
    cr.execute("""
        UPDATE sale_order so
           SET rework_origin_id = r.origin_sale_order_id,
               rework_reason_id = r.reason_id,
               rework_responsibility = r.responsibility,
               rework_note = left(COALESCE(r.description, ''), 255),
               is_rework = TRUE
          FROM lab_rework r
         WHERE r.rework_sale_order_id = so.id
    """)
    _logger.info("reworks folded into their sale orders: %s", cr.rowcount)
    # reworks that never spawned an order (draft / cancelled) are reported once and left
    cr.execute("SELECT count(*) FROM lab_rework WHERE rework_sale_order_id IS NULL")
    orphans = cr.fetchone()[0]
    if orphans:
        _logger.info("%s rework record(s) had no order and are dropped with the model", orphans)
