# -*- coding: utf-8 -*-
"""Collected cash: into the float by itself, back to the office by code.

The numbers an executive, the desk and the accountant read must be ONE number,
and it must go DOWN when the office counts the envelope. (client, 2026-09-18)
"""
import re

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from .test_fieldwork import LAT, LON

CODE = re.compile(r'^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{3}-[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{3}$')


@tagged('post_install', '-at_install')
class TestCashHandover(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        G = cls.env.ref
        base = G('base.group_user').id
        cls.exec_user = cls.env['res.users'].create({
            'name': 'Cash Exec', 'login': 'cash_exec',
            'group_ids': [(6, 0, [base, G('lab_fieldwork.group_fieldwork_executive').id])]})
        cls.other_exec = cls.env['res.users'].create({
            'name': 'Other Cash Exec', 'login': 'cash_other',
            'group_ids': [(6, 0, [base, G('lab_fieldwork.group_fieldwork_executive').id])]})
        cls.officer = cls.env['res.users'].create({
            'name': 'Cash Officer', 'login': 'cash_officer',
            'group_ids': [(6, 0, [base, G('petty_cash.group_petty_cash_officer').id])]})
        cls.ops = cls.env['res.users'].create({
            'name': 'Ops Desk', 'login': 'cash_ops',
            'group_ids': [(6, 0, [base, G('lab_fieldwork.group_fieldwork_ops_manager').id])]})
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Cash Clinic', 'is_clinic': True,
            'partner_latitude': LAT, 'partner_longitude': LON, 'visit_radius_m': 200.0})
        cls.Handover = cls.env['lab.cash.handover']
        cls.env['ir.config_parameter'].sudo().set_param('lab_fieldwork.auto_float', 'True')

    def _cash_visit(self, amount, user=None):
        v = self.env['lab.visit'].create({
            'partner_id': self.clinic.id, 'user_id': (user or self.exec_user).id})
        v.do_check_in(LAT, LON)
        v.write({'outcome': 'payment', 'collected': amount, 'pay_mode': 'cash'})
        v.do_check_out(LAT, LON)
        return v

    def _position(self, user=None):
        user = user or self.exec_user
        return self.Handover.cash_position(user)[user.id]

    def _declare(self, amount=None, user=None):
        user = user or self.exec_user
        vals = {'user_id': user.id}
        if amount is not None:
            vals['amount'] = amount
        h = self.Handover.with_user(user).create(vals)
        h.action_declare()
        return h

    # ------------------------------------------------------- into the float
    def test_check_out_puts_the_cash_in_a_float_that_opens_itself(self):
        """0 allocations on the live database, and the button refused everyone.
        Now the float is opened the first time cash is taken, and the cash is in
        it the moment the visit closes - nothing for the executive to remember."""
        self.assertFalse(self.exec_user.petty_cash_allocation_id)
        v = self._cash_visit(1500.0)
        self.exec_user.invalidate_recordset()
        alloc = self.exec_user.petty_cash_allocation_id
        self.assertTrue(alloc, "a float opened itself")
        self.assertEqual(alloc.state, 'allocated')
        self.assertFalse(alloc.journal_id, "a custody float books nothing")
        self.assertTrue(v.cash_banked)
        self.assertEqual(v.cash_txn_id.type, 'collection')
        self.assertFalse(v.cash_txn_id.move_id, "no journal entry for custody")
        alloc.invalidate_recordset()
        self.assertEqual(alloc.amount_collected, 1500.0)
        self.assertEqual(alloc.amount_balance, 1500.0)
        self.assertEqual(self._position()['with_you'], 1500.0)

    def test_a_setting_nobody_has_saved_still_opens_floats(self):
        """get_param returns False for a key never saved, and str(False) is
        'False' - read naively, an untouched setting meant OFF. The browser
        walk caught it; the unit tests had all set the key. (2026-09-18)"""
        params = self.env['ir.config_parameter'].sudo()
        params.search([('key', '=', 'lab_fieldwork.auto_float')]).unlink()
        self.assertIs(params.get_param('lab_fieldwork.auto_float'), False)
        v = self._cash_visit(300.0)
        self.assertTrue(v.cash_banked, "the default is on")

    def test_the_setting_can_leave_floats_to_accounts(self):
        self.env['ir.config_parameter'].sudo().set_param('lab_fieldwork.auto_float', 'False')
        v = self._cash_visit(900.0)
        self.exec_user.invalidate_recordset()
        self.assertFalse(self.exec_user.petty_cash_allocation_id)
        self.assertFalse(v.cash_banked)
        # The money is still the executive's to answer for.
        self.assertEqual(self._position()['with_you'], 900.0)
        self.assertEqual(self._position()['unbanked'], 900.0)
        self.assertIn("did not go into the float", v.message_ids[0].body)

    def test_the_search_on_cash_banked_tells_the_float_from_the_pocket(self):
        """Odoo 19 hands the search method ('in', {True}) for `= True`; read as a
        negative, BOTH filters returned every visit, so banked cash still read
        as in the pocket and the figure could never go down. (2026-09-18)"""
        banked = self._cash_visit(1500.0)
        self.env['ir.config_parameter'].sudo().set_param('lab_fieldwork.auto_float', 'False')
        pocket = self._cash_visit(700.0, user=self.other_exec)
        Visit = self.env['lab.visit']
        both = Visit.search([('id', 'in', (banked | pocket).ids)])
        self.assertEqual(both.filtered_domain([('cash_banked', '=', True)]), banked)
        self.assertEqual(Visit.search([('id', 'in', both.ids), ('cash_banked', '=', True)]), banked)
        self.assertEqual(Visit.search([('id', 'in', both.ids), ('cash_banked', '=', False)]), pocket)
        self.assertEqual(Visit.search([('id', 'in', both.ids), ('cash_banked', '!=', True)]), pocket)

    # ------------------------------------------------------- back to the office
    def test_hand_over_gets_a_code_and_the_count_takes_it_off_the_float(self):
        self._cash_visit(1500.0)
        self._cash_visit(2550.0)
        self.assertEqual(self._position()['available'], 4050.0)

        h = self.Handover.with_user(self.exec_user).create({'user_id': self.exec_user.id})
        self.assertEqual(h.amount, 4050.0, "everything they hold, unless they say otherwise")
        h.action_declare()
        self.assertEqual(h.state, 'declared')
        self.assertRegex(h.code, CODE)
        self.assertEqual(h.counted_amount, 4050.0)
        # Declared is not counted: still theirs, but spoken for.
        pos = self._position()
        self.assertEqual(pos['with_you'], 4050.0)
        self.assertEqual(pos['pending'], 4050.0)
        self.assertEqual(pos['available'], 0.0)
        day = self.env['lab.my.day'].with_user(self.exec_user).get_day()
        self.assertEqual(day['summary']['cash_in_hand'], 0.0)
        self.assertEqual(day['cash']['pending'], 4050.0)
        self.assertEqual(day['cash']['pending_rows'][0]['code'], h.code)

        # The office, by code - spaced and lower-case, as typed at a desk.
        wiz = self.env['lab.cash.receive'].with_user(self.officer).create(
            {'code': h.code.lower().replace('-', ' ')})
        wiz._onchange_code()
        self.assertEqual(wiz.handover_id, h)
        self.assertEqual(wiz.counted_amount, 4050.0)
        wiz.action_confirm()

        h.invalidate_recordset()
        self.assertEqual(h.state, 'received')
        self.assertEqual(h.received_by_id, self.officer)
        self.assertEqual(h.difference, 0.0)
        txn = h.txn_id
        self.assertEqual((txn.type, txn.state, txn.amount), ('handover', 'posted', 4050.0))
        self.assertFalse(txn.move_id, "custody only - nothing in the ledger")
        alloc = self.exec_user.petty_cash_allocation_id
        alloc.invalidate_recordset()
        self.assertEqual(alloc.amount_handed_over, 4050.0)
        self.assertEqual(alloc.amount_balance, 0.0, "the float is empty again")
        pos = self._position()
        self.assertEqual((pos['with_you'], pos['pending'], pos['available']), (0.0, 0.0, 0.0))
        day = self.env['lab.my.day'].with_user(self.exec_user).get_day()
        self.assertEqual(day['summary']['cash_in_hand'], 0.0)
        self.assertFalse(day['cash']['with_you'], "the card is gone")

    def test_a_count_that_is_short_needs_a_reason_and_stays_owed(self):
        self._cash_visit(1500.0)
        h = self._declare(1500.0)
        h.with_user(self.officer).write({'counted_amount': 1200.0})
        with self.assertRaises(UserError, msg="a difference with no reason is not a count"):
            h.with_user(self.officer).action_confirm_receipt()
        h.with_user(self.officer).write({'difference_reason': 'One 500 note was a 200'})
        h.with_user(self.officer).action_confirm_receipt()
        self.assertEqual(h.state, 'received')
        self.assertEqual(h.difference, -300.0)
        self.assertEqual(h.txn_id.amount, 1200.0, "only what was counted leaves the float")
        alloc = self.exec_user.petty_cash_allocation_id
        alloc.invalidate_recordset()
        self.assertEqual(alloc.amount_balance, 300.0, "the shortfall is still owed")
        self.assertEqual(self._position()['with_you'], 300.0)
        todo = h.activity_ids.filtered(lambda a: a.user_id == self.exec_user)
        self.assertTrue(todo, "the executive is told, in their own name")
        self.assertIn('Short by', todo.summary)

    def test_you_cannot_hand_over_more_than_you_hold(self):
        self._cash_visit(1500.0)
        with self.assertRaises(UserError):
            self._declare(1600.0)
        h = self._declare(1000.0)
        self.assertEqual(self._position()['available'], 500.0)
        with self.assertRaises(UserError, msg="the declared part is spoken for"):
            self._declare(600.0)
        h.action_cancel()
        self.assertEqual(self._position()['available'], 1500.0, "cancelled frees it again")

    def test_only_the_office_counts(self):
        self._cash_visit(1500.0)
        h = self._declare(1500.0)
        with self.assertRaises(UserError):
            h.with_user(self.exec_user).action_confirm_receipt()
        h.with_user(self.ops).action_confirm_receipt()
        self.assertEqual(h.state, 'received')

    def test_an_executive_cannot_hand_over_in_somebody_elses_name(self):
        with self.assertRaises(UserError):
            self.Handover.with_user(self.exec_user).create(
                {'user_id': self.other_exec.id, 'amount': 100.0})

    def test_an_executive_sees_only_their_own_slips_and_the_officer_sees_all(self):
        self._cash_visit(1500.0)
        self._cash_visit(700.0, user=self.other_exec)
        mine = self._declare(1500.0)
        theirs = self._declare(700.0, user=self.other_exec)
        seen = self.Handover.with_user(self.exec_user).search([])
        self.assertEqual(seen, mine)
        self.assertEqual(self.Handover.with_user(self.officer).search([]), mine | theirs)

    def test_the_notes_counted_must_add_up_and_fill_the_amount(self):
        self._cash_visit(1500.0)
        h = self.Handover.with_user(self.exec_user).create(
            {'user_id': self.exec_user.id, 'amount': 1500.0})
        h.action_fill_denominations()
        self.assertEqual(set(h.line_ids.mapped('denomination')), {500, 200, 100, 50, 20, 10})
        h.line_ids.filtered(lambda l: l.denomination == 500).count = 3
        # A wrong sheet is refused, not silently overridden.
        with self.assertRaises(ValidationError):
            h.write({'amount': 1400.0})
        h.action_declare()
        self.assertEqual(h.state, 'declared')

    def test_a_received_slip_is_locked(self):
        self._cash_visit(1500.0)
        h = self._declare(1500.0)
        h.with_user(self.officer).action_confirm_receipt()
        with self.assertRaises(UserError):
            h.with_user(self.exec_user).write({'amount': 100.0})
        h.with_user(self.exec_user).write({'note': 'thanks'})

    # ------------------------------------------------------- the desk and the chase
    def test_the_desk_reads_the_same_number_as_the_phone(self):
        self._cash_visit(1500.0)
        self._declare(400.0)
        row = next(r for r in self.env['lab.desk']._cash_held(limit=50)
                   if r['user_id'] == self.exec_user.id)
        pos = self._position()
        self.assertEqual(row['amount'], pos['with_you'])
        self.assertEqual(row['pending'], 400.0)
        self.assertTrue(row['allocation_id'])

    def test_cash_past_the_policy_is_chased_once(self):
        self._cash_visit(1500.0)
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('lab_fieldwork.cash_max_days', '0')
        params.set_param('lab_fieldwork.cash_ceiling', '1000')
        self.assertTrue(self.Handover._is_overdue(self._position()))
        # Other people on this database may be carrying cash too; the claim
        # is about THIS person: chased, and chased once.
        self.assertGreaterEqual(self.Handover._cron_cash_reminders(), 1)
        self.Handover._cron_cash_reminders()
        todo = self.exec_user.partner_id.activity_ids.filtered(
            lambda a: a.user_id == self.exec_user)
        self.assertEqual(len(todo), 1, "once")
        self.assertIn('Hand over', todo.summary)
        day = self.env['lab.my.day'].with_user(self.exec_user).get_day()
        self.assertTrue(day['cash']['overdue'])
        params.set_param('lab_fieldwork.cash_ceiling', '0')
        self.assertFalse(self.Handover._is_overdue(self._position()))

    def test_the_receipt_prints_with_the_code(self):
        self._cash_visit(1500.0)
        h = self._declare(1500.0)
        html = self.env['ir.actions.report']._render_qweb_html(
            'lab_fieldwork.report_cash_handover_document', h.ids)[0].decode()
        self.assertIn(h.code, html)
        self.assertIn('Cash Handover Receipt', html)
        self.assertTrue(h.qr_image, "the QR that opens the slip")
