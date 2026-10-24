# -*- coding: utf-8 -*-
"""Partial failures: one patient, or one arch of a patient, lost on a plate - and the
lost models going on to the next plate with the new scans.
(client, 2026-10-23: "one or two scans will be failed, or the U or L of one scan will
be failed in a print order... the unprinted scans or U or L of scans will move to
another print order which contains new scans also"; "the dashboard is visible to
the Scan manager and the Administrator user")

A model is an arch: a patient with upper and lower is two models. The figures below
are exact because the lab runs in a company of its own."""
import importlib.util
import os
import re
from datetime import timedelta

from lxml import etree
from psycopg2 import IntegrityError

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form, tagged
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger
from odoo.tools.safe_eval import safe_eval

from .common import ORDER, PrintingSteps

HERE = os.path.dirname(__file__)
MIGRATION = os.path.join(HERE, '..', 'migrations', '20.0.3.0.0', 'post-migration.py')
DASHBOARD = os.path.join(HERE, '..', 'static', 'src', 'dashboard')


@tagged('post_install', '-at_install')
class TestPartialFailures(PrintingSteps, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env['res.company'].create({'name': 'Partial Print Lab'})
        cls.env.user.write({'company_ids': [(4, cls.company.id)], 'tz': 'Asia/Kolkata'})
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=[cls.company.id]))
        cls.Scan = cls.env['lab.scan']
        cls.Order = cls.env[ORDER]
        cls.Line = cls.env[ORDER + '.line']
        cls.clinic = cls.env['res.partner'].create({'name': 'Partial Clinic', 'is_company': True})
        cls.group = cls.env['lab.scan.channel'].create(
            {'name': 'Partial Group', 'kind': 'whatsapp', 'company_id': cls.company.id})
        cls.product = cls.env['product.product'].create(
            {'name': 'Partial Appliance', 'type': 'consu', 'sale_ok': True})
        Printer = cls.env['lab.scan.printer']
        cls.printer = Printer.create({'name': 'Sonic Partial', 'company_id': cls.company.id,
                                      'plate_capacity': 8, 'typical_hours': 3.0})
        cls.p2 = Printer.create({'name': 'Mars Partial', 'company_id': cls.company.id,
                                 'plate_capacity': 8, 'typical_hours': 2.0})

    # ------------------------------------------------------------------ helpers
    def _register(self, *patients, **line_vals):
        """One register sent for printing. A patient is a name - upper and lower - or
        (name, what the doctor wrote), the arch read from the words; `line_vals`
        (priority, delivery date) go on every patient before the register is
        confirmed, which settles them."""
        lines = []
        for patient in patients:
            name, desc = (patient, False) if isinstance(patient, str) else patient
            lines.append((0, 0, dict({'patient_name': name, 'description': desc, 'product_id': self.product.id},
                                     **line_vals)))
        scan = self.Scan.create({'channel_id': self.group.id, 'partner_id': self.clinic.id,
                                 'company_id': self.company.id, 'download_url': 'https://x', 'line_ids': lines})
        scan.action_confirm()
        scan._send_to_print()
        return scan

    def _unplanned(self, lines):
        """A plate made with no machine - the way New Print Order makes them."""
        wiz = self.env[ORDER + '.make'].with_context(
            active_model='lab.scan.line', active_ids=lines.ids).create({})
        return self.Order.browse(wiz.action_create()['res_id'])

    def _board(self, order, rows=None, mode='off', context=None, **vals):
        """The Plate Off board as the room fills it in. `rows`: {patient: values}
        written on that patient's row - ok, failed_part, reason, note, hold."""
        ctx = dict(context or {}, active_model=ORDER, active_ids=order.ids, active_id=order.id)
        wiz = self.env[ORDER + '.off'].with_context(ctx).create(dict({'mode': mode}, **vals))
        for patient, values in (rows or {}).items():
            row = wiz.line_ids.filtered(lambda r: r.line_id.scan_line_id == patient)
            self.assertTrue(row, "%s is not on %s" % (patient.patient_name, order.name))
            row.write(values)
        return wiz

    def _line(self, order, scan):
        return order.line_ids.filtered(lambda l: l.scan_id == scan)

    def _said(self, record, text):
        """`text` is in one of the record's chatter messages."""
        return any(text in (m.body or '') for m in record.message_ids)

    # ------------------------------------------------------------------ a model is an arch
    def test_a_model_is_an_arch_on_the_plate_the_machine_and_the_register(self):
        anu = self._register('Anu')
        ben = self._register(('Ben', 'upper retainer'))
        plate = self._start(self._plate((anu | ben).line_ids))
        self.assertEqual(plate.model_count, 3)
        self._off(plate, failed=[(anu.line_ids, 'lower', 'supports')])
        line = self._line(plate, anu)
        self.assertEqual((line.printed_arches, line.failed_arches, line.printed_models, line.failed_models),
                         ('upper', 'lower', 1, 1))
        self.assertEqual((plate.state, plate.printed_models, plate.failed_models, plate.is_partial),
                         ('washing', 2, 1, True))
        self.assertEqual((plate.printed_count, plate.failed_count), (1, 1), "patients: Ben clean, Anu not")
        self.assertEqual((self.printer.model_done_count, self.printer.model_failed_count,
                          self.printer.failure_rate), (2, 1, 33.3))
        patient = anu.line_ids
        self.assertEqual((patient.print_state, patient.print_part, patient.print_models, patient.replate_pending),
                         ('to_plate', 'lower', 1, True))
        self.assertEqual((anu.replate_pending_count, anu.replate_waiting_count), (1, 1))
        self.assertIn('Anu (lower): Supports failed', anu.last_print_failure)
        self.assertIn('waiting for a plate', anu.last_print_failure)
        self.assertIn('Anu (lower) → waiting for a plate', plate.failed_summary)
        self.assertTrue(self._said(plate, '2 model(s) printed, 1 failed'))

    def test_a_plate_whose_only_patient_lost_the_lower_still_printed_the_upper(self):
        anu = self._register('Anu')
        plate = self._start(self._plate(anu.line_ids))
        replate = self._off(plate, failed=[(anu.line_ids, 'lower', False)], reason='supports',
                            replate=True, replate_mode='new')
        self.assertEqual((plate.state, plate.is_partial), ('washing', True), "the upper goes to wash & cure")
        new = replate.line_ids
        self.assertEqual((new.part, new.model_count, new.attempt, new.replated_from_id),
                         ('lower', 1, 2, plate.line_ids))
        self.assertEqual((replate.model_count, replate.replate_count), (1, 1))
        self.assertIn('Anu lower #2 from %s' % plate.name, replate.replate_summary)
        self.assertEqual((anu.line_ids.print_state, anu.line_ids.print_part), ('plated', 'lower'))
        self.assertEqual(anu.replate_pending_count, 1, "on a plate, not printed yet")
        plate.action_cure()
        self.assertEqual((plate.state, anu.state), ('done', 'printing'), "the lower still waits")
        self._off(self._start(replate))
        replate.action_cure()
        self.assertEqual((anu.state, anu.line_ids.print_state, anu.line_ids.print_part), ('printed', 'printed', False))
        self.assertEqual((anu.replate_pending_count, anu.last_print_failure), (0, False))

    def test_the_room_plans_with_the_arch_still_to_print(self):
        anu = self._register('Anu')
        self._off(self._start(self._plate(anu.line_ids)), failed=[(anu.line_ids, 'lower', 'supports')])
        self._register('Wendy')
        wiz = self.env[ORDER + '.make'].with_context(
            active_model='lab.scan.line', active_ids=anu.line_ids.ids).create({})
        self.assertEqual(wiz.model_count, 1, "the gauge counts Anu's lower alone")
        d = self.Scan.get_dashboard('month')
        fc = d['forecast']
        self.assertEqual((fc['patients'], fc['models'], fc['replates'], fc['replate_models'], fc['upper'], fc['lower']),
                         (2, 3, 1, 1, 1, 2))
        self.assertEqual([(n['patient'], n['arch'], n['part_key'], n['replate']) for n in d['print_next']],
                         [('Anu', 'Lower', 'lower', True), ('Wendy', 'Upper & Lower', 'both', False)],
                         "the re-plate first, with the arch it needs")

    def test_a_plate_holding_one_arch_can_only_lose_that_arch(self):
        ben = self._register(('Ben', 'lower retainer'))
        plate = self._start(self._plate(ben.line_ids))
        line = plate.line_ids
        with self.assertRaises(UserError):
            line._normalised_failed_part('upper')
        self.assertEqual(line._normalised_failed_part('all'), 'lower', "'All' of a lower is the lower")
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'), self.env.cr.savepoint():
            self.env.cr.execute("UPDATE lab_scan_print_order_line SET failed_part = 'lower' WHERE id = %s",
                                (line.id,))
        self._fail(plate, reason='power')
        self.assertEqual(line.failed_part, 'lower', "Print Failed records the arch the plate held")
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'), self.env.cr.savepoint():
            self.env.cr.execute("UPDATE lab_scan_print_order_line SET failed_part = 'upper' WHERE id = %s",
                                (line.id,))

    # ------------------------------------------------------------------ the board
    def test_u_and_l_lose_one_arch_and_printed_loses_both(self):
        anu = self._register('Anu')
        plate = self._start(self._plate(anu.line_ids))
        form = Form(self.env[ORDER + '.off'].with_context(
            active_model=ORDER, active_ids=plate.ids, active_id=plate.id))
        with form.line_ids.edit(0) as row:
            row.lower_ok = False
            self.assertEqual((row.ok, row.upper_ok, row.failed_part), (False, True, 'lower'),
                             "the lower alone: the upper stays printed")
            row.lower_ok = True
            self.assertEqual((row.ok, row.upper_ok, row.lower_ok), (True, True, True))
            row.ok = False
            self.assertEqual((row.upper_ok, row.lower_ok, row.failed_part), (False, False, 'all'))
            row.ok = True
            self.assertEqual((row.upper_ok, row.lower_ok), (True, True))
            row.upper_ok = False
            self.assertEqual((row.ok, row.lower_ok, row.failed_part), (False, True, 'upper'))
        self.assertEqual((form.failed_count, form.failed_models, form.replate_models), (1, 1, 1))
        form.reason = 'supports'
        form.replate = False
        form.save().action_confirm()
        line = plate.line_ids
        self.assertEqual((line.result, line.failed_part, line.printed_arches, plate.state),
                         ('failed', 'upper', 'lower', 'washing'))

    def test_auto_sends_the_lost_models_to_the_best_plate_being_plated(self):
        self._plate(self._register('F1', 'F2', 'F3', 'F4').line_ids, printer=self.p2)      # 8 of 8: no room
        room = self._unplanned(self._register('Wendy').line_ids)                           # 2 of 8
        anu, ben = self._register('Anu'), self._register('Ben')
        plate = self._start(self._plate((anu | ben).line_ids))
        wiz = self._board(plate, {ben.line_ids: {'ok': False, 'failed_part': 'all'}}, reason='adhesion')
        self.assertEqual((wiz.replate, wiz.replate_mode, wiz.replate_models, wiz.suggested_order_id),
                         (True, 'auto', 2, room))
        for bit in (room.name, 'machine picked at Start', '2 of 8 models', 'not sliced yet'):
            self.assertIn(bit, wiz.suggest_text)
        self.assertEqual(wiz.action_confirm()['res_id'], room.id)
        self.assertEqual((self._line(room, ben).part, room.patient_count, room.model_count), ('both', 2, 4))
        self.assertTrue(any('Re-plate of' in m.body and plate.name in m.body for m in room.message_ids))
        self.assertEqual(self._line(plate, ben).replated_by_id, self._line(room, ben))

    def test_the_plate_planned_on_the_machine_just_freed_is_offered_first(self):
        self._unplanned(self._register('U1').line_ids)
        here = self._plate(self._register('H1').line_ids, printer=self.printer)
        down = self._plate(self._register('D1').line_ids, printer=self.p2)
        self.p2.state = 'maintenance'
        anu = self._register('Anu')
        plate = self._start(self._plate(anu.line_ids, printer=self.printer))
        self.assertEqual(self.Order._suggest_plate(2, machine=self.printer, exclude=plate, company=self.company), here)
        self.assertNotEqual(self.Order._suggest_plate(2, company=self.company), down,
                            "a plate planned on a machine out of service is not offered")
        wiz = self._board(plate, {anu.line_ids: {'ok': False, 'failed_part': 'all'}}, reason='power',
                          replate_mode='same')
        self.assertFalse(wiz.same_text, "the machine is in service")
        self.printer.state = 'maintenance'
        wiz = self._board(plate, {anu.line_ids: {'ok': False, 'failed_part': 'all'}}, reason='power',
                          replate_mode='same')
        self.assertIn('is not in service', wiz.same_text, "said, not done silently")
        replate = self.Order.browse(wiz.action_confirm()['res_id'])
        self.assertFalse(replate.printer_id, "unplanned: the machine is picked at Start")

    def test_auto_makes_a_new_plate_when_no_plate_has_room(self):
        full = self._unplanned(self._register('F1', 'F2', 'F3', 'F4').line_ids)
        anu = self._register('Anu')
        plate = self._start(self._plate(anu.line_ids))
        wiz = self._board(plate, {anu.line_ids: {'ok': False, 'failed_part': 'all'}}, reason='adhesion')
        self.assertFalse(wiz.suggested_order_id)
        self.assertIn('No plate being plated has room', wiz.suggest_text)
        replate = self.Order.browse(wiz.action_confirm()['res_id'])
        self.assertNotEqual(replate, full)
        self.assertEqual((replate.state, replate.patient_count), ('draft', 1))
        self.assertFalse(replate.printer_id, "its machine is picked at Start")
        self.assertEqual(full.patient_count, 4)

    def test_one_plate_off_makes_the_next_plate_with_the_patients_waiting(self):
        """Ben's lower and the patients waiting, by urgency, up to what a plate holds;
        one who does not fit is passed over for a smaller one who does."""
        w1, w2, w3 = self._register('W1'), self._register('W2'), self._register('W3')
        urgent = self._register('Urgent', priority='1')
        small = self._register(('Small', 'upper retainer'))
        anu, ben = self._register('Anu'), self._register('Ben')
        plate = self._start(self._plate((anu | ben).line_ids))
        ctx = plate.action_plate_off()['context']
        self.assertTrue(ctx.get('default_fill'), "Plate Off opens ready to make the next plate")
        wiz = self._board(plate, {ben.line_ids: {'ok': False, 'failed_part': 'lower'}}, context=ctx,
                          reason='supports')
        self.assertTrue(wiz.fill)
        self.assertEqual(wiz.fill_line_ids, (urgent | w1 | w2 | small).line_ids, "W3's two do not fit the last one")
        self.assertEqual(wiz.capacity_text, '1 re-plated + 7 waiting = 8 of 8 models')
        self.assertFalse(wiz.over_capacity)
        nxt = self.Order.browse(wiz.action_confirm()['res_id'])
        self.assertEqual((nxt.patient_count, nxt.model_count), (5, 8))
        self.assertEqual(self._line(nxt, ben).part, 'lower')
        self.assertEqual(self._line(nxt, ben).replated_from_id, self._line(plate, ben))
        self.assertEqual(w3.line_ids.print_state, 'to_plate')
        self.assertEqual(set((urgent | w1 | w2 | small).line_ids.mapped('print_state')), {'plated'})

    def test_waiting_patients_need_a_plate_to_go_on(self):
        w1 = self._register('W1')
        anu = self._register('Anu')
        plate = self._start(self._plate(anu.line_ids))
        with self.assertRaises(UserError):
            plate._plate_off({plate.line_ids.id: ('failed', 'power', False, 'all')}, replate_on=None,
                             routes={anu.line_ids.id: None}, fill=w1.line_ids)

    def test_wait_leaves_a_patient_waiting_while_the_others_go_on_a_plate(self):
        anu, ben, cia = self._register('Anu'), self._register('Ben'), self._register('Cia')
        plate = self._start(self._plate((anu | ben | cia).line_ids))
        wiz = self._board(plate, {anu.line_ids: {'ok': False, 'failed_part': 'all', 'hold': True},
                                  ben.line_ids: {'ok': False, 'failed_part': 'all'}},
                          reason='adhesion', replate_mode='new')
        self.assertEqual((wiz.failed_count, wiz.failed_models, wiz.replate_models), (2, 4, 2),
                         "Anu waits: only Ben's two go on a plate")
        replate = self.Order.browse(wiz.action_confirm()['res_id'])
        self.assertEqual(replate.line_ids.scan_line_id, ben.line_ids)
        self.assertEqual((anu.line_ids.print_state, ben.line_ids.print_state), ('to_plate', 'plated'))
        self.assertEqual((anu.replate_waiting_count, ben.replate_waiting_count), (1, 0))
        self.assertEqual((anu.replate_pending_count, ben.replate_pending_count), (1, 1))
        self.assertIn('Anu → waiting for a plate', plate.failed_summary)
        self.assertIn('Ben → %s' % replate.name, plate.failed_summary)

    def test_the_plate_s_note_reaches_every_failed_model(self):
        anu, ben, cia = self._register('Anu'), self._register('Ben'), self._register('Cia')
        plate = self._start(self._plate((anu | ben | cia).line_ids))
        self._board(plate, {anu.line_ids: {'ok': False, 'failed_part': 'all'},
                            ben.line_ids: {'ok': False, 'failed_part': 'all', 'note': 'cracked base'}},
                    reason='adhesion', note='Vat film cloudy', replate=False).action_confirm()
        self.assertEqual((self._line(plate, anu).failure_note, self._line(plate, ben).failure_note),
                         ('Vat film cloudy', 'cracked base'), "a row's own note beats the plate's")
        self.assertFalse(self._line(plate, cia).failure_note)
        self.assertEqual(plate.failure_note, 'Vat film cloudy')
        self.assertTrue(self._said(plate, 'Vat film cloudy'))

    # ------------------------------------------------------------------ a model failed mid-print
    def test_one_arch_lost_mid_print_is_settled_at_plate_off(self):
        anu, ben = self._register('Anu'), self._register('Ben')
        plate = self._start(self._plate((anu | ben).line_ids))
        ctx = plate.action_model_failed()['context']
        self.assertEqual(ctx.get('default_mode'), 'lost')
        wiz = self._board(plate, {anu.line_ids: {'ok': False, 'failed_part': 'upper'}}, mode='lost', context=ctx,
                          reason='supports')
        self.assertEqual(wiz.replate_models, 0, "Anu's lower is still printing: nothing to re-plate yet")
        self.assertFalse(wiz.fill_line_ids)
        wiz.action_record_failure()
        line = self._line(plate, anu)
        self.assertEqual((plate.state, line.result, line.lost_part, line.failed_models), ('printing', 'pending', 'upper', 1))
        self.assertEqual((plate.lost_count, plate.failed_models), (1, 1))
        self.assertEqual((anu.line_ids.print_state, anu.line_ids.replate_pending), ('printing', True))
        self.assertIn('lost mid-print on %s' % plate.name, anu.last_print_failure)
        self.assertIn('Anu (upper) lost mid-print, settled at Plate Off', plate.failed_summary)
        self.assertTrue(self._said(plate, 'A model failed mid-print'))
        # at Plate Off the row comes up with the upper lost; the lower printed
        off = self._board(plate, replate_mode='new')
        row = off.line_ids.filtered(lambda r: r.line_id == line)
        self.assertEqual((row.ok, row.upper_ok, row.lower_ok, row.failed_part, row.reason),
                         (False, False, True, 'upper', 'supports'))
        self.assertEqual(off.replate_models, 1)
        replate = self.Order.browse(off.action_confirm()['res_id'])
        self.assertEqual((line.result, line.failed_part, line.printed_arches, line.lost_part, line.failure_reason),
                         ('failed', 'upper', 'lower', False, 'supports'))
        self.assertEqual((plate.state, self._line(plate, ben).result), ('washing', 'ok'))
        self.assertEqual((replate.line_ids.scan_line_id, replate.line_ids.part), (anu.line_ids, 'upper'))

    def test_a_patient_lost_whole_mid_print_goes_on_another_plate_at_once(self):
        cia = self._register(('Cia', 'upper retainer'))
        dan = self._register('Dan')
        plate = self._start(self._plate((cia | dan).line_ids))
        wiz = self._board(plate, {cia.line_ids: {'ok': False, 'failed_part': 'all'}}, mode='lost',
                          context=plate.action_model_failed()['context'], reason='adhesion', replate_mode='new')
        self.assertEqual(wiz.replate_models, 1)
        made = self.Order.browse(wiz.action_record_failure()['res_id'])
        line = self._line(plate, cia)
        self.assertEqual((line.result, line.failed_part, plate.state), ('failed', 'upper', 'printing'),
                         "the plate goes on for Dan")
        self.assertEqual((made.state, made.line_ids.scan_line_id, made.line_ids.part), ('draft', cia.line_ids, 'upper'))
        self.assertEqual(cia.line_ids.print_state, 'plated')
        self._off(plate)
        self.assertEqual((plate.state, plate.printed_models, plate.failed_models), ('washing', 2, 1))

    def test_every_model_lost_is_print_failed_and_a_loss_can_be_taken_back(self):
        anu = self._register('Anu')
        plate = self._start(self._plate(anu.line_ids))
        wiz = self._board(plate, {anu.line_ids: {'ok': False, 'failed_part': 'all'}}, mode='lost', reason='power')
        with self.assertRaises(UserError):
            wiz.action_record_failure()
        self.assertEqual(plate.line_ids.result, 'pending', "nothing written")
        self._board(plate, {anu.line_ids: {'ok': False, 'failed_part': 'lower'}}, mode='lost',
                    reason='supports').action_record_failure()
        self.assertEqual(plate.line_ids.lost_part, 'lower')
        plate.line_ids.action_clear_loss()
        self.assertEqual((plate.line_ids.lost_part, plate.line_ids.failure_reason, plate.lost_count), (False, False, 0))
        self.assertFalse(anu.line_ids.replate_pending)
        self._off(plate)
        self.assertEqual((plate.line_ids.result, plate.printed_models), ('ok', 2))

    def test_a_restart_prints_only_the_arch_the_plate_held(self):
        anu = self._register('Anu')
        first = self._start(self._plate(anu.line_ids))
        replate = self._off(first, failed=[(anu.line_ids, 'lower', 'supports')], replate=True, replate_mode='new')
        self._start(replate)
        restart = self._fail(replate, reason='power', restart=True, printer=self.p2)
        self.assertEqual(replate.line_ids.failed_part, 'lower')
        self.assertEqual((restart.line_ids.part, restart.line_ids.model_count, restart.line_ids.attempt),
                         ('lower', 1, 3), "a lower-only plate restarted prints the lower, not both")

    def test_print_again_prints_the_arch_that_broke(self):
        anu = self._register('Anu')
        self._off(self._start(self._plate(anu.line_ids)))
        action = anu.line_ids.action_print_again()
        wiz = self.env[action['res_model']].with_context(action['context']).create({})
        self.assertEqual((wiz.again, wiz.again_part, wiz.model_count), (True, 'both', 2))
        wiz.again_part = 'lower'
        self.assertEqual(wiz.model_count, 1, "the gauge counts the lower alone")
        again = self.Order.browse(wiz.action_create()['res_id'])
        self.assertEqual((again.line_ids.part, again.line_ids.model_count, again.line_ids.attempt), ('lower', 1, 2))
        self.assertFalse(again.line_ids.replated_from_id, "printed again - nothing failed on a plate")
        ben = self._register(('Ben', 'upper retainer'))
        self._off(self._start(self._plate(ben.line_ids, printer=self.p2)))
        action = ben.line_ids.action_print_again()
        wiz = self.env[action['res_model']].with_context(action['context']).create({'again_part': 'lower'})
        with self.assertRaises(UserError):
            wiz.action_create()

    # ------------------------------------------------------------------ the register
    def test_the_register_names_every_patient_missing_a_model_whatever_its_status(self):
        reg = self._register('Pia', 'Quin', 'Ravi')
        pia, quin, _ravi = reg.line_ids.sorted('id')
        plate = self._start(self._plate(reg.line_ids))
        self._off(plate, failed=[(pia, 'lower', 'supports'), (quin, 'all', 'layers')])
        self.assertEqual(reg.state, 'in_progress', "Ravi washes")
        self.assertEqual((reg.replate_pending_count, reg.replate_waiting_count), (2, 2))
        text = reg.last_print_failure
        self.assertIn('Pia (lower): Supports failed', text)
        self.assertIn('Quin: Layer shift or artefacts', text)
        self.assertLess(text.index('Quin'), text.index('Pia'), "newest first")
        tile = self.env.ref('lab_scan_register.tile_scan_print_failed')
        self.assertIn(reg, self.Scan.search(safe_eval(tile.domain)), "the tile sees Print In Progress too")
        search = self.env.ref('lab_scan_register.view_scan_search')
        arch = etree.fromstring(self.Scan.get_view(search.id, 'search')['arch'])
        for name in ('failed_in_queue', 'failed_pending'):
            [node] = arch.xpath("//filter[@name='%s']" % name)
            self.assertIn(reg, self.Scan.search(safe_eval(node.get('domain'))), name)

    # ------------------------------------------------------------------ the dashboard
    def test_the_dashboard_is_the_scan_managers_and_the_administrators(self):
        def user(login, *groups):
            return self.env['res.users'].create({
                'name': login, 'login': login, 'company_id': self.company.id,
                'company_ids': [(6, 0, [self.company.id])],
                'group_ids': [(6, 0, [self.env.ref('base.group_user').id] + [self.env.ref(g).id for g in groups])]})
        desk = user('partial_desk', 'lab_scan_register.group_scan_user')
        manager = user('partial_manager', 'lab_scan_register.group_scan_manager')
        with self.assertRaises(AccessError):
            self.Scan.with_user(desk).get_dashboard('today')
        self.assertIn('replate_backlog', self.Scan.with_user(manager).get_dashboard('today'))
        admin = self.env.ref('base.user_admin')
        admin.write({'company_ids': [(4, self.company.id)]})
        self.assertIn('quality', self.Scan.with_user(admin).with_company(self.company).get_dashboard('month'))
        menu = self.env.ref('lab_scan_register.menu_scan_dashboard')
        self.assertEqual(menu.group_ids, self.env.ref('lab_scan_register.group_scan_manager'))
        Menu = self.env['ir.ui.menu']
        self.assertNotIn(menu.id, Menu.with_user(desk)._visible_menu_ids())
        self.assertIn(menu.id, Menu.with_user(manager)._visible_menu_ids())
        if 'lab.ceo.dashboard' not in self.env:
            return
        hub = 'lab_ceo_dashboard.group_lab_executive'
        boss = user('partial_boss', hub)
        boss_manager = user('partial_boss_manager', hub, 'lab_scan_register.group_scan_manager')
        Hub = self.env['lab.ceo.dashboard']
        # a bare executive cannot read every screen's records: the hub logs those figures
        with mute_logger('odoo.addons.lab_ceo_dashboard.models.lab_ceo_dashboard'):
            offered = [r['key'] for r in Hub.with_user(boss).get_launchers()]
            offered_manager = [r['key'] for r in Hub.with_user(boss_manager).get_launchers()]
        self.assertNotIn('scans', offered, "not offered to an executive who is not the scan manager")
        self.assertIn('scans', offered_manager)

    def _scenario(self):
        """The printing room on one day: a plate that lost Anu's lower and the whole
        of Ben, Ben's re-plate lost again, a plate printing that lost Dan's upper, and
        Wendy waiting."""
        anu, ben = self._register('Anu'), self._register('Ben')
        cia = self._register(('Cia', 'upper retainer'),
                             date_delivery=fields.Date.context_today(self.env.user) + timedelta(days=3))
        first = self._start(self._plate((anu | ben | cia).line_ids))
        replate = self.Order.browse(self._board(first, {
            anu.line_ids: {'ok': False, 'failed_part': 'lower', 'reason': 'supports', 'hold': True},
            ben.line_ids: {'ok': False, 'failed_part': 'all'}}, reason='adhesion', replate_mode='new')
            .action_confirm()['res_id'])
        self._off(self._start(replate), failed=ben.line_ids, reason='layers')
        dan, eva = self._register('Dan'), self._register('Eva')
        running = self._start(self._plate((dan | eva).line_ids, printer=self.p2))
        self._board(running, {dan.line_ids: {'ok': False, 'failed_part': 'upper'}}, mode='lost',
                    reason='supports').action_record_failure()
        wendy = self._register('Wendy')
        return {'anu': anu, 'ben': ben, 'cia': cia, 'dan': dan, 'eva': eva, 'wendy': wendy,
                'first': first, 'replate': replate, 'running': running}

    def test_the_board_counts_arches_and_flags_what_needs_the_room(self):
        s = self._scenario()
        d = self.Scan.get_dashboard('month')
        h = d['headline']
        self.assertEqual((h['models_printed'], h['models_failed'], h['patients_printed'], h['patients_failed']),
                         (2, 6, 2, 4), "printed: Anu's upper, Cia; lost: Anu's lower, Ben twice, Dan's upper")
        self.assertEqual((h['models_upper'], h['models_lower'], h['half_failed'], h['plates_partial']), (2, 0, 1, 1))
        att = {a['key']: a['count'] for a in d['attention']}
        self.assertEqual({k: att[k] for k in ('failed', 'half_printed', 'failed_twice', 'lost_mid_print',
                                              'replate_waiting')},
                         {'failed': 2, 'half_printed': 1, 'failed_twice': 1, 'lost_mid_print': 1,
                          'replate_waiting': 2})
        backlog = d['replate_backlog']
        self.assertEqual((backlog['count'], backlog['models'], backlog['failed_twice']), (2, 3, 1))
        self.assertEqual([(r['patient'], r['part_key'], r['models'], r['attempts']) for r in backlog['rows']],
                         [('Anu', 'lower', 1, 1), ('Ben', 'both', 2, 2)])
        self.assertEqual(self.env[backlog['model']].search_count(backlog['domain']), backlog['count'])
        cards = {p['id']: p for p in d['printers']}
        mine, mars = cards[self.printer.id], cards[self.p2.id]
        self.assertEqual((mine['plates'], mine['models_done'], mine['models_failed'], mine['failure_rate'],
                          mine['patients'], mine['patients_done'], mine['patients_failed']), (2, 2, 5, 71, 4, 2, 3))
        self.assertEqual((mars['running_order']['failed_models'], mars['running_order']['lost']), (1, 1))
        recent = {p['id']: p for p in d['recent_plates']}
        first = recent[s['first'].id]
        self.assertEqual((first['printed_models'], first['failed_models'], first['replated_on']),
                         (2, 3, s['replate'].name))
        self.assertTrue(recent[s['replate'].id]['replate'])

    def test_the_board_s_new_panels_open_exactly_their_rows(self):
        self._scenario()
        d = self.Scan.get_dashboard('month')
        q = d['quality']
        model = d['models_failed_model']
        checks = []
        for row in q['matrix']['rows']:
            checks.append((model, row['domain'], row['rows']))
            checks += [(model, c['domain'], c['rows']) for c in row['cells']]
        checks += [(model, c['domain'], c['rows']) for c in q['matrix']['total_row']['cells']]
        checks += [(model, a['domain'], a['rows']) for a in q['by_attempt']]
        checks += [(model, b['domain'], b['rows']) for b in q['by_batch']]
        checks += [(model, p['domain'], p['rows']) for p in d['print_trend']]
        checks += [(model, r['domain'], r['rows']) for r in d['failure_reasons']]
        checks += [(model, fp['domain'], fp['count']) for fp in d['failed_parts']]
        for item in d['attention']:
            checks.append((item['model'], item['domain'], item['count']))
        for model_, domain, count in checks:
            with self.subTest(model=model_, domain=domain):
                self.assertEqual(self.env[model_].search_count(domain), count)
        cells = {(row['printer']['id'], c['reason']): c['count'] for row in q['matrix']['rows'] for c in row['cells']}
        self.assertEqual((cells[(self.printer.id, 'supports')], cells[(self.printer.id, 'adhesion')],
                          cells[(self.printer.id, 'layers')], cells[(self.p2.id, 'supports')]), (1, 2, 2, 1))
        self.assertEqual(q['matrix']['total_row']['total'], 6)
        attempts = {a['attempt']: (a['ok'], a['failed'], a['rows']) for a in q['by_attempt']}
        self.assertEqual(attempts, {1: (2, 4, 4), 2: (0, 2, 1)}, "re-plates fail again: the second print")
        self.assertEqual([(r['patient'], r['attempts']) for r in q['repeat']], [('Ben', 2)])
        self.assertEqual(sum(p['ok'] for p in d['print_trend']), 2)
        self.assertEqual(sum(p['failed'] for p in d['print_trend']), 6)
        self.assertEqual((q['on_time']['with_due'], q['on_time']['on_time']), (1, 1), "Cia, ready days early")

    def test_the_dashboard_scrolls_in_its_own_pane(self):
        """Odoo's action pane hides its overflow on a desktop, so the board scrolls
        inside: a pinned head over a full-width scroller (client, 2026-10-23:
        "scroll is not working in the existing dashboard")."""
        with open(os.path.join(DASHBOARD, 'scan_dashboard.xml')) as f:
            xml = f.read()
        with open(os.path.join(DASHBOARD, 'scan_dashboard.scss')) as f:
            scss = f.read()
        root = re.search(r'<t t-name="lab_scan_register\.ScanDashboard">\s*(?:<!--.*?-->\s*)*<div class="([^"]+)"',
                         xml, re.S)
        self.assertTrue(root, "the template's root")
        self.assertLessEqual({'o_sd', 'o_action'}, set(root.group(1).split()), "the root is the action")
        self.assertRegex(xml, r'class="o_sd_scroll"[^>]*t-ref="this\.scrollRef"')
        rule = re.search(r'>\s*\.o_sd_scroll\s*\{([^}]*)\}', scss)
        self.assertTrue(rule, "the scroller's rule")
        for prop in ('overflow-y: auto', 'min-height: 0', 'flex: 1 1 auto'):
            self.assertIn(prop, rule.group(1))
        # inside the Management hub the body does not overflow: a contained overscroll
        # there would stop the wheel before the hub's own pane
        self.assertNotRegex(rule.group(1), r'(?m)^\s*overscroll-behavior:\s*contain')

    # ------------------------------------------------------------------ the upgrade
    def test_the_upgrade_moves_the_seeded_tile_and_leaves_an_edited_one(self):
        spec = importlib.util.spec_from_file_location('scan_register_3_8_0_post', MIGRATION)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        tile = self.env.ref('lab_scan_register.tile_scan_print_failed')
        tile.domain = module.OLD
        module.migrate(self.env.cr, '19.0.3.7.0')
        tile.invalidate_recordset(['domain'])
        self.assertEqual(tile.domain, module.NEW)
        tile.domain = "[('state', '=', 'printing')]"
        module.migrate(self.env.cr, '19.0.3.7.0')
        tile.invalidate_recordset(['domain'])
        self.assertEqual(tile.domain, "[('state', '=', 'printing')]", "the lab's own edit stays")
