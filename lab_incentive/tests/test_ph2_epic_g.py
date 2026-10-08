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
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged('post_install', '-at_install')
class TestEpicGIncentive(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.params = cls.env['ir.config_parameter'].sudo()
        cls.params.set_str('lab_incentive.basis', 'invoiced')
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
        # billed on the order and without tax, so a figure here is the figure billed
        cls.plain = cls.env['product.product'].create({
            'name': 'Plain Appliance', 'type': 'consu', 'list_price': 1000.0,
            'invoice_policy': 'order', 'taxes_id': [(5, 0, 0)]})
        cls.costly = cls.env['product.product'].create({
            'name': 'Costly Appliance', 'type': 'consu', 'list_price': 5000.0,
            'incentive_price': 500.0, 'invoice_policy': 'order', 'taxes_id': [(5, 0, 0)]})

        rule = cls.env['lab.incentive.rule'].create({
            'name': 'Test Rule', 'date_from': '2020-01-01', 'slab_mode': 'flat'})
        cls.env['lab.incentive.rule.slab'].create([
            {'rule_id': rule.id, 'amount_from': 0, 'amount_to': 10000,
             'incentive_type': 'percent', 'value': 5},
            {'rule_id': rule.id, 'amount_from': 10000, 'amount_to': 0,
             'incentive_type': 'percent', 'value': 10},
        ])
        cls.rule = rule

    def _order(self, product, qty=1, user=None, visit_user=None, bill=True):
        """An order sold by `user` (the executive unless given), taken all the way to
        confirmed the way a real one goes.

        The salesperson is what an incentive follows (client, 2026-10-08). No visit
        unless `visit_user` asks for one, because no lab order carries one; an order
        that does must be verified first - the origin rule (client, 2026-08-09) wants
        a second person to check it - and short-cutting that here would test an
        incentive on work that could never have reached production.
        """
        vals = {'partner_id': self.clinic.id, 'user_id': (user or self.executive).id,
                'order_line': [(0, 0, {'product_id': product.id,
                                       'product_uom_qty': qty})]}
        if visit_user:
            vals['visit_id'] = self.env['lab.visit'].create({
                'partner_id': self.clinic.id, 'user_id': visit_user.id}).id
        order = self.env['sale.order'].create(vals)
        if order._verification_required():
            order.action_submit_for_verification()
            order.with_user(self.manager).action_verify()
        order.action_confirm()
        if bill:
            # only what is billed counts (client, 2026-10-08)
            order._create_invoices().action_post()
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

    # ------------------------------------------------------------------ whose order it is
    def test_an_order_counts_for_its_salesperson_without_a_visit(self):
        """The lab's orders carry no field visit at all; the Salesperson is who sold it."""
        order = self._order(self.plain)
        self.assertFalse(order.visit_id)
        sheet = self._sheet()
        sheet.action_compute()
        self.assertEqual(sheet.line_ids.sale_order_id, order)

    def test_the_salesperson_wins_over_the_visit(self):
        """An order taken on a colleague's visit but sold by the executive is the
        executive's, and not the colleague's as well."""
        colleague = self.env['res.users'].create({
            'name': 'Visit Colleague', 'login': 'epicg_visit_colleague',
            'group_ids': [(6, 0, [
                self.env.ref('lab_fieldwork.group_fieldwork_executive').id])]})
        order = self._order(self.plain, visit_user=colleague)
        mine = self._sheet()
        mine.action_compute()
        self.assertEqual(mine.line_ids.sale_order_id, order)
        theirs = self._sheet(executive=colleague)
        theirs.action_compute()
        self.assertFalse(theirs.line_ids)

    def test_month_end_makes_a_sheet_for_every_salesperson_who_sold(self):
        """Not only the Field Work executives: nobody on the lab's book holds that
        group, and month-end generated no sheet at all."""
        seller = self.env['res.users'].create({
            'name': 'Office Seller', 'login': 'epicg_office_seller',
            'group_ids': [(6, 0, [self.env.ref('sales_team.group_sale_salesman').id])]})
        idle = self.env['res.users'].create({
            'name': 'Idle Seller', 'login': 'epicg_idle_seller',
            'group_ids': [(6, 0, [self.env.ref('sales_team.group_sale_salesman').id])]})
        self._order(self.plain, user=seller)
        action = self.env['lab.incentive.generate.wizard'].with_user(self.manager).create(
            {'period': self._this_month()}).action_generate()
        sheets = self.env['lab.incentive.sheet'].search(action['domain'])
        self.assertIn(seller, sheets.executive_id)
        self.assertNotIn(idle, sheets.executive_id, "nothing sold, no sheet")
        sheet = sheets.filtered(lambda s: s.executive_id == seller)
        self.assertEqual(sheet.state, 'computed')
        self.assertEqual(len(sheet.line_ids), 1)

    # ------------------------------------------------------------------ R20 — incentive price
    def test_a_product_with_an_incentive_price_uses_it_not_the_sale_price(self):
        self._order(self.costly)
        sheet = self._sheet()
        sheet.action_compute()
        line = sheet.line_ids[0]
        self.assertEqual(line.sale_amount, 5000.0)
        self.assertEqual(line.incentive_base, 500.0,
                         "the override, not the ₹5000 the doctor was actually charged")
        self.assertTrue(line.used_incentive_price)

    def test_a_plain_product_uses_its_actual_sale_price(self):
        self._order(self.plain)
        sheet = self._sheet()
        sheet.action_compute()
        line = sheet.line_ids[0]
        self.assertEqual(line.incentive_base, 1000.0)
        self.assertFalse(line.used_incentive_price)

    def test_slab_math_flat_mode(self):
        # 1000 * 8 = 8000, inside the 0-10000 / 5% slab -> 400.
        self._order(self.plain, qty=8)
        sheet = self._sheet()
        sheet.action_compute()
        self.assertEqual(sheet.incentive_amount, 400.0)

    def test_slab_math_crossing_into_the_higher_flat_slab(self):
        # 1000 * 12 = 12000 -> flat: the WHOLE base at the 10%-slab rate.
        self._order(self.plain, qty=12)
        sheet = self._sheet()
        sheet.action_compute()
        self.assertEqual(sheet.incentive_amount, 1200.0)

    # ------------------------------------------------------------------ the three-step process
    def test_the_process_is_three_separate_deliberate_steps(self):
        self._order(self.plain)
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
        self._order(self.plain)
        sheet = self._sheet()
        sheet.action_compute()
        with self.assertRaises(UserError):
            sheet.with_user(self.executive).action_approve()

    def test_a_paid_sheet_cannot_be_reset_or_recomputed(self):
        self._order(self.plain)
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
        order = self._order(self.plain)
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
        self._order(self.plain)
        other = self.env['res.company'].create({'name': 'Epic G Other Co'})
        sheet = self._sheet(company_id=other.id)
        sheet.action_compute()
        self.assertFalse(sheet.line_ids,
                         "orders are paid under their own company's sheet only")

    # ------------------------------------------------------------------ basis
    def test_paid_basis_follows_the_countback_not_payment_state(self):
        self.params.set_str('lab_incentive.basis', 'paid')
        product = self.env['product.product'].create({
            'name': 'Paid Basis Appliance', 'type': 'service',
            'list_price': 700.0, 'invoice_policy': 'order'})
        order = self._order(product)
        invoice = order.invoice_ids
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
        # Odoo 20 renamed the states: 19 'paid' is 20 'reconciled', and 20 'paid'
        # is 19 'in_process' (paid, not yet matched). Neither may hold here.
        self.assertNotIn(invoice.payment_state, ('paid', 'reconciled'),
                         "fixture: the receipt is not reconciled")
        sheet.action_compute()
        self.assertEqual(sheet.line_ids.sale_order_id, order)

    # ------------------------------------------------------------------ billed only
    def test_an_order_not_yet_billed_does_not_count(self):
        self._order(self.plain, bill=False)
        sheet = self._sheet()
        sheet.action_compute()
        self.assertFalse(sheet.line_ids)

    def test_only_what_was_billed_counts(self):
        order = self._order(self.plain, qty=3, bill=False)
        invoice = order._create_invoices()
        invoice.invoice_line_ids.quantity = 2       # two of the three billed so far
        invoice.action_post()
        sheet = self._sheet()
        sheet.action_compute()
        self.assertEqual(sheet.total_sales, 2000.0)
        line = sheet.line_ids
        self.assertEqual((line.invoice_id, line.sale_order_id), (invoice, order))
        self.assertEqual(line.invoice_date, invoice.invoice_date)
        self.assertEqual(line.quantity, 2)

    def test_a_credit_note_takes_its_amount_back(self):
        order = self._order(self.plain, qty=2)
        refund = order.invoice_ids._reverse_moves()
        refund.invoice_line_ids.quantity = 1
        refund.action_post()
        sheet = self._sheet()
        sheet.action_compute()
        self.assertEqual(sheet.total_sales, 1000.0)
        self.assertEqual(sorted(sheet.line_ids.mapped('sale_amount')), [-1000.0, 2000.0])
        self.assertEqual(sheet.total_base, 1000.0, "and its incentive base with it")

    def test_a_bill_dated_in_another_month_counts_there(self):
        order = self._order(self.plain, bill=False)
        invoice = order._create_invoices()
        invoice.invoice_date = fields.Date.subtract(self._this_month(), months=1)
        invoice.action_post()
        sheet = self._sheet()
        sheet.action_compute()
        self.assertFalse(sheet.line_ids)

    def test_the_lines_carry_no_share_of_the_incentive(self):
        """Each order's share of the slab total read as if it earned its own; the
        sheet keeps the slab figure whole. (client, 2026-10-08)"""
        self._order(self.plain, qty=8)
        sheet = self._sheet()
        sheet.action_compute()
        self.assertNotIn('incentive_amount', sheet.line_ids._fields)
        self.assertEqual(sheet.slab_amount, 400.0)
        self.assertEqual(sheet.incentive_amount, 400.0)

    # ------------------------------------------------------------------ new clinics
    def _clinic(self, name, user=None, lead=True):
        return self.env['res.partner'].create({
            'name': name, 'incentive_new_lead': lead,
            'user_id': (user or self.executive).id if user is not False else False})

    def test_only_a_clinic_marked_as_the_salespersons_lead_is_a_new_clinic(self):
        self.rule.new_clinic_bonus = 500.0
        colleague = self.env['res.users'].create({
            'name': 'Lead Colleague', 'login': 'epicg_lead_colleague',
            'group_ids': [(6, 0, [self.env.ref('sales_team.group_sale_salesman').id])]})
        theirs_and_marked = self._clinic('Marked Lead Clinic')
        self._clinic('Unmarked Clinic', lead=False)
        self._clinic('Colleague Lead Clinic', user=colleague)
        self._order(self.plain)
        sheet = self._sheet()
        sheet.action_compute()
        self.assertEqual(sheet.new_clinic_ids, theirs_and_marked)
        self.assertEqual((sheet.new_clinic_count, sheet.new_clinic_amount), (1, 500.0))
        self.assertEqual(sheet.incentive_amount, sheet.slab_amount + 500.0)
        action = sheet.action_view_new_clinics()
        self.assertEqual(self.env['res.partner'].search(action['domain']), theirs_and_marked,
                         "the smart button lists the clinics the sheet counted")

    def test_a_contact_under_a_clinic_is_not_another_clinic(self):
        self.rule.new_clinic_bonus = 500.0
        clinic = self._clinic('Parent Lead Clinic')
        self.env['res.partner'].create({
            'name': 'Dr. Under The Clinic', 'parent_id': clinic.id, 'type': 'contact',
            'incentive_new_lead': True, 'user_id': self.executive.id})
        sheet = self._sheet()
        sheet.action_compute()
        self.assertEqual(sheet.new_clinic_ids, clinic)
        self.assertEqual(sheet.new_clinic_amount, 500.0)

    def test_a_lead_marked_later_is_taken_by_recompute(self):
        self.rule.new_clinic_bonus = 500.0
        clinic = self._clinic('Late Lead Clinic', lead=False)
        sheet = self._sheet()
        sheet.action_compute()
        self.assertFalse(sheet.new_clinic_ids)
        clinic.incentive_new_lead = True
        sheet.action_compute()
        self.assertEqual(sheet.new_clinic_ids, clinic)

    def test_month_end_makes_a_sheet_for_a_salesperson_with_only_a_new_lead(self):
        seller = self.env['res.users'].create({
            'name': 'Lead Only Seller', 'login': 'epicg_lead_only',
            'group_ids': [(6, 0, [self.env.ref('sales_team.group_sale_salesman').id])]})
        self._clinic('Only A Lead Clinic', user=seller)
        action = self.env['lab.incentive.generate.wizard'].with_user(self.manager).create(
            {'period': self._this_month()}).action_generate()
        sheets = self.env['lab.incentive.sheet'].search(action['domain'])
        self.assertIn(seller, sheets.executive_id)
        self.assertEqual(sheets.filtered(lambda s: s.executive_id == seller).new_clinic_count, 1)

    def test_a_new_lead_names_its_salesperson(self):
        with self.assertRaises(ValidationError):
            self._clinic('Nobody Brought Me', user=False)

    def test_an_approved_sheet_is_reset_before_it_is_recomputed(self):
        self._order(self.plain)
        sheet = self._sheet()
        sheet.action_compute()
        sheet.with_user(self.manager).action_approve()
        with self.assertRaises(UserError):
            sheet.action_compute()
        sheet.action_reset_to_draft()
        sheet.action_compute()
        self.assertEqual(sheet.state, 'computed')

    # ------------------------------------------------------------------ the working
    def test_a_telescopic_figure_shows_each_band(self):
        """client, 2026-10-08: a telescopic figure with only its slabs beside it
        "makes confusion"."""
        self.rule.slab_mode = 'telescopic'
        self._order(self.plain, qty=12)            # 12,000
        sheet = self._sheet()
        sheet.action_compute()
        self.assertEqual(sheet.slab_amount, 700.0, "5% of the first 10,000 + 10% of the next 2,000")
        steps = sheet.slab_widget_data['steps']
        self.assertEqual([s['applied'] for s in steps], [True, True])
        self.assertIn('10,000', steps[0]['amount'])
        self.assertIn('500', steps[0]['result'])
        self.assertIn('2,000', steps[1]['amount'])
        self.assertIn('200', steps[1]['result'])
        self.assertIn('×', steps[1]['working'])

    def test_a_flat_figure_shows_the_one_slab_on_the_whole_base(self):
        self._order(self.plain, qty=12)
        sheet = self._sheet()
        sheet.action_compute()
        steps = sheet.slab_widget_data['steps']
        self.assertEqual([s['applied'] for s in steps], [False, True])
        self.assertIn('12,000', steps[1]['amount'])
        self.assertIn('1,200', steps[1]['result'])
        self.assertEqual(sum(s['applied'] for s in steps), 1)

    def test_the_working_and_the_amount_never_disagree(self):
        for mode in ('flat', 'telescopic'):
            self.rule.slab_mode = mode
            for base in (0, 1, 9999.99, 10000, 10000.01, 25000):
                with self.subTest(mode=mode, base=base):
                    steps = self.rule.explain(base)
                    self.assertAlmostEqual(sum(s['result'] for s in steps if s['applied']),
                                           self.rule.compute_incentive(base), 2)

    # ------------------------------------------------------------------ regeneration
    def test_regenerating_the_month_leaves_an_approved_sheet_alone(self):
        self._order(self.plain, qty=2)
        sheet = self._sheet()
        sheet.action_compute()
        sheet.with_user(self.manager).action_approve()
        approved_amount = sheet.incentive_amount
        self._order(self.plain, qty=5)     # more work after sign-off
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
        self._order(self.plain)
        self._order(self.plain, user=colleague)
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

    def test_an_executive_reads_only_their_own_sheet_and_changes_none(self):
        """The sheet fence, proven as the executive (Odoo 20 ir.access: the
        executive's read permission carries the own-sheet domain)."""
        colleague = self.env['res.users'].create({
            'name': 'Incentive Colleague 2', 'login': 'epicg_colleague2',
            'group_ids': [(6, 0, [
                self.env.ref('lab_fieldwork.group_fieldwork_executive').id])]})
        mine = self._sheet()
        theirs = self._sheet(executive=colleague)
        Sheet = self.env['lab.incentive.sheet']
        self.assertEqual(Sheet.with_user(self.executive).search([]), mine)
        self.assertEqual(Sheet.with_user(self.manager).search(
            [('id', 'in', (mine | theirs).ids)]), mine | theirs)
        # Cache is per transaction: drop what root already read, or the read below
        # is served without the access check.
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            theirs.with_user(self.executive).read(['incentive_amount'])
        self.assertTrue(mine.with_user(self.executive).read(['incentive_amount']))
        with self.assertRaises(AccessError):
            mine.with_user(self.executive).write({'paid_reference': 'SELF'})
        with self.assertRaises(AccessError):
            Sheet.with_user(self.executive).create({
                'executive_id': self.executive.id, 'period': '2020-01-01'})
        with self.assertRaises(AccessError):
            self.rule.with_user(self.executive).write({'name': 'Mine now'})

    # ------------------------------------------------------------------ widget data
    def test_the_slab_widget_data_reflects_the_active_rule(self):
        self._order(self.plain, qty=8)
        sheet = self._sheet()
        sheet.action_compute()
        data = sheet.slab_widget_data
        self.assertEqual(len(data['slabs']), 2)
        self.assertTrue(any(s['current'] for s in data['slabs']))

    def test_the_executive_sees_the_slabs_on_their_own_sheet(self):
        """The executive reads the rule but not the slab model; the widget must
        still show the slabs that produced their figure."""
        self._order(self.plain, qty=8)
        sheet = self._sheet()
        sheet.action_compute()
        self.env.invalidate_all()
        data = sheet.with_user(self.executive).slab_widget_data
        self.assertEqual(len(data['slabs']), 2)
        self.assertEqual(data['amount'], 400.0)
