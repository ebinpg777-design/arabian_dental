# -*- coding: utf-8 -*-
"""Print orders: several registers' patients on one plate, one machine, printed once.
(client, 2026-10-13: "multiple scans are printed together as a print order; the
machines should be specified in the print orders")"""
import io
import itertools
import zipfile
from datetime import datetime, timedelta
from unittest.mock import patch

from psycopg2 import IntegrityError

from lxml import etree

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form, tagged
from odoo.tools import mute_logger

from ..models import parsers
from ..models.print_order import LAB_HOURS
from .common import ORDER, ScanCase
from .test_slice_file import ctb


@tagged('post_install', '-at_install')
class TestPrintOrders(ScanCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Order = cls.env['lab.scan.print.order']
        cls.Line = cls.env['lab.scan.print.order.line']
        cls.Printer = cls.env['lab.scan.printer']
        cls.p1 = cls.printer
        cls.p1.write({'profile_name': 'Phrozen Sonic Mini 8K', 'bed_x_mm': 68.04, 'bed_y_mm': 120.96,
                      'resolution_x': 1620, 'resolution_y': 2560, 'plate_capacity': 8})
        cls.p2 = cls.Printer.create({'name': 'Mars 4', 'profile_name': 'Elegoo Mars 4 Ultra', 'typical_hours': 2.0})
        cls.Printer.search([('id', 'not in', (cls.p1 | cls.p2).ids)]).write({'state': 'retired'})
        # and the lab's own plates still being plated are set aside: a free machine
        # now offers any ready plate nobody planned, so they would answer for these
        cls.env['lab.scan.print.order'].search([('state', '=', 'draft')]).with_context(
            print_workflow=True).write({'state': 'cancel'})

    def _patients(self, *names, **vals):
        """One register sent for printing, a patient per name, upper and lower each."""
        return self._queued(patients=tuple((n, 'Upper & Lower Hawley') for n in names), **vals)

    def _backdate(self, order, hours):
        order.with_context(print_workflow=True).write(
            {'date_start': fields.Datetime.now() - timedelta(hours=hours)})

    # ------------------------------------------------------------------ plating
    def test_send_to_printing_opens_the_dialog_and_one_plate_holds_several_registers(self):
        a = self._register(patients=(('Anu', 'Upper & Lower Hawley'), ('Binu', 'lower retainer')))
        b = self._register(patients=(('Cini', 'U/L Essix'),))
        for scan in a | b:
            scan.attachment_ids = [(4, self._file().id)]
            scan.action_confirm()
        action = (a | b).action_send_to_print()
        self.assertEqual(action['res_model'], 'lab.scan.print.order.make')
        wiz = self.env[action['res_model']].with_context(**action['context']).create({'printer_id': self.p1.id})
        self.assertEqual(wiz.line_ids, (a | b).line_ids, "every patient of the registers, ticked")
        self.assertEqual((wiz.model_count, wiz.capacity_text), (5, '5 of 8 models'))
        order = self.Order.browse(wiz.action_create()['res_id'])
        self.assertTrue(order.name.startswith('PRINT/'))
        self.assertEqual((order.state, order.printer_id, order.register_count, order.patient_count, order.model_count),
                         ('draft', self.p1, 2, 3, 5))
        self.assertEqual(set(order.line_ids.mapped('attempt')), {1})
        self.assertEqual(sorted(order.line_ids.mapped('part')), ['both', 'both', 'lower'])
        for scan in a | b:
            with self.subTest(scan=scan.name):
                self.assertEqual(scan.state, 'printing')
                self.assertTrue(scan.date_print_start)
                self.assertIn(order.name, scan.message_ids[0].body)
                self.assertEqual(set(scan.line_ids.mapped('print_state')), {'plated'})

    def test_queue_only_sends_without_a_plate(self):
        scan = self._register()
        scan.attachment_ids = [(4, self._file().id)]
        scan.action_confirm()
        wiz = self.env['lab.scan.print.order.make'].with_context(
            active_model='lab.scan', active_ids=scan.ids).create({})
        wiz.action_queue_only()
        self.assertEqual(scan.state, 'printing')
        self.assertFalse(self.Order.search([('scan_ids', 'in', scan.ids)]))
        self.assertEqual(scan.line_ids.print_state, 'to_plate')
        self.assertEqual(scan.waiting_count, 1)
        self.assertIn(scan.line_ids, self.Order._waiting_lines())

    def test_a_patient_sits_on_one_open_plate_at_most(self):
        scan = self._patients('Anu')
        order = self._plate(scan.line_ids)
        with self.assertRaises(UserError):
            self._plate(scan.line_ids, printer=self.p2)
        other = self.Order.create({'printer_id': self.p2.id})
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'), self.env.cr.savepoint():
            self.Line.with_context(print_workflow=True).create({'order_id': other.id, 'scan_line_id': scan.line_ids.id})
        self._fail(self._start(order), reason='power')
        self._plate(scan.line_ids, printer=self.p2)

    def test_the_waiting_list_puts_replates_first_then_urgent_then_earliest_delivery(self):
        today = fields.Date.context_today(self.env.user)
        late = self._patients('Late')
        late.line_ids.with_context(scan_workflow=True).write({'date_delivery': today + timedelta(days=9)})
        soon = self._patients('Soon')
        soon.line_ids.with_context(scan_workflow=True).write({'date_delivery': today + timedelta(days=2)})
        urgent = self._patients('Urgent')
        urgent.line_ids.with_context(scan_workflow=True).write({'priority': '1'})
        failed = self._patients('Failed')
        self._off(self._start(self._plate(failed.line_ids)), failed=failed.line_ids)
        mine = (late | soon | urgent | failed).line_ids
        order = [l.patient_name for l in self.Order._waiting_lines() if l in mine]
        self.assertEqual(order, ['Failed', 'Urgent', 'Soon', 'Late'])

    def test_the_gauge_warns_past_capacity_but_never_blocks(self):
        scan = self._patients('A', 'B', 'C', 'D', 'E')
        order = self._plate(scan.line_ids)
        self.assertEqual((order.model_count, order.capacity, order.over_capacity), (10, 8, True))
        self.assertEqual(order.state, 'draft', "made all the same")

    # ------------------------------------------------------------------ on the machine
    def test_a_plate_starts_only_on_a_ready_free_machine(self):
        first = self._plate(self._patients('A').line_ids)
        second = self._plate(self._patients('B').line_ids)
        self.p1.action_set_maintenance()
        with self.assertRaises(UserError):
            self._start(first)
        self.p1.action_set_ready()
        self._start(first)
        with self.assertRaisesRegex(UserError, first.name):
            self._start(second)
        self.assertEqual((self.p1.running_order_id, self.p1.is_busy), (first, True))
        self.assertEqual(first.scan_ids.state, 'in_progress')
        self.assertEqual(first.scan_ids.date_in_progress, first.date_start)
        # two operators at once: the database holds the line
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'), self.env.cr.savepoint():
            second.with_context(print_workflow=True).write({'state': 'printing'})
            self.env.flush_all()

    def test_scanning_another_sticker_moves_the_plate_and_a_ticket_picks_it(self):
        order = self._plate(self._patients('A').line_ids)
        form = Form(self.env['lab.scan.print.order.start'].with_context(
            active_model='lab.scan.print.order', active_ids=order.ids))
        self.assertEqual((form.order_id, form.printer_id), (order, self.p1))
        form.code = self.p2.code
        self.assertEqual(form.printer_id, self.p2)
        self.assertFalse(form.code, "cleared for the next scan")
        form.code = 'no-such-thing'
        self.assertEqual(form.printer_id, self.p2, "an unknown code changes nothing")
        form.code = order.name.replace('/', '-')
        self.assertEqual(form.order_id, order, "a plate ticket picks the plate")
        form.save().action_start()
        self.assertEqual(order.printer_id, self.p2)
        self.assertIn('Planned on', ''.join(order.message_ids.mapped('body')))

    def test_reading_the_file_head_fills_the_plate(self):
        order = self._plate(self._patients('A').line_ids)
        order._apply_slice_head(ctb(), 'PRINT-0001_3DP-01.ctb', 52_000_000)
        self.assertTrue(order.slice_parsed)
        self.assertEqual((order.slice_format, order.layer_count, order.estimated_hours, order.resin_ml),
                         ('ctb', 500, 2.0, 12.5))
        self.assertEqual(order.machine_name, 'Phrozen Sonic Mini 8K')
        self.assertTrue(bytes(order.preview_image).startswith(b'\x89PNG'))
        self.assertTrue(order.date_sliced)
        self.assertEqual(order.slice_size_text, '49.6 MB')
        self.assertTrue(order.is_ready)
        self.assertEqual(order.suggested_filename, '%s_%s.ctb' % (order.name.replace('/', '-'), self.p1.code))
        self.assertFalse(order.profile_mismatch, "sliced for this very machine")
        order.estimated_hours = 2.5
        order._apply_slice_head(None)
        self.assertFalse(order.slice_filename or order.layer_count or order.preview_image or order.machine_name)
        self.assertEqual(order.estimated_hours, 2.5, "a typed estimate stays")

    def test_an_encrypted_or_foreign_head_is_kept_but_not_read_and_the_figures_can_be_typed(self):
        order = self._plate(self._patients('A').line_ids)
        head = b'\x07\x01\xfd\x12' + b'\x00' * 400
        order._apply_slice_head(head, 'plate.ctb', 0)
        self.assertEqual((order.slice_format, order.slice_parsed), ('ctb-encrypted', False))
        self.assertIn('encrypted', order.slice_error)
        self.assertEqual(order.slice_filename, 'plate.ctb')
        self.assertFalse(order.is_ready, "no time known yet")
        order.write({'estimated_hours': 2.5, 'resin_ml': 30.0})
        self.assertTrue(order.is_ready)
        self._start(order)
        self.assertEqual(order.expected_end, order.date_start + timedelta(hours=2.5))
        other = self._plate(self._patients('B').line_ids, printer=self.p2)
        other._apply_slice_head(b'solid cube\n', 'cube.stl', 0)
        self.assertTrue(other.slice_error)
        self.assertFalse(other.slice_parsed)

    def test_a_file_sliced_for_another_machine_warns_and_does_not_block(self):
        order = self._plate(self._patients('A').line_ids, printer=self.p2)
        order._apply_slice_head(ctb(), 'a.ctb', 0)
        self.assertTrue(order.profile_mismatch)
        self.assertIn('Phrozen Sonic Mini 8K', order.profile_warning)
        self.assertIn('Elegoo Mars 4 Ultra', order.profile_warning)
        start = self.env['lab.scan.print.order.start'].with_context(
            active_model='lab.scan.print.order', active_ids=order.ids).create({})
        self.assertTrue(start.profile_warning)
        start.printer_id = self.p1
        self.assertFalse(start.profile_warning, "on the machine it was sliced for")
        self.p2.profile_name = False
        order.invalidate_recordset(['profile_mismatch', 'profile_warning'])
        self.assertFalse(order.profile_mismatch, "a blank profile checks nothing")
        self.p2.profile_name = 'Elegoo Mars 4 Ultra'
        self._start(order)
        self.assertEqual(order.state, 'printing', "warned, never blocked")

    def test_expected_off_and_running_long_follow_the_file(self):
        order = self._plate(self._patients('A').line_ids)
        order.estimated_hours = 2.0
        self._start(order)
        self.assertEqual(order.expected_end, order.date_start + timedelta(hours=2))
        self._backdate(order, 2.4)
        self.assertTrue(order.due_off)
        self.assertFalse(order.running_long)
        self._backdate(order, 2.8)
        self.assertTrue(order.running_long)
        self.assertIn(order, self.Order.search([('running_long', '=', True)]))
        self.assertNotIn(order, self.Order.search([('running_long', '!=', True)]))
        self.assertIn(order, self.Order.search([('due_off', '=', True)]))
        # no file, no typed time: the machine's typical print, twice over
        plain = self._plate(self._patients('B').line_ids, printer=self.p2)
        self._start(plain)
        self.assertEqual(plain.expected_end, plain.date_start + timedelta(hours=2))
        self._backdate(plain, 3.5)
        self.assertFalse(plain.running_long)
        self._backdate(plain, 4.5)
        self.assertTrue(plain.running_long)

    # ------------------------------------------------------------------ off the machine
    def test_plate_off_frees_the_machine_and_the_plate_being_printed_prints_the_register(self):
        scan = self._patients('A')
        order = self._start(self._plate(scan.line_ids))
        self._backdate(order, 2.5)
        self.assertFalse(self._off(order))
        self.assertEqual(order.state, 'washing')
        self.assertTrue(order.date_end)
        self.assertAlmostEqual(order.duration_hours, 2.5, places=1)
        self.assertFalse(self.p1.running_order_id, "free the moment the plate is off")
        self.assertEqual(order.line_ids.result, 'ok')
        self.assertEqual(scan.state, 'in_progress', "in wash & cure: the print is still in progress")
        self.assertFalse(scan.date_printed)
        self.assertAlmostEqual(scan.print_hours, 2.5, places=1)
        self.assertEqual(scan.line_ids.print_state, 'washing')
        self.assertIn('2h 30m', ''.join(scan.message_ids.mapped('body')))
        order.action_cure()
        self.assertEqual((order.state, scan.line_ids.print_state, scan.state), ('done', 'printed', 'printed'),
                         "the print order Printed makes the register Printed")
        self.assertTrue(order.date_cured)
        self.assertTrue(scan.date_printed)
        self.assertTrue(scan.date_in_progress, "kept, so the print can be timed")
        scan.action_create_orders()
        sale = scan.line_ids.sale_order_id
        self.assertEqual((sale.print_order_id, sale.print_printer_id), (order, self.p1))

    def test_already_washed_and_cured_skips_the_step(self):
        order = self._start(self._plate(self._patients('A').line_ids))
        self._off(order, skip_cure=True)
        self.assertEqual(order.state, 'done')
        self.assertTrue(order.date_cured)
        self.assertEqual(order.scan_ids.state, 'printed')

    def test_a_partial_failure_replates_only_the_failed_patients(self):
        a = self._patients('Anu')
        b = self._patients('Binu', 'Bindu')
        c = self._patients('Cini')
        binu, bindu = b.line_ids.sorted('sequence')
        order = self._start(self._plate((a | b | c).line_ids))
        replate = self._off(order, failed=[(binu, 'lower', 'supports'), c.line_ids], reason='adhesion', replate=True)
        self.assertEqual(order.state, 'washing')
        self.assertTrue(order.is_partial)
        self.assertEqual((order.printed_count, order.failed_count), (2, 2))
        binu_line = order.line_ids.filtered(lambda l: l.scan_line_id == binu)
        cini_line = order.line_ids.filtered(lambda l: l.scan_id == c)
        self.assertEqual((binu_line.failure_reason, binu_line.failed_part), ('supports', 'lower'), "its own reason")
        self.assertEqual((cini_line.failure_reason, cini_line.failed_part), ('adhesion', 'all'), "the plate's")
        # the re-plate: just the lost models, on a new plate whose machine is picked at
        # Start (client, 2026-10-14: "machine only to be set before print in progress")
        self.assertEqual((replate.state, replate.printer_id, replate.patient_count), ('draft', self.Printer, 2))
        new_binu = replate.line_ids.filtered(lambda l: l.scan_line_id == binu)
        self.assertEqual((new_binu.attempt, new_binu.part, new_binu.model_count), (2, 'lower', 1))
        self.assertEqual(new_binu.replated_from_id, binu_line)
        self.assertEqual(binu_line.replated_by_id, new_binu)
        self.assertEqual(replate.replate_of_ids, order)
        self.assertEqual((a.state, b.state, c.state), ('in_progress', 'in_progress', 'printing'),
                         "A and Bindu in wash & cure; Cini waits on the re-plate")
        self.assertEqual((a.failed_print_count, b.failed_print_count, c.failed_print_count), (0, 1, 1))
        self.assertIn('Binu (lower)', b.last_print_failure)
        self.assertIn('Supports failed', b.last_print_failure)
        self.assertIn(replate.name, b.last_print_failure)
        tile = self.env.ref('lab_scan_register.tile_scan_print_failed')
        self.assertEqual(self.Scan.search(eval(tile.domain)) & (a | b | c), c,
                         "back in the queue with a failed model: C (B is still in progress)")
        self.assertEqual(self.Line.search([('replated_by_id', '=', False), ('result', '=', 'failed')]) & order.line_ids,
                         self.Line, "both re-plated")
        self._off(self._start(replate))
        self.assertEqual((b.state, c.state), ('in_progress', 'in_progress'))
        (order | replate).action_cure()
        self.assertEqual((a.state, b.state, c.state), ('printed', 'printed', 'printed'))
        self.assertFalse(b.last_print_failure)

    def test_plate_off_with_nothing_good_is_a_failed_plate(self):
        scan = self._patients('A', 'B')
        order = self._start(self._plate(scan.line_ids))
        self._off(order, failed=scan.line_ids, reason='material')
        self.assertEqual((order.state, order.failure_reason), ('failed', 'material'))
        self.assertEqual(scan.state, 'printing')
        self.assertEqual(set(scan.line_ids.mapped('print_state')), {'to_plate'})
        self.assertEqual(set(scan.line_ids.mapped('last_failure')), {'Ran out of resin / material'})

    def test_a_whole_plate_fails_mid_print_and_restarts_with_the_same_figures(self):
        scan = self._patients('A', 'B')
        order = self._plate(scan.line_ids)
        order._apply_slice_head(ctb(), 'a.ctb', 0)
        self._start(order)
        restart = self._fail(order, reason='power', restart=True, printer=self.p2)
        self.assertEqual(order.state, 'failed')
        self.assertEqual(set(order.line_ids.mapped('result')), {'failed'})
        self.assertEqual(set(order.line_ids.mapped('failure_reason')), {'power'})
        self.assertEqual((restart.state, restart.printer_id, restart.restarted_from_id), ('printing', self.p2, order))
        self.assertEqual(order.restart_id, restart)
        self.assertEqual((restart.estimated_hours, restart.layer_count, restart.slice_filename), (2.0, 500, 'a.ctb'))
        self.assertTrue(restart.preview_image)
        self.assertEqual(set(restart.line_ids.mapped('attempt')), {2})
        self.assertEqual(restart.line_ids.replated_from_id, order.line_ids)
        self.assertEqual((scan.state, scan.printer_id), ('in_progress', self.p2))
        self.assertEqual((self.p1.failed_count, self.p1.model_failed_count), (1, 2))

    # ------------------------------------------------------------------ the register's status
    def test_a_register_split_over_two_plates_rolls_up_correctly(self):
        scan = self._patients('P', 'Q')
        p, q = scan.line_ids.sorted('sequence')
        first = self._start(self._plate(p))
        self.assertEqual((scan.state, p.print_state, q.print_state), ('in_progress', 'printing', 'to_plate'))
        self._off(first)
        self.assertEqual(scan.state, 'in_progress', "P in wash & cure")
        first.action_cure()
        self.assertEqual(scan.state, 'printing', "P printed, Q still waits for a plate")
        second = self._start(self._plate(q, printer=self.p2))
        self.assertEqual(scan.state, 'in_progress')
        self._fail(second, reason='machine')
        self.assertEqual(scan.state, 'printing')
        self.assertIn('Q', scan.last_print_failure)
        third = self._start(self._plate(q, printer=self.p2))
        self._off(third, skip_cure=True)
        self.assertEqual(scan.state, 'printed')

    def test_register_sync_is_total_over_every_mix_of_patient_states(self):
        """Whatever its two patients are doing, the register reads one status and the
        rule has no gap: every plate Printed is Printed; a patient printing or in wash
        & cure is Print In Progress; anything else is Sent for Printing."""
        machines = [self.Printer.create({'name': 'Sync %d' % i}) for i in range(2)]
        steps = ('to_plate', 'plated', 'printing', 'washing', 'printed')
        for combo in itertools.product(steps, repeat=2):
            scan = self._patients('One', 'Two')
            for line, target, machine in zip(scan.line_ids.sorted('sequence'), combo, machines):
                if target == 'to_plate':
                    continue
                order = self._plate(line, printer=machine)
                if target == 'plated':
                    continue
                self._start(order)
                if target == 'printing':
                    continue
                self._off(order, skip_cure=(target == 'printed'))
            want = 'printed' if set(combo) == {'printed'} else (
                'in_progress' if set(combo) & {'printing', 'washing'} else 'printing')
            with self.subTest(combo=combo):
                self.assertEqual(tuple(scan.line_ids.sorted('sequence').mapped('print_state')), combo)
                self.assertEqual(scan.state, want)
            # free the machines for the next combination
            for order in self.Order.search([('printer_id', 'in', [m.id for m in machines]), ('state', '=', 'printing')]):
                self._off(order)

    def test_reset_or_cancel_takes_the_patient_off_the_plate(self):
        lone = self._patients('Lone')
        draft_plate = self._plate(lone.line_ids)
        lone.action_reset()
        self.assertEqual((lone.state, draft_plate.state), ('draft', 'cancel'), "an emptied plate is given up")
        self.assertEqual(draft_plate.line_ids.result, 'withdrawn')
        # a plate on the machine goes on for the others
        leaving, staying = self._patients('Leaving'), self._patients('Staying')
        plate = self._start(self._plate((leaving | staying).line_ids))
        leaving.action_reset()
        self.assertEqual((plate.state, staying.state, leaving.state), ('printing', 'in_progress', 'draft'))
        self.assertEqual(leaving.line_ids.print_state, 'none')
        self.assertFalse(leaving.printer_id)
        self.assertEqual(leaving.print_hours, 0.0)
        self.assertTrue(all(leaving.print_line_ids.mapped('superseded')))
        self._off(plate)
        self.assertEqual((plate.printed_count, plate.withdrawn_count), (1, 1))
        self.assertEqual((self.p1.model_done_count, self.p1.model_failed_count), (1, 0), "taken off is neither")
        # sent again later, the patient starts afresh
        leaving.action_confirm()
        leaving._send_to_print()
        self.assertEqual(leaving.line_ids.print_state, 'to_plate')
        # cancelling works the same way
        cancelled, other = self._patients('Cancelled'), self._patients('Other')
        plate = self._start(self._plate((cancelled | other).line_ids, printer=self.p2))
        cancelled.action_cancel()
        self.assertEqual((cancelled.state, plate.state), ('cancel', 'printing'))
        self.assertEqual(plate.line_ids.filtered(lambda l: l.scan_id == cancelled).result, 'withdrawn')

    def test_print_again_after_a_broken_model(self):
        scan = self._to_printed(self._register())
        line = scan.line_ids
        action = line.action_print_again()
        self.assertEqual(action['res_model'], 'lab.scan.print.order.make')
        again = self._plate(line)
        self.assertEqual((line.print_state, scan.state), ('plated', 'printing'))
        self.assertEqual(again.line_ids.attempt, 2)
        self._off(self._start(again), skip_cure=True)
        self.assertEqual(scan.state, 'printed')
        scan.action_create_orders()
        with self.assertRaises(UserError, msg="not once the order exists"):
            line.action_print_again()

    def test_sending_from_the_list_takes_confirmed_registers_only(self):
        """The register list's Send to 3D Printing: every ticked register confirmed, or
        none goes - the dialog would otherwise quietly leave some behind."""
        ready, other = self._register(), self._register(patients=(('Diya', 'x'),))
        ready.action_confirm()
        with self.assertRaisesRegex(UserError, other.name):
            (ready | other).action_send_to_print()
        with self.assertRaises(UserError):
            self.Scan.action_send_to_print()
        action = ready.action_send_to_print()
        self.assertEqual((action['res_model'], action['context']['active_ids']),
                         ('lab.scan.print.order.make', ready.ids))

    def test_the_front_desk_sees_where_each_patients_model_is(self):
        scan = self._register(patients=(('Tanvi', 'Upper & Lower Hawley'),))
        line = scan.line_ids
        self.assertEqual(line.print_state, 'none')
        scan.attachment_ids = [(4, self._file().id)]
        scan.action_confirm()
        scan._send_to_print()
        self.assertEqual(line.print_state, 'to_plate')
        order = self._plate(line)
        self.assertEqual((line.print_state, line.current_order_id), ('plated', order))
        self._start(order)
        self.assertEqual((line.print_state, line.current_printer_id, line.expected_end),
                         ('printing', self.p1, order.expected_end))
        self.assertIn('1 printing on %s' % self.p1.code, scan.print_summary)
        self.assertEqual(scan.current_order_ids, order)
        self._off(order)
        self.assertEqual((line.print_state, line.current_order_id.id), ('washing', False))
        self.assertIn('1 wash & cure', scan.print_summary)
        order.action_cure()
        self.assertEqual(line.print_state, 'printed')
        self.assertEqual(scan.print_order_ids, order)

    # ------------------------------------------------------------------ the paper and the files
    def test_the_files_zip_has_a_folder_per_patient_and_a_manifest(self):
        scan = self._register(patients=(('Anu', 'x'), ('Binu', 'y')))
        anu, binu = scan.line_ids.sorted('sequence')
        own, bite = self._file('anu-upper.stl'), self._file('bite.stl')
        scan.attachment_ids = [(6, 0, (own | bite).ids)]
        anu.attachment_ids = [(6, 0, own.ids)]
        scan.action_confirm()
        order = self._plate(scan.line_ids)
        with zipfile.ZipFile(order._zip_files()) as archive:
            names = archive.namelist()
            folder = order.name.replace('/', '-')
            register = scan.name.replace('/', '-')
            self.assertIn('%s/%s - Anu/anu-upper.stl' % (folder, register), names)
            self.assertIn('%s/%s - Anu/bite.stl' % (folder, register), names, "the shared file under every patient")
            self.assertIn('%s/%s - Binu/bite.stl' % (folder, register), names)
            self.assertNotIn('%s/%s - Binu/anu-upper.stl' % (folder, register), names)
            manifest = archive.read('%s/manifest.txt' % folder).decode()
        self.assertIn(order.suggested_filename, manifest)
        self.assertIn('Binu', manifest)
        self.assertEqual(order.action_download_files()['url'], '/scan/print-order/%d/files.zip' % order.id)

    def test_the_plate_ticket_renders_the_number_the_qr_and_the_patients(self):
        first = self._plate(self._patients('Anu', 'Binu').line_ids)
        second = self._plate(self._patients('Cini').line_ids, printer=self.p2)
        html = self.env['ir.actions.report']._render_qweb_html(
            'lab_scan_register.action_report_plate_ticket', (first | second).ids)[0].decode()
        for order in (first, second):
            self.assertIn(order.name, html)
            self.assertIn(order.printer_id.code, html)
        for name in ('Anu', 'Binu', 'Cini'):
            self.assertIn(name, html)
        self.assertEqual(html.count('data:image/png;base64,'), 2, "a QR per plate")
        self.assertEqual(html.count('page-break-after:always'), 1, "one ticket a page, no blank last page")
        self.assertEqual(first.action_print_ticket()['report_name'], 'lab_scan_register.report_plate_ticket')

    def test_a_scanned_plate_ticket_names_its_order(self):
        order = self._plate(self._patients('A').line_ids)
        year, number = order.name.split('/')[1:]
        for text in (order.name, order.name.lower(), order.name.replace('/', '-'), str(int(number))):
            with self.subTest(text=text):
                self.assertEqual(self.Order._by_code(text), order)
        self.assertFalse(self.Order._by_code('PRINT/1999/9999'))
        self.assertFalse(self.Order._by_code('3DP-01'))

    # ------------------------------------------------------------------ the record
    def test_the_failed_models_report_reads_lines_by_machine_and_reason(self):
        a, b, c = self._patients('A'), self._patients('B'), self._patients('C')
        self._off(self._start(self._plate((a | b).line_ids)), failed=[(a.line_ids, 'all', 'supports'), b.line_ids],
                  reason='adhesion')
        self._fail(self._start(self._plate(c.line_ids, printer=self.p2)), reason='power')
        failed = self.Line.search([('result', '=', 'failed'), ('scan_id', 'in', (a | b | c).ids)])
        by = {(p.id, r): n for p, r, n in self.Line._read_group(
            [('id', 'in', failed.ids)], ['printer_id', 'failure_reason'], ['__count'])}
        self.assertEqual(by, {(self.p1.id, 'supports'): 1, (self.p1.id, 'adhesion'): 1, (self.p2.id, 'power'): 1})
        action = self.env.ref('lab_scan_register.action_scan_print_order_line_failed')
        self.assertEqual(self.Line.search(eval(action.domain)) & failed, failed)
        self.assertEqual(self.Line.search_count(self.p1.action_view_failed()['domain']), 2)
        self.assertEqual(self.p2.failed_count, 1, "a whole failed plate is a failed plate")

    def test_the_due_off_cron_warns_once_inside_lab_hours(self):
        self.p1.user_id = self.desk
        order = self._plate(self._patients('A').line_ids)
        order.estimated_hours = 2.0
        self._start(order)
        lab_today = fields.Date.context_today(self.env.user)
        inside = datetime.combine(lab_today, datetime.min.time()) + timedelta(hours=LAB_HOURS[0] + 2 - 5.5)
        outside = datetime.combine(lab_today, datetime.min.time()) + timedelta(hours=LAB_HOURS[1] + 2 - 5.5)
        order.with_context(print_workflow=True).write({'date_start': inside - timedelta(hours=5)})
        with patch.object(fields.Datetime, 'now', return_value=outside):
            self.assertEqual(self.Order._cron_watch_prints(), 0, "nobody is in the lab to hear it")
        with patch.object(fields.Datetime, 'now', return_value=inside):
            self.assertEqual(self.Order._cron_watch_prints(), 1)
            self.assertEqual(self.Order._cron_watch_prints(), 0, "said once")
        activity = order.activity_ids
        self.assertEqual((len(activity), activity.user_id), (1, self.desk), "to the machine's in-charge")

    def test_arch_is_guessed_from_the_description(self):
        cases = {"Upper & Lower Hawley's retainer": 'both', 'U/L Essix': 'both', 'upper bilateral expansion': 'upper',
                 'Lower retainer': 'lower', 'maxillary expander': 'upper', 'Bite plate': None, '': None}
        for text, arch in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parsers.guess_arch(text), arch)
        scan = self._queued(patients=(('Both', 'Upper & Lower Hawley'), ('Low', 'lower retainer'),
                                      ('Plain', 'Bite plate')))
        arches = {l.patient_name: l.arch for l in scan.line_ids}
        self.assertEqual(arches, {'Both': 'both', 'Low': 'lower', 'Plain': 'both'})
        order = self._plate(scan.line_ids)
        self.assertEqual(order.model_count, 5, "2 + 1 + 2 models on the plate")

    def test_a_plates_status_and_its_patients_move_only_through_the_buttons(self):
        order = self._plate(self._patients('A').line_ids)
        with self.assertRaises(UserError):
            order.write({'state': 'printing'})
        with self.assertRaises(UserError):
            order.line_ids.write({'result': 'ok'})
        order.write({'resin_batch': 'LOT 7', 'note': 'plate 2'})
        self._start(order)
        for vals in ({'printer_id': self.p2.id}, {'estimated_hours': 9.0}):
            with self.subTest(vals=vals), self.assertRaises(UserError):
                order.write(vals)
        order.write({'resin_batch': 'LOT 8', 'note': 'read off the machine'})
        self._off(order)
        with self.assertRaises(UserError):
            order.write({'resin_batch': 'LOT 9'})
        with self.assertRaises(UserError):
            order.unlink()

    # ------------------------------------------------------------------ rights
    def test_the_front_desk_runs_the_printing_room_but_does_not_add_machines_or_delete_plates(self):
        desk = self.env(user=self.desk)
        scan = self._patients('A')
        wiz = desk['lab.scan.print.order.make'].with_context(
            active_model='lab.scan.line', active_ids=scan.line_ids.ids).create({'printer_id': self.p1.id})
        order = desk['lab.scan.print.order'].browse(wiz.action_create()['res_id'])
        desk['lab.scan.print.order.start'].with_context(
            active_model='lab.scan.print.order', active_ids=order.ids).create({}).action_start()
        desk['lab.scan.print.order.off'].with_context(
            active_model='lab.scan.print.order', active_ids=order.ids).create({}).action_confirm()
        order.with_env(desk).action_cure()
        self.assertEqual((order.state, scan.state), ('done', 'printed'))
        with self.assertRaises(AccessError):
            desk['lab.scan.printer'].create({'name': 'Mine'})
        with self.assertRaises(AccessError):
            order.with_env(desk).unlink()
        with self.assertRaises(AccessError):
            order.line_ids.with_env(desk).unlink()

    def test_the_plate_holds_the_files_and_the_links(self):
        """The printing room works the files on the plate: a patient's scans added there
        are the register's (and go to the order), the register's link shows and can be
        mended there, and the shared files show under each of its patients."""
        linked = self._register(patients=(('Anu', 'x'),), download_url='https://wetransfer.com/downloads/anu')
        bare = self._register(patients=(('Binu', 'y'), ('Bindu', 'z')))
        for scan in linked | bare:
            scan.action_confirm()
        # sent with Binu's scan attached in the dialog; Bindu's comes on the register's bite record
        upper = self._file('binu-upper.stl')
        bite = self._file('bite.stl')
        binu = bare.line_ids.filtered(lambda l: l.patient_name == 'Binu')
        bare.with_context(scan_workflow=True).write({'attachment_ids': [(4, bite.id)]})
        patients = (linked | bare).line_ids
        order = self._plate(patients, line_ids=[(6, 0, patients.ids),
                                                (1, binu.id, {'attachment_ids': [(4, upper.id)]})])
        rows = {l.patient_name: l for l in order.file_line_ids}
        self.assertTrue(rows['Anu'].has_files, "a link is something to slice from")
        self.assertEqual(rows['Anu'].file_count, 0)
        self.assertEqual(rows['Anu'].action_open_link()['url'], 'https://wetransfer.com/downloads/anu')
        self.assertEqual(bare.line_ids.filtered(lambda l: l.patient_name == 'Binu').attachment_ids, upper)
        self.assertIn(upper, bare.attachment_ids, "the patient's file is the register's too")
        self.assertEqual((upper.res_model, upper.res_id), ('lab.scan', bare.id))
        self.assertEqual(rows['Bindu'].shared_file_ids, bite)
        self.assertEqual((rows['Binu'].file_count, rows['Bindu'].file_count), (2, 1))
        self.assertEqual(order.files_missing_count, 0)
        # the link mended on the plate is the register's link, with its lifetime
        with Form(order) as form:
            with form.file_line_ids.edit(0) as row:
                row.download_url = 'https://wetransfer.com/downloads/anu-again'
                self.assertTrue(row.download_expiry, "a WeTransfer link brings its expiry")
        self.assertEqual(linked.download_url, 'https://wetransfer.com/downloads/anu-again')
        with zipfile.ZipFile(order._zip_files()) as archive:
            names = archive.namelist()
        self.assertTrue(any(n.endswith('Binu/binu-upper.stl') for n in names))
        self.assertTrue(any(n.endswith('Bindu/bite.stl') for n in names))

    def test_the_checklist_says_what_stands_between_the_plate_and_the_machine(self):
        busy = self._start(self._plate(self._patients('Busy').line_ids, printer=self.p2))
        scan = self._register(patients=(('Diya', 'x'),), download_url='https://wetransfer.com/downloads/diya')
        scan.action_confirm()
        order = self._plate(scan.line_ids, printer=self.p2)
        # the link it was sent with is withdrawn before anything was downloaded
        scan.download_url = False
        items = order._checklist_items()
        self.assertIn(('block', "No files and no download link for Diya - add them on the Files tab."), items)
        self.assertTrue(any(level == 'warn' and busy.name in text for level, text in items), "the machine is busy")
        self.assertFalse(order.is_ready)
        self.assertIn(order, self.Order.search([('is_ready', '=', False)]))
        self.assertIn('fa-times-circle', order.checklist_html)
        start = self.env['lab.scan.print.order.start'].with_context(
            active_model='lab.scan.print.order', active_ids=order.ids).create({'printer_id': self.p1.id})
        self.assertTrue(start.blocked, "no files: the dialog will not start it")
        self.assertIn('is free', start.checklist_html, "the checklist is for the machine chosen in the dialog")
        order.file_line_ids.file_ids = [(4, self._file().id)]
        order._apply_slice_head(ctb(), 'diya.ctb', 0)
        start.invalidate_recordset(['blocked', 'checklist_html'])
        self.assertFalse(start.blocked)
        self.assertTrue(order.is_ready)
        self.assertIn(order, self.Order.search([('is_ready', '=', True)]))
        texts = ' '.join(text for _level, text in order._checklist_items())
        self.assertIn('Files for every patient', texts)
        self.assertIn('ChituBox file read', texts)
        self.p2.action_set_maintenance()
        self.assertEqual(order._checklist_items()[0][0], 'block')
        self.assertFalse(order.is_ready)

    def test_a_big_selection_is_spread_over_the_free_machines(self):
        """Ten arches for an eight-arch plate: the dialog fills a plate on each machine
        in service, urgent patients first, a patient's two arches together."""
        self.p2.plate_capacity = 4
        scan = self._patients('A', 'B', 'C', 'D', 'E')
        e = scan.line_ids.filtered(lambda l: l.patient_name == 'E')
        e.with_context(scan_workflow=True).write({'priority': '1'})
        wiz = self.env['lab.scan.print.order.make'].with_context(
            active_model='lab.scan.line', active_ids=scan.line_ids.ids).create({'printer_id': self.p1.id})
        self.assertTrue(wiz.over_capacity)
        wiz.split = True
        self.assertIn(self.p1.code, wiz.plan_text)
        self.assertIn(self.p2.code, wiz.plan_text)
        action = wiz.action_create()
        plates = self.Order.search(action['domain'])
        self.assertEqual(len(plates), 2)
        first = plates.filtered(lambda o: o.printer_id == self.p1)
        second = plates.filtered(lambda o: o.printer_id == self.p2)
        self.assertEqual((first.model_count, second.model_count), (8, 2), "full first, the rest on the next")
        self.assertIn(e, first.line_ids.scan_line_id, "the urgent patient goes on the first plate")
        self.assertEqual(set(plates.mapped('state')), {'draft'})
        # more than all the machines hold at once: round again, queued on the machines
        more = self._patients(*['P%d' % i for i in range(10)])
        wiz = self.env['lab.scan.print.order.make'].with_context(
            active_model='lab.scan.line', active_ids=more.line_ids.ids).create(
            {'printer_id': self.p1.id, 'split': True})
        plates = self.Order.search(wiz.action_create()['domain'])
        self.assertEqual(sorted(plates.mapped('model_count')), [4, 8, 8], "20 arches: 8 + 4, then 8 more")
        self.assertEqual(sum(plates.mapped('patient_count')), 10)

    def test_the_doctor_can_be_told_the_models_are_printed(self):
        scan = self._to_printed(self._register(patients=(('Tanvi', 'x'),)))
        text = self.env.ref('lab_scan_register.template_printed').render(scan)
        self.assertIn('Tanvi', text)
        self.assertIn('printed', text)

    def test_the_send_dialog_takes_scans_links_and_shared_files(self):
        """Every patient going to the printing room has something to print from: their
        own scan uploaded in the dialog, the register's link pasted there, or files the
        register already shares between its patients. Queue Only asks the same."""
        own = self._register(patients=(('Own', 'x'),))
        linked = self._register(patients=(('Linked', 'x'),))
        shared = self._register(patients=(('Shared A', 'x'), ('Shared B', 'y')))
        shared.attachment_ids = [(4, self._file('case.stl').id)]
        for scan in own | linked | shared:
            scan.action_confirm()
        Make = self.env['lab.scan.print.order.make'].with_context(
            active_model='lab.scan', active_ids=(own | linked | shared).ids)
        wiz = Make.create({'printer_id': self.p1.id})
        ready = {l.patient_name: l.has_print_files for l in wiz.line_ids}
        self.assertEqual(ready, {'Own': False, 'Linked': False, 'Shared A': True, 'Shared B': True})
        # as the dialog sees a row while the link is being typed, before anything is saved
        typing = linked.line_ids.new({'download_url': 'https://wetransfer.com/downloads/x'}, origin=linked.line_ids)
        self.assertTrue(typing.has_print_files, "the row turns ready as the link is typed")
        with self.assertRaisesRegex(UserError, 'Own'):
            wiz.action_queue_only()
        wiz.write({'line_ids': [
            (1, own.line_ids.id, {'attachment_ids': [(4, self._file('own.stl').id)]}),
            (1, linked.line_ids.id, {'download_url': 'https://wetransfer.com/downloads/linked'}),
        ]})
        self.assertEqual(linked.download_url, 'https://wetransfer.com/downloads/linked', "the register's link")
        wiz.action_queue_only()
        self.assertEqual(set((own | linked | shared).mapped('state')), {'printing'})
        self.assertEqual(set((own | linked | shared).line_ids.mapped('print_state')), {'to_plate'})

    # ------------------------------------------------------------------ the machine at Start, the file on the plate
    # (client, 2026-10-14: "machine only to be set before print in progress - no need
    # in the wizard; the sliced file uploaded in the wizard, downloaded or edited from
    # the print order; Download File downloads the sliced file; a colour per machine")
    def _make_wizard(self, lines, **vals):
        return self.env[ORDER + '.make'].with_context(
            active_model='lab.scan.line', active_ids=lines.ids).create(vals)

    def test_a_plate_needs_no_machine_until_start(self):
        lines = self._patients('A').line_ids
        order = self.Order.browse(self._make_wizard(lines).action_create()['res_id'])
        self.assertFalse(order.printer_id, "no machine planned: it is picked at Start")
        levels = dict((text, level) for level, text in order._checklist_items())
        self.assertNotIn('block', [level for text, level in levels.items() if 'machine' in text.lower()],
                         "no machine is not a block while plating")
        self.assertIn('info', levels.values())
        order.estimated_hours = 1.5
        self.assertTrue(order.is_ready, "files and a time: ready, machine or not")
        self.assertIn(order, self.Order.search([('is_ready', '=', True)]))
        start = self.env[ORDER + '.start'].with_context(active_model=ORDER, active_ids=order.ids).create({})
        self.assertEqual(start.printer_id, self.Printer._suggest(), "the free, reliable machine is offered")
        start.printer_id = False
        self.assertTrue(start.blocked, "but at Start a machine is needed")
        start.printer_id = self.p2
        start.action_start()
        self.assertEqual((order.state, order.printer_id), ('printing', self.p2))
        self.assertNotIn('Planned on', ''.join(order.message_ids.mapped('body')), "nothing was planned")

    def _upload(self, data, name='plate.ctb', user=None):
        """What /scan/slice/chunk leaves behind: an attachment of nobody yet."""
        return self.env['ir.attachment'].with_user(user or self.env.user).create(
            {'name': name, 'raw': data, 'mimetype': 'application/octet-stream'})

    def test_the_sliced_file_comes_with_the_plate_and_is_read_there(self):
        upload = self._upload(ctb(), 'PLATE_3DP-01.ctb')
        order = self.Order.browse(self._make_wizard(self._patients('A').line_ids,
                                                    slice_attachment_id=upload.id).action_create()['res_id'])
        self.assertEqual(order.slice_attachment_id, upload, "the same attachment - never a second copy")
        self.assertEqual((upload.res_model, upload.res_id), (ORDER, order.id), "and it is the plate's now")
        self.assertEqual((order.slice_filename, order.slice_parsed, order.layer_count, order.estimated_hours),
                         ('PLATE_3DP-01.ctb', True, 500, 2.0))
        self.assertEqual(order.slice_size, len(ctb()))
        self.assertTrue(order.preview_image)
        self.assertEqual(order.action_download_file()['url'], '/web/content/%d?download=true' % upload.id)

    def test_a_file_added_to_a_plate_being_plated_replaces_its_file(self):
        order = self._plate(self._patients('A').line_ids)
        first = self._upload(ctb(layers=300), 'a.ctb')
        order.slice_attachment_id = first
        second = self._upload(ctb(layers=420), 'b.ctb')
        wiz = self.env[ORDER + '.make'].with_context(active_model='lab.scan.line',
                                                     active_ids=self._patients('B').line_ids.ids).create(
            {'mode': 'existing', 'order_id': order.id, 'slice_attachment_id': second.id})
        wiz.action_create()
        self.assertEqual((order.slice_attachment_id, order.layer_count, order.slice_filename), (second, 420, 'b.ctb'))
        self.assertFalse(first.exists(), "the file it replaces goes")
        self.assertEqual(self.env['ir.attachment'].search_count([('res_model', '=', ORDER), ('res_id', '=', order.id)]), 1)

    def test_the_sliced_file_can_be_replaced_while_plated_or_printing_and_settles_off_the_machine(self):
        order = self._plate(self._patients('A').line_ids)
        with self.assertRaises(UserError, msg="nothing to download yet"):
            order.action_download_file()
        order.slice_attachment_id = self._upload(ctb(layers=300), 'a.ctb')
        self.assertEqual((order.layer_count, order.slice_filename), (300, 'a.ctb'))
        self._start(order)
        order.slice_attachment_id = self._upload(ctb(layers=420), 'b.ctb')
        self.assertEqual((order.layer_count, order.slice_filename), (420, 'b.ctb'), "re-sliced while printing")
        self._off(order)
        with self.assertRaises(UserError, msg="off the machine, the file is the record"):
            order.slice_attachment_id = self._upload(ctb(), 'c.ctb')

    def test_taking_the_sliced_file_off_the_plate_clears_its_figures(self):
        order = self._plate(self._patients('A').line_ids)
        upload = self._upload(ctb(), 'a.ctb')
        order.slice_attachment_id = upload
        order.estimated_hours = 2.5
        order.slice_attachment_id = False
        self.assertFalse(order.slice_filename or order.layer_count or order.preview_image)
        self.assertFalse(upload.exists())
        self.assertEqual(order.estimated_hours, 2.5, "a typed estimate stays")

    def test_only_an_upload_of_ones_own_or_another_plates_file_can_become_a_plates_file(self):
        """The plate re-points the attachment it is given: it must not be able to take
        somebody else's upload, or any other record's file."""
        self.desk.group_ids = [(4, self.env.ref('lab_scan_register.group_scan_user').id)]
        order = self._plate(self._patients('A').line_ids)
        theirs = self._upload(ctb(), 'theirs.ctb', user=self.desk)
        invoice = self.env['ir.attachment'].create({'name': 'invoice.pdf', 'raw': b'%PDF',
                                                    'res_model': 'res.partner', 'res_id': self.clinic.id})
        mine = self._upload(ctb(), 'mine.ctb')
        for foreign in (invoice,):
            with self.subTest(attachment=foreign.name), self.assertRaises(AccessError):
                order.with_user(self.desk).slice_attachment_id = foreign
        with self.assertRaises(AccessError):
            order.with_user(self.desk).slice_attachment_id = mine      # created by somebody else
        order.with_user(self.desk).slice_attachment_id = theirs
        self.assertEqual((theirs.res_model, theirs.res_id), (ORDER, order.id))
        self.assertEqual((invoice.res_model, invoice.res_id), ('res.partner', self.clinic.id), "untouched")

    def test_a_restart_prints_the_failed_plates_file_and_can_download_it(self):
        order = self._plate(self._patients('A').line_ids)
        upload = self._upload(ctb(), 'a.ctb')
        order.slice_attachment_id = upload
        self._start(order)
        restart = self._fail(order, restart=True, printer=self.p2)
        self.assertEqual(restart.slice_attachment_id, upload, "the same file - shared, not copied")
        self.assertEqual((upload.res_model, upload.res_id), (ORDER, order.id), "still the failed plate's")
        self.assertEqual(restart.action_download_file()['url'], '/web/content/%d?download=true' % upload.id)
        self.assertEqual(restart.layer_count, 500)

    def test_a_plate_whose_file_was_never_kept_offers_no_download(self):
        """Plates read by the old browser widget kept the figures, never the file."""
        order = self._plate(self._patients('A').line_ids)
        order._apply_slice_head(ctb(), 'old.ctb', 0)
        self.assertTrue(order.slice_filename and not order.slice_attachment_id)
        arch = etree.fromstring(self.Order.get_view(
            self.env.ref('lab_scan_register.view_scan_print_order_form').id, 'form')['arch'])
        button = arch.xpath("//header/button[@name='action_download_file']")[0]
        self.assertIn('not slice_attachment_id', button.get('invisible'))
        with self.assertRaises(UserError):
            order.action_download_file()

    def test_queue_only_does_not_lose_an_uploaded_file(self):
        scan = self._patients('A')
        wiz = self.env[ORDER + '.make'].with_context(active_model='lab.scan', active_ids=scan.ids).create(
            {'slice_attachment_id': self._upload(ctb()).id})
        with self.assertRaises(UserError):
            wiz.action_queue_only()

    def test_patients_who_came_with_the_file_are_not_flagged_as_added_after_it(self):
        """The file is read in the same transaction the plate is made in: a line keeps
        the transaction's microseconds, date_sliced is whole seconds."""
        frozen = self.env.cr.now().replace(microsecond=0)
        with patch.object(fields.Datetime, 'now', return_value=frozen):
            order = self.Order.browse(self._make_wizard(
                self._patients('A').line_ids, slice_attachment_id=self._upload(ctb()).id).action_create()['res_id'])
        self.assertFalse([t for level, t in order._checklist_items() if 'after the file was sliced' in t])

    def test_a_patient_added_after_the_slice_is_flagged(self):
        order = self._plate(self._patients('A').line_ids)
        order.slice_attachment_id = self._upload(ctb(), 'a.ctb')
        order.date_sliced = datetime.now() - timedelta(hours=1)
        self._make_wizard(self._patients('Late').line_ids, mode='existing', order_id=order.id).action_create()
        warnings = [text for level, text in order._checklist_items() if level == 'warn']
        self.assertTrue(any('after the file was sliced' in t and 'Late' in t for t in warnings), warnings)

    def test_the_header_downloads_the_sliced_file_and_the_files_tab_keeps_the_zip(self):
        arch = etree.fromstring(self.Order.get_view(
            self.env.ref('lab_scan_register.view_scan_print_order_form').id, 'form')['arch'])
        header = [b.get('name') for b in arch.xpath('//header/button')]
        self.assertIn('action_download_file', header)
        self.assertNotIn('action_download_files', header, "the zip moved to the Files tab")
        self.assertTrue(arch.xpath("//page[@name='files']//button[@name='action_download_files']"))
        self.assertTrue(arch.xpath("//field[@name='slice_attachment_id'][@widget='slice_upload']"),
                        "the file goes up in pieces")

    # ------------------------------------------------------------------ re-plates and the machines' queue
    def test_a_re_plate_needs_no_machine(self):
        a, b = self._patients('A'), self._patients('B')
        order = self._start(self._plate((a | b).line_ids))
        replate = self._off(order, failed=b.line_ids, replate=True, replate_mode='new')
        self.assertEqual((replate.state, replate.printer_id, replate.patient_count), ('draft', self.Printer, 1))

    def test_a_re_plate_can_still_be_planned_on_the_same_machine(self):
        a, b = self._patients('A'), self._patients('B')
        order = self._start(self._plate((a | b).line_ids))
        replate = self._off(order, failed=b.line_ids, replate=True, replate_mode='same')
        self.assertEqual(replate.printer_id, self.p1, "a plan the Start dialog offers")

    def test_a_re_plate_on_a_machine_gone_into_maintenance_is_simply_unplanned(self):
        a, b = self._patients('A'), self._patients('B')
        order = self._start(self._plate((a | b).line_ids))
        self.p1.state = 'maintenance'
        replate = self._off(order, failed=b.line_ids, replate=True, replate_mode='same')
        self.assertFalse(replate.printer_id, "no error: the machine is picked at Start")

    def test_a_free_machine_starts_the_next_ready_plate_nobody_planned(self):
        order = self.Order.browse(self._make_wizard(self._patients('A').line_ids).action_create()['res_id'])
        order.estimated_hours = 1.0
        self.assertTrue(order.is_ready and not order.printer_id)
        self.assertEqual(self.p2.next_order_id, order)
        action = self.p2.action_start_next()
        self.assertEqual((action['context']['default_order_id'], action['context']['default_printer_id']),
                         (order.id, self.p2.id))
        planned = self._plate(self._patients('B').line_ids, printer=self.p1)
        self.assertEqual(self.p1.next_order_id, planned, "a plate planned on the machine comes first")

    def test_every_machine_has_its_own_colour(self):
        colours = {self.p1.color, self.p2.color}
        self.assertTrue(all(1 <= c <= 11 for c in colours), colours)
        self.assertEqual(len(colours), 2, "two machines, two colours")
        third = self.Printer.create({'name': 'Saturn 4 Ultra'})
        self.assertNotIn(third.color, colours, "the colour least in use")
        order = self._plate(self._patients('A').line_ids, printer=third)
        self.assertEqual(order.printer_tag_ids, third, "the tag is the machine, in its colour")
        self.assertEqual(self.Printer.create({'name': 'Mars 5', 'color': 4}).color, 4, "a colour given is kept")

