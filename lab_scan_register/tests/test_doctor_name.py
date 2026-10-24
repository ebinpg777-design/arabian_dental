# -*- coding: utf-8 -*-
"""The doctor's name is typed in when the scan arrives, and the work goes ahead on it.

(client, 2026-10-23: "Add a char field in Scan register to enter the doctor name. partner_id
should be mandatory only before creating order, because the actual partner will be identified
at the last stage sometimes. Work should not be delayed for finding the partner.")
"""
from lxml import etree

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tools.safe_eval import safe_eval

from .common import ScanCase

DOCTOR = 'Dr. Arun Varma'


@tagged('post_install', '-at_install')
class TestDoctorName(ScanCase):

    def _unmatched(self, **vals):
        """Confirmed on a typed name, with no contact picked yet."""
        scan = self._register(partner_id=False, doctor_name=DOCTOR, **vals)
        scan.attachment_ids = [(4, self._file(scan=scan).id)]
        scan.action_confirm()
        return scan

    # ------------------------------------------------------------- the work goes ahead
    def test_a_register_on_a_typed_name_goes_to_printing(self):
        scan = self._unmatched()
        self.assertEqual((scan.state, scan.partner_id.id, scan.doctor_name), ('confirmed', False, DOCTOR))
        scan._send_to_print()
        self.assertEqual(scan.state, 'printing')

    def test_the_name_can_be_typed_or_corrected_until_the_orders_are_made(self):
        scan = self._unmatched()
        scan.with_user(self.desk).doctor_name = 'Dr. Arun Varma (Smile Care)'
        self.assertEqual(scan.doctor_name, 'Dr. Arun Varma (Smile Care)', "confirmed, and still open to it")
        scan.with_user(self.desk).partner_id = self.clinic
        scan.already_printed = True
        scan.action_create_orders()
        self.assertEqual(scan.state, 'done')
        with self.assertRaises(UserError, msg="ordered: the orders carry the contact now"):
            scan.with_user(self.desk).doctor_name = 'Somebody else'

    def test_create_orders_asks_for_the_contact_by_the_name_it_was_given(self):
        scan = self._unmatched()
        scan.already_printed = True
        with self.assertRaises(UserError) as caught:
            scan.action_create_orders()
        self.assertIn(DOCTOR, str(caught.exception))
        self.assertFalse(scan.line_ids.sale_order_id, "no order without the contact")
        scan.partner_id = self.clinic
        scan.action_create_orders()
        self.assertEqual(scan.line_ids.sale_order_id.partner_id, self.clinic)

    # ------------------------------------------------------------- who it is for, everywhere
    def test_the_contact_once_picked_and_the_name_until_then(self):
        scan = self._register(partner_id=False, doctor_name=DOCTOR)
        self.assertEqual(scan._doctor_label(), DOCTOR)
        scan.partner_id = self.clinic
        self.assertEqual(scan._doctor_label(), self.clinic.display_name)
        self.assertEqual(self._register(partner_id=False)._doctor_label(), '')

    def test_the_plate_ticket_and_the_print_queue_name_the_doctor(self):
        scan = self._register(partner_id=False, doctor_name=DOCTOR)
        scan.attachment_ids = [(4, self._file(scan=scan).id)]
        scan.action_confirm()
        scan._send_to_print()
        board = self.env['lab.scan'].get_dashboard()
        row = next(r for r in board['print_next'] if r['scan_id'] == scan.id)
        self.assertEqual(row['doctor'], DOCTOR, "the printing room's queue")
        plate = self._plate(scan.line_ids)
        self.assertEqual(plate.line_ids.doctor_name, DOCTOR)
        html = self.env['ir.actions.report']._render_qweb_html(
            'lab_scan_register.report_plate_ticket', plate.ids)[0]
        self.assertIn(DOCTOR, html.decode() if isinstance(html, bytes) else html, "the plate ticket")

    def test_the_form_always_offers_the_name_and_search_finds_it(self):
        Scan = self.env['lab.scan']
        form = etree.fromstring(Scan.get_view(view_type='form')['arch'])
        field = form.xpath("//field[@name='doctor_name']")[0]
        self.assertFalse(field.get('invisible'), "typed in, not only filled from an e-mail")
        self.assertEqual(field.get('readonly'), "state in ('done', 'cancel')")
        for kind in ('list', 'kanban'):
            arch = etree.fromstring(Scan.get_view(view_type=kind)['arch'])
            self.assertTrue(arch.xpath("//field[@name='doctor_name']"), kind)
        # the search box's Doctor / Clinic finds a register by the typed name as well
        search = etree.fromstring(Scan.get_view(view_type='search')['arch'])
        box = search.xpath("//field[@name='partner_id']")[0]
        domain = safe_eval(box.get('filter_domain'), {'self': 'arun varma'})
        unmatched = self._register(partner_id=False, doctor_name=DOCTOR)
        matched = self._register()
        found = Scan.search(domain)
        self.assertIn(unmatched, found)
        self.assertNotIn(matched, found)
        self.assertIn(matched, Scan.search(safe_eval(box.get('filter_domain'), {'self': self.clinic.name})))
