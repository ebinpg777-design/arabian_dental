# -*- coding: utf-8 -*-
"""Leave the house accounts off the leaderboards by default.

The accounts desk carries every sale and receipt nobody else was credited with, so
it came out #1 on both money boards on work it never sold - which is what the client
reported. OdooBot is the migration robot. Set by LOGIN rather than by id, and
undoable from the dashboard: this is a sensible default, not a rule.
"""
import logging

_logger = logging.getLogger(__name__)

DEFAULT_UNRANKED = ('account_manager', 'account_manager2', '__system__')


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE res_users SET lab_exclude_from_ranking = TRUE
         WHERE login IN %s AND lab_exclude_from_ranking IS NOT TRUE
     RETURNING login
    """, (DEFAULT_UNRANKED,))
    marked = [r[0] for r in cr.fetchall()]
    if marked:
        _logger.info("lab_collections: left off the leaderboards by default: %s",
                     ', '.join(marked))
