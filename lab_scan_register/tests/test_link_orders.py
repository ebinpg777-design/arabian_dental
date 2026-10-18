# -*- coding: utf-8 -*-
"""Orders booked straight into Sales, linked to the register that holds their scans.

(client, 2026-10-15: "a button in scan register to map the SOs if the SOs are directly
created, not from scan registers")
"""
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import ScanCase


@tagged('post_install', '-at_install')
class TestLinkOrders(ScanCase):

    def _booked(self, patient, partner=None):
        """An order typed at the counter, the register nowhere in sight."""
        return self.env['sale.order'].create({
            'partner_id': (partner or self.clinic).id, 'patient': patient,
            'order_line': [(0, 0, {'product_id': self.hawley.id, 'product_uom_qty': 1})]})

    def _confirmed(self):
        scan = self._register(patients=(('Tanvi', 'upper'), ('Aarav', 'lower')))
        scan.attachment_ids = [(4, self._file(scan=scan).id)]
        scan.action_confirm()
        return scan

    def _wizard(self, scan):
        action = scan.action_open_link_orders()
        return self.env[action['res_model']].with_context(action['context']).create({})

    def test_the_clinics_orders_for_its_patients_are_found_and_linked(self):
        scan = self._confirmed()
        tanvi, aarav = self._booked(' tanvi (UL)'), self._booked('Aarav')
        self._booked('Tanvi', partner=self.other_clinic)            # another clinic's patient
        wiz = self._wizard(scan)
        rows = {r.patient_name: r.sale_order_id for r in wiz.line_ids}
        self.assertEqual(rows, {'Tanvi': tanvi, 'Aarav': aarav}, "found by the patient's name, at this clinic")
        wiz.action_link()
        self.assertEqual(scan.line_ids.sale_order_id, tanvi | aarav)
        self.assertEqual((tanvi.scan_id, tanvi.scan_line_id.patient_name), (scan, 'Tanvi'))
        self.assertEqual(scan.state, 'done', "every patient has an order: nothing is left to make")
        self.assertEqual(scan.order_count, 2)
        self.assertTrue(self.env['ir.attachment'].search_count(
            [('res_model', '=', 'sale.order'), ('res_id', '=', aarav.id)]), "the scans are on the order")

    def test_a_patient_left_without_an_order_is_made_by_create_orders(self):
        scan = self._confirmed()
        tanvi = self._booked('Tanvi')
        self._wizard(scan).action_link()
        self.assertEqual(scan.state, 'confirmed', "Aarav has no order yet")
        scan.already_printed = True
        scan.action_create_orders()
        self.assertEqual(scan.state, 'done')
        self.assertEqual(len(scan.line_ids.sale_order_id), 2)
        self.assertIn(tanvi, scan.line_ids.sale_order_id, "the linked order is not made twice")

    def test_an_order_of_another_register_is_neither_offered_nor_taken(self):
        first, second = self._confirmed(), self._confirmed()
        order = self._booked('Tanvi')
        self._wizard(first).action_link()
        self.assertEqual(order.scan_id, first)
        self.assertFalse(self._wizard(second).line_ids.sale_order_id, "not offered again")
        line = second.line_ids.filtered(lambda l: l.patient_name == 'Tanvi')
        with self.assertRaises(UserError):
            second._link_orders([(line, order)])

    def test_one_order_is_one_patient(self):
        scan = self._confirmed()
        order = self._booked('Tanvi')
        with self.assertRaises(UserError):
            scan._link_orders([(scan.line_ids[0], order), (scan.line_ids[1], order)])

    def test_a_register_on_a_machine_finishes_printing_first(self):
        scan = self._queued(patients=(('Tanvi', 'upper'),))
        order = self._booked('Tanvi')
        self._wizard(scan).action_link()
        self.assertEqual(scan.line_ids.sale_order_id, order)
        self.assertEqual(scan.state, 'printing', "printing goes on; Create Orders closes it")

    def test_an_ordered_register_has_nothing_to_link(self):
        scan = self._confirmed()
        self._booked('Tanvi'), self._booked('Aarav')
        self._wizard(scan).action_link()
        with self.assertRaises(UserError):
            scan.action_open_link_orders()
