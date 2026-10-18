# -*- coding: utf-8 -*-
"""A register whose models are printed already goes from Confirmed to its orders.

(client, 2026-10-15: "add a boolean Already Printed in the scan register - if a scan
register is already printed there is no need of the other steps; those scans can be
converted to orders directly. The boolean can be set in confirmed status")
"""
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import ScanCase


@tagged('post_install', '-at_install')
class TestAlreadyPrinted(ScanCase):

    def _confirmed(self, **vals):
        scan = self._register(patients=(('Tanvi', 'upper'), ('Aarav', 'lower')), **vals)
        scan.attachment_ids = [(4, self._file(scan=scan).id)]
        scan.action_confirm()
        return scan

    def test_an_already_printed_register_goes_from_confirmed_to_its_orders(self):
        scan = self._confirmed()
        scan.already_printed = True
        scan.action_create_orders()
        self.assertEqual(scan.state, 'done')
        orders = scan.line_ids.sale_order_id
        self.assertEqual(len(orders), 2, "one order per patient, as ever")
        self.assertEqual(set(orders.mapped('patient')), {'Tanvi', 'Aarav'})
        self.assertFalse(scan.print_order_ids, "no plate was ever made")
        self.assertFalse(scan.date_printed, "it was not printed here")
        self.assertTrue(self.env['ir.attachment'].search_count(
            [('res_model', '=', 'sale.order'), ('res_id', 'in', orders.ids)]), "the scans go to the orders")

    def test_without_the_tick_a_confirmed_register_still_waits_for_printing(self):
        scan = self._confirmed()
        with self.assertRaises(UserError):
            scan.action_create_orders()
        self.assertEqual(scan.state, 'confirmed')

    def test_it_is_set_on_a_confirmed_register_only(self):
        draft = self._register()
        with self.assertRaises(UserError, msg="not on a draft"):
            draft.already_printed = True
        printing = self._queued()
        with self.assertRaises(UserError, msg="not once it is in the printing room"):
            printing.already_printed = True
        scan = self._confirmed()
        scan.already_printed = True
        scan.already_printed = False
        self.assertFalse(scan.already_printed, "and untick it, while it is confirmed")

    def test_it_never_goes_to_the_printing_room(self):
        scan = self._confirmed()
        scan.already_printed = True
        with self.assertRaises(UserError):
            scan.action_send_to_print()
        with self.assertRaises(UserError):
            scan._send_to_print()
        with self.assertRaises(UserError):
            scan.line_ids.action_put_on_plate()
        scan.already_printed = False
        scan.line_ids.action_put_on_plate()            # unticked, it can go after all

    def test_the_product_is_still_needed_to_make_the_orders(self):
        scan = self._confirmed(product=False)
        scan.already_printed = True
        with self.assertRaises(UserError):
            scan.action_create_orders()
        scan.line_ids.write({'product_id': self.hawley.id})
        scan.action_create_orders()
        self.assertEqual(scan.state, 'done')

    def test_the_printing_lists_leave_it_out(self):
        waiting, printed = self._confirmed(), self._confirmed()
        printed.already_printed = True
        tile = self.env.ref('lab_scan_register.tile_scan_to_print')
        Scan = self.env['lab.scan']
        to_print = Scan.search(eval(tile.domain))
        self.assertIn(waiting, to_print)
        self.assertNotIn(printed, to_print, "nothing to print")
        room = self.env.ref('lab_scan_register.action_scan_printing')
        in_room = Scan.search(eval(room.domain))
        self.assertIn(waiting, in_room)
        self.assertNotIn(printed, in_room)
