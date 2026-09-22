# -*- coding: utf-8 -*-
"""Statement and open receivable from the clinic card and the visit form.

A field executive has no accounting role, so the statement is rendered for them
elevated - but only for a clinic on their round or one they have visited.
(client, 2026-09-17)
"""
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestStatementAndReceivable(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        base = cls.env.ref('base.group_user').id
        cls.executive = cls.env['res.users'].create({
            'name': 'Money Exec', 'login': 'money.exec@test',
            'group_ids': [(6, 0, [base, cls.env.ref(
                'lab_fieldwork.group_fieldwork_executive').id])]})
        cls.accountant = cls.env['res.users'].create({
            'name': 'Money Accountant', 'login': 'money.acct@test',
            'group_ids': [(6, 0, [base, cls.env.ref('account.group_account_readonly').id])]})
        cls.route = cls.env['crm.team'].create({'name': 'Money Route', 'user_id': cls.executive.id})
        away = cls.env['crm.team'].create({'name': 'Money Elsewhere'})
        Partner = cls.env['res.partner']
        cls.mine = Partner.create({'name': 'Money Clinic Mine', 'team_id': cls.route.id})
        cls.visited = Partner.create({'name': 'Money Clinic Visited', 'team_id': away.id})
        cls.stranger = Partner.create({'name': 'Money Clinic Stranger', 'team_id': away.id})
        cls.Perf = cls.env['lab.collection.performance']

    def test_an_executive_opens_the_statement_dialog_for_a_clinic_on_their_round(self):
        """The dialog, fixed to that clinic: the period is the reader's to choose
        wherever they are standing. (client, 2026-09-18)"""
        action = self.Perf.with_user(self.executive).action_statement_for_partner(self.mine.id)
        self.assertEqual(action['res_model'], 'epg.partner.statement.wizard')
        wizard = self.env['epg.partner.statement.wizard'].browse(
            action['res_id']).with_user(self.executive)
        self.assertEqual(wizard.partner_ids, self.mine)
        self.assertTrue(wizard.scoped_to_round)
        self.assertFalse(wizard.min_outstanding, "no floor to ask the ledger about")
        printed = wizard.action_print()
        self.assertEqual(printed['type'], 'ir.actions.act_url')
        self.assertIn('/web/content/', printed['url'])

    def test_an_accountant_gets_the_full_statement_dialog(self):
        action = self.Perf.with_user(self.accountant).action_statement_for_partner(self.mine.id)
        self.assertEqual(action['res_model'], 'epg.partner.statement.wizard')
        # The My Day card passes this straight to doAction(), which needs
        # `views` spelled out or the client crashes on `views.map`.
        self.assertEqual(action.get('views'), [(False, 'form')])

    def test_a_clinic_off_the_round_is_refused_until_it_has_been_visited(self):
        Perf = self.Perf.with_user(self.executive)
        with self.assertRaises(AccessError):
            Perf.action_statement_for_partner(self.stranger.id)
        with self.assertRaises(AccessError):
            Perf.action_open_items_for_partner(self.stranger.id)
        self.env['lab.visit'].create({'partner_id': self.visited.id,
                                        'user_id': self.executive.id})
        self.assertEqual(Perf.action_open_items_for_partner(self.visited.id)['res_model'],
                         'lab.collection.open.item')
        self.assertEqual(Perf.action_statement_for_partner(self.visited.id)['res_model'],
                         'epg.partner.statement.wizard')

    def test_the_visit_form_opens_both(self):
        visit = self.env['lab.visit'].create({'partner_id': self.mine.id,
                                                'user_id': self.executive.id})
        visit = visit.with_user(self.executive)
        self.assertEqual(visit.clinic_open_receivable, 0.0, "nothing owed yet")
        self.assertEqual(visit.action_open_receivable()['res_model'],
                         'lab.collection.open.item')
        self.assertEqual(visit.action_open_statement()['res_model'],
                         'epg.partner.statement.wizard')


@tagged('post_install', '-at_install')
class TestOpenItemPrintingForExecutives(TransactionCase):
    """The open-items list's Invoice and Statement buttons, for the people the
    clinic card and the visit send there. (client, 2026-09-17)"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        base = cls.env.ref('base.group_user').id
        cls.executive = cls.env['res.users'].create({
            'name': 'Print Exec', 'login': 'print.exec@test',
            'group_ids': [(6, 0, [base, cls.env.ref(
                'lab_fieldwork.group_fieldwork_executive').id])]})
        cls.accountant = cls.env['res.users'].create({
            'name': 'Print Accountant', 'login': 'print.acct@test',
            'group_ids': [(6, 0, [base, cls.env.ref('account.group_account_readonly').id])]})
        route = cls.env['crm.team'].create({'name': 'Print Route', 'user_id': cls.executive.id})
        away = cls.env['crm.team'].create({'name': 'Print Elsewhere'})
        cls.mine = cls.env['res.partner'].create({'name': 'Print Clinic Mine', 'team_id': route.id})
        cls.other = cls.env['res.partner'].create({'name': 'Print Clinic Other', 'team_id': away.id})
        product = cls.env['product.product'].create({
            'name': 'Print Appliance', 'type': 'consu', 'list_price': 1000.0})
        cls.item_mine = cls._open_invoice_item(cls.mine, product)
        cls.item_other = cls._open_invoice_item(cls.other, product)

    @classmethod
    def _open_invoice_item(cls, partner, product):
        order = cls.env['sale.order'].create({
            'partner_id': partner.id,
            'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': 1})]})
        order.action_confirm()
        order._create_invoices()
        order.invoice_ids.action_post()
        items = cls.env['lab.collection.performance']._populate_open_items(
            cls.env.company, [partner.id])
        return items.filtered(lambda i: i.move_id == order.invoice_ids)

    def test_an_executive_downloads_the_invoice_and_opens_the_statement_dialog(self):
        """The dialog, not a fixed PDF: an executive at the door is asked "since
        when?" as often as the office is. (client, 2026-09-18)"""
        item = self.item_mine.with_user(self.executive)
        self.assertEqual(item.move_type, 'out_invoice')
        self.assertEqual(item.action_print_invoice()['type'], 'ir.actions.act_url')
        action = item.action_print_statement()
        self.assertEqual(action['res_model'], 'epg.partner.statement.wizard')
        wizard = self.env['epg.partner.statement.wizard'].browse(
            action['res_id']).with_user(self.executive)
        self.assertEqual(wizard.partner_ids, self.mine, "fixed to the clinic they came from")
        self.assertTrue(wizard.scoped_to_round)
        # And it prints: the ledger is read elevated, once the round is checked.
        printed = wizard.action_print()
        self.assertEqual(printed['type'], 'ir.actions.act_url')
        self.assertIn('download=true', printed['url'])

    def _scoped_wizard(self, partners):
        """A dialog as Collections opens it for a field executive."""
        wizard = self.env['epg.partner.statement.wizard'].with_user(self.executive).create({
            'partner_ids': [(6, 0, partners.ids)],
            'scoped_to_round': True, 'min_outstanding': 0.0})
        # Flushed here: assertRaises(AccessError) clears the cursor cache, and
        # anything still pending would go with it - the dialog would then be
        # judged on a state it never had. A wizard per assertion for the same
        # reason: the first rolls its savepoint back. (2026-09-18)
        self.env.flush_all()
        return wizard

    def test_an_executive_cannot_widen_the_statement_dialog_past_their_round(self):
        """Whatever reaches the dialog, the print is scope-checked."""
        Perf = self.env['lab.collection.performance'].with_user(self.executive)
        self.assertTrue(Perf._partner_in_viewer_scope(self.mine), "fixture: their clinic")
        self.assertFalse(Perf._partner_in_viewer_scope(self.other),
                         "fixture: the other clinic is off their round")
        both = self.mine | self.other
        with self.assertRaises(AccessError):
            self._scoped_wizard(both).action_print()
        with self.assertRaises(AccessError):
            self._scoped_wizard(both).action_send_email()
        self.assertEqual(self._scoped_wizard(self.mine).action_print()['type'],
                         'ir.actions.act_url', "their own clinic still prints")

    def test_an_executive_cannot_open_a_statement_off_their_round(self):
        with self.assertRaises(UserError):
            self.item_other.with_user(self.executive).action_print_statement()

    def test_an_accountant_gets_the_report_and_the_dialog(self):
        item = self.item_mine.with_user(self.accountant)
        self.assertEqual(item.action_print_invoice()['type'], 'ir.actions.report')
        action = item.action_print_statement()
        self.assertEqual(action['res_model'], 'epg.partner.statement.wizard')
        wizard = self.env['epg.partner.statement.wizard'].browse(action['res_id'])
        self.assertFalse(wizard.scoped_to_round, "the office reads the ledger itself")
        self.assertEqual(wizard.with_user(self.accountant).action_print()['type'],
                         'ir.actions.report')

    def test_an_executive_cannot_print_a_clinic_off_their_round(self):
        with self.assertRaises(AccessError):
            self.item_other.with_user(self.executive).action_print_invoice()
