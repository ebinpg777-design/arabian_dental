# -*- coding: utf-8 -*-
"""Confirmed means settled; draft means editable; the order is the point of no return.
(client, 2026-10-11)"""
import base64

from lxml import etree

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged

from ..models.scan import LOCKED_FIELDS


@tagged('post_install', '-at_install')
class TestScanLock(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clinic = cls.env['res.partner'].create({'name': 'Lock Clinic', 'is_company': True})
        cls.group = cls.env['lab.scan.channel'].create({'name': 'Lock Group', 'kind': 'whatsapp'})
        cls.product = cls.env['product.product'].create({
            'name': 'Lock Appliance', 'type': 'consu', 'sale_ok': True, 'list_price': 500})

    def _confirmed(self):
        scan = self.env['lab.scan'].create({
            'channel_id': self.group.id, 'partner_id': self.clinic.id,
            'patient': 'Diya', 'product_id': self.product.id})
        scan.action_confirm()
        return scan

    # ------------------------------------------------------------------ the lock
    def test_a_draft_scan_can_be_edited(self):
        scan = self.env['lab.scan'].create({'channel_id': self.group.id})
        scan.write({'patient': 'Changed', 'notes': 'fine'})
        self.assertEqual(scan.patient, 'Changed')

    def test_a_confirmed_scan_cannot_be_edited(self):
        scan = self._confirmed()
        for vals in ({'patient': 'Someone else'}, {'product_id': False}, {'notes': 'x'},
                     {'delivery_requested': '2026-12-01'}, {'channel_id': self.group.id}):
            with self.subTest(vals=vals), self.assertRaises(UserError):
                scan.write(vals)
        self.assertEqual(scan.patient, 'Diya')

    def test_the_systems_own_bookkeeping_carries_on_after_confirming(self):
        """Files still arrive, the doctor can still be told, and the order still gets made."""
        scan = self._confirmed()
        att = self.env['ir.attachment'].create({
            'name': 'upper.stl', 'res_model': scan._name, 'res_id': scan.id,
            'raw': b'solid'})
        scan.message_post(body='files', attachment_ids=att.ids)
        self.assertEqual(scan.file_count, 1)
        scan.action_create_order()
        self.assertEqual(scan.state, 'ordered')

    def test_registered_by_is_read_only_in_the_model(self):
        self.assertTrue(self.env['lab.scan']._fields['user_id'].readonly)
        self.assertEqual(self.env['lab.scan'].create({'channel_id': self.group.id}).user_id,
                         self.env.user, "and still filled in by itself")

    # ------------------------------------------------------------------ reset
    def test_reset_to_draft_makes_it_editable_again(self):
        scan = self._confirmed()
        scan.action_reset()
        self.assertEqual(scan.state, 'new')
        self.assertFalse(scan.confirmed_by_id)
        scan.write({'patient': 'Diya S Nair'})
        self.assertEqual(scan.patient, 'Diya S Nair')

    def test_a_cancelled_scan_can_be_reset_too(self):
        scan = self.env['lab.scan'].create({'channel_id': self.group.id})
        scan.action_cancel()
        scan.action_reset()
        self.assertEqual(scan.state, 'new')

    def test_once_the_order_exists_there_is_no_going_back(self):
        scan = self._confirmed()
        scan.action_create_order()
        with self.assertRaises(UserError):
            scan.action_reset()
        self.assertEqual(scan.state, 'ordered')

    # ------------------------------------------------------------------ the doors
    def test_the_status_moves_only_through_the_buttons(self):
        """A kanban card dragged from Draft to Confirmed would skip Confirm's checks -
        this one has no product - and one dragged out of Ordered would orphan the order."""
        scan = self.env['lab.scan'].create({'channel_id': self.group.id})
        with self.assertRaises(UserError):
            scan.write({'state': 'confirmed'})
        self.assertEqual(scan.state, 'new')

    def test_the_kanban_cannot_drag_cards_between_states(self):
        arch = self.env['lab.scan'].get_view(
            self.env.ref('lab_scan_register.view_scan_kanban').id, 'kanban')['arch']
        self.assertEqual(etree.fromstring(arch).get('records_draggable'), '0')

    def test_every_editable_field_on_the_form_locks_once_confirmed(self):
        """So a field added to the form later cannot quietly stay editable."""
        arch = etree.fromstring(self.env['lab.scan'].get_view(
            self.env.ref('lab_scan_register.view_scan_form').id, 'form')['arch'])
        unlocked = sorted({node.get('name') for node in arch.iter('field')
                           if node.get('name') in LOCKED_FIELDS and node.get('name') != 'user_id'
                           and "state != 'new'" not in (node.get('readonly') or '')})
        self.assertFalse(unlocked, "editable after confirming: %s" % unlocked)
