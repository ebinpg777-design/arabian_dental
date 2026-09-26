# -*- coding: utf-8 -*-
"""Drop the settings the migration carried over for modules nobody installed.

`_sync_configuration` copied the v17 system parameters with `set_param`, which
is the right API for a runtime value and writes a row with NO external id. For
a module that is installed here, that is fine and the value is worth having.

For a module that is NOT installed, it is a landmine. The setting means nothing
on its own, and the day somebody installs that module its data file tries to
CREATE the same key and hits the unique index:

    psycopg2.errors.UniqueViolation: duplicate key value violates unique
    constraint "ir_config_parameter_key_uniq"
    DETAIL: Key (key)=(crm.pls_fields) already exists.

which takes down the whole install, not just that record. Two of these -
`crm.pls_fields` and `crm.pls_start_date` - stopped the lab installing CRM on
2026-09-26. Two more of the same class were waiting behind them.

So: delete any parameter that has no external id and whose key names a module
this database has not installed. Once the module is installed, its own data
file writes the key properly, owned and with its default.

`_param_module_is_absent` on the engine is the same rule, applied at copy time
so these are never created again.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    backend = env['migration.backend']

    cr.execute("""
        SELECT p.id, p.key
          FROM ir_config_parameter p
         WHERE NOT EXISTS (
                   SELECT 1 FROM ir_model_data d
                    WHERE d.model = 'ir.config_parameter'
                      AND d.res_id = p.id)
    """)
    orphans = cr.fetchall()
    doomed = [(pid, key) for pid, key in orphans
              if backend._param_module_is_absent(key)]
    if not doomed:
        _logger.info("lab_migration: no orphan parameters for absent modules")
        return

    env['ir.config_parameter'].sudo().browse([pid for pid, _ in doomed]).unlink()
    _logger.info("lab_migration: dropped %s settings for modules that are not "
                 "installed: %s", len(doomed), ', '.join(key for _, key in doomed))
