# -*- coding: utf-8 -*-
"""Repair the Waiting work-order tile: it matched no state at all.

Its domain asked for `state in ('pending', 'waiting')`. A work order in Odoo 19
is blocked, ready, progress, done or cancel - there is no pending and no
waiting - so the tile read 0 on a floor with 5,802 blocked operations, which
is worse than not having it: a zero looks like an answer.

The tile data is `noupdate`, because a manager may recolour and reorder tiles
from the Tile Studio and an upgrade must not undo that. So the repair is done
here, and ONLY where the tile still carries the broken domain we shipped:
anybody who has already changed it keeps their version.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)

SHIPPED_BROKEN = "[('state','in',['pending','waiting'])]"
FIXED = "[('state','=','blocked')]"


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    _headline_done_today(env)
    _headline_finished(env)
    tile = env.ref('lab_order_control.tile_wo_waiting', raise_if_not_found=False)
    if not tile:
        return
    current = (tile.domain or '').replace(' ', '')
    if current != SHIPPED_BROKEN.replace(' ', ''):
        _logger.info("Waiting tile has been edited (%s) - left alone", tile.domain)
        return
    tile.domain = FIXED
    _logger.info("Waiting work-order tile repaired: %s -> %s", SHIPPED_BROKEN, FIXED)


DONE_TODAY = "[('state','=','done'),('date_finished','>=', context_today().strftime('%Y-%m-%d'))]"


def _headline_done_today(env):
    """Done Today has the same fault as Finished: every work-order screen
    defaults to "to do", so a count of finished work inside the reader's own
    filters is 0 whatever the floor did. It is a headline figure - it counts
    the screen, not the slice - so it is marked as one, where nobody has
    edited it."""
    tile = env.ref('lab_order_control.tile_wo_done_today', raise_if_not_found=False)
    if not tile or tile.ignore_filters:
        return
    if (tile.domain or '').replace(' ', '') != DONE_TODAY.replace(' ', ''):
        _logger.info("Done Today tile has been edited - left alone")
        return
    tile.ignore_filters = True
    _logger.info("Done Today work-order tile now counts regardless of the filters")


def _headline_finished(env):
    """The two Finished tiles count the screen, not the reader's slice of it.

    They are shipped that way, but a database that took this module's data
    between the tiles landing and the switch existing has them without it, and
    the data is `noupdate` so a later upgrade will not add it. Nobody can have
    turned a switch off before it existed, so it is simply set.
    """
    for xml_id in ('lab_order_control.tile_wo_finished',
                   'lab_order_control.tile_wo_finished_month'):
        tile = env.ref(xml_id, raise_if_not_found=False)
        if tile and not tile.ignore_filters:
            tile.ignore_filters = True
            _logger.info("%s now counts regardless of the filters on screen", xml_id)
