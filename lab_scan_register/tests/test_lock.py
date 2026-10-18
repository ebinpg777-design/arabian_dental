# -*- coding: utf-8 -*-
"""Confirmed means settled; draft means editable; the orders are the point of no return."""
from lxml import etree

from odoo.exceptions import UserError
from odoo.tests import Form, tagged

from ..models.scan import FILE_FIELDS, LOCKED_FIELDS
from .common import ScanCase


@tagged('post_install', '-at_install')
class TestLock(ScanCase):

    def _confirmed(self):
        scan = self._register()
        scan.action_confirm()
        return scan

    def test_a_draft_can_be_edited(self):
        scan = self._register()
        scan.write({'note': 'fine'})
        scan.line_ids.write({'patient_name': 'Changed'})
        self.assertEqual(scan.line_ids.patient_name, 'Changed')

    def test_a_confirmed_register_cannot_be_edited(self):
        scan = self._confirmed()
        for vals in ({'note': 'x'}, {'partner_id': self.other_clinic.id}, {'channel_id': self.group.id}):
            with self.subTest(vals=vals), self.assertRaises(UserError):
                scan.write(vals)

    def test_its_patients_cannot_be_edited_added_or_removed(self):
        scan = self._confirmed()
        with self.assertRaises(UserError):
            scan.line_ids.write({'patient_name': 'Someone else'})
        with self.assertRaises(UserError):
            self.env['lab.scan.line'].create({'scan_id': scan.id, 'patient_name': 'Extra'})
        with self.assertRaises(UserError):
            scan.line_ids.unlink()

    def test_the_systems_bookkeeping_carries_on(self):
        scan = self._confirmed()
        att = self._file(scan=scan)
        scan.message_post(body='files', attachment_ids=att.ids)
        self.assertEqual(scan.attachment_count, 1, "files still arrive in the chatter")
        order = self._start(self._plate(scan.line_ids))
        self._off(order)
        order.action_cure()
        scan.action_create_orders()
        self.assertEqual(scan.state, 'done')

    def test_registered_by_is_read_only_and_filled_in(self):
        self.assertTrue(self.Scan._fields['user_id'].readonly)
        self.assertEqual(self._register().user_id, self.env.user)

    def test_reset_to_draft_reopens_it_from_any_step_before_the_orders(self):
        to_print = lambda s: s._send_to_print()
        plate = lambda s: self._plate(s.line_ids)
        start = lambda s: self._start(s.open_order_ids)
        printed = lambda s: self._off(s.current_order_ids)
        for steps in ((), (to_print,), (plate,), (plate, start), (plate, start, printed)):
            scan = self._confirmed()
            scan.with_context(scan_workflow=True).write({'download_url': 'https://x'})
            for step in steps:
                step(scan)
            with self.subTest(at=scan.state):
                scan.action_reset()
                self.assertEqual(scan.state, 'draft')
                self.assertFalse(scan.date_print_start)
                self.assertFalse(scan.printer_id)
                scan.line_ids.write({'patient_name': 'Editable again'})

    def test_a_cancelled_register_can_be_reset_too(self):
        scan = self._register()
        scan.action_cancel()
        scan.action_reset()
        self.assertEqual(scan.state, 'draft')

    def test_once_the_orders_exist_there_is_no_going_back(self):
        scan = self._to_printed(self._register())
        scan.action_create_orders()
        with self.assertRaises(UserError):
            scan.action_reset()

    def test_the_status_moves_only_through_the_buttons(self):
        scan = self._register(product=False)
        with self.assertRaises(UserError):
            scan.write({'state': 'confirmed'})
        self.assertEqual(scan.state, 'draft')

    def test_the_kanban_cannot_drag_cards_between_statuses(self):
        arch = self.Scan.get_view(self.env.ref('lab_scan_register.view_scan_kanban').id, 'kanban')['arch']
        self.assertEqual(etree.fromstring(arch).get('records_draggable'), '0')

    def _form_arch(self):
        return etree.fromstring(self.Scan.get_view(
            self.env.ref('lab_scan_register.view_scan_form').id, 'form')['arch'])

    def test_every_editable_field_on_the_form_locks_once_confirmed(self):
        """So a field added to the form later cannot quietly stay editable."""
        arch = self._form_arch()
        sheet_fields = [n for n in arch.iter('field') if not any(a.tag == 'list' for a in n.iterancestors())]
        unlocked = sorted({n.get('name') for n in sheet_fields
                           if n.get('name') in LOCKED_FIELDS - {'user_id', 'line_ids'}
                           and "state != 'draft'" not in (n.get('readonly') or '')})
        self.assertFalse(unlocked, "editable after confirming: %s" % unlocked)
        files = sorted({n.get('name') for n in sheet_fields if n.get('name') in FILE_FIELDS
                        and "state in ('done', 'cancel')" not in (n.get('readonly') or '')})
        self.assertFalse(files, "the link stays open until the orders are made: %s" % files)
        self.assertNotIn('attachment_ids', {n.get('name') for n in sheet_fields},
                         "the files are worked on the print order, not on the register")

    def test_the_patient_list_locks_once_confirmed(self):
        arch = self._form_arch()
        lines = arch.xpath("//field[@name='line_ids']")[0]
        self.assertIn("'create': [('state', '=', 'draft')]", lines.get('options'))
        self.assertIn("'delete': [('state', '=', 'draft')]", lines.get('options'))
        for col in lines.iter('field'):
            name = col.get('name')
            if name in ('line_ids', 'sale_order_id', 'delivery_at_risk', 'history_count', 'duplicate_count',
                        # where the patient's models are: computed, never typed
                        'print_state', 'current_order_id', 'current_printer_id', 'expected_end',
                        'last_failure', 'print_line_ids'):
                continue
            with self.subTest(column=name):
                self.assertNotEqual(name, 'attachment_ids', "a patient's files are picked on the print order")
                if name == 'product_id':
                    # open until the patient's order is made (client, 2026-10-14)
                    self.assertEqual(col.get('readonly'), "parent.state in ('done', 'cancel') or sale_order_id")
                else:
                    self.assertEqual(col.get('readonly'), "parent.state != 'draft'")
        self.assertEqual(lines.get('readonly'), "state in ('done', 'cancel')",
                         "the list stays open for the product and the file picks until the orders are made")

    def test_the_whatsapp_message_tab_is_there_in_draft_and_kept_once_pasted(self):
        page = self._form_arch().xpath("//page[@name='message']")[0]
        self.assertEqual(page.get('invisible'), "state != 'draft' and not message_text",
                         "any source while draft; read-only on record after that")

    # ------------------------------------------------------------------ files after Confirm
    def test_files_and_the_link_can_be_added_once_confirmed(self):
        """The files arrive after the case is confirmed. (client, 2026-10-13)"""
        scan = self._confirmed()
        scan.write({'download_url': 'https://wetransfer.com/x', 'download_expiry': '2026-10-20',
                    'attachment_ids': [(4, self._file('upper.stl').id)]})
        self.assertEqual(scan.attachment_count, 1)
        scan.line_ids.write({'attachment_ids': [(6, 0, scan.attachment_ids.ids)]})
        self.assertEqual(scan.line_ids.attachment_ids, scan.attachment_ids)

    def test_through_the_form_a_confirmed_register_takes_file_picks_but_not_other_edits(self):
        scan = self._confirmed()
        scan.attachment_ids = [(4, self._file('upper.stl').id)]
        line = scan.line_ids
        scan.write({'line_ids': [(1, line.id, {'attachment_ids': [(6, 0, scan.attachment_ids.ids)]})]})
        self.assertEqual(line.attachment_ids, scan.attachment_ids)
        with self.assertRaises(UserError):
            scan.write({'line_ids': [(1, line.id, {'patient_name': 'Someone else'})]})
        with self.assertRaises(UserError):
            scan.write({'line_ids': [(0, 0, {'patient_name': 'Extra'})]})

    def test_through_the_form_the_product_is_picked_while_the_register_prints(self):
        """The path the desk takes: the register's form, the patient's row, save."""
        scan = self._confirmed()
        scan.write({'download_url': 'https://wetransfer.com/x'})
        scan._send_to_print()
        self.assertEqual(scan.state, 'printing')
        with Form(scan) as form:
            with form.line_ids.edit(0) as row:
                row.product_id = self.expander
        self.assertEqual(scan.line_ids.product_id, self.expander)

    def test_the_product_stays_open_until_the_order_is_made(self):
        scan = self._confirmed()
        line = scan.line_ids
        scan.write({'line_ids': [(1, line.id, {'product_id': self.expander.id})]})
        self.assertEqual(line.product_id, self.expander, "through the form, confirmed")
        scan.write({'download_url': 'https://wetransfer.com/x'})
        scan._send_to_print()
        line.product_id = self.hawley
        self.assertEqual(line.product_id, self.hawley, "and while it prints")
        with self.assertRaises(UserError):
            line.write({'patient_name': 'Someone else'})

    def test_files_stay_open_through_printing_and_settle_with_the_orders(self):
        """The printing room adds the files on the plate, before and after it prints;
        once the orders are made from them, they are settled."""
        scan = self._confirmed()
        scan.write({'download_url': 'https://wetransfer.com/x', 'attachment_ids': [(4, self._file().id)]})
        scan._send_to_print()
        scan.write({'attachment_ids': [(4, self._file('late.stl').id)]})
        scan.line_ids.write({'attachment_ids': [(6, 0, scan.attachment_ids[:1].ids)]})
        self.assertEqual(scan.attachment_count, 2)
        order = self._start(self._plate(scan.line_ids))
        self._off(order)
        order.action_cure()
        scan.action_create_orders()
        for vals in ({'download_url': 'https://other'}, {'download_expiry': '2026-12-01'},
                     {'attachment_ids': [(4, self._file('later.stl').id)]}):
            with self.subTest(vals=vals), self.assertRaises(UserError):
                scan.write(vals)
        with self.assertRaises(UserError):
            scan.line_ids.write({'attachment_ids': [(5, 0, 0)]})

    def test_a_draft_saves_and_confirms_without_a_product(self):
        """The product is needed to make the orders, not before (client, 2026-10-14)."""
        form = Form(self.Scan)
        form.channel_id = self.group
        form.partner_id = self.clinic
        with form.line_ids.new() as line:
            line.patient_name = 'Diya'
        scan = form.save()
        self.assertFalse(scan.line_ids.product_id)
        scan.action_confirm()
        self.assertEqual(scan.state, 'confirmed')
        scan.line_ids.product_id = self.hawley
        self.assertEqual(scan.line_ids.product_id, self.hawley, "picked after Confirm")
