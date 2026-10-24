# -*- coding: utf-8 -*-
"""A model is an arch (2026-10-23) - after the new code loads.

* Partly Failed is now judged in models: a plate whose only patient lost the lower
  printed the upper. The column is not new, so it is recomputed here by hand (the new
  model counts beside it are computed by the update itself).
* The registers' printing status is read again: the patients' new To Print and
  Failed, Not Printed Yet come from the same plate lines.
* The seeded Failed Print tile is noupdate; it now shows a register with a model
  failed and not printed yet in Print In Progress too - where it still carries the
  seed domain (3.4.0 pattern), so a tile the lab edited is left as the lab made it.
* A plate that failed while one of its patients' arches printed is left as recorded
  (no cure that never happened is written); its printed arch counts from now on, and
  how many there are is logged.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

OLD = "[('state', '=', 'printing'), ('failed_print_count', '>', 0)]"
NEW = "[('state', 'in', ('printing', 'in_progress')), ('replate_pending_count', '>', 0)]"


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Order = env['lab.scan.print.order']
    plates = Order.with_context(active_test=False).search([])
    env.add_to_compute(Order._fields['is_partial'], plates)
    env.flush_all()

    Scan = env['lab.scan']
    Scan.search([('state', 'in', ('printing', 'in_progress', 'printed'))])._sync_print_state()
    env.flush_all()

    tile = env.ref('lab_scan_register.tile_scan_print_failed', raise_if_not_found=False)
    if tile and (tile.domain or '').strip() == OLD:
        tile.domain = NEW

    printed_on_failed = Order.search_count([('state', '=', 'failed'), ('printed_models', '>', 0)])
    _logger.info("scan register 20.0.3.0.0: %d failed plate(s) printed an arch; counted as printed models "
                 "from now on, the plates left as recorded", printed_on_failed)
