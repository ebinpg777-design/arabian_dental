# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
from . import controllers
from . import models
from . import report
from . import wizard


def post_init_hook(env):
    """Queue the first build of the daily tables and backfill planned
    consumption on the manufacturing orders that already exist."""
    env['stock.move']._asr_backfill_planned_qty()
    env['asr.stock.dirty']._mark_full_rebuild(env['res.company'].sudo().search([]))
