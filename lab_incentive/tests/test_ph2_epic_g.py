# -*- coding: utf-8 -*-
"""Epic G — incentives (R19/R20).

Client rules (2026-08-09): calculate, approve and disburse are three separate,
deliberate steps; a manager/CEO approves; some products carry their own incentive
price because their real cost is too high to pay incentive on the full sale price.
"""
from datetime import date, datetime

import pytz
from psycopg2 import IntegrityError

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged('post_install', '-at_install')
class TestEpicGIncentive(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.params = cls.env['ir.config_parameter'].sudo()
        cls.params.set_param('lab_incentive.basis', 'confirmed')
        cls.manager = cls.env['res.users'].create({
            'name': 'Incentive Manager', 'login': 'epicg_mgr',
            'group_ids': [(6, 0, [
                cls.env.ref('lab_fieldwork.group_fieldwork_manager').id,
                cls.env.ref('lab_order_control.group_lab_order_checker').id,
                cls.env.ref('sales_team.group_sale_manager').id])]})
        cls.executive = cls.env['res.users'].create({
            'name': 'Incentive Exec', 'login': 'epicg_exec',
            'group_ids': [(6, 0, [
                cls.env.ref('lab_fieldwork.group_fieldwork_executive').id])]})
        cls.clinic = cls.env['res.partner'].create({'name': 'Epic G Clinic'})
        cls.plain = cls.env['product.product'].create({
            'name': 'Plain Appliance', 'type': 'consu', 'list_price': 1000.0})
        cls.costly = cls.env['product.product'].create({
            'name': 'Costly Appliance', 'type': 'consu', 'list_price': 5000.0,
            'incentive_price': 500.0})

        rule = cls.env['lab.incentive.rule'].create({
            'name': 'Test Rule', 'date_from': '2020-01-01', 'slab_mode': 'flat'})
        cls.env['lab.incentive.rule.slab'].create([
            {'rule_id': rule.id, 'amount_from': 0, 'amount_to': 10000,
             'incentive_type': 'percent', 'value': 5},
            {'rule_id': rule.id, 'amount_from': 10000, 'amount_to': 0,
             'incentive_type': 'percent', 'value': 10},
        ])
        cls.rule = rule

    def _visit_order(self, product, qty=1, user=None):
        """A field order, taken all the way to confirmed the way a real one goes.

        It must be verified first: this order carries a `visit_id`, so the
        origin rule (client, 2026-08-09) requires a second person to check it before
        it can be confirmed. Short-cutting that here would test an incentive on work
        that could never have reached production.
        """
        visit = self.env['lab.visit'].create({
            'partner_id': self.clinic.id, 'user_id': (user or self.executive).id})
        order = self.env['sale.order'].create({
            'partner_id': self.clinic.id, 'visit_id': visit.id,
            'order_line': [(0, 0, {'product_id': product.id,
                                   'product_uom_qty': qty})]})
        order.action_submit_for_verification()
        order.with_user(self.manager).action_verify()
        order.action_confirm()
        return order

    def _this_month(self):
        """The lab's current month - the one a just-confirmed order lands in.

        The sheet used to be pinned to '2026-08-01'. action_confirm stamps
        date_order with now, so from September on every order fell outside that
        month and the sheet had no lines at all (IndexError, 0.0 != 400.0).
        """
        Sheet = self.env['lab.incentive.sheet']
        now = pytz.utc.localize(fields.Datetime.now()).astimezone(Sheet._lab_tz())
        return now.date().replace(day=1)

    def _sheet(self, executive=None, **vals):
        return self.env['lab.incentive.sheet'].create(dict({
            'executive_id': (executive or self.executive).id,
            'period': self._this_month()}, **vals))

    # ------------------------------------------------------------------ R20 — incentive price
    def test_a_product_with_an_incentive_price_uses_it_not_the_sale_price(self):
        self._visit_order(self.costly)
        sheet = self._sheet()
        sheet.action_compute()
        line = sheet.line_ids[0]
        self.assertEqual(line.sale_amount, 5000.0)
        self.assertEqual(line.incentive_base, 500.0,
                         "the override, not the ₹5000 the doctor was actually charged")
        self.assertTrue(line.used_incentive_price)

    def test_a_plain_product_uses_its_actual_sale_price(self):
        self._visit_order(self.plain)
        sheet = self._sheet()
        sheet.action_compute()
        line = sheet.line_ids[0]
        self.assertEqual(line.incentive_base, 1000.0)
        self.assertFalse(line.used_incentive_price)

    def test_slab_math_flat_mode(self):
        # 1000 * 8 = 8000, inside the 0-10000 / 5% slab -> 400.
        self._visit_order(self.plain, qty=8)
        sheet = self._sheet()
        sheet.action_compute()
        self.assertEqual(sheet.incentive_amount, 400.0)

    def test_slab_math_crossing_into_the_higher_flat_slab(self):
        # 1000 * 12 = 12000 -> flat: the WHOLE base at the 10%-slab rate.
        self._visit_order(self.plain, qty=12)
        sheet = self._sheet()
        sheet.action_compute()
        self.assertEqual(sheet.incentive_amount, 1200.0)

    # ------------------------------------------------------------------ the three-step process
    def test_the_process_is_three_separate_deliberate_steps(self):
        self._visit_order(self.plain)
        sheet = self._sheet()
        self.assertEqual(sheet.state, 'draft')

        with self.assertRaises(UserError):
            sheet.action_approve()          # cannot approve before computing
        sheet.action_compute()
        self.assertEqual(sheet.state, 'computed')

        with self.assertRaises(UserError):
            sheet.action_disburse()         # cannot disburse before approving
        sheet.with_user(self.manager).action_approve()
        self.assertEqual(sheet.state, 'approved')
        self.assertEqual(sheet.approved_by_id, self.manager)

        with self.assertRaises(UserError):
            sheet.action_disburse()         # a reference is required
        sheet.paid_reference = 'NEFT/2026/0001'
        sheet.action_disburse()
        self.assertEqual(sheet.state, 'paid')

    def test_only_a_manager_or_ceo_may_approve(self):
        self._visit_order(self.plain)
        sheet = self._sheet()
        sheet.action_compute()
        with self.assertRaises(UserError):
            sheet.with_user(self.executive).action_approve()

    def test_a_paid_sheet_cannot_be_reset_or_recomputed(self):
        self._visit_order(self.plain)
        sheet = self._sheet()
        sheet.action_compute()
        sheet.with_user(self.manager).action_approve()
        sheet.paid_reference = 'REF-1'
        sheet.action_disburse()
        with self.assertRaises(UserError):
            sheet.action_compute()
        with self.assertRaises(UserError):
            sheet.action_reset_to_draft()

    # ------------------------------------------------------------------ scope
    def test_reworks_never_count_towards_the_base(self):
        order = self._visit_order(self.plain)
        order.is_rework = True
        sheet = self._sheet()
        sheet.action_compute()
        self.assertEqual(sheet.total_base, 0.0)

    def test_two_sheets_for_the_same_executive_and_month_are_blocked(self):
        """Any date in the month normalises to the 1st, so a second sheet for the
        same executive and month collides even when a different day is picked.

        The savepoint matters: a constraint violation aborts the transaction, and
        without isolating it here the failure would poison every assertion after it.
        mute_logger keeps the expected error out of the run's output, where it would
        read like a real failure.
        """
        self._sheet()
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'):
            with self.cr.savepoint():
                # Another day of the SAME month as _sheet(): it normalises to the
                # 1st and collides. A fixed August date stopped colliding once the
                # fixture moved to the current month.
                self.env['lab.incentive.sheet'].create({
                    'executive_id': self.executive.id,
                    'period': fields.Date.to_date(self._this_month()).replace(day=15),
                }).flush_recordset()

    # ------------------------------------------------------------------ windows / company
    def test_the_month_starts_at_local_midnight_not_utc(self):
        start, end = self.env['lab.incentive.sheet'].with_context(
            tz='Asia/Kolkata')._month_window(date(2026, 9, 17))
        self.assertEqual(start, datetime(2026, 8, 31, 18, 30))
        self.assertEqual(end, datetime(2026, 9, 30, 18, 30))

    def test_another_companys_sheet_does_not_take_this_companys_orders(self):
        self._visit_order(self.plain)
        other = self.env['res.company'].create({'name': 'Epic G Other Co'})
        sheet = self._sheet(company_id=other.id)
        sheet.action_compute()
        self.assertFalse(sheet.line_ids,
                         "orders are paid under their own company's sheet only")

    # ------------------------------------------------------------------ basis
    def test_delivered_basis_waits_for_the_outgoing_transfer(self):
        self.params.set_param('lab_incentive.basis', 'delivered')
        order = self._visit_order(self.plain)
        pickings = order.picking_ids.filtered(
            lambda p: p.picking_type_code == 'outgoing')
        self.assertTrue(pickings, "a confirmed goods order must raise a delivery")
        sheet = self._sheet()
        sheet.action_compute()
        self.assertFalse(sheet.line_ids, "not shipped yet, so nothing to pay on")

        for move in pickings.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        pickings._action_done()
        self.assertEqual(set(pickings.mapped('state')), {'done'})
        sheet.action_compute()
        self.assertEqual(sheet.line_ids.sale_order_id, order)

    def test_paid_basis_follows_the_countback_not_payment_state(self):
        self.params.set_param('lab_incentive.basis', 'paid')
        product = self.env['product.product'].create({
            'name': 'Paid Basis Appliance', 'type': 'service',
            'list_price': 700.0, 'invoice_policy': 'order'})
        order = self._visit_order(product)
        invoice = order._create_invoices()
        invoice.action_post()
        sheet = self._sheet()
        sheet.action_compute()
        self.assertFalse(sheet.line_ids, "billed, not yet paid")

        # A receipt booked the way this lab books them: a plain journal entry on
        # the clinic's receivable, never reconciled against the invoice.
        receivable = invoice.line_ids.filtered(
            lambda l: l.account_id.account_type == 'asset_receivable')
        cash = self.env['account.account'].search(
            [('account_type', '=', 'asset_cash'),
             ('company_ids', 'in', invoice.company_id.ids)], limit=1)
        journal = self.env['account.journal'].search(
            [('type', '=', 'general'), ('company_id', '=', invoice.company_id.id)],
            limit=1)
        self.assertTrue(cash and journal, "fixture: a cash account and a journal")
        receipt = self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': journal.id,
            'date': invoice.invoice_date,
            'line_ids': [
                (0, 0, {'account_id': cash.id, 'debit': invoice.amount_total}),
                (0, 0, {'account_id': receivable.account_id.id,
                        'partner_id': receivable.partner_id.id,
                        'credit': invoice.amount_total}),
            ]})
        receipt.action_post()
        self.assertNotEqual(invoice.payment_state, 'paid',
                            "fixture: the receipt is not reconciled")
        sheet.action_compute()
        self.assertEqual(sheet.line_ids.sale_order_id, order)

    # ------------------------------------------------------------------ regeneration
    def test_regenerating_the_month_leaves_an_approved_sheet_alone(self):
        self._visit_order(self.plain, qty=2)
        sheet = self._sheet()
        sheet.action_compute()
        sheet.with_user(self.manager).action_approve()
        approved_amount = sheet.incentive_amount
        self._visit_order(self.plain, qty=5)     # more work after sign-off
        self.env['lab.incentive.generate.wizard'].create(
            {'period': sheet.period}).action_generate()
        self.assertEqual(sheet.state, 'approved')
        self.assertEqual(sheet.incentive_amount, approved_amount)

    # ------------------------------------------------------------------ line access
    def test_an_executive_reads_only_their_own_sheet_lines(self):
        colleague = self.env['res.users'].create({
            'name': 'Incentive Colleague', 'login': 'epicg_colleague',
            'group_ids': [(6, 0, [
                self.env.ref('lab_fieldwork.group_fieldwork_executive').id])]})
        self._visit_order(self.plain)
        self._visit_order(self.plain, user=colleague)
        mine = self._sheet()
        mine.action_compute()
        theirs = self._sheet(executive=colleague)
        theirs.action_compute()
        self.assertTrue(mine.line_ids and theirs.line_ids, "fixture: both have lines")

        Line = self.env['lab.incentive.sheet.line']
        visible = Line.with_user(self.executive).search([])
        self.assertTrue(mine.line_ids <= visible)
        self.assertFalse(theirs.line_ids & visible)
        self.assertTrue(theirs.line_ids <= Line.with_user(self.manager).search([]))

    # ------------------------------------------------------------------ widget data
    def test_the_slab_widget_data_reflects_the_active_rule(self):
        self._visit_order(self.plain, qty=8)
        sheet = self._sheet()
        sheet.action_compute()
        data = sheet.slab_widget_data
        self.assertEqual(len(data['slabs']), 2)
        self.assertTrue(any(s['current'] for s in data['slabs']))
