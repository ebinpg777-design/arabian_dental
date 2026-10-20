# -*- coding: utf-8 -*-
"""Apps on the home screen follow the access rights (client, 2026-10-20)."""
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestAppsFollowRights(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.contacts = cls.env.ref('contacts.menu_contacts', raise_if_not_found=False)
        cls.dashboards = cls.env.ref('spreadsheet_dashboard.spreadsheet_dashboard_menu_root', raise_if_not_found=False)
        cls.plain = new_test_user(cls.env, login='af_plain', groups='base.group_user')
        cls.admin = new_test_user(cls.env, login='af_admin', groups='base.group_user,base.group_system')

    def _hidden(self, user):
        return set(self.env['ir.ui.menu'].with_user(user)._load_menus_blacklist())

    def test_an_app_open_to_everybody_is_hidden_without_its_right(self):
        if not self.contacts:
            self.skipTest("contacts not installed")
        self.assertIn(self.contacts.id, self._hidden(self.plain))

    def test_granting_the_right_shows_the_app(self):
        if not self.contacts:
            self.skipTest("contacts not installed")
        self.plain.write({'group_ids': [(4, self.env.ref('base.group_partner_manager').id)]})
        self.assertNotIn(self.contacts.id, self._hidden(self.plain))

    def test_dashboards_stay_for_everybody(self):
        if not self.dashboards:
            self.skipTest("spreadsheet_dashboard not installed")
        self.assertNotIn(self.dashboards.id, self._hidden(self.plain))

    def test_an_administrator_keeps_every_app(self):
        if not self.contacts:
            self.skipTest("contacts not installed")
        self.assertNotIn(self.contacts.id, self._hidden(self.admin))

    def test_the_rule_can_be_switched_off(self):
        if not self.contacts:
            self.skipTest("contacts not installed")
        self.env['ir.config_parameter'].sudo().set_str('lab_access_control.apps_follow_rights', 'False')
        self.assertNotIn(self.contacts.id, self._hidden(self.plain))
