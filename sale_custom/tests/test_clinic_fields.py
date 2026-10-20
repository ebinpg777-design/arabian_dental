# -*- coding: utf-8 -*-
"""v17 dental_sale's clinic fields on the clinic itself: Doctor, Billing Name, District.

(client, 2026-10-20: the migration had made the doctor a child contact and kept the
district only as a tag)
"""
from psycopg2 import IntegrityError

from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged('post_install', '-at_install')
class TestClinicFields(TransactionCase):

    def test_a_clinic_carries_its_doctor_billing_name_and_district(self):
        district = self.env['lab.district'].create({'name': 'CF MALAPPURAM'})
        clinic = self.env['res.partner'].create({
            'name': 'CF Dental Care', 'doctor_name': 'DR. ASHA', 'billing_name': 'CF Dental',
            'district_id': district.id})
        self.assertEqual((clinic.doctor_name, clinic.billing_name, clinic.district_id), ('DR. ASHA', 'CF Dental', district))
        self.assertFalse(clinic.child_ids, "the doctor is a field, not a contact")

    def test_a_district_name_is_unique(self):
        self.env['lab.district'].create({'name': 'CF PALAKKAD'})
        with mute_logger('odoo.sql_db'), self.assertRaises(IntegrityError), self.cr.savepoint():
            self.env['lab.district'].create({'name': 'CF PALAKKAD'})

    def test_the_contact_form_and_search_show_them(self):
        form = self.env['res.partner'].get_view(view_type='form')['arch']
        search = self.env['res.partner'].get_view(view_type='search')['arch']
        for name in ('doctor_name', 'billing_name', 'district_id'):
            self.assertIn(name, form)
        self.assertIn('group_district', search)
