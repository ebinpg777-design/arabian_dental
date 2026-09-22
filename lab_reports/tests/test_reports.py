# -*- coding: utf-8 -*-
from datetime import datetime

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestLabReports(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Partner = cls.env['res.partner']
        cls.env.user.tz = False
        cls.env.company.partner_id.tz = False

    # ------------------------------------------------------------------ CR numbers
    def test_clinics_get_distinct_numbers_past_the_highest_in_use(self):
        self.Partner.create({'name': 'CR HIGH', 'cr_number': '8999'})
        clinics = self.Partner.create([{'name': 'CR A', 'is_clinic': True},
                                       {'name': 'CR B', 'is_clinic': True}])
        numbers = clinics.mapped('cr_number')
        self.assertEqual(len(set(numbers)), 2, numbers)
        self.assertTrue(all(n.isdigit() and len(n) >= 4 for n in numbers), numbers)
        self.assertTrue(all(int(n) > 8999 for n in numbers), numbers)
        later = self.Partner.create({'name': 'CR C', 'is_clinic': True})
        self.assertGreater(int(later.cr_number), max(int(n) for n in numbers))

    def test_numbers_come_from_the_sequence(self):
        sequence = self.env.ref('lab_reports.seq_partner_cr_number')
        clinic = self.Partner.create({'name': 'CR SEQ', 'is_clinic': True})
        self.assertGreater(sequence.number_next_actual, int(clinic.cr_number))

    def test_duplicating_a_clinic_does_not_copy_its_cr_number(self):
        clinic = self.Partner.create({'name': 'CR ORIG', 'is_clinic': True})
        self.assertTrue(clinic.cr_number)
        twin = clinic.copy()
        self.assertTrue(twin.cr_number, "a copied clinic is numbered afresh")
        self.assertNotEqual(twin.cr_number, clinic.cr_number)

    def test_a_given_cr_number_is_kept(self):
        clinic = self.Partner.create({'name': 'CR GIVEN', 'is_clinic': True, 'cr_number': '0042'})
        self.assertEqual(clinic.cr_number, '0042')

    # ------------------------------------------------------------------ printed times
    def test_printed_times_are_on_the_lab_clock(self):
        wizard = self.env['wizard.print.jobcard'].create({})
        stamp = datetime(2026, 1, 1, 20, 0)   # UTC
        self.assertEqual(wizard._dt(stamp), '02-01-2026 01:30', "no tz anywhere: Kolkata")
        self.assertEqual(wizard._dt(False), '')
        self.assertEqual(wizard.with_context(tz='UTC')._dt(stamp), '01-01-2026 20:00')
        self.env.company.partner_id.tz = 'Asia/Dubai'
        self.assertEqual(wizard._dt(stamp, '%H:%M'), '00:00')

    # ------------------------------------------------------------------ partner sheets
    def test_partner_sheets_render_without_a_mobile_field(self):
        partner = self.Partner.create({'name': 'VENDOR ONE', 'phone': '0484 111'})
        for method in ('print_customer', 'print_customer_registration', 'print_vendor',
                       'print_vendor_registration', 'print_vendor_evaluation'):
            wizard = self.env['wizard.print.partner'].with_context(active_ids=partner.ids).create({})
            action = getattr(wizard, method)()
            self.assertEqual(action['type'], 'ir.actions.act_url', method)
            self.assertTrue(wizard.report_data, method)
