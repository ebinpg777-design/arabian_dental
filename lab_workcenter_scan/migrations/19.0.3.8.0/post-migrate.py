# -*- coding: utf-8 -*-
"""Let the jobs that were closed and re-opened be closed again.

Every one of the 219 jobs carrying `lab_close_failed` on this database failed
for the same reason, and it was ours: the close worked, and then the write that
puts the job's real arrival date back raised "You cannot move a manufacturing
order once it is cancelled or done" — inside the savepoint, so the close was
rolled back with it. The flag means "a person is needed here", which was never
true of these.

The flag is cleared, not the jobs closed: the delivery path closes each one the
next time its case is delivered, and the catch-up sweep takes the rest in its own
time. Anything that genuinely needs a person will be flagged again on the next
attempt, this time truthfully. (client, 2026-09-09)
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        UPDATE mrp_production SET lab_close_failed = FALSE
         WHERE lab_close_failed = TRUE
    """)
    _logger.info("auto-close: %s job(s) put back in the sweep", cr.rowcount)
