# -*- coding: utf-8 -*-
"""Stored computes that were reading a field they did not depend on.

Every test here failed before the depends were corrected. They are worth keeping because
the failure mode is invisible: the value is right the first time it is computed and
silently stale from then on, so nothing in the UI ever looks broken.
"""
from odoo import fields
from odoo.tests import TransactionCase, tagged

LAT, LON = 9.98160, 76.57790


@tagged('post_install', '-at_install')
class TestStaleComputes(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        G = cls.env.ref
        cls.exec_user = cls.env['res.users'].create({
            'name': 'Stale Exec', 'login': 'stale_exec',
            'group_ids': [(6, 0, [G('base.group_user').id,
                                  G('lab_fieldwork.group_fieldwork_executive').id])]})
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Stale Clinic', 'is_clinic': True,
            'partner_latitude': LAT, 'partner_longitude': LON})

    def test_checking_in_without_a_gps_fix_is_reported_as_nofix(self):
        """`gps_state` branches on check_in, so it has to depend on it.

        This is the case the field force actually hits: the phone refuses the location
        permission, so no coordinates arrive and only `check_in` is written. Without
        `check_in` in the depends nothing invalidates the stored value, and the visit
        keeps the blank state it was created with — a visit with no proof of presence
        then reads on every report as though the question was never asked.
        """
        visit = self.env['lab.visit'].create({
            'partner_id': self.clinic.id, 'user_id': self.exec_user.id})
        self.assertFalse(visit.gps_state)

        # exactly what a denied location permission does: a check-in, no coordinates
        visit.check_in = fields.Datetime.now()

        self.assertEqual(
            visit.gps_state, 'nofix',
            "checking in with no coordinates must be recorded as 'nofix'; a blank "
            "gps_state is indistinguishable from a visit nobody has started")

    def test_a_clinic_getting_its_pin_updates_visits_already_recorded(self):
        """`nopin` has to clear once the clinic is finally geolocated."""
        blank = self.env['res.partner'].create({'name': 'Unpinned Clinic',
                                                'is_clinic': True})
        visit = self.env['lab.visit'].create({
            'partner_id': blank.id, 'user_id': self.exec_user.id})
        visit.write({'check_in': fields.Datetime.now(),
                     'gps_lat': LAT, 'gps_lon': LON})
        self.assertEqual(visit.gps_state, 'nopin')

        blank.write({'partner_latitude': LAT, 'partner_longitude': LON})
        self.assertEqual(visit.gps_state, 'ok')

    def test_an_urgent_slip_is_counted_the_moment_it_turns_urgent(self):
        """`is_urgent_open` carried two stacked @api.depends. The outer one
        replaced the inner, so the stored flag never heard about priority or
        state changing. (2026-09-15)"""
        visit = self.env['lab.visit'].create({
            'partner_id': self.clinic.id, 'user_id': self.exec_user.id})
        product = self.env['product.product'].create(
            {'name': 'Stale Plate', 'sale_ok': True})
        case = self.env['lab.case'].create({
            'visit_id': visit.id, 'patient': 'Stale Patient',
            'line_ids': [(0, 0, {'product_id': product.id, 'ul': 'upper',
                                 'quantity': 1.0})]})
        self.assertFalse(case.is_urgent_open)
        case.priority = 'urgent'
        self.assertTrue(case.is_urgent_open)
        case.action_cancel()
        self.assertFalse(case.is_urgent_open, "a cancelled slip needs nobody")
