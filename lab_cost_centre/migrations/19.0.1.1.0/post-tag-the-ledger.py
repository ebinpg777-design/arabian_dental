# -*- coding: utf-8 -*-
"""Put a cost centre on the journal items that already carry a department.

The departments were back-filled onto the ledger without touching its analytic
side, deliberately: re-tagging entries somebody has closed is the lab's call,
not a script's. The lab has now made that call, so this does it.

What it tags
------------
Posted journal items that have a department and sit on a PROFIT AND LOSS
account. That is the whole of analytic accounting: income, expense, direct
cost. On this database that is 87,218 lines across 9 cost centres.

What it leaves alone, and why it matters
----------------------------------------
Nine thousand six hundred lines have a department and sit on a BALANCE SHEET
account - "Inventories" and "GRN - Not billed", 9.6 million rupees of stock
movement. Tagging those would post the same material to a cost centre twice:
once when it arrives in stock, and again on the Cost of Goods Sold line when it
is consumed. A cockpit that double-counts its own material is worse than one
that shows nothing, so the balance sheet stays out.

Nothing else needs excluding: receivable, payable, tax, cash and equity lines
never got a department in the first place, because a department is derived from
the product and those lines have none.

How
---
Through the ORM, one write per cost centre. Writing `analytic_distribution` on
a posted line fires `_inverse_analytic_distribution`, which is what creates the
analytic items - the point of the exercise. It is allowed on a posted line
because it changes no debit and no credit: the field is in neither
`_get_lock_date_protected_fields` nor the integrity hash.

Idempotent: only lines with no distribution are touched, so running it twice
tags nothing the second time.
"""
import logging
import time

_logger = logging.getLogger(__name__)

# Big enough that the per-batch overhead disappears, small enough that the
# cache of a batch and its new analytic items fits comfortably.
BATCH = 500


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    Line = env['account.move.line']

    departments = env['hr.department'].search([('cost_centre_id', '!=', False)])
    if not departments:
        _logger.info("lab_cost_centre: no department has a cost centre; nothing to tag")
        return

    started = time.time()
    tagged = 0
    for department in departments:
        centre = department.cost_centre_id
        # The rule lives on the model, not here: a migration is run once and
        # never read again, and this is a rule the lab will want to apply to
        # stragglers later. See `account.move.line._lab_taggable_domain`.
        domain = Line._lab_taggable_domain(department)
        if not Line.search_count(domain):
            continue
        done = 0
        while True:
            # Re-searched rather than paged by id: a written line drops out of
            # this domain, so the next search returns the next untagged ones.
            # It also makes the migration restartable - if it dies half way,
            # running it again picks up exactly where it stopped.
            lines = Line.search(domain, limit=BATCH)
            if not lines:
                break
            lines.write({'analytic_distribution': {str(centre.id): 100.0}})
            done += len(lines)
            env.cr.commit()
            env.invalidate_all()
        tagged += done
        _logger.info("lab_cost_centre: %s - %s journal items now post to %s",
                     department.code or department.name, done, centre.code or centre.name)

    items = env['account.analytic.line'].search_count([])
    _logger.info("lab_cost_centre: tagged %s journal items in %.0fs; "
                 "the ledger now carries %s analytic items",
                 tagged, time.time() - started, items)
