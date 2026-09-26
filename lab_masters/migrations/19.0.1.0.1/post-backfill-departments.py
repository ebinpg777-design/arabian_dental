# -*- coding: utf-8 -*-
"""Give the lab's history the department it was always going to have.

`lab_cost_centre` adds `department_id` to every document and derives it from the
product's category, the location, or the bench. But it was installed before any
category or location HAD a department, so the compute ran once over seventy-one
thousand orders and stored nothing, and a stored computed field does not run
again by itself - nothing on the order changed, only the master data behind it.

So the lab's whole history sat outside the cost centres it was meant to be
inside. This asks the ORM to compute the field again, once, for the records that
are still blank.

Through `add_to_compute` rather than `write`: the ORM writes a computed value
directly, so `sale.order.line.write` never runs and nothing stamps an analytic
distribution onto a posted invoice. Attributing history for a report is one
thing; re-tagging a ledger somebody has closed is another, and that is the lab's
decision, not this script's.

Headers follow their lines on their own - `sale.order.department_id` depends on
`order_line.department_id` - so only the lines are asked for, and the headers are
checked afterwards.
"""
import logging
import time

_logger = logging.getLogger(__name__)

# Lines first, then anything whose department is its own. A batch of five
# thousand keeps the recompute's working set small on a laptop with the whole
# database already in memory; the measured rate is about seven thousand rows a
# second, so the whole history is a couple of minutes.
BATCH = 5000

LINES = [
    'sale.order.line',
    'purchase.order.line',
    'stock.move',
    'account.move.line',
]
DOCUMENTS = [
    'sale.order',
    'purchase.order',
    'stock.picking',
    'account.move',
    'mrp.production',
    'mrp.workorder',
    'material.request',
]


def _backfill(env, model):
    if model not in env:
        return
    Model = env[model].with_context(active_test=False)
    field = Model._fields.get('department_id')
    if not field or not field.store:
        return
    started = time.time()
    done = filled = 0
    last_id = 0
    # Paged by id, not by re-searching for blanks. Two thirds of the journal
    # items have no product at all - a tax line, a receivable - and will never
    # get a department, so a loop that re-reads "still blank" would hand itself
    # the same rows for ever.
    while True:
        records = Model.search(
            [('department_id', '=', False), ('id', '>', last_id)],
            order='id', limit=BATCH)
        if not records:
            break
        last_id = records[-1].id
        env.add_to_compute(field, records)
        records.flush_recordset(['department_id'])
        filled += Model.search_count(
            [('id', 'in', records.ids), ('department_id', '!=', False)])
        done += len(records)
        env.cr.commit()
    if done:
        _logger.info("lab_masters: %s - %s of %s rows got a department (%.1fs)",
                     model, filled, done, time.time() - started)


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    for model in LINES:
        _backfill(env, model)
    for model in DOCUMENTS:
        _backfill(env, model)
