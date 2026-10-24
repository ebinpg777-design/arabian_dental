# -*- coding: utf-8 -*-
"""The register: one arrival, many patients, an order each - via 3D printing."""
from datetime import date, timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form, tagged

from . import fixtures as F
from .common import ScanCase


@tagged('post_install', '-at_install')
class TestRegister(ScanCase):

    def test_a_register_gets_a_reference_and_starts_as_draft(self):
        scan = self._register()
        self.assertTrue(scan.name.startswith('SCN/'))
        self.assertEqual(scan.state, 'draft')
        self.assertEqual(scan.source, 'whatsapp')
        self.assertEqual(scan.patient_count, 1)
        self.assertIn('Tanvi', scan.display_name)

    def test_picking_the_group_fills_the_clinic(self):
        form = Form(self.Scan)
        form.channel_id = self.group
        self.assertEqual(form.partner_id, self.clinic)

    def test_pasting_messages_adds_a_patient_line_each(self):
        form = Form(self.Scan)
        form.channel_id = self.group
        form.message_text = F.CHAT_WEB_COPY
        scan = form.save()
        self.assertEqual(sorted(scan.line_ids.mapped('patient_name')), ['Ayesha Nadia. P', 'Rahim VP'])
        self.assertEqual(scan.line_ids.filtered(lambda l: l.patient_name == 'Rahim VP').date_delivery,
                         date(2026, 10, 13))
        self.assertEqual(scan.sender_name, 'ANJALI MENON K')

    def test_pasting_the_same_patient_again_does_not_add_a_second_line(self):
        form = Form(self.Scan)
        form.channel_id = self.group
        form.message_text = F.WHATSAPP_RAHIM
        form.message_text = F.WHATSAPP_RAHIM + '\n'
        self.assertEqual(len(form.save().line_ids), 1)

    def test_the_register_sums_up_its_patients(self):
        scan = self._register(patients=(('A', 'x'), ('B', 'y')))
        scan.line_ids[0].date_delivery = date(2026, 10, 20)
        scan.line_ids[1].write({'date_delivery': date(2026, 10, 15), 'priority': '1'})
        self.assertEqual(scan.patient_count, 2)
        self.assertEqual(scan.patient_names, 'A, B')
        self.assertEqual(scan.date_delivery, date(2026, 10, 15), "the earliest one")
        self.assertEqual(scan.priority, '1', "urgent if any patient is")

    # ------------------------------------------------------------------ confirm
    def test_confirming_needs_named_patients(self):
        # the doctor or clinic is not one of them: it is needed at Create Orders only
        # (client, 2026-10-23)
        self._register(partner_id=False).action_confirm()
        with self.assertRaises(UserError):
            self._register(patients=()).action_confirm()
        with self.assertRaises(UserError):
            self._register(patients=(('', 'no name'),)).action_confirm()
        scan = self._register()
        scan.with_user(self.desk).action_confirm()
        self.assertEqual(scan.state, 'confirmed')
        self.assertEqual(scan.confirm_user_id, self.desk)

    def test_the_product_is_needed_to_make_the_orders_not_to_confirm(self):
        """The desk often learns the appliance after the scan arrives: the product is
        picked, and can be changed, right up to Create Orders. (client, 2026-10-14)"""
        scan = self._register(product=False, download_url='https://wetransfer.com/x')
        self._to_printed(scan)          # confirms on the way: no product needed for that
        self.assertEqual(scan.state, 'printed')
        with self.assertRaises(UserError, msg="the product is what the order is made of"):
            scan.action_create_orders()
        scan.line_ids.product_id = self.hawley          # on a printed register, still open
        scan.line_ids.product_id = self.expander        # and changed again
        scan.action_create_orders()
        self.assertEqual(scan.line_ids.sale_order_id.order_line.product_id, self.expander)
        with self.assertRaises(UserError, msg="settled with the order"):
            scan.line_ids.product_id = self.hawley

    # ------------------------------------------------------------------ 3D printing
    def test_the_steps_go_in_order(self):
        """The print order moves the register: Printing on the machine makes it Print
        In Progress, and only the plate's Printed (cured) makes it Printed."""
        scan = self._register(download_url='https://wetransfer.com/x')
        for early in (scan.action_send_to_print, scan.action_create_orders):
            with self.subTest(step=early.__name__), self.assertRaises(UserError):
                early()
        scan.action_confirm()
        with self.assertRaises(UserError):
            scan.action_create_orders()
        order = self._plate(scan.line_ids)
        self.assertEqual(scan.state, 'printing', "on a plate being made: sent for printing")
        self.assertTrue(scan.date_print_start)
        with self.assertRaises(UserError, msg="orders only once printed"):
            scan.action_create_orders()
        self._start(order)
        self.assertEqual(scan.state, 'in_progress')
        self._off(order)
        self.assertEqual(scan.state, 'in_progress', "off the machine, in wash & cure: still in progress")
        with self.assertRaises(UserError, msg="orders only once printed"):
            scan.action_create_orders()
        order.action_cure()
        self.assertEqual(scan.state, 'printed', "the plate is Printed, and so is the register")
        self.assertTrue(scan.date_printed)
        scan.action_create_orders()
        self.assertEqual(scan.state, 'done')

    def test_the_scans_are_attached_when_it_is_sent_for_printing(self):
        """The list's button opens the dialog; the dialog takes each patient's scans and
        will not send a patient who has neither scans nor a link. (client, 2026-10-14)"""
        scan = self._register()
        scan.action_confirm()
        action = scan.action_send_to_print()
        self.assertEqual(action['res_model'], 'lab.scan.print.order.make', "the New Print Order dialog")
        self.assertEqual(scan.state, 'confirmed', "the dialog moves it, not the button")
        with self.assertRaisesRegex(UserError, 'Attach the scans for Tanvi'):
            scan._send_to_print()
        wiz = self.env[action['res_model']].with_context(**action['context']).create({'printer_id': self.printer.id})
        self.assertFalse(wiz.line_ids.has_print_files)
        with self.assertRaisesRegex(UserError, 'Attach the scans for Tanvi'):
            wiz.action_create()
        self.assertEqual(scan.state, 'confirmed', "nothing was sent")
        # the scan uploaded in the patient's row, as the dialog saves it
        upper = self._file('upper.stl')
        wiz.write({'line_ids': [(1, scan.line_ids.id, {'attachment_ids': [(4, upper.id)]})]})
        self.assertTrue(wiz.line_ids.has_print_files)
        order = self.env['lab.scan.print.order'].browse(wiz.action_create()['res_id'])
        self.assertEqual(scan.state, 'printing')
        self.assertEqual(scan.line_ids.attachment_ids, upper, "the patient's own scan")
        self.assertIn(upper, scan.attachment_ids, "and the register's")
        self.assertEqual(order.files_missing_count, 0)
        self._start(order)
        self.assertEqual(scan.state, 'in_progress')

    # ------------------------------------------------------------------ orders
    def test_each_patient_becomes_an_order_with_their_own_files_and_the_shared_ones(self):
        scan = self._register(patients=(('Rahim VP', "Hawley's retainer"), ('Ayesha Nadia', 'Exis retainer')),
                              ref='OJF-5570')
        rahim, ayesha = scan.line_ids.sorted('patient_name')[1], scan.line_ids.sorted('patient_name')[0]
        m_file, f_file, bite = self._file('rahim.stl'), self._file('ayesha.stl'), self._file('bite.stl')
        scan.attachment_ids = [(6, 0, (m_file | f_file | bite).ids)]
        rahim.write({'attachment_ids': [(6, 0, m_file.ids)], 'date_delivery': date(2026, 10, 13),
                       'priority': '1', 'product_id': self.hawley.id})
        ayesha.write({'attachment_ids': [(6, 0, f_file.ids)], 'product_id': self.expander.id})
        self._to_printed(scan)
        scan.action_create_orders()

        self.assertEqual(scan.order_count, 2)
        for line, product, own in ((rahim, self.hawley, 'rahim.stl'), (ayesha, self.expander, 'ayesha.stl')):
            order = line.sale_order_id
            with self.subTest(patient=line.patient_name):
                self.assertEqual(order.patient, line.patient_name)
                self.assertEqual(order.partner_id, self.clinic)
                self.assertEqual(order.order_line.product_id, product)
                self.assertEqual(order.scan_id, scan)
                self.assertEqual(order.scan_line_id, line)
                self.assertEqual(order.client_order_ref, 'OJF-5570')
                self.assertFalse(order.is_3d_model_print, "the lab's 3D Model Print means a printed model")
                self.assertEqual(order.scanned_impression, 'Yes', "and the job card says so")
                files = self.env['ir.attachment'].search([('res_model', '=', 'sale.order'), ('res_id', '=', order.id)])
                self.assertEqual(sorted(files.mapped('name')), sorted([own, 'bite.stl']),
                                 "their own file and the shared bite, not the other patient's")
        self.assertEqual(rahim.sale_order_id.date_due, date(2026, 10, 13))
        self.assertEqual(rahim.sale_order_id.instruction, "Hawley's retainer")

    def test_a_single_patient_gets_every_file(self):
        scan = self._register()
        scan.attachment_ids = [(6, 0, (self._file('upper.stl') | self._file('lower.stl')).ids)]
        self._to_printed(scan)
        scan.action_create_orders()
        order = scan.line_ids.sale_order_id
        files = self.env['ir.attachment'].search([('res_model', '=', 'sale.order'), ('res_id', '=', order.id)])
        self.assertEqual(sorted(files.mapped('name')), ['lower.stl', 'upper.stl'])

    def test_orders_are_made_once(self):
        scan = self._to_printed(self._register())
        scan.action_create_orders()
        with self.assertRaises(UserError):
            scan.action_create_orders()
        self.assertEqual(self.env['sale.order'].search_count([('scan_id', '=', scan.id)]), 1)

    def test_a_register_with_orders_can_be_neither_cancelled_nor_deleted(self):
        scan = self._to_printed(self._register())
        scan.action_create_orders()
        for refused in (scan.action_cancel, scan.unlink):
            with self.subTest(action=refused.__name__), self.assertRaises(UserError):
                refused()

    # ------------------------------------------------------------------ files
    def test_files_uploaded_on_the_form_become_the_registers(self):
        loose = self._file('upper.stl', size=2048)            # as many2many_binary leaves it
        scan = self._register(attachment_ids=[(6, 0, loose.ids)])
        self.assertEqual((loose.res_model, loose.res_id), ('lab.scan', scan.id))
        self.assertEqual(scan.attachment_count, 1)
        self.assertTrue(scan.has_attachments)
        self.assertIn('KB', scan.attachment_size)

    def test_files_dropped_in_the_chatter_join_the_files(self):
        scan = self._register()
        att = self._file('lower.stl', scan=scan)
        scan.message_post(body='files', attachment_ids=att.ids)
        self.assertIn(att, scan.attachment_ids)
        self.assertEqual(scan.attachment_count, 1)

    def test_the_download_state(self):
        today = fields.Date.context_today(self.env.user)
        regs = {state: self._register(download_url='https://x', download_expiry=expiry)
                for state, expiry in (('ok', today + timedelta(days=10)), ('soon', today + timedelta(days=1)),
                                      ('expired', today - timedelta(days=1)))}
        regs['none'] = self._register()
        for state, scan in regs.items():
            self.assertEqual(scan.download_state, state)
        mine = self.Scan.browse([s.id for s in regs.values()])
        self.assertEqual(self.Scan.search([('download_state', 'in', ('soon', 'expired'))]) & mine,
                         regs['soon'] | regs['expired'])

    def test_a_dying_link_with_no_files_raises_one_activity_not_two(self):
        today = fields.Date.context_today(self.env.user)
        scan = self._register(download_url='https://wetransfer.com/x', download_expiry=today + timedelta(days=1),
                              user_id=self.desk.id)
        safe = self._register(download_url='https://wetransfer.com/y', download_expiry=today + timedelta(days=1))
        safe.attachment_ids = [(4, self._file().id)]
        self.Scan._cron_watch_links()
        self.Scan._cron_watch_links()
        todo = scan.activity_ids.filtered(lambda a: 'link' in (a.summary or '').lower())
        self.assertEqual(len(todo), 1)
        self.assertEqual(todo.user_id, self.desk)
        self.assertFalse(safe.activity_ids)

    # ------------------------------------------------------------------ duplicates, history, risk
    def test_the_same_patient_twice_in_a_week_is_flagged_not_blocked(self):
        first = self._register(patients=(('Rahim VP', 'x'),))
        second = self._register(patients=(('rahim vp', 'x'), ('Someone Else', 'y')))
        self.assertEqual(second.line_ids.filtered(lambda l: l.patient_name == 'rahim vp').duplicate_line_ids,
                         first.line_ids)
        self.assertEqual(second.duplicate_count, 1)
        second.action_confirm()

    def test_another_clinics_patient_of_the_same_name_is_not_a_duplicate(self):
        self._register(patients=(('Diya', 'x'),), partner_id=self.other_clinic.id)
        self.assertEqual(self._register(patients=(('Diya', 'x'),)).duplicate_count, 0)

    def test_earlier_orders_for_a_patient_are_counted_per_line(self):
        self.env['sale.order'].create({'partner_id': self.clinic.id, 'patient': 'Rahim VP'})
        scan = self._register(patients=(('rahim vp', 'x'), ('New Patient', 'y')))
        counts = {l.patient_name: l.history_count for l in scan.line_ids}
        self.assertEqual(counts, {'rahim vp': 1, 'New Patient': 0})

    def test_delivery_at_risk_is_searchable_on_the_register(self):
        today = fields.Date.context_today(self.env.user)
        soon = self._register()
        soon.line_ids.date_delivery = today + timedelta(days=1)
        later = self._register()
        later.line_ids.date_delivery = today + timedelta(days=9)
        self.assertTrue(soon.delivery_at_risk)
        self.assertFalse(later.delivery_at_risk)
        found = self.Scan.search([('delivery_at_risk', '=', True)])
        self.assertIn(soon, found)
        self.assertNotIn(later, found)

    # ------------------------------------------------------------------ rights
    def test_the_front_desk_registers_but_does_not_configure(self):
        scan = self._register()
        scan.with_user(self.desk).write({'note': 'ok'})
        with self.assertRaises(AccessError):
            self.env['lab.scan.channel'].with_user(self.desk).create({'name': 'x'})
        with self.assertRaises(AccessError):
            scan.with_user(self.desk).unlink()
