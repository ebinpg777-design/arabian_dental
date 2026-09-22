# -*- coding: utf-8 -*-
from . import controllers
from . import models
from . import wizard


def post_init_hook(env):
    """Give every EXISTING work centre a scan code.

    The create() override only covers stations made after this module is installed, so
    without this the feature is dead on arrival in any real database: every station that
    already exists — which is all of them — would have a blank code and answer to no
    scan at all.
    """
    workcenters = env['mrp.workcenter'].with_context(active_test=False).search([])
    assigned = workcenters.action_assign_scan_codes()
    if assigned:
        env['ir.logging'].sudo().create({
            'name': 'lab_workcenter_scan', 'type': 'server', 'level': 'INFO',
            'dbname': env.cr.dbname, 'message': 'Assigned %s work centre scan codes'
                                                % assigned,
            'path': 'post_init_hook', 'func': 'post_init_hook', 'line': '0',
        })
