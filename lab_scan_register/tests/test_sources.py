# -*- coding: utf-8 -*-
"""The three sources, the WhatsApp group master, and what the register does with them."""
from datetime import datetime, timedelta

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import Form
from odoo.tests.common import TransactionCase, tagged

from . import fixtures as F


@tagged('post_install', '-at_install')
class TestSources(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.tz = 'Asia/Kolkata'
        cls.Scan = cls.env['lab.scan']
        cls.Channel = cls.env['lab.scan.channel']
        cls.malappuram = cls.env['res.partner'].create({'name': 'Malappuram Ortho Clinic', 'is_company': True})
        cls.calicut = cls.env['res.partner'].create({'name': 'Kids Dental Clinic Calicut', 'is_company': True})
        # a lab group carrying many clinics: no clinic of its own, its members have one each
        cls.group = cls.Channel.create({'name': 'ARABIAN DENTAL CALICUT', 'kind': 'whatsapp'})
        cls.poster = cls.env['lab.scan.channel.member'].create({
            'channel_id': cls.group.id, 'name': 'ANJALI MENON K', 'phone': '+91 90000 67890',
            'role': 'staff', 'partner_id': cls.malappuram.id})
        cls.product = cls.env['product.product'].create({
            'name': 'Hawley retainer', 'type': 'consu', 'sale_ok': True, 'list_price': 800})
        cls.wetransfer = cls.env.ref('lab_scan_register.channel_wetransfer')
        cls.drive = cls.env.ref('lab_scan_register.channel_google_drive')

    # ------------------------------------------------------------------ source
    def test_the_source_follows_the_channel(self):
        self.assertEqual(self.Scan.create({'channel_id': self.group.id}).source, 'whatsapp')
        self.assertEqual(self.Scan.create({'channel_id': self.wetransfer.id}).source, 'transfer')

    def test_a_channel_of_another_source_is_refused(self):
        with self.assertRaises(ValidationError):
            self.Scan.create({'source': 'email', 'channel_id': self.group.id})

    def test_switching_source_in_the_form_drops_a_channel_that_no_longer_fits(self):
        form = Form(self.Scan)
        form.channel_id = self.group
        self.assertEqual(form.source, 'whatsapp')
        form.source = 'transfer'
        self.assertFalse(form.channel_id)

    # ------------------------------------------------------------------ the group master
    def test_picking_the_poster_fills_their_clinic(self):
        form = Form(self.Scan)
        form.channel_id = self.group
        self.assertFalse(form.partner_id, "the group has no clinic of its own")
        form.member_id = self.poster
        self.assertEqual(form.partner_id, self.malappuram)

    def test_pasting_with_whatsapps_header_finds_the_poster_and_the_time(self):
        form = Form(self.Scan)
        form.channel_id = self.group
        form.message_text = F.CHAT_WEB_COPY.split('\n[4:02')[0]
        scan = form.save()
        self.assertEqual(scan.line_ids.patient_name, 'Rahim VP')
        self.assertEqual(scan.member_id, self.poster, "recognised among the group's members")
        self.assertEqual(scan.partner_id, self.malappuram)
        # 12:36 in the lab is 07:06 UTC
        self.assertEqual(scan.date_received, datetime(2026, 10, 11, 7, 6))

    def test_a_poster_shown_as_a_number_is_matched_by_number(self):
        self.assertEqual(self.group._match_member('+91 90000 67890'), self.poster)
        self.assertEqual(self.group._match_member('90000 67890'), self.poster)
        self.assertEqual(self.group._match_member('anjali menon k'), self.poster)
        self.assertFalse(self.group._match_member('Someone Else'))

    def test_a_new_poster_joins_the_member_list_by_themselves(self):
        scan = self.Scan.create({'channel_id': self.group.id, 'sender_name': '+91 90000 12345',
                                 'line_ids': [(0, 0, {'patient_name': 'Ayesha Nadia. P'})]})
        self.assertTrue(scan.member_id)
        self.assertEqual(scan.member_id.channel_id, self.group)
        self.assertEqual(scan.member_id.phone, '+91 90000 12345')
        again = self.Scan.create({'channel_id': self.group.id, 'sender_phone': '919000012345'})
        self.assertEqual(again.member_id, scan.member_id, "the second time, the same member")

    # ------------------------------------------------------------------ links
    def test_a_wetransfer_link_brings_its_expiry_with_it(self):
        form = Form(self.Scan)
        form.source = 'transfer'
        form.date_received = datetime(2026, 10, 10, 11, 0)
        form.download_url = 'https://wetransfer.com/downloads/51a1bff11af0bdec354c0b'
        self.assertEqual(form.channel_id, self.wetransfer, "the platform is known from the link")
        self.assertEqual(form.download_expiry, datetime(2026, 10, 13).date(), "three days after it arrived")

    def test_a_drive_link_does_not_expire(self):
        form = Form(self.Scan)
        form.source = 'transfer'
        form.download_url = 'https://drive.google.com/file/d/abc/view'
        self.assertEqual(form.channel_id, self.drive)
        self.assertFalse(form.download_expiry)

    def test_a_link_in_a_whatsapp_message_keeps_the_group_as_the_channel(self):
        form = Form(self.Scan)
        form.channel_id = self.group
        form.download_url = 'https://wetransfer.com/downloads/xyz'
        self.assertEqual(form.channel_id, self.group, "it still arrived by WhatsApp")
        self.assertTrue(form.download_expiry, "but the link still dies in three days")

    # ------------------------------------------------------------------ delivery + history
    def test_delivery_at_risk(self):
        today = fields.Date.context_today(self.env.user)
        soon = self.Scan.create({'channel_id': self.group.id, 'line_ids': [
            (0, 0, {'patient_name': 'A', 'date_delivery': today + timedelta(days=1)})]})
        later = self.Scan.create({'channel_id': self.group.id, 'line_ids': [
            (0, 0, {'patient_name': 'B', 'date_delivery': today + timedelta(days=9)})]})
        self.assertTrue(soon.delivery_at_risk)
        self.assertEqual(soon.line_ids.delivery_in_days, 1)
        self.assertFalse(later.delivery_at_risk)
        found = self.Scan.search([('delivery_at_risk', '=', True)])
        self.assertIn(soon, found)
        self.assertNotIn(later, found)
        self.assertIn(later, self.Scan.search([('delivery_at_risk', '=', False)]))

    def test_earlier_orders_for_the_same_patient_are_counted(self):
        self.env['sale.order'].create({'partner_id': self.malappuram.id, 'patient': 'Rahim VP'})
        self.env['sale.order'].create({'partner_id': self.calicut.id, 'patient': 'Rahim VP'})
        scan = self.Scan.create({'channel_id': self.group.id, 'partner_id': self.malappuram.id,
                                 'line_ids': [(0, 0, {'patient_name': 'rahim vp'})]})
        self.assertEqual(scan.line_ids.history_count, 1, "same patient, same clinic only")
        action = scan.line_ids.action_view_history()
        self.assertEqual(self.env['sale.order'].search_count(action['domain']), 1)


@tagged('post_install', '-at_install')
class TestPasteMessages(TransactionCase):
    """A run of messages, registered at once."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.tz = 'Asia/Kolkata'
        cls.clinic = cls.env['res.partner'].create({'name': 'Malappuram Ortho Clinic', 'is_company': True})
        cls.group = cls.env['lab.scan.channel'].create({'name': 'ARABIAN DENTAL CALICUT', 'kind': 'whatsapp',
                                                          'partner_id': cls.clinic.id})

    def _read(self, text):
        paste = self.env['lab.scan.paste'].create({'channel_id': self.group.id, 'text': text})
        paste.action_read()
        return paste

    def test_each_message_becomes_a_line_and_chat_is_left_out(self):
        paste = self._read(F.CHAT_ANDROID_EXPORT)
        self.assertEqual(len(paste.line_ids), 2)
        scan_line, chat_line = paste.line_ids.sorted('date_received')
        self.assertTrue(scan_line.include)
        self.assertEqual(scan_line.patient_name, 'Rahim VP')
        self.assertIn('UpperJawScan.stl', scan_line.file_names)
        self.assertFalse(chat_line.include)
        self.assertIn('chat', chat_line.note.lower())

    def test_registering_makes_the_scans_and_the_members(self):
        paste = self._read(F.CHAT_WEB_COPY)
        self.assertEqual(paste.selected_count, 2)
        action = paste.action_register()
        scans = self.env['lab.scan'].browse(action['domain'][0][2])
        self.assertEqual(len(scans), 2, "two senders, two registers")
        self.assertEqual(sorted(scans.line_ids.mapped('patient_name')), ['Ayesha Nadia. P', 'Rahim VP'])
        self.assertTrue(all(s.source == 'whatsapp' and s.channel_id == self.group for s in scans))
        self.assertEqual(set(scans.mapped('partner_id')), {self.clinic}, "the group's clinic")
        self.assertEqual(len(self.group.member_ids), 2, "both posters are on the list now")
        rahim = scans.filtered(lambda s: s.patient_names == 'Rahim VP')
        self.assertEqual(rahim.date_received, datetime(2026, 10, 11, 7, 6), "the message's own time")

    def test_one_sender_several_patients_is_one_register(self):
        text = ("[10:00 am, 11/10/2026] ANJALI MENON K: Patient name - Anu\nTPA\n"
                "[10:05 am, 11/10/2026] ANJALI MENON K: Patient name - Binu\nHawley retainer\n"
                "[10:07 am, 11/10/2026] +91 90000 12345: Patient name - Cini\nBite plate")
        action = self._read(text).action_register()
        scans = self.env['lab.scan'].browse(action['domain'][0][2])
        self.assertEqual(len(scans), 2, "Anjali's two patients together, the other sender apart")
        anjali = scans.filtered(lambda s: s.sender_name == 'ANJALI MENON K')
        self.assertEqual(anjali.line_ids.mapped('patient_name'), ['Anu', 'Binu'])

    def test_pasting_the_same_messages_twice_does_not_register_them_twice(self):
        self._read(F.CHAT_WEB_COPY).action_register()
        again = self._read(F.CHAT_WEB_COPY)
        self.assertFalse(again.line_ids.filtered('include'))
        self.assertTrue(all(l.existing_line_id for l in again.line_ids))
        with self.assertRaises(UserError):
            again.action_register()

    def test_nothing_pasted_says_so(self):
        paste = self.env['lab.scan.paste'].create({'channel_id': self.group.id, 'text': ''})
        with self.assertRaises(UserError):
            paste.action_read()
