# -*- coding: utf-8 -*-
"""Unplan the steps nobody started (2026-11-23).

Confirming an order planned its manufacturing orders: every step became a block on its
work centre's calendar, and each new order searched past all the blocks already there.
Nothing in this lab releases them - the jobs run off scans and close with the delivery -
so live held 82,232 blocks reaching May 2027 and a confirmation took up to 15 s.

The steps still waiting (blocked or ready) of open orders lose their plan exactly as
core's own "Unplan" does (mrp.workorder.action_unplan: the calendar block deleted, the
planned start and end cleared); their orders read "not planned". Started, finished and
cancelled steps keep their dates. Done in SQL: tens of thousands of rows.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        CREATE TEMP TABLE lab_unplanned_steps AS
        SELECT wo.id, wo.leave_id, wo.production_id
          FROM mrp_workorder wo
          JOIN mrp_production mo ON mo.id = wo.production_id
         WHERE wo.state IN ('blocked', 'ready')
           AND mo.state IN ('confirmed', 'progress', 'to_close')
           AND (wo.leave_id IS NOT NULL OR wo.date_start IS NOT NULL OR wo.date_finished IS NOT NULL)
    """)
    cr.execute("""
        UPDATE mrp_workorder wo
           SET leave_id = NULL, date_start = NULL, date_finished = NULL
          FROM lab_unplanned_steps u
         WHERE wo.id = u.id
    """)
    steps = cr.rowcount
    cr.execute("""
        DELETE FROM resource_calendar_leaves
         WHERE id IN (SELECT leave_id FROM lab_unplanned_steps WHERE leave_id IS NOT NULL)
    """)
    blocks = cr.rowcount
    cr.execute("""
        UPDATE mrp_production mo
           SET is_planned = FALSE
         WHERE mo.is_planned
           AND mo.id IN (SELECT production_id FROM lab_unplanned_steps)
    """)
    orders = cr.rowcount
    cr.execute("DROP TABLE lab_unplanned_steps")
    _logger.info("sale_custom: %s waiting steps unplanned, %s calendar blocks removed, %s orders not planned",
                 steps, blocks, orders)
