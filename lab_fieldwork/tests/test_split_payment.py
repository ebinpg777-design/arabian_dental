# -*- coding: utf-8 -*-
"""A payment that came two ways at one counter (client, 2026-11-09).

A doctor pays part in cash and the rest by cheque or UPI. `collected` stays the
total every figure reads; the three parts say how it came; only the cash part is
ever held as field cash - and a caller that only knows the total and one mode is
still right.
"""
from unittest.mock import patch

from psycopg2.errors import CheckViolation

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

from .test_fieldwork import LAT, LON


@tagged('post_install', '-at_install')
class TestSplitPayment(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        G = cls.env.ref
        base = G('base.group_user').id
        cls.exec_user = cls.env['res.users'].create({
            'name': 'Split Exec', 'login': 'split_exec',
            'group_ids': [(6, 0, [base, G('lab_fieldwork.group_fieldwork_executive').id])]})
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Split Clinic', 'is_clinic': True,
            'partner_latitude': LAT, 'partner_longitude': LON, 'visit_radius_m': 200.0})
        cls.Handover = cls.env['lab.cash.handover']

    def _open(self):
        v = self.env['lab.visit'].create({
            'partner_id': self.clinic.id, 'user_id': self.exec_user.id})
        v.do_check_in(LAT, LON)
        return v

    def _parts(self, v):
        return (v.collected_cash, v.collected_cheque, v.collected_online)

    def _held(self):
        self.exec_user.invalidate_recordset(['fw_cash_in_hand'])
        return self.exec_user.fw_cash_in_hand

    def _position(self):
        return self.Handover.cash_position(self.exec_user)[self.exec_user.id]

    # ------------------------------------------------------------ the agreement
    def test_the_parts_make_the_total_and_the_mode(self):
        v = self._open()
        v.write({'collected_cash': 5000.0, 'collected_cheque': 10000.0})
        self.assertEqual(v.collected, 15000.0)
        self.assertEqual(v.pay_mode, 'mixed')
        self.assertIn('Cash', v.pay_split_text)
        self.assertIn('Cheque', v.pay_split_text)
        self.assertNotIn('UPI', v.pay_split_text)

    def test_one_part_is_just_that_mode(self):
        """A "split" with money one way only is not a split."""
        v = self._open()
        v.write({'pay_mode': 'mixed', 'collected_online': 700.0})
        self.assertEqual((v.collected, v.pay_mode), (700.0, 'online'))
        self.assertEqual(self._parts(v), (0.0, 0.0, 700.0))

    def test_the_old_way_of_writing_it_still_fills_the_part(self):
        """Every older caller writes the amount and one mode."""
        v = self._open()
        v.write({'collected': 1200.0, 'pay_mode': 'cheque'})
        self.assertEqual(self._parts(v), (0.0, 1200.0, 0.0))
        v.write({'pay_mode': 'cash'})
        self.assertEqual(self._parts(v), (1200.0, 0.0, 0.0))
        v.write({'collected': 900.0})
        self.assertEqual(self._parts(v), (900.0, 0.0, 0.0))
        created = self.env['lab.visit'].create({
            'partner_id': self.clinic.id, 'user_id': self.exec_user.id,
            'collected': 300.0, 'pay_mode': 'online'})
        self.assertEqual(self._parts(created), (0.0, 0.0, 300.0))

    def test_an_amount_with_no_mode_has_no_parts(self):
        v = self._open()
        v.write({'collected': 400.0})
        self.assertEqual(self._parts(v), (0.0, 0.0, 0.0))
        self.assertFalse(v.pay_mode)
        v.outcome = 'payment'
        with self.assertRaises(UserError):
            v.do_check_out(LAT, LON)

    def test_a_split_is_never_guessed(self):
        v = self._open()
        v.write({'collected_cash': 500.0, 'collected_online': 500.0})
        with self.assertRaises(UserError):
            v.write({'collected': 2000.0})
        v.write({'collected': 1000.0})            # the same total: nothing to guess
        self.assertEqual(self._parts(v), (500.0, 0.0, 500.0))
        v.write({'collected': 0.0})               # taken away altogether
        self.assertEqual((v.collected, v.pay_mode), (0.0, False))
        self.assertEqual(self._parts(v), (0.0, 0.0, 0.0))

    def test_leaving_a_split_for_one_mode_moves_the_whole_amount(self):
        """What the chips write: the mode, the parts and the total together."""
        v = self._open()
        v.write({'collected_cash': 500.0, 'collected_cheque': 1500.0})
        v.write({'pay_mode': 'cheque', 'collected': 2000.0,
                 'collected_cash': 0.0, 'collected_cheque': 2000.0, 'collected_online': 0.0})
        self.assertEqual((v.collected, v.pay_mode), (2000.0, 'cheque'))
        self.assertEqual(self._parts(v), (0.0, 2000.0, 0.0))

    # ------------------------------------------------------------- the custody
    def test_only_the_cash_part_is_held(self):
        opening = self._held()
        v = self._open()
        v.write({'outcome': 'payment', 'collected_cash': 5000.0, 'collected_cheque': 10000.0})
        v.do_check_out(LAT, LON)
        self.assertTrue(v.cash_banked, "the cash part is taken in at check-out")
        self.assertEqual(v.cash_txn_id.amount, 5000.0)
        self.assertEqual(self._held(), opening + 5000.0)
        self.assertEqual(self._position()['with_you'], opening + 5000.0)

    def test_a_cheque_and_upi_split_holds_nothing(self):
        opening = self._held()
        v = self._open()
        v.write({'outcome': 'payment', 'collected_cheque': 800.0, 'collected_online': 200.0})
        v.do_check_out(LAT, LON)
        self.assertFalse(v.cash_banked)
        self.assertEqual(self._held(), opening)
        with self.assertRaises(UserError):
            v.action_cash_to_float()

    def test_cash_held_follows_a_payment_split_after_check_out(self):
        """The whole amount entered as cash at the counter, split a minute later."""
        opening = self._held()
        v = self._open()
        v.write({'outcome': 'payment', 'collected': 15000.0, 'pay_mode': 'cash'})
        v.do_check_out(LAT, LON)
        self.assertEqual(self._held(), opening + 15000.0)
        v.with_user(self.exec_user).write({
            'pay_mode': 'mixed', 'collected_cash': 5000.0, 'collected_cheque': 10000.0})
        self.assertEqual(v.collected, 15000.0)
        self.assertEqual(v.cash_txn_id.amount, 5000.0)
        self.assertEqual(self._held(), opening + 5000.0)
        # adding the UPI part leaves the cash alone
        v.with_user(self.exec_user).write({'collected_online': 1000.0})
        self.assertEqual((v.collected, v.cash_txn_id.amount), (16000.0, 5000.0))
        # ...and none of it cash after all: nothing is held, nothing is "in the pocket"
        v.with_user(self.exec_user).write({'pay_mode': 'cheque', 'collected': 16000.0})
        self.assertFalse(v.cash_banked)
        self.assertEqual(self._held(), opening)
        self.assertEqual(self._position()['unbanked'], 0.0)

    def test_cash_never_taken_in_counts_only_its_cash_part(self):
        """The failure path: check-out could not take the cash in."""
        Move = type(self.env['lab.cash.movement'])
        v = self._open()
        v.write({'outcome': 'payment', 'collected_cash': 600.0, 'collected_online': 400.0})
        with patch.object(Move, '_collect', side_effect=UserError("No cash today.")):
            v.do_check_out(LAT, LON)
        self.assertFalse(v.cash_banked)
        self.assertEqual(self._position()['unbanked'], 600.0)
        self.assertFalse(self.Handover._sweep_unbanked(self.exec_user))
        self.assertEqual(v.cash_txn_id.amount, 600.0)

    # --------------------------------------------------------------- the day
    def test_my_day_counts_the_whole_payment_and_says_the_split(self):
        v = self._open()
        v.write({'outcome': 'payment', 'collected_cash': 250.0, 'collected_online': 750.0})
        v.do_check_out(LAT, LON)
        day = self.env['lab.my.day'].with_user(self.exec_user).get_day()
        self.assertEqual(day['summary']['collected'], 1000.0)
        row = next(r for r in day['visits'] if r['id'] == v.id)
        self.assertEqual(row['collected'], 1000.0)
        self.assertIn('Cash', row['split'])
        self.assertFalse(row['unbanked'])

    # ---------------------------------------------- money taken without a visit
    def test_a_split_without_a_visit_is_one_collection_each_way(self):
        opening = self._held()
        Collection = self.env['lab.cash.collection']
        before = Collection.search([('user_id', '=', self.exec_user.id)])
        self.env['lab.collect.cash'].with_user(self.exec_user).create({
            'partner_id': self.clinic.id, 'pay_mode': 'mixed', 'amount': 1800.0,
            'amount_cash': 800.0, 'amount_cheque': 1000.0}).action_record()
        made = Collection.search([('user_id', '=', self.exec_user.id)]) - before
        self.assertEqual(sorted(made.mapped(lambda c: (c.pay_mode, c.amount))),
                         [('cash', 800.0), ('cheque', 1000.0)])
        self.assertEqual(self._held(), opening + 800.0)
        with self.assertRaises(UserError):
            self.env['lab.collect.cash'].with_user(self.exec_user).create({
                'partner_id': self.clinic.id, 'pay_mode': 'mixed', 'amount': 0.0}).action_record()

    # last: a CHECK the database raises, inside its own savepoint
    def test_zz_a_part_cannot_be_less_than_nothing(self):
        v = self._open()
        with mute_logger('odoo.sql_db'), self.assertRaises(CheckViolation), self.env.cr.savepoint():
            v.write({'collected_cash': -5.0, 'collected_cheque': 10.0})
            v.flush_recordset()
