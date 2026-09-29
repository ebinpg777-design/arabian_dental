# -*- coding: utf-8 -*-
"""Two presses of the same button, released together when Chrome's "Allow location?"
question is answered, are one press - and only that."""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from .test_fieldwork import LAT, LON


@tagged('post_install', '-at_install')
class TestRepeatedPress(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        G = cls.env.ref
        groups = [G('base.group_user').id, G('lab_fieldwork.group_fieldwork_executive').id]
        cls.exec_user = cls.env['res.users'].create({
            'name': 'Twice Exec', 'login': 'twice_exec', 'group_ids': [(6, 0, groups)]})
        cls.other = cls.env['res.users'].create({
            'name': 'Other Exec', 'login': 'other_twice_exec', 'group_ids': [(6, 0, groups)]})
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Twice Clinic', 'is_clinic': True,
            'partner_latitude': LAT, 'partner_longitude': LON, 'visit_radius_m': 200.0})

    def _visit(self, user=None):
        user = user or self.exec_user
        visit = self.env['lab.visit'].create({'partner_id': self.clinic.id, 'user_id': user.id})
        # sudo keeps env.user as this person and skips the on-duty check, which is
        # not what these tests are about
        return visit.with_user(user).sudo()

    def _give_outcome(self, visit):
        visit.write({'outcome': visit._fields['outcome'].selection[0][0]})

    def test_a_second_check_in_a_moment_later_is_the_same_press(self):
        v = self._visit()
        v.do_check_in(LAT, LON)
        first = v.check_in
        self.assertTrue(v.do_check_in(LAT, LON), "the repeat is answered, not refused")
        self.assertEqual(v.state, 'open')
        self.assertEqual(v.check_in, first, "and it does not stamp the arrival again")

    def test_a_repeat_long_after_is_still_refused(self):
        v = self._visit()
        v.do_check_in(LAT, LON)
        v.write({'check_in': fields.Datetime.now() - timedelta(minutes=10)})
        with self.assertRaises(UserError):
            v.do_check_in(LAT, LON)

    def test_somebody_else_starting_an_open_visit_is_still_refused(self):
        v = self._visit()
        v.do_check_in(LAT, LON)
        with self.assertRaises(UserError):
            v.with_user(self.other).sudo().do_check_in(LAT, LON)

    def test_a_second_check_out_a_moment_later_is_the_same_press(self):
        v = self._visit()
        v.do_check_in(LAT, LON)
        self._give_outcome(v)
        v.do_check_out(LAT, LON)
        first = v.check_out
        self.assertTrue(v.do_check_out(LAT, LON))
        self.assertEqual(v.state, 'done')
        self.assertEqual(v.check_out, first)

    def test_checking_out_a_visit_never_started_is_still_refused(self):
        v = self._visit()
        self._give_outcome(v)
        with self.assertRaises(UserError):
            v.do_check_out(LAT, LON)
