# -*- coding: utf-8 -*-
"""The rule that decides which v17 system parameters are worth carrying."""
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestConfigParameters(TransactionCase):

    def setUp(self):
        super().setUp()
        self.backend = self.env['migration.backend']

    def test_a_setting_for_an_uninstalled_module_is_not_carried(self):
        """It is meaningless, and it is a landmine.

        `set_param` writes a row with no external id. When the module is later
        installed, its own data file tries to CREATE the same key and dies on
        the unique index - taking the whole install with it, not just that
        record. `crm.pls_fields` stopped the lab installing CRM.
        """
        crm = self.env['ir.module.module'].search([('name', '=', 'crm')], limit=1)
        if not crm or crm.state == 'installed':
            self.skipTest("this database has CRM installed; nothing to prove")
        self.assertTrue(self.backend._param_module_is_absent('crm.pls_fields'))

    def test_a_setting_for_an_installed_module_is_carried(self):
        """The lab's own value for something it actually runs is worth having."""
        self.assertFalse(self.backend._param_module_is_absent('sale.default_deposit'))
        self.assertFalse(self.backend._param_module_is_absent('base.login_cooldown_after'))

    def test_a_prefix_that_is_not_a_module_is_left_alone(self):
        """`database.` and `report.` are not modules; the deny lists own those."""
        self.assertFalse(self.backend._param_module_is_absent('database.uuid'))
        self.assertFalse(self.backend._param_module_is_absent('report.url'))
        self.assertFalse(self.backend._param_module_is_absent('no_dot_at_all'))
        self.assertFalse(self.backend._param_module_is_absent(''))
        self.assertFalse(self.backend._param_module_is_absent(None))

    def test_no_parameter_is_left_that_an_install_would_collide_with(self):
        """On this database, after the repair: none of the orphans name an
        uninstalled module."""
        self.env.cr.execute("""
            SELECT p.key FROM ir_config_parameter p
             WHERE NOT EXISTS (SELECT 1 FROM ir_model_data d
                                WHERE d.model = 'ir.config_parameter'
                                  AND d.res_id = p.id)
        """)
        landmines = [key for (key,) in self.env.cr.fetchall()
                     if self.backend._param_module_is_absent(key)]
        self.assertFalse(landmines,
                         "these will break the next install of their module: %s"
                         % landmines)
