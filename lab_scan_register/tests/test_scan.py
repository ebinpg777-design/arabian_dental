# -*- coding: utf-8 -*-
"""The register itself: from a scan landing to the order it becomes."""
import base64
from datetime import date, timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import Form
from odoo.tests.common import TransactionCase, tagged

from . import fixtures as F


@tagged('post_install', '-at_install')
class TestScanRegister(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Smile Point Dental Care Feroke', 'is_company': True,
            'email': 'feroke@example.com'})
        cls.other_clinic = cls.env['res.partner'].create({
            'name': 'Kids Dental Clinic Calicut', 'is_company': True})
        cls.group = cls.env['lab.scan.channel'].create({
            'name': 'Smile Point Dental Care Feroke (WA)', 'kind': 'whatsapp',
            'partner_id': cls.clinic.id})
        cls.product = cls.env['product.product'].create({
            'name': "Hawley's retainer", 'type': 'consu', 'list_price': 1000.0, 'sale_ok': True})
        cls.user = cls.env['res.users'].create({
            'name': 'Front Desk', 'login': 'scan_front_desk',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('lab_scan_register.group_scan_user').id])]})

    def _scan(self, **vals):
        return self.env['lab.scan'].create(dict({
            'channel_id': self.group.id, 'partner_id': self.clinic.id,
            'patient': 'Tanvi', 'appliance': 'upper bilateral expansion',
            'product_id': self.product.id}, **vals))

    def _attach(self, scan, name='upper.stl', size=1024):
        return self.env['ir.attachment'].create({
            'name': name, 'res_model': scan._name, 'res_id': scan.id,
            'raw': b'x' * size})

    # ------------------------------------------------------------- registering
    def test_a_scan_gets_a_number(self):
        scan = self._scan()
        self.assertTrue(scan.name.startswith('SCN/'))
        self.assertEqual(scan.state, 'new')
        self.assertEqual(scan.source, 'whatsapp')

    def test_picking_the_group_fills_the_clinic(self):
        form = Form(self.env['lab.scan'])
        form.channel_id = self.group
        self.assertEqual(form.partner_id, self.clinic)

    def test_pasting_the_message_fills_the_empty_fields_only(self):
        form = Form(self.env['lab.scan'])
        form.channel_id = self.group
        form.appliance = 'typed by hand'
        form.pasted_message = F.WHATSAPP_RAHIM
        self.assertEqual(form.patient, 'Rahim VP')
        self.assertEqual(form.delivery_requested, date(2026, 10, 13))
        self.assertEqual(form.appliance, 'typed by hand', "never over what somebody typed")

    # ------------------------------------------------------------- confirming
    def test_confirming_needs_a_doctor_and_a_patient(self):
        scan = self._scan(partner_id=False)
        with self.assertRaises(UserError):
            scan.action_confirm()
        scan.partner_id = self.clinic
        scan.patient = False
        with self.assertRaises(UserError):
            scan.action_confirm()
        scan.patient = 'Tanvi'
        scan.product_id = False
        with self.assertRaises(UserError, msg="the product is mandatory"):
            scan.action_confirm()
        scan.product_id = self.product
        scan.with_user(self.user).action_confirm()
        self.assertEqual(scan.state, 'confirmed')
        self.assertEqual(scan.confirmed_by_id, self.user)

    def test_the_order_waits_for_confirmation(self):
        scan = self._scan()
        with self.assertRaises(UserError):
            scan.action_create_order()

    # ------------------------------------------------------------- the order
    def test_the_order_carries_everything_the_scan_knows(self):
        scan = self._scan(product_id=self.product.id, notes='call before casting',
                          delivery_requested=date(2026, 10, 13), external_ref='OJF-5570',
                          urgent=True)
        self._attach(scan, 'upper.stl')
        self._attach(scan, 'lower.stl')
        scan.action_confirm()
        action = scan.action_create_order()
        order = self.env['sale.order'].browse(action['res_id'])

        self.assertEqual(scan.state, 'ordered')
        self.assertEqual(scan.sale_order_id, order)
        self.assertEqual(order.scan_id, scan)
        self.assertEqual(order.partner_id, self.clinic)
        self.assertEqual(order.patient, 'Tanvi')
        self.assertEqual(order.instruction, 'upper bilateral expansion')
        self.assertEqual(order.date_due, date(2026, 10, 13))
        self.assertEqual(order.client_order_ref, 'OJF-5570')
        self.assertEqual(order.origin, scan.name)
        self.assertIn('call before casting', order.note)
        self.assertEqual(order.order_line.product_id, self.product)
        self.assertFalse(order.is_3d_model_print, "the lab's 3D Model Print means a printed model")
        self.assertEqual(order.scanned_impression, 'Yes', "and the job card says so")
        files = self.env['ir.attachment'].search([('res_model', '=', 'sale.order'),
                                                  ('res_id', '=', order.id)])
        self.assertEqual(sorted(files.mapped('name')), ['lower.stl', 'upper.stl'],
                         "the STL files travel with the order")

    def test_creating_the_order_twice_opens_the_same_one(self):
        scan = self._scan()
        scan.action_confirm()
        first = scan.action_create_order()['res_id']
        self.assertEqual(scan.action_create_order()['res_id'], first)
        self.assertEqual(self.env['sale.order'].search_count([('scan_id', '=', scan.id)]), 1)

    def test_an_ordered_scan_can_neither_be_cancelled_nor_deleted(self):
        scan = self._scan()
        scan.action_confirm()
        scan.action_create_order()
        with self.assertRaises(UserError):
            scan.action_cancel()
        with self.assertRaises(UserError):
            scan.unlink()

    # ------------------------------------------------------------- files + links
    def test_files_are_counted_as_they_arrive(self):
        scan = self._scan()
        self.assertFalse(scan.files_received)
        att = self._attach(scan, 'upper.stl', size=2048)
        scan.message_post(body='files', attachment_ids=att.ids)
        self.assertEqual(scan.file_count, 1)
        self.assertTrue(scan.files_received)
        self.assertIn('KB', scan.file_size)

    def test_the_link_state_says_what_the_register_needs_to_know(self):
        today = fields.Date.context_today(self.env.user)
        self.assertEqual(self._scan().link_state, 'none')
        self.assertEqual(self._scan(link='https://x', link_expires=today + timedelta(days=10)).link_state, 'ok')
        self.assertEqual(self._scan(link='https://x', link_expires=today + timedelta(days=1)).link_state, 'soon')
        self.assertEqual(self._scan(link='https://x', link_expires=today - timedelta(days=1)).link_state, 'expired')

    def test_the_link_state_can_be_searched_without_browser_date_maths(self):
        """The register's 'link expiring' filter goes through this, because a search
        domain runs in py.js, which has no date arithmetic."""
        today = fields.Date.context_today(self.env.user)
        ok = self._scan(link='https://x', link_expires=today + timedelta(days=10))
        soon = self._scan(link='https://x', link_expires=today + timedelta(days=1))
        gone = self._scan(link='https://x', link_expires=today - timedelta(days=1))
        bare = self._scan()
        Scan = self.env['lab.scan']
        mine = lambda dom: Scan.search(dom) & (ok | soon | gone | bare)
        self.assertEqual(mine([('link_state', 'in', ('soon', 'expired'))]), soon | gone)
        self.assertEqual(mine([('link_state', '=', 'ok')]), ok)
        self.assertEqual(mine([('link_state', '=', 'none')]), bare)
        self.assertEqual(mine([('link_state', '!=', 'none')]), ok | soon | gone)

    def test_a_dying_link_with_no_files_raises_one_activity_not_two(self):
        today = fields.Date.context_today(self.env.user)
        scan = self._scan(link='https://wetransfer.com/x', link_expires=today + timedelta(days=1),
                          user_id=self.user.id)
        safe = self._scan(link='https://wetransfer.com/y', link_expires=today + timedelta(days=1))
        self._attach(safe)
        self.env['lab.scan']._cron_watch_links()
        self.env['lab.scan']._cron_watch_links()
        todo = scan.activity_ids.filtered(lambda a: 'link' in (a.summary or '').lower())
        self.assertEqual(len(todo), 1, "warned once, to the person who registered it")
        self.assertEqual(todo.user_id, self.user)
        self.assertFalse(safe.activity_ids, "files are in: nothing to chase")

    # ------------------------------------------------------------- duplicates
    def test_the_same_patient_twice_in_a_week_is_flagged_not_blocked(self):
        first = self._scan(patient='Rahim VP')
        second = self._scan(patient='rahim vp', channel_id=self.env.ref(
            'lab_scan_register.channel_wetransfer').id)
        self.assertEqual(second.duplicate_ids, first)
        self.assertEqual(first.duplicate_count, 1)
        second.action_confirm()                       # allowed: it is a warning

    def test_a_different_clinics_patient_of_the_same_name_is_not_a_duplicate(self):
        self._scan(patient='Diya', partner_id=self.other_clinic.id)
        mine = self._scan(patient='Diya')
        self.assertEqual(mine.duplicate_count, 0)

    def test_a_month_apart_is_a_new_scan(self):
        old = self._scan(patient='Tanvi')
        old.received_on = fields.Datetime.now() - timedelta(days=40)
        self.assertEqual(self._scan(patient='Tanvi').duplicate_count, 0)

    # ------------------------------------------------------------- rights
    def test_the_front_desk_registers_but_does_not_configure(self):
        scan = self._scan()
        scan.with_user(self.user).write({'notes': 'ok'})
        from odoo.exceptions import AccessError
        with self.assertRaises(AccessError):
            self.env['lab.scan.channel'].with_user(self.user).create({'name': 'x'})
        with self.assertRaises(AccessError):
            scan.with_user(self.user).unlink()
