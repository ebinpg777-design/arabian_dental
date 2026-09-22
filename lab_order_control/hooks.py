# -*- coding: utf-8 -*-
"""Seed which roles are subject to maker-checker verification.

Defaults to the field-work executive group — the people who register cases out at a
clinic, and the only ones whose entries a second pair of eyes actually adds anything to.
Looked up by name rather than declared as a dependency: this module works without
`lab_fieldwork`, and where that module is absent the setting simply stays empty, which
means "check everyone" — the behaviour from before this was scoped.

Only seeded when unset, so a lab that has since chosen its own roles keeps them.
"""
import logging

_logger = logging.getLogger(__name__)

PARAM = 'lab_order_control.verification_maker_groups'
DEFAULT_GROUP = 'lab_fieldwork.group_fieldwork_executive'


def post_init_hook(env):
    # Sales Routes are a search panel now, not tiles (models/crm_team_tiles.py)
    env['crm.team']._lab_remove_route_tiles()
    env['crm.team']._lab_pin_invoice_tiles()
    params = env['ir.config_parameter'].sudo()
    if params.get_param(PARAM):
        return
    group = env.ref(DEFAULT_GROUP, raise_if_not_found=False)
    if not group:
        _logger.info("lab_order_control: %s not installed — verification will apply "
                     "to every order until roles are chosen in Settings", DEFAULT_GROUP)
        return
    params.set_param(PARAM, str(group.id))
    _logger.info("lab_order_control: verification scoped to %r", group.name)
