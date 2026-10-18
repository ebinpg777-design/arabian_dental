# -*- coding: utf-8 -*-
"""The machines: numbered stickers, one plate at a time, a queue of plates to start
next, and the record a machine keeps of what came off it. (client, 2026-10-13)"""

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import ScanCase


@tagged('post_install', '-at_install')
class TestPrinting(ScanCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Printer = cls.env['lab.scan.printer']
        cls.p1 = cls.printer                                  # from ScanCase: 'Sonic Mini'
        cls.p2 = cls.Printer.create({'name': 'Zephyr Mars 4', 'brand': 'Elegoo Mars 4 Ultra', 'typical_hours': 2.0})
        # the lab's own machines are retired for the length of the test, so "the free
        # one" and "a name that fits one machine" are about these two only
        cls.Printer.search([('id', 'not in', (cls.p1 | cls.p2).ids)]).write({'state': 'retired'})
        # and the lab's own plates still being plated are set aside: a free machine
        # now offers any ready plate nobody planned, so they would answer for these
        cls.env['lab.scan.print.order'].search([('state', '=', 'draft')]).with_context(
            print_workflow=True).write({'state': 'cancel'})

    def test_a_machine_is_numbered_in_order_and_gets_its_sticker(self):
        a = self.Printer.create({'name': 'A'})
        b = self.Printer.create({'name': 'B'})
        self.assertTrue(a.code.startswith('3DP-'))
        self.assertEqual(int(b.code[4:]), int(a.code[4:]) + 1, "the next number")
        self.assertEqual(a.display_name, '%s · A' % a.code)
        for image in (a.sticker_qr, a.sticker_barcode):
            self.assertTrue(bytes(image).startswith(b'\x89PNG'), "a real PNG, drawn here")

    def test_the_sticker_report_carries_the_number_and_the_codes(self):
        html = self.env['ir.actions.report']._render_qweb_html(
            'lab_scan_register.action_report_printer_sticker', (self.p1 | self.p2).ids)[0].decode()
        for printer in (self.p1, self.p2):
            self.assertIn(printer.code, html)
            self.assertIn(printer.name, html)
        self.assertEqual(html.count('data:image/png;base64,'), 4, "a QR and a barcode per machine")
        self.assertEqual(html.count('page-break-after:always'), 1, "one sticker per page, no blank last page")
        action = self.p1.action_print_sticker()
        self.assertEqual(action['report_name'], 'lab_scan_register.report_printer_sticker')

    def test_a_scanned_sticker_names_its_machine(self):
        self.assertEqual(self.Printer._by_code(self.p2.code), self.p2)
        self.assertEqual(self.Printer._by_code(self.p2.code.lower()), self.p2, "scanners shout, people don't")
        self.assertEqual(self.Printer._by_code('zephyr'), self.p2, "a name that fits one machine")
        self.assertFalse(self.Printer._by_code('3DP-9999'))
        self.Printer.create({'name': 'Zephyr Mars 5'})
        self.assertFalse(self.Printer._by_code('zephyr'), "two machines fit: nobody is picked")

    def test_a_machine_under_maintenance_takes_no_plate(self):
        order = self._plate(self._queued().line_ids, printer=self.p1)
        self.p1.action_set_maintenance()
        with self.assertRaises(UserError):
            self._start(order)
        self.p1.action_set_ready()
        self._start(order)
        self.assertEqual(order.state, 'printing')
        self.p1.action_set_maintenance()
        self.assertEqual(order.state, 'printing', "maintenance stops the next plate, not this one")
        self.assertIn(order.name, self.p1.message_ids[0].body)

    def test_the_machine_says_what_it_is_doing(self):
        self.assertEqual((self.p1.running_count, self.p1.is_busy, self.p1.status_text), (0, False, 'Free'))
        order = self._plate(self._queued().line_ids, printer=self.p1)
        self.assertEqual((self.p1.queued_count, self.p1.status_text), (1, 'Free · 1 queued'))
        self._start(order)
        self.assertEqual(self.p1.running_order_id, order)
        self.assertEqual((self.p1.running_count, self.p1.is_busy), (1, True))
        self.assertEqual(self.p1.expected_free_at, order.expected_end)
        self.assertTrue(self.p1.status_text.startswith('Printing %s · off ' % order.name))
        self.assertIn(self.p1, self.Printer.search([('running_count', '>', 0)]))
        self.assertIn(self.p2, self.Printer.search([('running_count', '=', 0)]))
        self.assertEqual(self.Printer.search([('running_order_id', '=', order.id)]), self.p1)

    def test_start_next_plate_takes_the_first_waiting_plate(self):
        first = self._plate(self._queued().line_ids, printer=self.p1)
        second = self._plate(self._queued(patients=(('Diya', 'x'),)).line_ids, printer=self.p1)
        second.sequence, first.sequence = 1, 5
        action = self.p1.action_start_next()
        self.assertEqual(action['context']['default_order_id'], second.id, "the queue's order, not the plating order")
        self._start(second)
        with self.assertRaises(UserError, msg="the machine is busy"):
            self._start(first)
        self._off(second)
        self.assertEqual(self.p1.action_start_next()['context']['default_order_id'], first.id)
        self.p2.action_set_ready()
        with self.assertRaises(UserError):
            self.p2.action_start_next()

    def test_the_free_reliable_machine_is_offered_first(self):
        self.assertIn(self.Printer._suggest(), self.p1 | self.p2)
        self._start(self._plate(self._queued().line_ids, printer=self.p1))
        self.assertEqual(self.Printer._suggest(), self.p2, "the one with nothing on it")
        self.p2.action_set_maintenance()
        self.assertEqual(self.Printer._suggest(), self.p1, "busy beats down for maintenance")

    def test_the_machine_record_counts_plates_and_models(self):
        a, b = self._queued(), self._queued(patients=(('Diya', 'x'),))
        self._off(self._start(self._plate((a | b).line_ids, printer=self.p1)), failed=b.line_ids)
        self.assertEqual((self.p1.print_count, self.p1.done_count, self.p1.failed_count), (1, 1, 0),
                         "a plate that lost one of two models still printed")
        self.assertEqual((self.p1.model_done_count, self.p1.model_failed_count, self.p1.failure_rate), (1, 1, 50.0))
        self.assertEqual(self.p1.plate_failure_rate, 0.0)
        self.assertEqual(self.p1.done_recent, 1)
        self.assertTrue(self.p1.last_print_date)
        failed = self.p1.action_view_failed()
        self.assertEqual(failed['res_model'], 'lab.scan.print.order.line')
        self.assertEqual(self.env[failed['res_model']].search(failed['domain']).scan_id, b)
        plates = self.p1.action_view_orders()
        self.assertEqual(self.env[plates['res_model']].search_count(plates['domain']), 1)
