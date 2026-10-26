# -*- coding: utf-8 -*-
"""Removing a visit added by mistake, from My Day (client, 2026-10-26: "Provide an
option to technicians to remove the visits from their window if it created wrongly")."""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

LAT, LON = 9.98160, 76.57790


@tagged('post_install', '-at_install')
class TestRemoveVisit(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        G = cls.env.ref
        exec_groups = [(6, 0, [G('base.group_user').id,
                               G('lab_fieldwork.group_fieldwork_executive').id])]
        cls.exec_user = cls.env['res.users'].create({
            'name': 'Remove Exec', 'login': 'fw_rm_exec', 'group_ids': exec_groups})
        cls.other_user = cls.env['res.users'].create({
            'name': 'Remove Other', 'login': 'fw_rm_other', 'group_ids': exec_groups})
        cls.manager = cls.env['res.users'].create({
            'name': 'Remove Manager', 'login': 'fw_rm_mgr',
            'group_ids': [(6, 0, [G('base.group_user').id,
                                  G('lab_fieldwork.group_fieldwork_manager').id])]})
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Wrong Tap Clinic', 'is_clinic': True,
            'partner_latitude': LAT, 'partner_longitude': LON})
        params = cls.env['ir.config_parameter'].sudo()
        params.set_str('lab_fieldwork.backdate_days', '3')
        # Attendance has tests of its own; here it would only stand in the door.
        params.set_str('lab_fieldwork.require_attendance', 'False')

    def _added(self):
        """A stop the executive added from My Day - how a wrong visit is made."""
        visit_id = self.env['lab.my.day'].with_user(self.exec_user).add_stop(self.clinic.id)
        return self.env['lab.visit'].with_user(self.exec_user).browse(visit_id)

    def _remove(self, visit, user=None):
        return self.env['lab.visit'].with_user(user or self.exec_user) \
            .browse(visit.id).action_remove_mistake()

    def _on_my_day(self, visit, day=None):
        data = self.env['lab.my.day'].with_user(self.exec_user).get_day(
            fields.Date.to_string(day or visit.sudo().date))
        return {card['id']: card for card in data['visits']}.get(visit.id)

    def test_a_stop_added_by_mistake_is_gone_without_a_trace(self):
        visit = self._added()
        day = visit.date
        card = self._on_my_day(visit)
        self.assertTrue(card and card['removable'], "the card offers Remove")
        self.assertEqual(self._remove(visit), 'deleted')
        self.assertFalse(visit.sudo().exists(), "never visited and added by them: deleted")
        self.assertIsNone(self._on_my_day(visit, day))
        self.assertEqual(self._remove(visit), 'deleted', "a second tap is not an error")

    def test_a_checked_in_visit_is_cancelled_and_its_check_in_kept(self):
        visit = self._added()
        visit.do_check_in(LAT, LON)
        stamp = visit.check_in
        self.assertEqual(self._remove(visit), 'cancelled')
        visit = visit.sudo()
        self.assertTrue(visit.exists())
        self.assertEqual(visit.state, 'cancel')
        self.assertEqual(visit.check_in, stamp, "the office still sees where and when")
        self.assertIn('Removed from My Day', visit.message_ids[:1].body)
        self.assertIsNone(self._on_my_day(visit), "and it is off the executive's day")

    def test_a_visit_planned_by_someone_else_is_cancelled_not_deleted(self):
        visit = self.env['lab.visit'].with_user(self.manager).create({
            'partner_id': self.clinic.id, 'user_id': self.exec_user.id})
        self.assertEqual(self._remove(visit), 'cancelled')
        self.assertEqual(visit.sudo().state, 'cancel')

    def test_work_on_the_visit_refuses_and_says_what_it_is(self):
        visit = self._added()
        visit.do_check_in(LAT, LON)
        visit.write({'collected': 500.0, 'pay_mode': 'cash'})
        with self.assertRaises(UserError) as caught:
            self._remove(visit)
        self.assertIn('payment', str(caught.exception))
        self.assertEqual(visit.sudo().state, 'open', "nothing changed")

    def test_a_case_slip_refuses_until_it_is_deleted(self):
        visit = self._added()
        visit.do_check_in(LAT, LON)
        product = self.env['product.product'].create({
            'name': 'Remove Crown', 'type': 'consu', 'sale_ok': True, 'list_price': 900.0})
        slip = self.env['lab.case'].with_user(self.exec_user).create({
            'visit_id': visit.id, 'patient': 'Anu Raj',
            'line_ids': [(0, 0, {'product_id': product.id, 'ul': 'upper', 'quantity': 1.0})]})
        with self.assertRaises(UserError) as caught:
            self._remove(visit)
        self.assertIn('case slip', str(caught.exception))
        slip.unlink()
        self.assertEqual(self._remove(visit), 'cancelled')

    def test_a_completed_visit_stays(self):
        visit = self._added()
        visit.do_check_in(LAT, LON)
        visit.outcome = 'none'
        visit.do_check_out(LAT, LON)
        self.assertFalse(self._on_my_day(visit)['removable'])
        with self.assertRaises(UserError):
            self._remove(visit)
        self.assertEqual(visit.sudo().state, 'done')

    def test_only_its_own_executive_or_a_manager(self):
        visit = self._added()
        with self.assertRaises((AccessError, UserError)):
            self._remove(visit, user=self.other_user)
        self.assertTrue(visit.sudo().exists())
        self.assertEqual(self._remove(visit, user=self.manager), 'deleted')

    def test_older_than_the_my_day_window_is_the_managers(self):
        old = fields.Date.context_today(self.env['lab.visit']) - timedelta(days=10)
        visit = self.env['lab.visit'].with_user(self.exec_user).create({
            'partner_id': self.clinic.id, 'user_id': self.exec_user.id, 'date': old})
        with self.assertRaises(UserError) as caught:
            self._remove(visit)
        self.assertIn('manager', str(caught.exception))
        self.assertEqual(self._remove(visit, user=self.manager), 'deleted')
