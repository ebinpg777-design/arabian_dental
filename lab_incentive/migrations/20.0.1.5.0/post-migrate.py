# -*- coding: utf-8 -*-
"""Only billed amounts count now (client, 2026-10-08): 'confirmed' and 'delivered'
were taken off the setting, and a value the selection no longer has would break the
settings screen. Both become 'invoiced' (billed)."""


def migrate(cr, version):
    cr.execute("""
        UPDATE ir_config_parameter SET value = 'invoiced'
         WHERE key = 'lab_incentive.basis' AND value NOT IN ('invoiced', 'paid')
    """)
