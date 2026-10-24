# -*- coding: utf-8 -*-
"""The doctor or clinic is needed to make the orders - and not before.

(client, 2026-10-23: "in scan registration the partner should be mandatory only before
Create Orders from the scan register")
"""
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import ScanCase


@tagged('post_install', '-at_install')
class TestClinicAtOrders(ScanCase):

    def _unknown(self):
        """Confirmed with nobody known yet - the scan came in before its sender was matched."""
        scan = self._register(partner_id=False)
        scan.attachment_ids = [(4, self._file(scan=scan).id)]
        scan.action_confirm()
        return scan

    def test_a_register_goes_to_printing_without_a_clinic(self):
        scan = self._unknown()
        self.assertEqual(scan.state, 'confirmed')
        scan._send_to_print()
        self.assertEqual(scan.state, 'printing')

    def test_the_clinic_is_set_after_confirm_until_the_orders_are_made(self):
        scan = self._unknown()
        scan.with_user(self.desk).partner_id = self.clinic
        self.assertEqual(scan.partner_id, self.clinic)
        scan.already_printed = True
        scan.action_create_orders()
        self.assertEqual(scan.line_ids.sale_order_id.partner_id, self.clinic)
        with self.assertRaises(UserError, msg="ordered: the orders carry it now"):
            scan.partner_id = self.other_clinic

    def test_the_orders_are_not_made_without_one(self):
        scan = self._unknown()
        scan.already_printed = True
        with self.assertRaises(UserError):
            scan.action_create_orders()
        self.assertEqual(scan.state, 'confirmed')
        self.assertFalse(scan.line_ids.sale_order_id)

    def test_linking_an_order_brings_its_clinic(self):
        scan = self._unknown()
        order = self.env['sale.order'].create({
            'partner_id': self.clinic.id, 'patient': 'Tara',
            'order_line': [(0, 0, {'product_id': self.hawley.id, 'product_uom_qty': 1})]})
        wiz = self.env['lab.scan.link.orders'].with_context(default_scan_id=scan.id).create({})
        self.assertFalse(wiz.line_ids.sale_order_id, "nothing suggested without a clinic to look under")
        wiz.line_ids[0].sale_order_id = order
        wiz.action_link()
        self.assertEqual(scan.partner_id, self.clinic, "the clinic the order was booked for")
        self.assertEqual(scan.state, 'done')
