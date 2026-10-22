# -*- coding: utf-8 -*-
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestLabPettyCash(TransactionCase):
    """Field Work's field cash and Ebshel Petty Cash, joined. (client, 2026-10-22)"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        G = cls.env.ref
        base = G('base.group_user').id
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        cls.exec_user = Users.create({
            'name': 'Box Exec', 'login': 'box_exec',
            'group_ids': [(6, 0, [base, G('lab_fieldwork.group_fieldwork_executive').id])]})
        cls.keeper = Users.create({
            'name': 'Box Keeper', 'login': 'box_keeper',
            'group_ids': [(6, 0, [base, G('ebshel_petty_cash.group_petty_cash_approver').id])]})
        cls.requester = Users.create({
            'name': 'Box Requester', 'login': 'box_requester',
            'group_ids': [(6, 0, [base, G('ebshel_petty_cash.group_petty_cash_user').id])]})
        cls.clinic = cls.env['res.partner'].create({'name': 'Box Clinic'})
        cls.Move = cls.env['lab.cash.movement']

    def _declared(self, amount=600.0):
        self.Move._collect(self.exec_user, self.clinic, amount)
        handover = self.env['lab.cash.handover'].with_user(self.exec_user).create(
            {'user_id': self.exec_user.id, 'amount': amount})
        handover.action_declare()
        return handover

    def test_the_petty_cash_box_counts_field_cash_in(self):
        """As the old petty_cash module's officer did: now an Approver of Petty
        Cash - who sees every fund - not everybody who may raise a voucher."""
        handover = self._declared()
        with self.assertRaises(UserError):
            handover.with_user(self.requester).action_confirm_receipt()
        wizard = self.env['lab.cash.receive'].with_user(self.keeper).create(
            {'code': handover.code})
        wizard._onchange_code()
        wizard.action_confirm()
        self.assertEqual(handover.state, 'received')
        self.assertEqual(handover.received_by_id, self.keeper)
        self.exec_user.invalidate_recordset(['fw_cash_in_hand'])
        self.assertEqual(self.exec_user.fw_cash_in_hand, 0.0)

    def test_the_box_sees_the_executives_field_cash(self):
        self.Move._collect(self.exec_user, self.clinic, 250.0)
        moves = self.Move.with_user(self.keeper).search([('user_id', '=', self.exec_user.id)])
        self.assertEqual(moves.mapped('amount'), [250.0])

    def test_the_field_cash_menu_is_in_the_petty_cash_app(self):
        menu = self.env.ref('lab_petty_cash.menu_field_cash')
        self.assertEqual(menu.parent_id, self.env.ref('ebshel_petty_cash.menu_root'))
        Menu = self.env['ir.ui.menu']
        self.assertIn(menu.id, Menu.with_user(self.keeper)._visible_menu_ids())
        self.assertNotIn(menu.id, Menu.with_user(self.requester)._visible_menu_ids())

    def test_the_management_board_adds_the_petty_cash_funds(self):
        Board = self.env['lab.ceo.dashboard']
        cash = Board._cash()
        self.assertIn('petty_funds', cash)
        self.Move._collect(self.exec_user, self.clinic, 300.0)
        self.assertEqual(Board._cash()['petty'], cash['petty'] + 300.0,
                         "the field force's cash is still read from field cash")
