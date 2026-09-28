# -*- coding: utf-8 -*-
"""A phone excused from giving a position: the time is taken, nothing is asked,
nothing is raised. The excuse is per person, and the trail stays off."""
from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from .test_fieldwork import LAT, LON


@tagged('post_install', '-at_install')
class TestPhoneExceptions(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        G = cls.env.ref
        base = G('base.group_user').id
        executive = G('lab_fieldwork.group_fieldwork_executive').id
        cls.excused = cls.env['res.users'].create({
            'name': 'Excused Exec', 'login': 'excused_exec',
            'fw_location_exception': True, 'fw_camera_exception': True,
            'group_ids': [(6, 0, [base, executive])]})
        cls.plain = cls.env['res.users'].create({
            'name': 'Plain Exec', 'login': 'plain_exec',
            'group_ids': [(6, 0, [base, executive])]})
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Excuse Clinic', 'is_clinic': True,
            'partner_latitude': LAT, 'partner_longitude': LON, 'visit_radius_m': 200.0})

    def _visit(self, user):
        return self.env['lab.visit'].create(
            {'partner_id': self.clinic.id, 'user_id': user.id})

    def test_an_excused_phone_checks_in_and_out_on_time_alone(self):
        v = self._visit(self.excused)
        v.do_check_in(False, False)
        self.assertEqual(v.state, 'open')
        self.assertTrue(v.check_in)
        self.assertEqual(v.gps_state, 'exempt', "not 'no location': nothing was asked")
        v.write({'outcome': 'visited'} if 'visited' in dict(
            v._fields['outcome'].selection) else {'outcome': v._fields['outcome'].selection[0][0]})
        v.do_check_out(False, False)
        self.assertEqual(v.state, 'done')
        self.assertEqual(v.out_gps_state, 'exempt')

    def test_the_excuse_is_per_person(self):
        v = self._visit(self.plain)
        with self.assertRaises(UserError):
            v.do_check_in(False, False)
        # and a real fix still works for the excused person if the phone gives one
        w = self._visit(self.excused)
        w.do_check_in(LAT, LON)
        self.assertEqual(w.gps_state, 'ok')

    def test_the_day_sheet_raises_no_location_flag_for_an_excused_phone(self):
        v = self._visit(self.excused)
        v.do_check_in(False, False)
        v.write({'outcome': v._fields['outcome'].selection[0][0]})
        v.do_check_out(False, False)
        sheet = self.env['lab.daily.update'].create({
            'user_id': self.excused.id, 'date': fields.Date.context_today(self.excused)})
        self.assertEqual(sheet.gps_far_count, 0)
        self.assertFalse(sheet.flag_gps)

    def test_the_trail_stays_off_for_an_excused_phone(self):
        self.env['ir.config_parameter'].sudo().set_param('lab_fieldwork.track_live', 'True')
        MyDay = self.env['lab.my.day']
        self.assertFalse(MyDay.with_user(self.excused).tracking_state()['enabled'])
        self.assertTrue(MyDay.with_user(self.plain).tracking_state()['enabled'])

    def test_the_session_reads_the_flags_the_person_cannot(self):
        flags = self.excused.with_user(self.excused)._fw_phone_exceptions()
        self.assertEqual(flags, {'location': True, 'camera': True})
        self.assertEqual(self.plain.with_user(self.plain)._fw_phone_exceptions(),
                         {'location': False, 'camera': False})
