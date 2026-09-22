# -*- coding: utf-8 -*-
"""Collect % and Sales target: whose numbers they are to move.

`crm.team` write already denies an ordinary salesperson/executive - but the
dashboard's inline edit box (pencil icon, click-to-type input) was offered to
every viewer regardless, so a route-meeting attendee without an Accounts Manager
seat clicked what looked like a normal editable cell and got a raw AccessError.
`can_edit_targets` is the flag the client hides the pencil behind; this pins the
one server-side fact the whole UI decision rests on. (client, 2026-08-29)
"""
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestTargetEditRights(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Perf = cls.env['lab.collection.performance']
        cls.route = cls.env['crm.team'].create({'name': 'Target Edit Route'})
        cls.exec_user = cls.env['res.users'].create({
            'name': 'Target Edit Exec', 'login': 'target.exec@test',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('sales_team.group_sale_salesman').id,
                # The screen is opened to a salesperson through Field Work; a login
                # holding no collections role at all is refused outright now
                # (test_access_gate).
                cls.env.ref('lab_fieldwork.group_fieldwork_executive').id,
            ])]})
        cls.manager_user = cls.env['res.users'].create({
            'name': 'Target Edit Manager', 'login': 'target.mgr@test',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('account.group_account_manager').id,
            ])]})

    def test_a_salesperson_is_not_offered_the_edit(self):
        data = self.Perf.with_user(self.exec_user).dashboard_data()
        self.assertFalse(data['can_edit_targets'])

    def test_an_accounts_manager_is_offered_the_edit(self):
        data = self.Perf.with_user(self.manager_user).dashboard_data()
        self.assertTrue(data['can_edit_targets'])

    def test_a_salesperson_still_cannot_write_the_fields_directly(self):
        """The flag reflects a real restriction, not a decoration: crm.team write
        must genuinely still be denied, whatever the flag says."""
        with self.assertRaises(AccessError):
            self.route.with_user(self.exec_user).write({'lab_collection_target': 60})
        with self.assertRaises(AccessError):
            self.route.with_user(self.exec_user).write({'lab_sales_target': 500000})

    def test_an_accounts_manager_can_actually_set_them_via_the_gated_setter(self):
        """crm.team's own ACL denies write to an Accounts Manager too - they are
        not a Sales Administrator - so this goes through set_team_setting,
        mirroring set_user_setting's existing fix for the person-level target."""
        self.Perf.with_user(self.manager_user).set_team_setting(
            self.route.id, {'lab_collection_target': 60, 'lab_sales_target': 500000})
        self.assertEqual(self.route.lab_collection_target, 60)
        self.assertEqual(self.route.lab_sales_target, 500000)

    def test_a_salesperson_cannot_use_the_setter_either(self):
        with self.assertRaises(AccessError):
            self.Perf.with_user(self.exec_user).set_team_setting(
                self.route.id, {'lab_collection_target': 60})
