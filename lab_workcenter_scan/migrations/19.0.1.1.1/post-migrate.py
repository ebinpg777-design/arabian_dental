# -*- coding: utf-8 -*-
"""Switch this module's own record rules back on.

Both of them were found inactive on the client's database — along with 268 others,
core rules included, all deactivated in one sweep on 2026-08-14 (an access-management
tool that was later uninstalled, leaving its handiwork behind). Reloading the module
does not undo it: the XML never sets `active`, so an existing rule keeps whatever flag
it was left with, and "Bench user (own work centres only)" quietly stopped restricting
anything at all.

Only this module's two rules are touched, and only when they are off. The wider damage
is the lab's to decide about — reactivating 268 rules changes what a great many people
can see, and that is not a migration's call to make.
"""
RULES = (
    'lab_workcenter_scan.rule_workorder_own_bench',
    'lab_workcenter_scan.rule_workcenter_own_bench',
)


def migrate(cr, version):
    if not version:
        return
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    for xml_id in RULES:
        rule = env.ref(xml_id, raise_if_not_found=False)
        if rule and not rule.active:
            rule.active = True
