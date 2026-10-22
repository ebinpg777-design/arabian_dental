# -*- coding: utf-8 -*-
"""The old desks' figures are the managers'.

The desk screens are gone (2026-10-20); the methods stay, read the whole field force
as superuser, and are callable over RPC by anyone logged in. (2026-10-22)
"""
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestDeskAccess(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        base = cls.env.ref('base.group_user')
        cls.executive = Users.create({
            'name': 'Desk Gate Exec', 'login': 'desk.gate.exec@test',
            'group_ids': [(6, 0, [base.id, cls.env.ref('lab_fieldwork.group_fieldwork_executive').id])]})
        cls.ops = Users.create({
            'name': 'Desk Gate Ops', 'login': 'desk.gate.ops@test',
            'group_ids': [(6, 0, [base.id, cls.env.ref('lab_fieldwork.group_fieldwork_ops_manager').id])]})

    def test_an_executive_cannot_fetch_the_desks(self):
        Desk = self.env['lab.desk'].with_user(self.executive)
        for method in ('get_ops_desk', 'get_marketing_desk', 'get_admin_desk'):
            with self.subTest(method=method), self.assertRaises(AccessError):
                getattr(Desk, method)()

    def test_a_manager_still_can(self):
        self.assertIn('queue', self.env['lab.desk'].with_user(self.ops).get_ops_desk())

    def test_one_executives_wizard_is_not_anothers(self):
        """Wizard rows are ordinary records in Odoo 20 - no creator-only rule - so an
        executive could read another's cash entry, or a case's patient, until the
        vacuum took it. (2026-10-22)"""
        other = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Desk Gate Exec Two', 'login': 'desk.gate.exec2@test',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('lab_fieldwork.group_fieldwork_executive').id])]})
        clinic = self.env['res.partner'].create({'name': 'Wizard Clinic'})
        # sudo keeps the user: the rows are the executive's, as the dialogs make them
        cash = self.env['lab.collect.cash'].with_user(self.executive).sudo().create(
            {'partner_id': clinic.id, 'amount': 500.0})
        entry = self.env['lab.case.quick.entry'].with_user(self.executive).sudo().create(
            {'partner_id': clinic.id})
        for record in (cash, entry):
            with self.subTest(model=record._name):
                Model = self.env[record._name]
                self.assertEqual(record.create_uid, self.executive)
                self.assertEqual(
                    Model.with_user(self.executive).search([('id', '=', record.id)]).ids, record.ids)
                self.assertFalse(Model.with_user(other).search([('id', '=', record.id)]))
