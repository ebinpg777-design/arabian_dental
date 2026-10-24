# -*- coding: utf-8 -*-
"""A model is an arch (2026-10-23) - repaired before the new checks are created.

The plate line now carries two database checks: only a failed model says which arch
failed, and a plate holding one arch of a patient can only have lost that arch. Old
rows broke both in small ways - Print Failed wrote 'All' on lines that held only the
upper, and nothing stopped a lower-only row being marked 'Upper' - so they are put
right first: a check that finds a bad row when it is created is skipped, not enforced.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        UPDATE lab_scan_print_order_line SET failed_part = part
         WHERE result = 'failed' AND part IN ('upper', 'lower') AND failed_part IS DISTINCT FROM part""")
    single = cr.rowcount
    cr.execute("""
        UPDATE lab_scan_print_order_line SET failed_part = 'all'
         WHERE result = 'failed' AND part = 'both' AND failed_part IS NULL""")
    both = cr.rowcount
    cr.execute("""
        UPDATE lab_scan_print_order_line SET failed_part = NULL
         WHERE result IS DISTINCT FROM 'failed' AND failed_part IS NOT NULL""")
    stray = cr.rowcount
    _logger.info("scan register 20.0.3.0.0: failed arch repaired on %d one-arch line(s), set on %d "
                 "both-arch line(s), cleared on %d line(s) that did not fail", single, both, stray)
