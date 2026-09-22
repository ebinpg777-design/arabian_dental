# -*- coding: utf-8 -*-
"""Separate the three field roles that used to be stacked.

Dropping `implied_ids` from the XML is NOT enough, and the way it fails is quiet.

Odoo only writes the fields a data file mentions; it never resets one that has been
removed. So the Manager group keeps its stored link to Executive, every user who already
held Manager keeps the Executive group that link granted them, and every newly created
Manager gets it again on create. The security file reads as though the roles are
separate while the running system still stacks them — the menus stay exactly as they
were and nothing anywhere reports a problem.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    executive = env.ref('lab_fieldwork.group_fieldwork_executive',
                        raise_if_not_found=False)
    manager = env.ref('lab_fieldwork.group_fieldwork_manager',
                      raise_if_not_found=False)
    if not executive or not manager:
        return

    # 1. Break the stored implication, so new managers stop inheriting it.
    if executive in manager.implied_ids:
        manager.write({'implied_ids': [(3, executive.id)]})

    # 2. Take the inherited group off the people who only ever got it that way.
    #    A manager keeps their own work only if somebody deliberately gave them the
    #    Executive role as well, which after this migration has to be done on purpose.
    stacked = manager.all_user_ids & executive.all_user_ids
    if stacked:
        stacked.write({'group_ids': [(3, executive.id)]})
